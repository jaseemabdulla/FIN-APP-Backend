from django.db import models
from django.utils import timezone
from datetime import date

class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

class Event(models.Model):
    name = models.CharField(max_length=100, unique=True)
    date = models.DateField(default=date.today)
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class Transaction(models.Model):
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
    related_debt = models.ForeignKey('Debt', on_delete=models.SET_NULL, null=True, blank=True, related_name='repayments')
    related_event = models.ForeignKey('Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    related_fund = models.ForeignKey('Fund', on_delete=models.SET_NULL, null=True, blank=True, related_name='account_transactions')

    def __str__(self):
        return f"{self.date} - {self.description} ({self.amount})"

class BalanceSnapshot(models.Model):
    date = models.DateField(unique=True, default=date.today)
    cash_in_hand = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_in_account = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def total_balance(self):
        return self.cash_in_hand + self.cash_in_account

    def __str__(self):
        return f"Balance for {self.date}"

class Debt(models.Model):
    DEBT_TYPE_CHOICES = [
        ('TAKEN', 'Taken'),
        ('GIVEN', 'Given'),
    ]

    person_name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    debt_type = models.CharField(max_length=10, choices=DEBT_TYPE_CHOICES)
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='CASH')
    is_cleared = models.BooleanField(default=False)
    description = models.CharField(max_length=255, blank=True, default="")

    date = models.DateField(default=date.today)
    transaction = models.OneToOneField('Transaction', on_delete=models.CASCADE, null=True, blank=True, related_name='debt_entry')

    def __str__(self):
        return f"{self.person_name} - {self.amount} ({self.debt_type})"

def get_fund_category():
    category, _ = Category.objects.get_or_create(name="Fund Management")
    return category

class Fund(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('SETTLED', 'Settled'),
    ]

    title = models.CharField(max_length=100)
    purpose = models.TextField()
    provider = models.CharField(max_length=100)
    initial_amount = models.DecimalField(max_digits=12, decimal_places=2)
    received_date = models.DateField(default=date.today)
    notes = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ACTIVE')
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='ACCOUNT')
    
    # Standard Transaction linkage
    transaction = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_initial_entry')
    
    # Settlement Fields
    settlement_date = models.DateField(null=True, blank=True)
    returned_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    additional_amount_required = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    settlement_notes = models.TextField(blank=True, default='')
    settlement_payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='ACCOUNT')
    
    settlement_return_txn = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_settlement_return')
    settlement_extra_txn = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_settlement_extra')

    def __str__(self):
        return f"{self.title} ({self.status})"

    def save(self, *args, **kwargs):
        # 1. Save first to get an ID (for new records) or save other updates
        super().save(*args, **kwargs)
        
        fund_cat = get_fund_category()
        
        # Initial Fund transaction
        if self.transaction:
            txn = self.transaction
            txn.date = self.received_date
            txn.amount = self.initial_amount
            txn.payment_mode = self.payment_mode
            txn.description = f"Fund Received: {self.title} (Provider: {self.provider})"
            txn.save()
        else:
            txn = Transaction.objects.create(
                date=self.received_date,
                amount=self.initial_amount,
                payment_mode=self.payment_mode,
                transaction_type='FUND_MANAGEMENT_INC',
                category=fund_cat,
                description=f"Fund Received: {self.title} (Provider: {self.provider})",
                related_fund=self
            )
            Fund.objects.filter(pk=self.pk).update(transaction=txn)
            self.transaction = txn

        # Settlement transactions
        if self.status == 'SETTLED':
            # Settlement Return Txn
            if self.returned_amount > 0:
                if self.settlement_return_txn:
                    ret_txn = self.settlement_return_txn
                    ret_txn.date = self.settlement_date or date.today()
                    ret_txn.amount = self.returned_amount
                    ret_txn.payment_mode = self.settlement_payment_mode
                    ret_txn.description = f"Fund Settled Return: {self.title}"
                    ret_txn.save()
                else:
                    ret_txn = Transaction.objects.create(
                        date=self.settlement_date or date.today(),
                        amount=self.returned_amount,
                        payment_mode=self.settlement_payment_mode,
                        transaction_type='FUND_MANAGEMENT_DEC',
                        category=fund_cat,
                        description=f"Fund Settled Return: {self.title}",
                        related_fund=self
                    )
                    Fund.objects.filter(pk=self.pk).update(settlement_return_txn=ret_txn)
                    self.settlement_return_txn = ret_txn
            else:
                if self.settlement_return_txn:
                    old_txn = self.settlement_return_txn
                    Fund.objects.filter(pk=self.pk).update(settlement_return_txn=None)
                    self.settlement_return_txn = None
                    old_txn.delete()

            # Settlement Extra Txn
            if self.additional_amount_required > 0:
                if self.settlement_extra_txn:
                    ext_txn = self.settlement_extra_txn
                    ext_txn.date = self.settlement_date or date.today()
                    ext_txn.amount = self.additional_amount_required
                    ext_txn.payment_mode = self.settlement_payment_mode
                    ext_txn.description = f"Fund Settled Extra Expense: {self.title}"
                    ext_txn.save()
                else:
                    ext_txn = Transaction.objects.create(
                        date=self.settlement_date or date.today(),
                        amount=self.additional_amount_required,
                        payment_mode=self.settlement_payment_mode,
                        transaction_type='FUND_MANAGEMENT_DEC',
                        category=fund_cat,
                        description=f"Fund Settled Extra Expense: {self.title}",
                        related_fund=self
                    )
                    Fund.objects.filter(pk=self.pk).update(settlement_extra_txn=ext_txn)
                    self.settlement_extra_txn = ext_txn
            else:
                if self.settlement_extra_txn:
                    old_txn = self.settlement_extra_txn
                    Fund.objects.filter(pk=self.pk).update(settlement_extra_txn=None)
                    self.settlement_extra_txn = None
                    old_txn.delete()
        else:
            # Delete settlement transactions if status is ACTIVE
            if self.settlement_return_txn:
                old_txn = self.settlement_return_txn
                Fund.objects.filter(pk=self.pk).update(settlement_return_txn=None)
                self.settlement_return_txn = None
                old_txn.delete()
            if self.settlement_extra_txn:
                old_txn = self.settlement_extra_txn
                Fund.objects.filter(pk=self.pk).update(settlement_extra_txn=None)
                self.settlement_extra_txn = None
                old_txn.delete()

    def delete(self, *args, **kwargs):
        txns_to_delete = []
        if self.transaction:
            txns_to_delete.append(self.transaction)
        if self.settlement_return_txn:
            txns_to_delete.append(self.settlement_return_txn)
        if self.settlement_extra_txn:
            txns_to_delete.append(self.settlement_extra_txn)
            
        # Collect additions and expenses transactions
        for addition in self.additions.all():
            if addition.transaction:
                txns_to_delete.append(addition.transaction)
                
        for expense in self.expenses.all():
            if expense.transaction:
                txns_to_delete.append(expense.transaction)
                
        super().delete(*args, **kwargs)
        
        # Delete transactions to trigger signals
        for txn in txns_to_delete:
            try:
                txn.delete()
            except Exception:
                pass

class FundAddition(models.Model):
    fund = models.ForeignKey(Fund, on_delete=models.CASCADE, related_name='additions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=date.today)
    notes = models.TextField(blank=True, default='')
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='ACCOUNT')
    
    transaction = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_addition_entry')

    def __str__(self):
        return f"Addition of {self.amount} to {self.fund.title} on {self.date}"

    def save(self, *args, **kwargs):
        fund_cat = get_fund_category()
        if self.transaction:
            txn = self.transaction
            txn.date = self.date
            txn.amount = self.amount
            txn.payment_mode = self.payment_mode
            txn.description = f"Fund Addition: {self.fund.title}"
            txn.save()
        else:
            txn = Transaction.objects.create(
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

    def delete(self, *args, **kwargs):
        txn = self.transaction
        super().delete(*args, **kwargs)
        if txn:
            txn.delete()

class FundExpense(models.Model):
    fund = models.ForeignKey(Fund, on_delete=models.CASCADE, related_name='expenses')
    title = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=date.today)
    description = models.TextField(blank=True, default='')
    attachment = models.FileField(upload_to='fund_attachments/', null=True, blank=True)
    payment_mode = models.CharField(max_length=10, choices=Transaction.PAYMENT_MODE_CHOICES, default='ACCOUNT')
    
    transaction = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='fund_expense_entry')

    def __str__(self):
        return f"Expense of {self.amount} from {self.fund.title}: {self.title}"

    def save(self, *args, **kwargs):
        fund_cat = get_fund_category()
        if self.transaction:
            txn = self.transaction
            txn.date = self.date
            txn.amount = self.amount
            txn.payment_mode = self.payment_mode
            txn.description = f"Fund Expense: {self.title} (Fund: {self.fund.title})"
            txn.category = fund_cat
            txn.save()
        else:
            txn = Transaction.objects.create(
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

    def delete(self, *args, **kwargs):
        txn = self.transaction
        super().delete(*args, **kwargs)
        if txn:
            txn.delete()

