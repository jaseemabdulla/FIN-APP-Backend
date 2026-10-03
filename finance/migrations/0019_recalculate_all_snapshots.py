from django.db import migrations
from collections import defaultdict
from datetime import timedelta

def recalculate_snapshots(apps, schema_editor):
    Profile = apps.get_model('users', 'Profile')
    Transaction = apps.get_model('finance', 'Transaction')
    BalanceSnapshot = apps.get_model('finance', 'BalanceSnapshot')

    for profile in Profile.objects.all():
        transactions = Transaction.objects.filter(profile=profile).order_by('date', 'id')
        BalanceSnapshot.objects.filter(profile=profile).delete()

        cash = 0
        account = 0

        if not transactions.exists():
            continue

        txns_by_date = defaultdict(list)
        for txn in transactions:
            txns_by_date[txn.date].append(txn)

        dates = sorted(txns_by_date.keys())
        start_date = dates[0]
        end_date = dates[-1]

        current_date = start_date
        while current_date <= end_date:
            day_txns = txns_by_date.get(current_date, [])

            for txn in day_txns:
                amount = txn.amount
                mode = txn.payment_mode
                ttype = txn.transaction_type

                if ttype in ['FUND_MANAGEMENT_INC', 'FUND_MANAGEMENT_DEC']:
                    continue

                if ttype == 'CASH_WITHDRAWAL':
                    cash += amount
                    account -= amount
                elif ttype == 'CASH_DEPOSIT':
                    cash -= amount
                    account += amount
                else:
                    multiplier = 1
                    if ttype in ['EXPENSE', 'DEBT_GIVEN', 'INVESTMENT', 'DEBT_TAKEN_RETURN']:
                        multiplier = -1

                    if mode == 'CASH':
                        cash += amount * multiplier
                    elif mode == 'ACCOUNT':
                        account += amount * multiplier

            BalanceSnapshot.objects.create(
                profile=profile,
                date=current_date,
                cash_in_hand=cash,
                cash_in_account=account
            )

            current_date += timedelta(days=1)

class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0018_migrate_legacy_investments'),
    ]

    operations = [
        migrations.RunPython(recalculate_snapshots, reverse_code=migrations.RunPython.noop),
    ]
