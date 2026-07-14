from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Transaction, BalanceSnapshot, Debt
from django.db.models import Sum
from datetime import timedelta, date
from collections import defaultdict

def recalculate_balances():
    # Fetch all transactions ordered by date and id
    transactions = Transaction.objects.all().order_by('date', 'id')
    
    BalanceSnapshot.objects.all().delete()
    
    cash = 0
    account = 0
    
    if not transactions.exists():
        return

    # Group transactions by date
    txns_by_date = defaultdict(list)
    for txn in transactions:
        txns_by_date[txn.date].append(txn)
        
    dates = sorted(txns_by_date.keys())
    start_date = dates[0]
    end_date = dates[-1]
    
    current_date = start_date
    while current_date <= end_date:
        # Process transactions for current_date
        day_txns = txns_by_date.get(current_date, [])
        
        for txn in day_txns:
            amount = txn.amount
            mode = txn.payment_mode
            type = txn.transaction_type
            
            # Logic
            # Expense: Reduce
            # Income: Increase
            # Debt Taken: Increase
            # Debt Given: Reduce
            # Cash Withdrawal: Transfer Account -> Cash
            
            if type == 'CASH_WITHDRAWAL':
                cash += amount
                account -= amount
            elif type == 'CASH_DEPOSIT':
                cash -= amount
                account += amount
            else:
                multiplier = 1
                if type in ['EXPENSE', 'DEBT_GIVEN', 'INVESTMENT', 'DEBT_TAKEN_RETURN', 'FUND_MANAGEMENT_DEC']:
                    multiplier = -1
                
                if mode == 'CASH':
                    cash += amount * multiplier
                elif mode == 'ACCOUNT':
                    account += amount * multiplier
        
        # Save Snapshot
        BalanceSnapshot.objects.create(
            date=current_date,
            cash_in_hand=cash,
            cash_in_account=account
        )
        
        current_date += timedelta(days=1)

@receiver(post_save, sender=Transaction)
def update_balances_on_save(sender, instance, **kwargs):
    recalculate_balances()

@receiver(post_delete, sender=Transaction)
def update_balances_on_delete(sender, instance, **kwargs):
    recalculate_balances()

@receiver(post_save, sender=Transaction)
def create_debt_from_transaction(sender, instance, created, **kwargs):
    """
    If a Transaction of type DEBT_TAKEN or DEBT_GIVEN is created,
    and it's not already linked to a Debt (via reverse relation),
    create a corresponding Debt entry.
    """
    if not created:
        return

    if instance.transaction_type in ['DEBT_TAKEN', 'DEBT_GIVEN']:
        # Check if already linked to a debt (to avoid loop from Debt->Transaction creation)
        if hasattr(instance, 'debt_entry'):
            return

        debt_type = 'TAKEN' if instance.transaction_type == 'DEBT_TAKEN' else 'GIVEN'
        
        # Create Debt and link it immediately to this transaction
        Debt.objects.create(
            date=instance.date,
            amount=instance.amount,
            debt_type=debt_type,
            payment_mode=instance.payment_mode,
            person_name=instance.description,
            transaction=instance
        )

@receiver(post_save, sender=Transaction)
def update_debt_on_transaction_save(sender, instance, created, **kwargs):
    """
    If a Transaction is linked to a Debt (Repayment), check if Debt is cleared.
    """
    if instance.related_debt:
        debt = instance.related_debt
        total_repaid = debt.repayments.aggregate(Sum('amount'))['amount__sum'] or 0
        
        if total_repaid >= debt.amount:
            if not debt.is_cleared:
                debt.is_cleared = True
                debt.save()
        else:
            if debt.is_cleared:
                debt.is_cleared = False
                debt.save()

@receiver(post_delete, sender=Transaction)
def update_debt_on_transaction_delete(sender, instance, **kwargs):
    """
    If a Repayment Transaction is deleted, re-evaluate if Debt is cleared.
    """
    if instance.related_debt:
        debt = instance.related_debt
        total_repaid = debt.repayments.aggregate(Sum('amount'))['amount__sum'] or 0
        
        if total_repaid >= debt.amount:
            if not debt.is_cleared:
                debt.is_cleared = True
                debt.save()
        else:
            if debt.is_cleared:
                debt.is_cleared = False
                debt.save()


# --- SYNC: Debt -> Transaction (For edits/deletes of Debts) ---

@receiver(post_save, sender=Debt)
def sync_transaction_on_debt_update(sender, instance, created, **kwargs):
    """
    If a Debt is updated (and has a linked transaction), update the transaction.
    This handles the case where user edits a Debt entry that was created via Transaction.
    Excludes 'created' because creation is either manual (no txn) or via txn (txn already correct).
    """
    if created:
        return

    if instance.transaction:
        txn = instance.transaction
        
        # Determine expected transaction type
        expected_type = 'DEBT_TAKEN' if instance.debt_type == 'TAKEN' else 'DEBT_GIVEN'
        
        # Check if any field differs to avoid unnecessary saves (and potential recursion loops if txn saves back)
        needs_save = False
        
        if txn.amount != instance.amount:
            txn.amount = instance.amount
            needs_save = True
        
        if txn.date != instance.date:
            txn.date = instance.date
            needs_save = True
            
        if txn.description != instance.person_name:
            txn.description = instance.person_name
            needs_save = True
            
        if txn.payment_mode != instance.payment_mode:
            txn.payment_mode = instance.payment_mode
            needs_save = True
            
        if txn.transaction_type != expected_type:
            txn.transaction_type = expected_type
            needs_save = True
            
        if needs_save:
            txn.save()

@receiver(post_delete, sender=Debt)
def delete_transaction_on_debt_delete(sender, instance, **kwargs):
    """
    If a Debt is deleted, delete the linked Transaction if it exists.
    """
    if instance.transaction:
        # Check if transaction still exists (might be deleted first, triggering cascade)
        # However, Debt.transaction is OneToOne.
        # If Transaction deleted first -> Debt deleted via CASCADE. 
        # In that case, Debt post_delete might trigger.
        # We need to ensure we don't try to delete the already-deleted transaction.
        try:
            if Transaction.objects.filter(pk=instance.transaction.pk).exists():
                instance.transaction.delete()
        except Transaction.DoesNotExist:
            pass
