from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from finance.models import (
    Category, Ledger, Event, Transaction, Debt,
    Fund, FundAddition, FundExpense, BalanceSnapshot
)
from finance.signals import recalculate_balances

class Command(BaseCommand):
    help = 'Assign all existing profile-less finance records to a designated user profile'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Username of the user whose profile will own the records',
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options.get('username')

        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User with username '{username}' does not exist.")
        else:
            users = User.objects.all()
            if not users.exists():
                raise CommandError("No users exist in the database. Please register a user first.")
            if users.count() > 1:
                usernames = ", ".join([u.username for u in users])
                raise CommandError(
                    f"Multiple users found ({usernames}). Please specify a target user with --username."
                )
            user = users.first()

        profile = user.profile
        self.stdout.write(self.style.WARNING(f"Assigning all orphan records to profile: {profile} (User: {user.username})"))

        models_to_update = [
            (Category, "Categories"),
            (Ledger, "Ledgers"),
            (Event, "Events"),
            (Transaction, "Transactions"),
            (Debt, "Debts"),
            (Fund, "Funds"),
            (FundAddition, "Fund Additions"),
            (FundExpense, "Fund Expenses"),
            (BalanceSnapshot, "Balance Snapshots"),
        ]

        with db_transaction.atomic():
            for model, label in models_to_update:
                orphan_count = model.objects.filter(profile__isnull=True).count()
                if orphan_count > 0:
                    model.objects.filter(profile__isnull=True).update(profile=profile)
                    self.stdout.write(self.style.SUCCESS(f"Updated {orphan_count} orphan {label}."))
                else:
                    self.stdout.write(f"No orphan {label} found.")

        self.stdout.write(self.style.WARNING("Recalculating balances..."))
        recalculate_balances(profile=profile)
        self.stdout.write(self.style.SUCCESS("Balances successfully recalculated! All records are migrated!"))
