from collections import defaultdict

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from common.db import transactional
from wallets.models import LedgerEntry, TopUp, Wallet
from wallets.provider_client import ProviderClient, ProviderIntegrityError, ProviderUnavailable
from wallets.topups import matches

UNRESOLVED_STATUSES = (TopUp.Status.PENDING, TopUp.Status.REVIEW_REQUIRED)


def projection_mismatches(wallet_id=None):
    # Filter inside both CTEs when repairing one wallet, rather than scanning all ledgers.
    entry_filter = "WHERE wallet_id = %s" if wallet_id is not None else ""
    wallet_filter = "w.id = %s AND" if wallet_id is not None else ""
    params = [wallet_id] * 3 if wallet_id is not None else []
    with connection.cursor() as cursor:
        cursor.execute(f"""
            WITH running AS (
                SELECT wallet_id, balance_after,
                    SUM(delta) OVER (PARTITION BY wallet_id ORDER BY wallet_sequence) AS expected
                FROM wallets_ledgerentry {entry_filter}
            ), totals AS (
                SELECT wallet_id, SUM(delta) AS balance, MAX(wallet_sequence) AS sequence,
                    COUNT(*) AS entries
                FROM wallets_ledgerentry {entry_filter} GROUP BY wallet_id
            )
            SELECT w.id FROM wallets_wallet w
            LEFT JOIN totals t ON t.wallet_id = w.id
            WHERE {wallet_filter} (
                w.balance <> COALESCE(t.balance, 0)
                OR w.last_sequence <> COALESCE(t.sequence, 0)
                OR COALESCE(t.entries, 0) <> COALESCE(t.sequence, 0)
                OR EXISTS (
                    SELECT 1 FROM running r WHERE r.wallet_id = w.id
                    AND (r.balance_after <> r.expected OR r.expected < 0)
                )
            )
        """, params)
        return [str(row[0]) for row in cursor.fetchall()]


def local_snapshots(*, since=None, batch_size=500):
    """Yield fully evaluated batches; no transaction remains open across a yield."""
    query = TopUp.objects.all()
    if since is not None:
        query = query.filter(Q(updated_at__gte=since) | Q(status__in=UNRESOLVED_STATUSES))
    after = None
    while True:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            page = query if after is None else query.filter(id__gt=after)
            top_ups = list(page.order_by("id")[:batch_size])
            if not top_ups:
                return
            entries = defaultdict(list)
            for entry in LedgerEntry.objects.filter(top_up_id__in=[row.id for row in top_ups]):
                entries[entry.top_up_id].append(entry)
        after = top_ups[-1].id
        yield [(top_up, entries[top_up.id]) for top_up in top_ups]


def matching_credit(top_up, entries):
    return (
        len(entries) == 1
        and entries[0].kind == LedgerEntry.Kind.CREDIT
        and entries[0].delta == top_up.amount
        and entries[0].wallet_id == top_up.wallet_id
    )


def classify_transfer(top_up, entries, observation):
    if observation is None:
        if top_up.status in {TopUp.Status.SETTLED, TopUp.Status.FAILED} or top_up.provider_status in {
            "SUCCEEDED", "FAILED"
        }:
            return "mismatched", "provider_record_missing"
        if entries:
            return "mismatched", "unexpected_credit"
        if top_up.status == TopUp.Status.REVIEW_REQUIRED:
            return "review_required", top_up.error_code
        return "pending", None
    if not matches(top_up, observation):
        return "mismatched", "transfer_mismatch"
    if top_up.status == TopUp.Status.SETTLED:
        if (
            observation["status"] != "SUCCEEDED"
            or top_up.provider_status != "SUCCEEDED"
            or not matching_credit(top_up, entries)
        ):
            return "mismatched", "settlement_mismatch"
        return "matched", None
    if top_up.status == TopUp.Status.FAILED:
        if observation["status"] != "FAILED" or entries:
            return "mismatched", "failure_mismatch"
        return "matched", None
    if entries:
        return "mismatched", "unexpected_credit"
    if top_up.status == TopUp.Status.REVIEW_REQUIRED:
        return "review_required", top_up.error_code
    # Provider confirmation after a pending local snapshot is ordinary progress.
    return "pending", None


@transactional()
def repair_pending_credit(top_up_id, observation):
    """Return category, issue code, repaired flag, and optional refusal reason."""
    reference = TopUp.objects.only("wallet_id").get(pk=top_up_id)
    Wallet.objects.select_for_update().get(pk=reference.wallet_id)
    top_up = TopUp.objects.select_for_update().get(pk=top_up_id)
    entries = list(LedgerEntry.objects.filter(top_up_id=top_up_id))
    category, reason = classify_transfer(top_up, entries, observation)
    if top_up.status == TopUp.Status.SETTLED:
        if category == "matched" and not projection_mismatches(top_up.wallet_id):
            return category, reason, False, None
        return "mismatched", "settlement_mismatch", False, "invalid_settlement"
    if top_up.status != TopUp.Status.PENDING:
        return category, reason, False, "unsupported_status"

    blocked = None
    if observation is None or observation["status"] != "SUCCEEDED":
        blocked = "provider_not_succeeded"
    elif not matches(top_up, observation):
        blocked = "transfer_mismatch"
    elif top_up.provider_status == "FAILED":
        blocked = "provider_status_conflict"
    elif not matching_credit(top_up, entries):
        blocked = "credit_mismatch"
    elif projection_mismatches(top_up.wallet_id):
        blocked = "wallet_projection_mismatch"
    if blocked:
        return "mismatched", reason or "unexpected_credit", False, blocked

    top_up.status = TopUp.Status.SETTLED
    top_up.provider_status = TopUp.ProviderStatus.SUCCEEDED
    top_up.error_code = None
    top_up.last_checked_at = timezone.now()
    top_up.save(
        update_fields=["status", "provider_status", "error_code", "last_checked_at", "updated_at"]
    )
    return "matched", None, True, None


def reconcile_known_transfers(client=None, *, mode="all", since=None, repair=False, batch_size=500):
    if mode not in {"all", "transfers", "ledger"}:
        raise ValueError("Mode must be all, transfers, or ledger.")
    if since is not None and timezone.is_naive(since):
        raise ValueError("Since must include a timezone.")
    if mode == "ledger" and (since is not None or repair):
        raise ValueError("Ledger-only mode cannot use since or repair.")
    if not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("Batch size must be a positive integer.")

    report = {
        name: 0 for name in ["matched", "pending", "unverified", "review_required", "mismatched"]
    }
    report.update(
        mode=mode,
        since=since.isoformat() if since is not None else None,
        transfers_checked=mode != "ledger",
        ledger_checked=mode != "transfers",
        repair_enabled=repair,
        repaired=0,
        repaired_top_up_ids=[],
        issues=[],
    )
    if report["transfers_checked"]:
        client = client or ProviderClient()
        for batch in local_snapshots(since=since, batch_size=batch_size):
            for top_up, entries in batch:
                # Read local state BEFORE GET; provider terminal outcomes cannot regress.
                blocked = None
                try:
                    observation = client.get(top_up.id)
                    category, reason = classify_transfer(top_up, entries, observation)
                    if repair and top_up.status == TopUp.Status.PENDING and entries:
                        category, reason, repaired, blocked = repair_pending_credit(
                            top_up.id, observation
                        )
                        if repaired:
                            report["repaired"] += 1
                            report["repaired_top_up_ids"].append(str(top_up.id))
                except ProviderUnavailable:
                    category, reason = "unverified", None
                except ProviderIntegrityError as exc:
                    category, reason = "mismatched", exc.code
                report[category] += 1
                if reason:
                    issue = {"top_up_id": str(top_up.id), "code": reason}
                    if blocked:
                        issue["repair_blocked"] = blocked
                    report["issues"].append(issue)
    report["projection_mismatches"] = projection_mismatches() if report["ledger_checked"] else []
    return report
