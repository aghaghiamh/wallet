import os
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from provider.models import Transfer
from provider.serializers import TransferSerializer
from provider.services import create_transfer, seed_accounts
from wallets.services import create_top_up, create_wallet
from wallets.topups import apply_observation


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", help="Run real HTTP/Celery/Redis tests.")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration") or os.environ.get("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="Use --integration with Redis running.")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def observation(top_up, status="SUCCEEDED", **changes):
    data = {
        "id": str(top_up.id),
        "status": status,
        "amount": str(top_up.amount),
        "currency": top_up.currency,
        "source_account_id": top_up.source_account_id,
        "destination_account_id": top_up.destination_account_id,
        "failure_code": None,
    }
    return {**data, **changes}


class ServiceProvider:
    """Unit-test adapter backed by the simulator's separate PostgreSQL database."""

    def get(self, transfer_id):
        transfer = Transfer.objects.filter(pk=transfer_id).first()
        return dict(TransferSerializer(transfer).data) if transfer else None

    def create(self, top_up):
        transfer, _ = create_transfer(
            top_up.id,
            source_account_id=top_up.source_account_id,
            destination_account_id=top_up.destination_account_id,
            amount=top_up.amount,
            currency=top_up.currency,
        )
        return dict(TransferSerializer(transfer).data)


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def wallet():
    return create_wallet(uuid4())


@pytest.fixture
def fund():
    def settle(wallet, amount):
        top_up = create_top_up(wallet.user_id, uuid4(), amount, "demo-customer-1")
        apply_observation(top_up.id, observation(top_up))
        wallet.refresh_from_db()
        return top_up

    return settle


@pytest.fixture
def service_provider():
    seed_accounts()
    return ServiceProvider()
