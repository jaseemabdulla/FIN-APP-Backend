from rest_framework import serializers
from .models import Transaction, BalanceSnapshot, Debt, Category, Event, Fund, FundAddition, FundExpense
from django.db.models import Sum

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class TransactionSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    event_name = serializers.CharField(source='related_event.name', read_only=True)
    related_fund_title = serializers.CharField(source='related_fund.title', read_only=True)
    debt_description = serializers.CharField(required=False, allow_blank=True, default='')
    
    class Meta:
        model = Transaction
        fields = '__all__'

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

class DebtSerializer(serializers.ModelSerializer):
    remaining_amount = serializers.SerializerMethodField()
    total_repaid = serializers.SerializerMethodField()
    repayments = TransactionSerializer(many=True, read_only=True)
    cleared_date = serializers.SerializerMethodField()

    class Meta:
        model = Debt
        fields = '__all__'

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

class FundExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = FundExpense
        fields = '__all__'

class FundSerializer(serializers.ModelSerializer):
    additions = FundAdditionSerializer(many=True, read_only=True)
    expenses = FundExpenseSerializer(many=True, read_only=True)
    
    total_received = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()
    number_of_transactions = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()

    class Meta:
        model = Fund
        fields = '__all__'

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
        timeline.append({
            'id': f"initial_{obj.id}",
            'type': 'INITIAL_FUND',
            'date': format_date(obj.received_date),
            'title': 'Fund Created',
            'amount': float(obj.initial_amount),
            'notes': f"Fund created with initial amount of {obj.initial_amount} from {obj.provider}. Purpose: {obj.purpose}. Notes: {obj.notes}"
        })
        
        # Additions
        for add in obj.additions.all():
            timeline.append({
                'id': f"addition_{add.id}",
                'type': 'ADDITIONAL_FUND',
                'date': format_date(add.date),
                'title': 'Additional Funds Added',
                'amount': float(add.amount),
                'notes': add.notes
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
                'category': exp.category.name if exp.category else 'Uncategorized',
                'amount': float(exp.amount),
                'notes': exp.description,
                'attachment_url': attachment_url
            })
            
        # Settlement
        if obj.status == 'SETTLED':
            timeline.append({
                'id': f"settlement_{obj.id}",
                'type': 'SETTLEMENT',
                'date': format_date(obj.settlement_date),
                'title': 'Fund Settled & Closed',
                'returned_amount': float(obj.returned_amount) if obj.returned_amount else 0.0,
                'additional_amount_required': float(obj.additional_amount_required) if obj.additional_amount_required else 0.0,
                'notes': obj.settlement_notes
            })
            
        # Sort by date, type, id to keep a consistent timeline
        timeline.sort(key=lambda x: (x['date'], x['id']))
        return timeline

