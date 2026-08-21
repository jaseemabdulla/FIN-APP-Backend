from rest_framework import serializers
from .models import Transaction, BalanceSnapshot, Debt, Category, Event, Fund, FundAddition, FundExpense, Ledger, Investment
from django.db.models import Sum

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'
        read_only_fields = ['profile']

class TransactionSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    event_name = serializers.CharField(source='related_event.name', read_only=True)
    related_fund_title = serializers.CharField(source='related_fund.title', read_only=True)
    related_investment_name = serializers.CharField(source='related_investment.name', read_only=True)
    debt_description = serializers.CharField(required=False, allow_blank=True, default='')
    ledger_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Transaction
        fields = '__all__'
        read_only_fields = ['profile']

    def validate(self, attrs):
        # Validation for investment returns and additions
        txn_type = attrs.get('transaction_type', self.instance.transaction_type if self.instance else None)
        if txn_type == 'INVESTMENT_RETURN':
            related_investment = attrs.get('related_investment', self.instance.related_investment if self.instance else None)
            if not related_investment:
                raise serializers.ValidationError({"related_investment": "This field is required for Investment Return transactions."})
            
            amount = attrs.get('amount', self.instance.amount if self.instance else 0)
            
            # Compute remaining balance on this investment
            invested_txns = related_investment.transactions.filter(transaction_type='INVESTMENT')
            if self.instance:
                invested_txns = invested_txns.exclude(pk=self.instance.pk)
            total_invested = invested_txns.aggregate(Sum('amount'))['amount__sum'] or 0
            
            returned_txns = related_investment.transactions.filter(transaction_type='INVESTMENT_RETURN')
            if self.instance:
                returned_txns = returned_txns.exclude(pk=self.instance.pk)
            total_returned = returned_txns.aggregate(Sum('amount'))['amount__sum'] or 0
            
            remaining_balance = total_invested - total_returned
            
            if amount > remaining_balance:
                raise serializers.ValidationError({
                    "amount": f"Withdrawal amount cannot exceed the available investment balance of ₹{remaining_balance:,.2f}"
                })
        elif txn_type == 'INVESTMENT':
            related_investment = attrs.get('related_investment', self.instance.related_investment if self.instance else None)
            if not related_investment:
                raise serializers.ValidationError({"related_investment": "This field is required for Investment transactions."})
                
        return attrs

    def get_ledger_name(self, obj):
        if obj.ledger:
            return obj.ledger.name
        if obj.related_debt and obj.related_debt.ledger:
            return obj.related_debt.ledger.name
        if obj.related_debt and obj.related_debt.person_name:
            return obj.related_debt.person_name
        if obj.transaction_type in ['DEBT_TAKEN', 'DEBT_GIVEN'] and obj.description:
            return obj.description
        return ''

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        
        representation['debt_is_cleared'] = None
        representation['debt_total_repaid'] = None
        representation['debt_remaining_amount'] = None
        representation['fund_status'] = None
        
        if hasattr(instance, 'debt_entry') and instance.debt_entry:
            debt = instance.debt_entry
            total_repaid = debt.repayments.aggregate(Sum('amount'))['amount__sum'] or 0
            representation['debt_is_cleared'] = debt.is_cleared
            representation['debt_total_repaid'] = float(total_repaid)
            representation['debt_remaining_amount'] = float(debt.amount - total_repaid)
        elif instance.related_debt:
            debt = instance.related_debt
            total_repaid = debt.repayments.aggregate(Sum('amount'))['amount__sum'] or 0
            representation['debt_is_cleared'] = debt.is_cleared
            representation['debt_total_repaid'] = float(total_repaid)
            representation['debt_remaining_amount'] = float(debt.amount - total_repaid)
            
        if instance.related_fund:
            representation['fund_status'] = instance.related_fund.status
            
        if hasattr(instance, 'debt_entry'):
            representation['debt_description'] = instance.debt_entry.description
        else:
            representation['debt_description'] = ''
            
        return representation

class EventSerializer(serializers.ModelSerializer):
    amount_received = serializers.SerializerMethodField()
    amount_spent = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()
    transactions = TransactionSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = '__all__'
        read_only_fields = ['profile']

    def get_amount_received(self, obj):
        return obj.transactions.filter(transaction_type='INCOME').aggregate(Sum('amount'))['amount__sum'] or 0

    def get_amount_spent(self, obj):
        return obj.transactions.filter(transaction_type='EXPENSE').aggregate(Sum('amount'))['amount__sum'] or 0

    def get_balance(self, obj):
        return self.get_amount_received(obj) - self.get_amount_spent(obj)

class BalanceSnapshotSerializer(serializers.ModelSerializer):
    total_balance = serializers.ReadOnlyField()

    class Meta:
        model = BalanceSnapshot
        fields = '__all__'
        read_only_fields = ['profile']

class LedgerSerializer(serializers.ModelSerializer):
    debt_count = serializers.IntegerField(source='debts.count', read_only=True)

    class Meta:
        model = Ledger
        fields = '__all__'
        read_only_fields = ['profile']

class DebtSerializer(serializers.ModelSerializer):
    remaining_amount = serializers.SerializerMethodField()
    total_repaid = serializers.SerializerMethodField()
    repayments = TransactionSerializer(many=True, read_only=True)
    cleared_date = serializers.SerializerMethodField()
    person_name = serializers.CharField(required=False)
    ledger_details = LedgerSerializer(source='ledger', read_only=True)

    class Meta:
        model = Debt
        fields = '__all__'
        read_only_fields = ['profile']

    def validate(self, attrs):
        ledger = attrs.get('ledger')
        if ledger:
            attrs['person_name'] = ledger.name
        elif not attrs.get('person_name'):
            if not self.instance or not self.instance.person_name:
                raise serializers.ValidationError({"person_name": "This field is required if ledger is not provided."})
        return attrs

    def get_total_repaid(self, obj):
        return obj.repayments.aggregate(Sum('amount'))['amount__sum'] or 0

    def get_remaining_amount(self, obj):
        repaid = self.get_total_repaid(obj)
        return obj.amount - repaid

    def get_cleared_date(self, obj):
        if obj.is_cleared:
            last_repayment = obj.repayments.order_by('-date', '-id').first()
            if last_repayment:
                return last_repayment.date.strftime('%Y-%m-%d')
            return obj.date.strftime('%Y-%m-%d')
        return None

class FundAdditionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FundAddition
        fields = '__all__'
        read_only_fields = ['profile']

class FundExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = FundExpense
        fields = '__all__'
        read_only_fields = ['profile']

class FundSerializer(serializers.ModelSerializer):
    additions = FundAdditionSerializer(many=True, read_only=True)
    expenses = FundExpenseSerializer(many=True, read_only=True)
    ledger_details = LedgerSerializer(source='ledger', read_only=True)
    
    total_received = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()
    number_of_transactions = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()

    class Meta:
        model = Fund
        fields = '__all__'
        read_only_fields = ['profile']

    def validate(self, attrs):
        if not self.instance:
            if not attrs.get('ledger'):
                raise serializers.ValidationError({"ledger": "This field is required."})
        return attrs

    def get_total_received(self, obj):
        initial = obj.initial_amount or 0
        additions_sum = obj.additions.aggregate(Sum('amount'))['amount__sum'] or 0
        return initial + additions_sum

    def get_total_spent(self, obj):
        return obj.expenses.aggregate(Sum('amount'))['amount__sum'] or 0

    def get_remaining_balance(self, obj):
        return self.get_total_received(obj) - self.get_total_spent(obj)

    def get_number_of_transactions(self, obj):
        return obj.additions.count() + obj.expenses.count()

    def get_timeline(self, obj):
        timeline = []

        def format_date(d):
            if not d:
                return ''
            if isinstance(d, str):
                return d
            return d.strftime('%Y-%m-%d')
        
        # Initial Fund
        provider_name = obj.ledger.name if obj.ledger else "Unknown"
        timeline.append({
            'id': f"initial_{obj.id}",
            'type': 'INITIAL_FUND',
            'date': format_date(obj.received_date),
            'title': 'Fund Created',
            'amount': float(obj.initial_amount),
            'notes': f"Fund created with initial amount of {obj.initial_amount} from {provider_name}. Notes: {obj.notes}"
        })
        
        # Additions
        for add in obj.additions.all():
            timeline.append({
                'id': f"addition_{add.id}",
                'type': 'ADDITIONAL_FUND',
                'date': format_date(add.date),
                'title': 'Additional Funds Added',
                'amount': float(add.amount),
                'notes': add.notes,
                'payment_mode': add.payment_mode
            })
            
        # Expenses
        request = self.context.get('request')
        for exp in obj.expenses.all():
            attachment_url = None
            if exp.attachment:
                if request:
                    attachment_url = request.build_absolute_uri(exp.attachment.url)
                else:
                    attachment_url = exp.attachment.url
            timeline.append({
                'id': f"expense_{exp.id}",
                'type': 'EXPENSE',
                'date': format_date(exp.date),
                'title': exp.title,
                'amount': float(exp.amount),
                'notes': exp.description,
                'attachment_url': attachment_url,
                'payment_mode': exp.payment_mode
            })
            
        # Sort by date, type, id to keep a consistent timeline
        timeline.sort(key=lambda x: (x['date'], x['id']))
        return timeline

class InvestmentSerializer(serializers.ModelSerializer):
    total_invested = serializers.SerializerMethodField()
    total_withdrawn = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    transactions = TransactionSerializer(many=True, read_only=True)

    class Meta:
        model = Investment
        fields = '__all__'
        read_only_fields = ['profile']

    def get_total_invested(self, obj):
        return obj.transactions.filter(transaction_type='INVESTMENT').aggregate(Sum('amount'))['amount__sum'] or 0

    def get_total_withdrawn(self, obj):
        return obj.transactions.filter(transaction_type='INVESTMENT_RETURN').aggregate(Sum('amount'))['amount__sum'] or 0

    def get_remaining_balance(self, obj):
        return self.get_total_invested(obj) - self.get_total_withdrawn(obj)

    def get_status(self, obj):
        balance = self.get_remaining_balance(obj)
        if balance > 0:
            return 'ACTIVE'
        return 'CLOSED'

