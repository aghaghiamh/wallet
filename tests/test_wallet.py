from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.db import DatabaseError, OperationalError, transaction
from psycopg.errors import DeadlockDetected

from common.errors import DomainError
from common.serializers import MAX_INT
from tests.conftest import observation
from wallets import topups
from wallets.models import LedgerEntry, TopUp, Wallet
from wallets.queries import history
from wallets.reconciliation import projection_mismatches
from wallets.serializers import TopUpStatusSerializer
from wallets.services import create_top_up, debit
from wallets.topups import apply_observation, process_top_up

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "provider"])


def base(wallet):
    return f"/v1/wallets/{wallet.user_id}"


def test_create_wallet_is_idempotent_and_never_resets(client, wallet, fund):
    fund(wallet, 100)
    response = client.put(base(wallet))
    assert response.status_code == 200
    assert response.data["balance"] == "100"
    assert response.data["currency"] == "IRR"
    assert Wallet.objects.count() == 1


def test_funding_and_spending_with_retry(client, wallet, service_provider):
    key = str(uuid4())
    request = {"amount": "1000", "source_account_id": "demo-customer-1"}
    response = client.post(
        base(wallet) + "/top-ups", request, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert response.status_code == 202
    receipt = dict(response.data)
    top_up = TopUp.objects.get(pk=receipt["id"])
    assert LedgerEntry.objects.count() == 0
    assert process_top_up(top_up.id, service_provider) == "PENDING"

    from provider.services import advance_transfer

    advance_transfer(top_up.id, force=True)
    assert process_top_up(top_up.id, service_provider) == "SETTLED"
    status = client.get(response["Location"])
    assert status.data["status"] == "SETTLED"
    assert status.data["ledger_entry_id"]

    repeated = client.post(
        base(wallet) + "/top-ups", request, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert repeated.status_code == 202 and repeated.data == receipt

    debit_key = str(uuid4())
    first = client.post(
        base(wallet) + "/debits", {"amount": "250"}, format="json", HTTP_IDEMPOTENCY_KEY=debit_key
    )
    replay = client.post(
        base(wallet) + "/debits", {"amount": "250"}, format="json", HTTP_IDEMPOTENCY_KEY=debit_key
    )
    assert first.status_code == replay.status_code == 201
    assert first.data == replay.data
    assert first.data["balance_after"] == "750"
    assert client.get(base(wallet)).data["balance"] == "750"
    assert LedgerEntry.objects.count() == 2
    assert projection_mismatches() == []


@pytest.mark.parametrize(
    "body",
    [
        {"amount": 10},
        {"amount": True},
        {"amount": "0"},
        {"amount": "-1"},
        {"amount": "1.5"},
        {"amount": "01"},
        {"amount": "1e3"},
        {"amount": " 10"},
        {"amount": "۱۰"},
        {"amount": "10", "extra": True},
        [],
    ],
)
def test_invalid_debit_is_atomic(client, wallet, body):
    response = client.post(
        base(wallet) + "/debits", body, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_request"
    wallet.refresh_from_db()
    assert wallet.balance == wallet.last_sequence == LedgerEntry.objects.count() == 0


@pytest.mark.parametrize("amount", [str(MAX_INT + 1), "9" * 100])
def test_amount_overflow(client, wallet, amount):
    response = client.post(
        base(wallet) + "/debits",
        {"amount": amount},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 422


def test_missing_key_missing_wallet_and_wrong_topup_wallet(client, wallet):
    response = client.post(base(wallet) + "/debits", {"amount": "1"}, format="json")
    assert response.status_code == 400
    assert client.get(f"/v1/wallets/{uuid4()}").status_code == 404
    top_up = create_top_up(wallet.user_id, uuid4(), 1, "demo-customer-1")
    other = Wallet.objects.create(user_id=uuid4())
    assert client.get(base(other) + f"/top-ups/{top_up.id}").status_code == 404


def test_insufficient_funds_does_not_reserve_key(client, wallet, fund):
    key = str(uuid4())
    url = base(wallet) + "/debits"
    rejected = client.post(url, {"amount": "80"}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert rejected.status_code == 409 and rejected.data["error"]["code"] == "insufficient_funds"
    assert LedgerEntry.objects.count() == 0
    fund(wallet, 100)
    accepted = client.post(url, {"amount": "80"}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    fund(wallet, 50)
    replay = client.post(url, {"amount": "80"}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert replay.data == accepted.data
    assert replay.data["balance_after"] == "20"
    assert client.get(base(wallet)).data["balance"] == "70"


def test_status_and_ledger_reference_share_one_snapshot(wallet):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    snapshot = TopUp.objects.select_related("ledger_entry").get(pk=top_up.id)
    apply_observation(top_up.id, observation(top_up))
    data = TopUpStatusSerializer(snapshot).data
    assert data["status"] == "PENDING" and data["ledger_entry_id"] is None
    current = TopUpStatusSerializer(
        TopUp.objects.select_related("ledger_entry").get(pk=top_up.id)
    ).data
    assert current["status"] == "SETTLED" and current["ledger_entry_id"]


def test_idempotency_conflicts_and_operation_scope(wallet, fund):
    key = uuid4()
    top_up = create_top_up(wallet.user_id, key, 100, "source")
    assert create_top_up(wallet.user_id, key, 100, "source").id == top_up.id
    for amount, source in [(101, "source"), (100, "other")]:
        with pytest.raises(DomainError, match="different parameters"):
            create_top_up(wallet.user_id, key, amount, source)
    fund(wallet, 100)
    entry = debit(wallet.user_id, key, 25)  # The key is scoped to operation.
    assert debit(wallet.user_id, key, 25).id == entry.id
    with pytest.raises(DomainError, match="different amount"):
        debit(wallet.user_id, key, 26)


def test_credit_and_debit_rollback(wallet, fund, monkeypatch):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    original = LedgerEntry.objects.create
    with monkeypatch.context() as patch:
        patch.setattr(LedgerEntry.objects, "create", Mock(side_effect=RuntimeError("injected")))
        with pytest.raises(RuntimeError):
            apply_observation(top_up.id, observation(top_up))
    wallet.refresh_from_db()
    top_up.refresh_from_db()
    assert wallet.balance == wallet.last_sequence == 0
    assert top_up.status == "PENDING" and top_up.provider_status is None
    assert LedgerEntry.objects.count() == 0
    assert LedgerEntry.objects.create == original
    fund(wallet, 100)
    with monkeypatch.context() as patch:
        patch.setattr(LedgerEntry.objects, "create", Mock(side_effect=RuntimeError("injected")))
        with pytest.raises(RuntimeError):
            debit(wallet.user_id, uuid4(), 25)
    wallet.refresh_from_db()
    assert wallet.balance == 100 and wallet.last_sequence == 1
    assert LedgerEntry.objects.count() == 1


def test_deadlock_retry_restarts_entire_transaction(wallet, monkeypatch):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    post = topups.post_entry
    calls = 0

    def deadlock_once(*args, **kwargs):
        nonlocal calls
        result = post(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise OperationalError("deadlock") from DeadlockDetected()
        return result

    monkeypatch.setattr(topups, "post_entry", deadlock_once)
    apply_observation(top_up.id, observation(top_up))
    wallet.refresh_from_db()
    assert calls == 2 and wallet.balance == 100 and LedgerEntry.objects.count() == 1


def test_bigint_strings_and_review_overflow(client, wallet, fund):
    fund(wallet, MAX_INT)
    assert client.get(base(wallet)).data["balance"] == str(MAX_INT)
    top_up = create_top_up(wallet.user_id, uuid4(), 1, "source")
    assert apply_observation(top_up.id, observation(top_up)) == "REVIEW_REQUIRED"
    top_up.refresh_from_db()
    assert top_up.provider_status == "SUCCEEDED" and top_up.error_code == "balance_limit"
    assert LedgerEntry.objects.count() == 1
    debit(wallet.user_id, uuid4(), 1)
    call_command("reprocess_top_up", str(top_up.id))
    assert apply_observation(top_up.id, observation(top_up, "PENDING")) == "PENDING"
    top_up.refresh_from_db()
    assert top_up.provider_status == "SUCCEEDED"
    assert apply_observation(top_up.id, observation(top_up)) == "SETTLED"
    assert projection_mismatches() == []


def test_sequence_capacity_does_not_mutate_funds(wallet, fund):
    fund(wallet, 5)
    Wallet.objects.filter(pk=wallet.id).update(last_sequence=MAX_INT)
    with pytest.raises(DomainError, match="sequence capacity"):
        debit(wallet.user_id, uuid4(), 1)
    wallet.refresh_from_db()
    assert wallet.balance == 5 and LedgerEntry.objects.count() == 1


def test_ledger_database_guards_and_funding_wallet_fk(wallet, fund):
    top_up = fund(wallet, 100)
    entry = LedgerEntry.objects.get(top_up=top_up)
    for mutation in [
        lambda: LedgerEntry.objects.filter(pk=entry.id).update(delta=101),
        lambda: LedgerEntry.objects.filter(pk=entry.id).delete(),
        lambda: TopUp.objects.filter(pk=top_up.id).update(amount=101),
        lambda: TopUp.objects.filter(pk=top_up.id).update(status="PENDING"),
    ]:
        with pytest.raises(DatabaseError), transaction.atomic():
            mutation()
    other = Wallet.objects.create(user_id=uuid4())
    other_top_up = create_top_up(other.user_id, uuid4(), 1, "source")
    with pytest.raises(DatabaseError), transaction.atomic():
        LedgerEntry.objects.create(
            wallet=wallet,
            top_up=other_top_up,
            wallet_sequence=2,
            kind="credit",
            delta=1,
            balance_after=101,
        )


def test_history_cursor_is_stable_during_append(wallet, fund):
    fund(wallet, 100)
    debit(wallet.user_id, uuid4(), 10)
    debit(wallet.user_id, uuid4(), 20)
    wallet.refresh_from_db()
    first = history(wallet, "2")
    assert [entry.wallet_sequence for entry in first["entries"]] == [3, 2]
    fund(wallet, 50)
    second = history(wallet, "2", first["next_cursor"])
    assert [entry.wallet_sequence for entry in second["entries"]] == [1]
    assert second["next_cursor"] is None
    other = Wallet.objects.create(user_id=uuid4())
    for limit, cursor, target in [
        ("0", None, wallet),
        ("101", None, wallet),
        ("1", "broken", wallet),
        ("1", first["next_cursor"], other),
    ]:
        with pytest.raises(DomainError):
            history(target, limit, cursor)
