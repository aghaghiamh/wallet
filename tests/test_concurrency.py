from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import connections

from common.errors import DomainError
from provider.models import Account, Transfer
from provider.services import advance_transfer, create_transfer, seed_accounts
from tests.conftest import observation
from wallets.models import LedgerEntry, TopUp
from wallets.reconciliation import projection_mismatches, repair_pending_credit
from wallets.services import create_top_up, debit
from wallets.topups import apply_observation

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "provider"])


def race(*operations):
    barrier = Barrier(len(operations))

    def execute(operation):
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            try:
                return operation()
            except DomainError as exc:
                return exc.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(execute, operations))


def test_two_debits_cannot_overspend(wallet, fund):
    fund(wallet, 100)
    results = race(
        lambda: debit(wallet.user_id, uuid4(), 80),
        lambda: debit(wallet.user_id, uuid4(), 80),
    )
    assert sum(result == "insufficient_funds" for result in results) == 1
    wallet.refresh_from_db()
    assert wallet.balance == 20 and LedgerEntry.objects.count() == 2
    assert projection_mismatches() == []


def test_concurrent_same_requests_replay(wallet, fund):
    key = uuid4()
    results = race(
        lambda: create_top_up(wallet.user_id, key, 10, "source"),
        lambda: create_top_up(wallet.user_id, key, 10, "source"),
    )
    assert results[0].id == results[1].id and TopUp.objects.count() == 1
    fund(wallet, 100)
    key = uuid4()
    results = race(
        lambda: debit(wallet.user_id, key, 30),
        lambda: debit(wallet.user_id, key, 30),
    )
    assert results[0].id == results[1].id
    wallet.refresh_from_db()
    assert wallet.balance == 70 and LedgerEntry.objects.count() == 2


def test_two_tasks_settle_once(wallet):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    results = race(
        lambda: apply_observation(top_up.id, observation(top_up)),
        lambda: apply_observation(top_up.id, observation(top_up)),
    )
    assert results == ["SETTLED", "SETTLED"]
    wallet.refresh_from_db()
    assert wallet.balance == 100 and LedgerEntry.objects.count() == 1


def test_debit_races_with_credit(wallet):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    race(
        lambda: apply_observation(top_up.id, observation(top_up)),
        lambda: debit(wallet.user_id, uuid4(), 80),
    )
    wallet.refresh_from_db()
    assert wallet.balance in {20, 100}
    assert projection_mismatches() == []


def test_simultaneous_repairs_change_status_once(wallet):
    from tests.test_reconciliation import pending_credit

    top_up = pending_credit(wallet)
    results = race(
        lambda: repair_pending_credit(top_up.id, observation(top_up)),
        lambda: repair_pending_credit(top_up.id, observation(top_up)),
    )
    assert all(result[0] == "matched" for result in results)
    assert sum(result[2] for result in results) == 1
    top_up.refresh_from_db()
    wallet.refresh_from_db()
    assert top_up.status == "SETTLED" and top_up.provider_status == "SUCCEEDED"
    assert wallet.balance == 100 and wallet.last_sequence == 1
    assert LedgerEntry.objects.count() == 1 and projection_mismatches() == []


def test_repair_races_with_spending_without_changing_funds(wallet):
    from tests.test_reconciliation import pending_credit

    top_up = pending_credit(wallet)
    results = race(
        lambda: repair_pending_credit(top_up.id, observation(top_up)),
        lambda: debit(wallet.user_id, uuid4(), 80),
    )
    assert results[0] == ("matched", None, True, None)
    wallet.refresh_from_db()
    assert wallet.balance == 20 and wallet.last_sequence == 2
    assert LedgerEntry.objects.count() == 2 and projection_mismatches() == []


def test_provider_deduplicates_concurrent_creation_and_execution():
    seed_accounts()
    transfer_id = uuid4()
    params = {
        "source_account_id": "demo-customer-1",
        "destination_account_id": "platform-account",
        "amount": 100,
        "currency": "IRR",
    }
    creations = race(
        lambda: create_transfer(transfer_id, **params),
        lambda: create_transfer(transfer_id, **params),
    )
    assert sum(created for _, created in creations) == 1
    assert race(
        lambda: advance_transfer(transfer_id, force=True),
        lambda: advance_transfer(transfer_id, force=True),
    ) == ["SUCCEEDED", "SUCCEEDED"]
    assert Transfer.objects.count() == 1
    assert Account.objects.get(pk="platform-account").balance == 100
    assert Account.objects.get(pk="demo-customer-1").balance == 10_000_000_000 - 100


def test_provider_transfers_cannot_overspend_source():
    Account.objects.create(pk="source", balance=100)
    Account.objects.create(pk="destination", balance=0)
    ids = [uuid4(), uuid4()]
    for transfer_id in ids:
        create_transfer(
            transfer_id,
            source_account_id="source",
            destination_account_id="destination",
            amount=80,
            currency="IRR",
        )
    results = race(
        *(
            lambda transfer_id=transfer_id: advance_transfer(transfer_id, force=True)
            for transfer_id in ids
        )
    )
    assert sorted(results) == ["FAILED", "SUCCEEDED"]
    assert Account.objects.get(pk="source").balance == 20
    assert Account.objects.get(pk="destination").balance == 80
