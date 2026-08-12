from rest_framework import serializers
from .models import Transaction, BalanceSnapshot, Debt, Category, Event, Fund, FundAddition, FundExpense, Ledger
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
    debt_description = serializers.CharField(required=False, allow_blank=True, default='')
    ledger_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Transaction
        fields = '__all__'
        read_only_fields = ['profile']

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

