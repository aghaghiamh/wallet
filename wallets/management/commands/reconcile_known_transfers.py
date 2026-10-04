import json

from django.core.management.base import BaseCommand, CommandError

from wallets.reconciliation import reconcile_known_transfers


class Command(BaseCommand):
    help = "Reconcile recorded provider transfers and verify ledger projections."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one audit (the default).")

    def handle(self, *args, **options):
        report = reconcile_known_transfers()
        self.stdout.write(json.dumps(report, indent=2))
        if report["mismatched"] or report["projection_mismatches"]:
            raise CommandError("Reconciliation found an integrity mismatch.")
