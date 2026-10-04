from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from common.db import transactional
from common.errors import DomainError
from common.serializers import MAX_INT
from provider.models import Account, Transfer


@transactional(using="provider")
def create_transfer(transfer_id, **parameters):
    transfer, created = Transfer.objects.get_or_create(
        id=transfer_id,
        defaults={
            **parameters,
            "ready_at": timezone.now() + timedelta(seconds=settings.PROVIDER_DELAY),
        },
    )
    if any(getattr(transfer, key) != value for key, value in parameters.items()):
        raise DomainError(
            "idempotency_conflict", "Transfer ID already has different parameters.", 409
        )
    return transfer, created


@transactional(using="provider")
def advance_transfer(transfer_id, *, force=False):
    transfer = Transfer.objects.select_for_update().get(pk=transfer_id)
    if transfer.status != Transfer.Status.PENDING or (
        not force and transfer.ready_at > timezone.now()
    ):
        return transfer.status
    ids = sorted({transfer.source_account_id, transfer.destination_account_id})
    accounts = {
        account.pk: account
        for account in Account.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    }
    source = accounts.get(transfer.source_account_id)
    destination = accounts.get(transfer.destination_account_id)
    failure = None
    if not source or not destination:
        failure = "unknown_account"
    elif source.pk == destination.pk:
        failure = "same_account"
    elif source.behavior == Account.Behavior.PENDING:
        return transfer.status
    elif source.behavior == Account.Behavior.FAIL:
        failure = "provider_rejected"
    elif source.balance < transfer.amount:
        failure = "insufficient_funds"
    elif destination.balance > MAX_INT - transfer.amount:
        failure = "destination_overflow"
    if failure:
        transfer.status = Transfer.Status.FAILED
        transfer.failure_code = failure
    else:
        source.balance -= transfer.amount
        destination.balance += transfer.amount
        source.save(update_fields=["balance"])
        destination.save(update_fields=["balance"])
        transfer.status = Transfer.Status.SUCCEEDED
    transfer.save(update_fields=["status", "failure_code"])
    return transfer.status


def advance_due(*, force=False):
    query = Transfer.objects.filter(status=Transfer.Status.PENDING)
    if not force:
        query = query.filter(ready_at__lte=timezone.now())
    count = 0
    for transfer_id in query.values_list("id", flat=True).iterator(chunk_size=500):
        advance_transfer(transfer_id, force=force)
        count += 1
    return count


def seed_accounts():
    accounts = [
        ("platform-account", 0, Account.Behavior.NORMAL),
        ("demo-customer-1", 10_000_000_000, Account.Behavior.NORMAL),
        ("demo-failure", 10_000_000_000, Account.Behavior.FAIL),
        ("demo-pending", 10_000_000_000, Account.Behavior.PENDING),
    ]
    for account_id, balance, behavior in accounts:
        Account.objects.get_or_create(
            pk=account_id, defaults={"balance": balance, "behavior": behavior}
        )
