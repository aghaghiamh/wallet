from django.db import connection, transaction

from wallets.models import LedgerEntry, TopUp
from wallets.provider_client import ProviderClient, ProviderIntegrityError, ProviderUnavailable
from wallets.topups import matches, process_top_up


def projection_mismatches():
    # One PostgreSQL statement sees one snapshot, including every window sum.
    with connection.cursor() as cursor:
        cursor.execute("""
            WITH running AS (
                SELECT wallet_id, balance_after,
                    SUM(delta) OVER (PARTITION BY wallet_id ORDER BY wallet_sequence) AS expected
                FROM wallets_ledgerentry
            ), totals AS (
                SELECT wallet_id, SUM(delta) AS balance, MAX(wallet_sequence) AS sequence,
                    COUNT(*) AS entries
                FROM wallets_ledgerentry GROUP BY wallet_id
            )
            SELECT w.id FROM wallets_wallet w
            LEFT JOIN totals t ON t.wallet_id = w.id
            WHERE w.balance <> COALESCE(t.balance, 0)
                OR w.last_sequence <> COALESCE(t.sequence, 0)
                OR COALESCE(t.entries, 0) <> COALESCE(t.sequence, 0)
                OR EXISTS (
                    SELECT 1 FROM running r WHERE r.wallet_id = w.id
                    AND (r.balance_after <> r.expected OR r.expected < 0)
                )
        """)
        return [str(row[0]) for row in cursor.fetchall()]


def local_snapshot(top_up_id):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        top_up = TopUp.objects.get(pk=top_up_id)
        entries = list(LedgerEntry.objects.filter(top_up_id=top_up_id))
        return top_up, entries


def reconcile_known_transfers(client=None):
    client = client or ProviderClient()
    report = {
        name: 0 for name in ["matched", "pending", "unverified", "review_required", "mismatched"]
    }
    report["issues"] = []
    ids = TopUp.objects.values_list("id", flat=True)
    for top_up_id in ids.iterator(chunk_size=500):
        top_up, entries = local_snapshot(top_up_id)
        if top_up.status == TopUp.Status.PENDING and not entries:
            process_top_up(top_up_id, client)
            top_up, entries = local_snapshot(top_up_id)
        # Snapshot local state BEFORE the GET: terminal provider outcomes cannot regress.
        category, reason = "matched", None
        try:
            observation = client.get(top_up.id)
            if observation is None:
                if top_up.status in {
                    TopUp.Status.SETTLED,
                    TopUp.Status.FAILED,
                } or top_up.provider_status in {"SUCCEEDED", "FAILED"}:
                    category, reason = "mismatched", "provider_record_missing"
                elif top_up.status == TopUp.Status.REVIEW_REQUIRED:
                    category, reason = "review_required", top_up.error_code
                else:
                    category = "pending"
            elif not matches(top_up, observation):
                category, reason = "mismatched", "transfer_mismatch"
            elif top_up.status == TopUp.Status.SETTLED:
                if (
                    observation["status"] != "SUCCEEDED"
                    or top_up.provider_status != "SUCCEEDED"
                    or len(entries) != 1
                    or entries[0].delta != top_up.amount
                    or entries[0].wallet_id != top_up.wallet_id
                ):
                    category, reason = "mismatched", "settlement_mismatch"
            elif top_up.status == TopUp.Status.FAILED:
                if observation["status"] != "FAILED" or entries:
                    category, reason = "mismatched", "failure_mismatch"
            elif entries:
                category, reason = "mismatched", "unexpected_credit"
            elif top_up.status == TopUp.Status.REVIEW_REQUIRED:
                category, reason = "review_required", top_up.error_code
            else:
                category = "pending"  # Confirmation after the local snapshot is normal progress.
        except ProviderUnavailable:
            category = "unverified"
        except ProviderIntegrityError as exc:
            category, reason = "mismatched", exc.code
        report[category] += 1
        if reason:
            report["issues"].append({"top_up_id": str(top_up.id), "code": reason})
    report["projection_mismatches"] = projection_mismatches()
    return report
