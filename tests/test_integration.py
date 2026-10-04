import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import connections
from django.test import Client, override_settings
from kombu.exceptions import OperationalError

from config.celery import app
from provider.models import Account, Transfer
from provider.services import advance_transfer, seed_accounts
from wallets.models import LedgerEntry, TopUp
from wallets.provider_client import ProviderClient
from wallets.services import create_top_up
from wallets.tasks import dispatch_pending_top_ups, process_top_up_task
from wallets.topups import process_top_up

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db(transaction=True, databases=["default", "provider"]),
]


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for background processing")


@pytest.fixture
def http_provider():
    """Real socket boundary around Django provider views and its own durable DB."""
    seed_accounts()

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            connections.close_all()
            try:
                assert not connections["default"].in_atomic_block
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size)
                client = Client()
                if self.command == "GET":
                    response = client.get(self.path)
                else:
                    response = client.put(self.path, data=body, content_type="application/json")
                self.server.request_count += 1
                if self.command == "PUT" and self.server.drop_next_put:
                    self.server.drop_next_put = False
                    # Complete the transfer before losing the creation response.
                    advance_transfer(response.data["id"], force=True)
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)
            finally:
                connections.close_all()

        do_GET = handle_request
        do_PUT = handle_request

        def log_message(self, *args):
            pass

    with override_settings(ROOT_URLCONF="provider.urls"):
        server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        server.drop_next_put = False
        server.request_count = 0
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_lost_provider_response_is_recovered_over_http(wallet, http_provider):
    server, url = http_provider
    server.drop_next_put = True
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "demo-customer-1")
    client = ProviderClient(url, timeout=2)
    assert process_top_up(top_up.id, client) == "PENDING"
    top_up.refresh_from_db()
    assert top_up.error_code == "provider_unavailable"
    assert Transfer.objects.count() == 1
    assert Transfer.objects.get(pk=top_up.id).status == "SUCCEEDED"
    assert LedgerEntry.objects.count() == 0
    assert process_top_up(top_up.id, client) == "SETTLED"
    assert process_top_up(top_up.id, client) == "SETTLED"
    wallet.refresh_from_db()
    assert wallet.balance == 100 and LedgerEntry.objects.count() == 1
    assert Account.objects.get(pk="platform-account").balance == 100


def test_real_worker_recovers_missed_and_duplicate_notifications(
    wallet, http_provider, tmp_path, monkeypatch
):
    _, url = http_provider
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "demo-customer-1")
    # A broker failure does not erase the persisted intent.
    with monkeypatch.context() as patch:
        patch.setattr(
            process_top_up_task, "apply_async", Mock(side_effect=OperationalError("broker offline"))
        )
        assert dispatch_pending_top_ups() == 0
    assert TopUp.objects.get(pk=top_up.id).status == "PENDING"

    queue = f"wallet-test-{uuid4().hex}"
    old_queue = app.conf.task_default_queue
    app.conf.task_default_queue = queue
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PGDATABASE": connections["default"].settings_dict["NAME"],
        "PROVIDER_DATABASE": connections["provider"].settings_dict["NAME"],
        "PROVIDER_URL": url,
        "CELERY_BROKER_URL": settings.CELERY_BROKER_URL,
        "CELERY_QUEUE": queue,
    }
    log_path = tmp_path / "celery.log"
    try:
        with log_path.open("w") as log:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "config",
                    "worker",
                    "--pool=threads",
                    "--concurrency=2",
                    "--loglevel=INFO",
                    "--without-gossip",
                    "--without-mingle",
                    "-Q",
                    queue,
                    "-n",
                    f"{queue}@%h",
                ],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_until(lambda: " ready." in log_path.read_text() or worker.poll() is not None)
                assert worker.poll() is None, log_path.read_text()
                # Exercise the actual dispatcher via Redis, not eager mode.
                app.send_task("wallets.dispatch_pending_top_ups", queue=queue)
                wait_until(lambda: Transfer.objects.filter(pk=top_up.id).exists())
                advance_transfer(top_up.id, force=True)
                app.send_task("wallets.dispatch_pending_top_ups", queue=queue)
                app.send_task("wallets.process_top_up", args=[str(top_up.id)], queue=queue)
                app.send_task("wallets.process_top_up", args=[str(top_up.id)], queue=queue)
                wait_until(lambda: TopUp.objects.get(pk=top_up.id).status == "SETTLED")
                wallet.refresh_from_db()
                assert wallet.balance == 100
                assert LedgerEntry.objects.filter(top_up=top_up).count() == 1
                assert Transfer.objects.count() == 1
            except Exception:
                print(log_path.read_text())
                raise
            finally:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)
    finally:
        app.conf.task_default_queue = old_queue
