import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from wallets.reconciliation import reconcile_known_transfers


class Command(BaseCommand):
    help = "Reconcile recorded provider transfers and verify ledger projections."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one audit (the default).")
        parser.add_argument("--mode", choices=["all", "transfers", "ledger"], default="all")
        parser.add_argument(
            "--since", help="Audit changes since a timezone-aware ISO 8601 timestamp, plus unresolved intents."
        )
        parser.add_argument(
            "--repair", action="store_true", help="Repair verified pending intents that already have a valid credit."
        )

    def handle(self, *args, **options):
        since = None
        if options["since"] is not None:
            try:
                since = parse_datetime(options["since"])
            except ValueError:
                pass
            if since is None or timezone.is_naive(since):
                raise CommandError("--since must be a timezone-aware ISO 8601 timestamp.")
        if options["mode"] == "ledger" and (since is not None or options["repair"]):
            raise CommandError("--since and --repair cannot be used with --mode ledger.")
        report = reconcile_known_transfers(mode=options["mode"], since=since, repair=options["repair"])
        self.stdout.write(json.dumps(report, indent=2))
        if report["mismatched"] or report["projection_mismatches"]:
            raise CommandError("Reconciliation found an integrity mismatch.")
