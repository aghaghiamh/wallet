import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from provider.services import advance_due


class Command(BaseCommand):
    help = "Advance due provider transfers once or continuously."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=float, default=1)
        parser.add_argument(
            "--force", action="store_true", help="Ignore the simulated settlement delay."
        )

    def handle(self, *args, **options):
        if options["interval"] <= 0:
            self.stderr.write("Interval must be positive.")
            return
        try:
            while True:
                close_old_connections()
                advance_due(force=options["force"])
                if not options["loop"] or options["once"]:
                    break
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            pass
