from django.core.management.base import BaseCommand, CommandError

from common.errors import DomainError
from common.serializers import uuid_value
from wallets.models import TopUp
from wallets.topups import reprocess_top_up


class Command(BaseCommand):
    help = "Return a reviewed top-up to polling without changing its identity or parameters."

    def add_arguments(self, parser):
        parser.add_argument("top_up_id")

    def handle(self, *args, **options):
        try:
            top_up = reprocess_top_up(uuid_value(options["top_up_id"]))
        except (DomainError, TopUp.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{top_up.id}: {top_up.status}")
