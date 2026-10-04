from django.core.management.base import BaseCommand

from provider.services import seed_accounts


class Command(BaseCommand):
    help = "Create demo provider accounts without resetting existing balances."

    def handle(self, *args, **options):
        seed_accounts()
        self.stdout.write("Provider demo accounts are ready.")
