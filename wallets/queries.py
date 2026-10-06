from common.errors import DomainError
from common.serializers import MAX_INT
from wallets.models import LedgerEntry


def history(wallet, limit_text="50", cursor=None):
    try:
        if not str(limit_text).isascii() or not str(limit_text).isdigit():
            raise ValueError
        limit = int(limit_text)
        if not 1 <= limit <= 100:
            raise ValueError
    except (TypeError, ValueError):
        raise DomainError("invalid_request", "Limit must be an integer from 1 to 100.") from None

    upper, before = wallet.last_sequence, wallet.last_sequence + 1
    if cursor:
        try:
            if not isinstance(cursor, str):
                raise ValueError
            wallet_id, upper_text, before_text = cursor.split(":")
            if wallet_id != str(wallet.id):
                raise ValueError
            for value in (upper_text, before_text):
                if not value.isascii() or not value.isdigit():
                    raise ValueError
            upper, before = int(upper_text), int(before_text)
            if not 0 <= upper <= min(MAX_INT, wallet.last_sequence) or not 1 <= before <= upper + 1:
                raise ValueError
        except (ValueError, TypeError):
            raise DomainError("invalid_request", "Malformed cursor or wrong wallet.") from None

    rows = list(
        LedgerEntry.objects.filter(
            wallet=wallet,
            wallet_sequence__lte=upper,
            wallet_sequence__lt=before,
        ).order_by("-wallet_sequence")[: limit + 1]
    )
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = f"{wallet.id}:{upper}:{rows[-1].wallet_sequence}"
    return {"entries": rows, "next_cursor": next_cursor}
