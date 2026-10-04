import json
from io import StringIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.db import DatabaseError, transaction
from kombu.exceptions import OperationalError

from common.serializers import MAX_INT
from provider.models import Account, Transfer
from provider.services import advance_transfer, create_transfer, seed_accounts
from tests.conftest import observation
from wallets.models import LedgerEntry, TopUp, Wallet
from wallets.provider_client import ProviderClient, ProviderIntegrityError, ProviderUnavailable
from wallets.reconciliation import projection_mismatches, reconcile_known_transfers
from wallets.services import create_top_up
from wallets.tasks import dispatch_pending_top_ups, process_top_up_task
from wallets.topups import apply_observation, process_top_up, reprocess_top_up

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "provider"])


@pytest.mark.parametrize(
    "source,expected",
    [("demo-failure", "FAILED"), ("demo-pending", "PENDING"), ("unknown", "FAILED")],
)
def test_provider_business_outcomes(wallet, service_provider, source, expected):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, source)
    process_top_up(top_up.id, service_provider)
    advance_transfer(top_up.id, force=True)
    assert process_top_up(top_up.id, service_provider) == expected
    wallet.refresh_from_db()
    assert wallet.balance == 0 and LedgerEntry.objects.count() == 0


def test_provider_atomic_rollback_and_destination_limit(monkeypatch):
    seed_accounts()
    transfer_id = uuid4()
    create_transfer(
        transfer_id,
        source_account_id="demo-customer-1",
        destination_account_id="platform-account",
        amount=100,
        currency="IRR",
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            Transfer, "save", Mock(side_effect=RuntimeError("crash before provider commit"))
        )
        with pytest.raises(RuntimeError):
            advance_transfer(transfer_id, force=True)
    assert Account.objects.get(pk="platform-account").balance == 0
    assert Account.objects.get(pk="demo-customer-1").balance == 10_000_000_000
    assert Transfer.objects.get(pk=transfer_id).status == "PENDING"
    Account.objects.filter(pk="platform-account").update(balance=MAX_INT)
    assert advance_transfer(transfer_id, force=True) == "FAILED"
    with pytest.raises(DatabaseError), transaction.atomic(using="provider"):
        Transfer.objects.filter(pk=transfer_id).update(amount=101)


def test_external_success_survives_crash_before_wallet_commit(
    wallet, service_provider, monkeypatch
):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "demo-customer-1")
    process_top_up(top_up.id, service_provider)
    advance_transfer(top_up.id, force=True)
    with monkeypatch.context() as patch:
        patch.setattr(LedgerEntry.objects, "create", Mock(side_effect=RuntimeError("crash")))
        with pytest.raises(RuntimeError):
            process_top_up(top_up.id, service_provider)
    assert Transfer.objects.get(pk=top_up.id).status == "SUCCEEDED"
    assert Account.objects.get(pk="platform-account").balance == 100
    assert TopUp.objects.get(pk=top_up.id).status == "PENDING"
    assert process_top_up(top_up.id, service_provider) == "SETTLED"
    assert process_top_up(top_up.id, service_provider) == "SETTLED"
    wallet.refresh_from_db()
    assert wallet.balance == 100 and LedgerEntry.objects.count() == 1


def test_timeout_is_uncertainty_and_mismatch_requires_review(wallet):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    client = Mock()
    client.get.side_effect = ProviderUnavailable
    assert process_top_up(top_up.id, client) == "PENDING"
    top_up.refresh_from_db()
    assert top_up.error_code == "provider_unavailable"
    client.get.side_effect = None
    client.get.return_value = observation(top_up, amount="101")
    assert process_top_up(top_up.id, client) == "REVIEW_REQUIRED"
    assert LedgerEntry.objects.count() == 0


def test_stale_observation_and_confirmed_missing_record(wallet, fund):
    top_up = fund(wallet, 100)
    assert apply_observation(top_up.id, observation(top_up, "PENDING")) == "SETTLED"
    top_up.refresh_from_db()
    assert top_up.provider_status == "SUCCEEDED"
    Wallet.objects.filter(pk=wallet.id).update(balance=MAX_INT)
    blocked = create_top_up(wallet.user_id, uuid4(), 1, "source")
    apply_observation(blocked.id, observation(blocked))
    reprocess_top_up(blocked.id)
    client = Mock()
    client.get.return_value = None
    assert process_top_up(blocked.id, client) == "REVIEW_REQUIRED"
    client.create.assert_not_called()
    report = reconcile_known_transfers(client)
    assert report["mismatched"] == 2
    client.create.assert_not_called()


def test_reconciliation_reports_known_transfers(wallet, service_provider, monkeypatch):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "demo-customer-1")
    process_top_up(top_up.id, service_provider)
    report = reconcile_known_transfers(service_provider)
    assert report["pending"] == 1
    advance_transfer(top_up.id, force=True)
    report = reconcile_known_transfers(service_provider)
    assert report["matched"] == 1 and report["projection_mismatches"] == []
    provider = Mock()
    provider.get.side_effect = ProviderUnavailable
    assert reconcile_known_transfers(provider)["unverified"] == 1
    provider.get.side_effect = None
    provider.get.return_value = observation(top_up, amount="999")
    assert reconcile_known_transfers(provider)["mismatched"] == 1
    assert LedgerEntry.objects.count() == 1  # Audit never edits confirmed history.
    Wallet.objects.filter(pk=wallet.id).update(balance=99)
    assert projection_mismatches() == [str(wallet.id)]
    Wallet.objects.filter(pk=wallet.id).update(balance=100)
    monkeypatch.setattr("wallets.reconciliation.ProviderClient", lambda: service_provider)
    output = StringIO()
    call_command("reconcile_known_transfers", "--once", stdout=output)
    assert json.loads(output.getvalue())["matched"] == 1


def test_dispatch_failure_preserves_intent_and_retry_recovers(wallet, monkeypatch):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    sender = Mock(side_effect=OperationalError("Redis offline"))
    monkeypatch.setattr(process_top_up_task, "apply_async", sender)
    assert dispatch_pending_top_ups() == 0
    assert TopUp.objects.get(pk=top_up.id).status == "PENDING"
    sender.side_effect = None
    assert dispatch_pending_top_ups() == 1
    assert sender.call_args.kwargs["args"] == [str(top_up.id)]


def test_http_adapter_rejects_bad_response_without_false_failure(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"status": "unknown"}
    monkeypatch.setattr("requests.request", Mock(return_value=response))
    with pytest.raises(ProviderIntegrityError):
        ProviderClient().get(uuid4())


def test_reconciliation_reports_pending_credit_without_posting_twice(wallet):
    from wallets.services import post_entry

    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    # Simulate inconsistent state from a buggy writer outside the supported workflow.
    with transaction.atomic():
        locked = Wallet.objects.select_for_update().get(pk=wallet.id)
        post_entry(locked, "credit", 100, top_up=top_up)
    client = Mock()
    client.get.return_value = observation(top_up)
    report = reconcile_known_transfers(client)
    assert report["mismatched"] == 1
    assert report["issues"][0]["code"] == "unexpected_credit"
    assert LedgerEntry.objects.count() == 1
    assert TopUp.objects.get(pk=top_up.id).status == "PENDING"


def test_reconciliation_tolerates_settlement_during_provider_check(wallet):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    client = Mock()

    calls = 0

    def racing_get(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            apply_observation(top_up.id, observation(top_up))
            return observation(top_up, "PENDING")
        return observation(top_up)

    client.get.side_effect = racing_get
    report = reconcile_known_transfers(client)
    assert report["matched"] == 1 and report["mismatched"] == 0
    assert report["projection_mismatches"] == []
