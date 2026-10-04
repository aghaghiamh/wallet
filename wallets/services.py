from django.conf import settings

from common.db import transactional
from common.errors import DomainError
from common.serializers import MAX_INT
from wallets.models import LedgerEntry, TopUp, Wallet


def get_wallet(user_id, *, lock=False):
    query = Wallet.objects.select_for_update() if lock else Wallet.objects
    try:
        return query.get(user_id=user_id)
    except Wallet.DoesNotExist:
        raise DomainError("wallet_not_found", "Wallet does not exist.", 404) from None


@transactional()
def create_wallet(user_id):
    wallet, _ = Wallet.objects.get_or_create(user_id=user_id)
    return wallet


@transactional()
def create_top_up(user_id, key, amount, source_account_id):
    wallet = get_wallet(user_id, lock=True)
    existing = TopUp.objects.filter(wallet=wallet, idempotency_key=key).first()
    if existing:
        if (existing.amount, existing.source_account_id) != (amount, source_account_id):
            raise DomainError(
                "idempotency_conflict", "This top-up key has different parameters.", 409
            )
        return existing
    return TopUp.objects.create(
        wallet=wallet,
        idempotency_key=key,
        amount=amount,
        source_account_id=source_account_id,
        destination_account_id=settings.PLATFORM_ACCOUNT,
    )


def post_entry(wallet, kind, delta, *, key=None, top_up=None):
    """Caller holds the wallet row lock and owns the database transaction."""
    if wallet.last_sequence == MAX_INT:
        raise DomainError("integrity_violation", "Wallet sequence capacity exhausted.", 500)
    wallet.balance += delta
    wallet.last_sequence += 1
    wallet.save(update_fields=["balance", "last_sequence"])
    return LedgerEntry.objects.create(
        wallet=wallet,
        wallet_sequence=wallet.last_sequence,
        kind=kind,
        delta=delta,
        balance_after=wallet.balance,
        idempotency_key=key,
        top_up=top_up,
    )


@transactional()
def debit(user_id, key, amount):
    wallet = get_wallet(user_id, lock=True)
    existing = LedgerEntry.objects.filter(wallet=wallet, idempotency_key=key).first()
    if existing:
        if existing.amount != amount:
            raise DomainError("idempotency_conflict", "This debit key has a different amount.", 409)
        return existing
    if wallet.balance < amount:
        raise DomainError("insufficient_funds", "Debit exceeds the available balance.", 409)
    return post_entry(wallet, LedgerEntry.Kind.DEBIT, -amount, key=key)
