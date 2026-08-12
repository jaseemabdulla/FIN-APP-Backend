from django.db import models
from django.utils import timezone
from datetime import date

class Category(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

    class Meta:
        unique_together = ('name', 'profile')

class Event(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    name = models.CharField(max_length=100)
    date = models.DateField(default=date.today)
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        unique_together = ('name', 'profile')

class Transaction(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    PAYMENT_MODE_CHOICES = [
        ('CASH', 'Cash'),
        ('ACCOUNT', 'Account'),
    ]

    TYPE_CHOICES = [
        ('EXPENSE', 'Expense'),
        ('INCOME', 'Income'),
        ('DEBT_TAKEN', 'Debt Taken'),
        ('DEBT_GIVEN', 'Debt Given'),
        ('DEBT_TAKEN_RETURN', 'Debt Taken Return'),
        ('DEBT_GIVEN_RETURN', 'Debt Given Return'),
        ('CASH_WITHDRAWAL', 'Cash Withdrawal'),
        ('CASH_DEPOSIT', 'Cash Deposit'),
        ('INVESTMENT', 'Investment'),
        ('FUND_MANAGEMENT_INC', 'Fund Management (Incoming)'),
        ('FUND_MANAGEMENT_DEC', 'Fund Management (Outgoing)'),
    ]

    date = models.DateField(default=date.today)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode = models.CharField(max_length=10, choices=PAYMENT_MODE_CHOICES)
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    ledger = models.ForeignKey('Ledger', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    related_debt = models.ForeignKey('Debt', on_delete=models.SET_NULL, null=True, blank=True, related_name='repayments')
    related_event = models.ForeignKey('Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    related_fund = models.ForeignKey('Fund', on_delete=models.SET_NULL, null=True, blank=True, related_name='account_transactions')

    def __str__(self):
        return f"{self.date} - {self.description} ({self.amount})"

class BalanceSnapshot(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    date = models.DateField(default=date.today)
    cash_in_hand = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_in_account = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def total_balance(self):
        return self.cash_in_hand + self.cash_in_account

    def __str__(self):
        return f"Balance for {self.date}"

    class Meta:
        unique_together = ('date', 'profile')

class Ledger(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        unique_together = ('name', 'profile')

class Debt(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    DEBT_TYPE_CHOICES = [
        ('TAKEN', 'Taken'),
        ('GIVEN', 'Given'),
    ]

    person_name = models.CharField(max_length=100)
    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT, null=True, blank=True, related_name='debts')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    debt_type = models.CharField(max_length=10, choices=DEBT_TYPE_CHOICES)
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='CASH')
    is_cleared = models.BooleanField(default=False)
    description = models.CharField(max_length=255, blank=True, default="")

    date = models.DateField(default=date.today)
    transaction = models.OneToOneField('Transaction', on_delete=models.CASCADE, null=True, blank=True, related_name='debt_entry')
    related_fund = models.ForeignKey('Fund', on_delete=models.CASCADE, null=True, blank=True, related_name='fund_debts')

    def save(self, *args, **kwargs):
        if self.ledger:
            self.person_name = self.ledger.name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.person_name} - {self.amount} ({self.debt_type})"

def get_fund_category(profile):
    category, _ = Category.objects.get_or_create(name="Fund Management", profile=profile)
    return category

class Fund(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('CLOSED', 'Closed'),
    ]

    title = models.CharField(max_length=100)
    ledger = models.ForeignKey('Ledger', on_delete=models.PROTECT, null=True, blank=True, related_name='funds')
    initial_amount = models.DecimalField(max_digits=12, decimal_places=2)
    received_date = models.DateField(default=date.today)
    notes = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ACTIVE')
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='ACCOUNT')
    
    # Standard Transaction linkage
    transaction = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_initial_entry')

    def __str__(self):
        return f"{self.title} ({self.status})"

    def update_status(self):
        initial = self.initial_amount or 0
        additions_sum = self.additions.aggregate(models.Sum('amount'))['amount__sum'] or 0
        total_received = initial + additions_sum
        total_spent = self.expenses.aggregate(models.Sum('amount'))['amount__sum'] or 0
        
        new_status = 'CLOSED' if total_received == total_spent else 'ACTIVE'
        if self.status != new_status:
            self.status = new_status
            Fund.objects.filter(pk=self.pk).update(status=new_status)
            
        self.update_fund_debt(total_received, total_spent)

    def update_fund_debt(self, total_received, total_spent):
        difference = total_received - total_spent
        
        # Find if a debt already exists for this fund
        debt = Debt.objects.filter(related_fund=self).first()
        
        if difference == 0:
            if debt:
                # If difference is zero, clear the debt and set amount to 0
                debt.amount = 0
                debt.is_cleared = True
                debt.save()
            return
            
        if difference > 0:
            debt_type = 'TAKEN'
            amount = difference
        else:
            debt_type = 'GIVEN'
            amount = abs(difference)
            
        if debt:
            debt.amount = amount
            debt.debt_type = debt_type
            debt.ledger = self.ledger
            debt.person_name = self.ledger.name if self.ledger else "Unknown"
            
            # Recalculate cleared status based on repayments
            total_repaid = debt.repayments.aggregate(models.Sum('amount'))['amount__sum'] or 0
            debt.is_cleared = (float(total_repaid) >= float(amount))
            debt.save()
        else:
            Debt.objects.create(
                profile=self.profile,
                ledger=self.ledger,
                person_name=self.ledger.name if self.ledger else "Unknown",
                amount=amount,
                debt_type=debt_type,
                date=self.received_date,
                description=f"Fund Balance: {self.title}",
                related_fund=self,
                is_cleared=False
            )

    def save(self, *args, **kwargs):
        # Determine status automatically on save
        if self.pk:
            initial = self.initial_amount or 0
            additions_sum = self.additions.aggregate(models.Sum('amount'))['amount__sum'] or 0
            total_received = initial + additions_sum
            total_spent = self.expenses.aggregate(models.Sum('amount'))['amount__sum'] or 0
            self.status = 'CLOSED' if total_received == total_spent else 'ACTIVE'
        else:
            initial = self.initial_amount or 0
            self.status = 'CLOSED' if initial == 0 else 'ACTIVE'
            
        super().save(*args, **kwargs)
        
        fund_cat = get_fund_category(self.profile)
        
        # Initial Fund transaction
        provider_name = self.ledger.name if self.ledger else "Unknown"
        if self.initial_amount > 0:
            if self.transaction:
                txn = self.transaction
                txn.date = self.received_date
                txn.amount = self.initial_amount
                txn.payment_mode = self.payment_mode
                txn.description = f"Fund Received: {self.title} (Provider: {provider_name})"
                txn.ledger = self.ledger
                txn.profile = self.profile
                txn.save()
            else:
                txn = Transaction.objects.create(
                    profile=self.profile,
                    date=self.received_date,
                    amount=self.initial_amount,
                    payment_mode=self.payment_mode,
                    transaction_type='FUND_MANAGEMENT_INC',
                    category=fund_cat,
                    description=f"Fund Received: {self.title} (Provider: {provider_name})",
                    related_fund=self,
                    ledger=self.ledger
                )
                Fund.objects.filter(pk=self.pk).update(transaction=txn)
                self.transaction = txn
        else:
            if self.transaction:
                txn = self.transaction
                Fund.objects.filter(pk=self.pk).update(transaction=None)
                self.transaction = None
                txn.delete()
                
        initial = self.initial_amount or 0
        additions_sum = self.additions.aggregate(models.Sum('amount'))['amount__sum'] or 0 if self.pk else 0
        total_received = initial + additions_sum
        total_spent = self.expenses.aggregate(models.Sum('amount'))['amount__sum'] or 0 if self.pk else 0
        self.update_fund_debt(total_received, total_spent)

    def delete(self, *args, **kwargs):
        txns_to_delete = []
        if self.transaction:
            txns_to_delete.append(self.transaction)
            
        # Collect additions and expenses transactions
        for addition in self.additions.all():
            if addition.transaction:
                txns_to_delete.append(addition.transaction)
                
        for expense in self.expenses.all():
            if expense.transaction:
                txns_to_delete.append(expense.transaction)
                
        # Collect repayments associated with any fund-generated debts
        # and delete the fund-generated debts explicitly to handle post_delete signals cleanly
        debts_to_delete = list(self.fund_debts.all())
        for debt in debts_to_delete:
            for repayment in debt.repayments.all():
                txns_to_delete.append(repayment)
                
        super().delete(*args, **kwargs)
        
        # Delete transactions to trigger signals
        for txn in txns_to_delete:
            try:
                txn.delete()
            except Exception:
                pass

        # Explicitly delete the debts
        for debt in debts_to_delete:
            try:
                debt.delete()
            except Exception:
                pass

class FundAddition(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    fund = models.ForeignKey(Fund, on_delete=models.CASCADE, related_name='additions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=date.today)
    notes = models.TextField(blank=True, default='')
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='ACCOUNT')
    
    transaction = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_addition_entry')

    def __str__(self):
        return f"Addition of {self.amount} to {self.fund.title} on {self.date}"

    def save(self, *args, **kwargs):
        if not self.profile and self.fund:
            self.profile = self.fund.profile
        fund_cat = get_fund_category(self.profile)
        if self.transaction:
            txn = self.transaction
            txn.date = self.date
            txn.amount = self.amount
            txn.payment_mode = self.payment_mode
            txn.description = f"Fund Addition: {self.fund.title}"
            txn.profile = self.profile
            txn.save()
        else:
            txn = Transaction.objects.create(
                profile=self.profile,
                date=self.date,
                amount=self.amount,
                payment_mode=self.payment_mode,
                transaction_type='FUND_MANAGEMENT_INC',
                category=fund_cat,
                description=f"Fund Addition: {self.fund.title}",
                related_fund=self.fund
            )
            self.transaction = txn
        super().save(*args, **kwargs)
        self.fund.update_status()

    def delete(self, *args, **kwargs):
        txn = self.transaction
        fund = self.fund
        super().delete(*args, **kwargs)
        if txn:
            txn.delete()
        fund.update_status()

class FundExpense(models.Model):
    profile = models.ForeignKey('users.Profile', on_delete=models.CASCADE, null=False, blank=False)
    fund = models.ForeignKey(Fund, on_delete=models.CASCADE, related_name='expenses')
    title = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=date.today)
    description = models.TextField(blank=True, default='')
    attachment = models.FileField(upload_to='fund_attachments/', null=True, blank=True)
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='ACCOUNT')
    
    transaction = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_expense_entry')

    def __str__(self):
        return f"Expense of {self.amount} from {self.fund.title}: {self.title}"

    def save(self, *args, **kwargs):
        if not self.profile and self.fund:
            self.profile = self.fund.profile
        fund_cat = get_fund_category(self.profile)
        if self.transaction:
            txn = self.transaction
            txn.date = self.date
            txn.amount = self.amount
            txn.payment_mode = self.payment_mode
            txn.description = f"Fund Expense: {self.title} (Fund: {self.fund.title})"
            txn.category = fund_cat
            txn.profile = self.profile
            txn.save()
        else:
            txn = Transaction.objects.create(
                profile=self.profile,
                date=self.date,
                amount=self.amount,
                payment_mode=self.payment_mode,
                transaction_type='FUND_MANAGEMENT_DEC',
                category=fund_cat,
                description=f"Fund Expense: {self.title} (Fund: {self.fund.title})",
                related_fund=self.fund
            )
            self.transaction = txn
        super().save(*args, **kwargs)
        self.fund.update_status()

    def delete(self, *args, **kwargs):
        txn = self.transaction
        fund = self.fund
        super().delete(*args, **kwargs)
        if txn:
            txn.delete()
        fund.update_status()

