from django.shortcuts import render
from rest_framework import viewsets, views, response, status
from rest_framework.decorators import action
from django.db.models import Sum
from .models import Transaction, BalanceSnapshot, Debt, Category, Event, Fund, FundAddition, FundExpense, Ledger
from .serializers import TransactionSerializer, BalanceSnapshotSerializer, DebtSerializer, CategorySerializer, EventSerializer, FundSerializer, FundAdditionSerializer, FundExpenseSerializer, LedgerSerializer
from datetime import datetime, date, timedelta
import csv
from django.http import HttpResponse
from .utils import generate_pdf_report, generate_yearly_pdf_report, generate_debt_pdf_report, generate_event_pdf_report

from django.db.models import Q

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def get_queryset(self):
        return Category.objects.filter(profile=self.request.user.profile).order_by('name')

    def perform_create(self, serializer):
        serializer.save(profile=self.request.user.profile)

from django.db.models import ProtectedError
from rest_framework.exceptions import ValidationError

class LedgerViewSet(viewsets.ModelViewSet):
    queryset = Ledger.objects.all()
    serializer_class = LedgerSerializer

    def get_queryset(self):
        return Ledger.objects.filter(profile=self.request.user.profile).order_by('name')

    def perform_create(self, serializer):
        serializer.save(profile=self.request.user.profile)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            raise ValidationError({
                "detail": "Cannot delete ledger because it is associated with existing debt records."
            })

class EventViewSet(viewsets.ModelViewSet):
    queryset = Event.objects.all()
    serializer_class = EventSerializer

    def get_queryset(self):
        return Event.objects.filter(profile=self.request.user.profile).order_by('-date', '-id')

    def perform_create(self, serializer):
        serializer.save(profile=self.request.user.profile)

class TransactionViewSet(viewsets.ModelViewSet):
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer

    def get_queryset(self):
        queryset = Transaction.objects.filter(profile=self.request.user.profile).order_by('-date', '-id')
        date_param = self.request.query_params.get('date')
        if date_param:
            queryset = queryset.filter(date=date_param)
        return queryset

    def perform_create(self, serializer):
        debt_description = serializer.validated_data.pop('debt_description', '')
        instance = serializer.save(profile=self.request.user.profile)
        if hasattr(instance, 'debt_entry') and debt_description:
            debt = instance.debt_entry
            debt.description = debt_description
            debt.save()

    def perform_update(self, serializer):
        debt_description = serializer.validated_data.pop('debt_description', None)
        instance = serializer.save()
        if hasattr(instance, 'debt_entry') and debt_description is not None:
            debt = instance.debt_entry
            debt.description = debt_description
            debt.save()

class DebtViewSet(viewsets.ModelViewSet):
    queryset = Debt.objects.all()
    serializer_class = DebtSerializer

    def get_queryset(self):
        return Debt.objects.filter(profile=self.request.user.profile).order_by('-date')

    def perform_create(self, serializer):
        serializer.save(profile=self.request.user.profile)

    @action(detail=False, methods=['get'], url_path='people')
    def people(self, request):
        names = Debt.objects.filter(profile=request.user.profile).values_list('person_name', flat=True).distinct()
        clean_names = sorted(list(set([name.strip() for name in names if name.strip()])))
        return response.Response(clean_names)

    @action(detail=False, methods=['post'], url_path='settle-person')
    def settle_person(self, request):
        person_name = request.data.get('person_name')
        amount = request.data.get('amount')
        debt_type = request.data.get('debt_type')
        payment_mode = request.data.get('payment_mode', 'CASH')
        date_str = request.data.get('date', str(date.today()))
        description = request.data.get('description', '')

        if not person_name or amount is None or not debt_type:
            return response.Response({"error": "person_name, amount, and debt_type are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            amount = float(amount)
        except ValueError:
            return response.Response({"error": "amount must be a number"}, status=status.HTTP_400_BAD_REQUEST)

        if amount <= 0:
            return response.Response({"error": "amount must be greater than zero"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            txn_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            try:
                txn_date = datetime.fromisoformat(date_str.replace('Z', '+00:00')).date()
            except ValueError:
                return response.Response({"error": "Invalid date format"}, status=status.HTTP_400_BAD_REQUEST)

        active_debts = Debt.objects.filter(
            profile=request.user.profile,
            person_name=person_name,
            debt_type=debt_type,
            is_cleared=False
        ).order_by('date', 'id')

        if not active_debts.exists():
            return response.Response({"error": f"No active debts of type {debt_type} found for {person_name}"}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction as db_transaction
        
        created_transactions = []
        payment_remaining = amount

        # Fetch Category
        loan_category = Category.objects.filter(profile=request.user.profile, name='Loan / Debt').first()
        if not loan_category:
            loan_category = Category.objects.filter(profile=request.user.profile, name__icontains='loan').first()

        txn_type = 'DEBT_TAKEN_RETURN' if debt_type == 'TAKEN' else 'DEBT_GIVEN_RETURN'

        with db_transaction.atomic():
            for debt in active_debts:
                if payment_remaining <= 0:
                    break

                # Calculate remaining balance on this debt
                total_repaid = debt.repayments.aggregate(Sum('amount'))['amount__sum'] or 0
                remaining = float(debt.amount) - float(total_repaid)

                if remaining <= 0:
                    continue

                to_apply = min(payment_remaining, remaining)

                # Create the transaction
                txn_desc = f"Repayment: {person_name}"
                if description:
                    txn_desc += f" - {description}"

                txn = Transaction.objects.create(
                    profile=request.user.profile,
                    date=txn_date,
                    amount=to_apply,
                    payment_mode=payment_mode,
                    transaction_type=txn_type,
                    category=loan_category,
                    description=txn_desc,
                    related_debt=debt,
                    ledger=debt.ledger
                )
                created_transactions.append(txn)

                payment_remaining -= to_apply

        applied_amount = amount - payment_remaining

        return response.Response({
            "success": True,
            "applied_amount": applied_amount,
            "remaining_amount": payment_remaining,
            "transactions_count": len(created_transactions)
        })

class DailyReportView(views.APIView):
    def get(self, request):
        date_str = request.query_params.get('date', str(date.today()))
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return response.Response({"error": "Invalid date format"}, status=status.HTTP_400_BAD_REQUEST)

        # Transactions
        transactions = Transaction.objects.filter(
            profile=request.user.profile,
            date=target_date
        )
        txn_serializer = TransactionSerializer(transactions, many=True)

        # Closing Balance (Snapshot of today)
        closing_snapshot = BalanceSnapshot.objects.filter(
            profile=request.user.profile,
            date=target_date
        ).first()
        
        if closing_snapshot:
            closing_data = {
                "cash": closing_snapshot.cash_in_hand,
                "account": closing_snapshot.cash_in_account,
                "total": closing_snapshot.total_balance
            }
        else:
            closing_data = {"cash": 0, "account": 0, "total": 0}
            last_snapshot = BalanceSnapshot.objects.filter(
                profile=request.user.profile,
                date__lte=target_date
            ).order_by('-date').first()
            if last_snapshot:
                 closing_data = {
                    "cash": last_snapshot.cash_in_hand,
                    "account": last_snapshot.cash_in_account,
                    "total": last_snapshot.total_balance
                }

        # Opening Balance (Snapshot of yesterday)
        prev_date = target_date - timedelta(days=1)
        prev_snapshot = BalanceSnapshot.objects.filter(
            profile=request.user.profile,
            date__lte=prev_date
        ).order_by('-date').first()
        opening_data = {"cash": 0, "account": 0, "total": 0}
        if prev_snapshot:
            opening_data = {
                "cash": prev_snapshot.cash_in_hand,
                "account": prev_snapshot.cash_in_account,
                "total": prev_snapshot.total_balance
            }

        # Summaries for the day
        total_expense = transactions.filter(transaction_type='EXPENSE', related_debt__isnull=True).aggregate(Sum('amount'))['amount__sum'] or 0
        total_income = transactions.filter(transaction_type='INCOME', related_debt__isnull=True).aggregate(Sum('amount'))['amount__sum'] or 0
        total_investment = transactions.filter(transaction_type='INVESTMENT').aggregate(Sum('amount'))['amount__sum'] or 0

        return response.Response({
            "date": date_str,
            "opening_balance": opening_data,
            "closing_balance": closing_data,
            "total_income": total_income,
            "total_expense": total_expense,
            "total_investment": total_investment,
            "transactions": txn_serializer.data
        })

class MonthlyReportView(views.APIView):
    def get(self, request):
        year = request.query_params.get('year', datetime.now().year)
        month = request.query_params.get('month', datetime.now().month)
        
        try:
            year = int(year)
            month = int(month)
        except:
            return response.Response({"error": "Invalid year/month"}, status=status.HTTP_400_BAD_REQUEST)

        txns = Transaction.objects.filter(
            profile=request.user.profile,
            date__year=year,
            date__month=month
        )

        total_income = txns.filter(transaction_type='INCOME', related_debt__isnull=True).aggregate(Sum('amount'))['amount__sum'] or 0
        total_expense = txns.filter(transaction_type='EXPENSE', related_debt__isnull=True).aggregate(Sum('amount'))['amount__sum'] or 0
        total_investment = txns.filter(transaction_type='INVESTMENT').aggregate(Sum('amount'))['amount__sum'] or 0
        
        # Net savings: Income - Expense (Investment is usually part of savings allocation, but if we consider it cash out...)
        # Let's define Net Savings as purely Income - Expense for now, or Income - (Expense + Investment)?
        # For personal finance, usually Income - Expense = Savings (which can be held in cash or invested).
        # So we leave Net Savings as Income - Expense.
        net_savings = total_income - total_expense

        # Total credit (all inflows: INCOME, DEBT_TAKEN, DEBT_GIVEN_RETURN, FUND_MANAGEMENT_INC)
        total_credit = txns.filter(
            transaction_type__in=['INCOME', 'DEBT_TAKEN', 'DEBT_GIVEN_RETURN', 'FUND_MANAGEMENT_INC']
        ).aggregate(Sum('amount'))['amount__sum'] or 0

        # Total debit (all outflows: EXPENSE, INVESTMENT, DEBT_GIVEN, DEBT_TAKEN_RETURN, FUND_MANAGEMENT_DEC)
        total_debit = txns.filter(
            transaction_type__in=['EXPENSE', 'INVESTMENT', 'DEBT_GIVEN', 'DEBT_TAKEN_RETURN', 'FUND_MANAGEMENT_DEC']
        ).aggregate(Sum('amount'))['amount__sum'] or 0

        # Remaining balance: closing balance of the last snapshot in the month, or prior fallback
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        target_date = date(year, month, last_day)

        closing_snapshot = BalanceSnapshot.objects.filter(
            profile=request.user.profile,
            date__lte=target_date
        ).order_by('-date').first()

        if closing_snapshot:
            remaining_amount = closing_snapshot.total_balance
            remaining_cash = closing_snapshot.cash_in_hand
            remaining_account = closing_snapshot.cash_in_account
        else:
            remaining_amount = 0
            remaining_cash = 0
            remaining_account = 0

        # Dynamically group category breakdown by transaction type and category
        stats = txns.values('transaction_type', 'category__name').annotate(total=Sum('amount')).order_by('transaction_type', '-total')
        
        category_stats_formatted = []
        for item in stats:
            txn_type = item['transaction_type']
            cat_name = item['category__name']
            
            # Fetch the transactions for this specific type and category
            cat_txns = txns.filter(transaction_type=txn_type, category__name=cat_name).order_by('-date')
            category_stats_formatted.append({
                "category": cat_name if cat_name else 'Uncategorized',
                "type": txn_type,
                "total": item['total'],
                "transactions": TransactionSerializer(cat_txns, many=True).data
            })

        # Debt Breakdown
        debt_taken_total = txns.filter(transaction_type='DEBT_TAKEN').aggregate(Sum('amount'))['amount__sum'] or 0
        debt_given_total = txns.filter(transaction_type='DEBT_GIVEN').aggregate(Sum('amount'))['amount__sum'] or 0
        debt_taken_return_total = txns.filter(transaction_type='DEBT_TAKEN_RETURN').aggregate(Sum('amount'))['amount__sum'] or 0
        debt_given_return_total = txns.filter(transaction_type='DEBT_GIVEN_RETURN').aggregate(Sum('amount'))['amount__sum'] or 0
        
        debt_txns = txns.filter(transaction_type__in=['DEBT_TAKEN', 'DEBT_GIVEN', 'DEBT_TAKEN_RETURN', 'DEBT_GIVEN_RETURN']).order_by('-date')
        debt_serializer = TransactionSerializer(debt_txns, many=True)
        
        return response.Response({
            "month": month,
            "year": year,
            "total_income": total_income,
            "total_expense": total_expense,
            "total_investment": total_investment,
            "net_savings": net_savings,
            "total_credit": total_credit,
            "total_debit": total_debit,
            "remaining_balance": remaining_amount,
            "remaining_amount": remaining_amount,
            "remaining_cash": remaining_cash,
            "remaining_account": remaining_account,
            "category_stats": category_stats_formatted,
            "category_breakdown": category_stats_formatted,
            "debts": {
                "total_taken": debt_taken_total,
                "total_given": debt_given_total,
                "total_taken_returned": debt_taken_return_total,
                "total_given_returned": debt_given_return_total,
                "transactions": debt_serializer.data
            },
            "debt_breakdown": {
                "debt_taken": debt_taken_total,
                "debt_given": debt_given_total,
                "debt_taken_return": debt_taken_return_total,
                "debt_given_return": debt_given_return_total,
                "transactions": debt_serializer.data
            }
        })




class ExportMonthlyCSVView(views.APIView):
    def get(self, request):
        year = request.query_params.get('year', datetime.now().year)
        month = request.query_params.get('month', datetime.now().month)
        
        try:
            year = int(year)
            month = int(month)
        except:
            return response.Response({"error": "Invalid year/month"}, status=status.HTTP_400_BAD_REQUEST)

        txns = Transaction.objects.filter(
            profile=request.user.profile,
            date__year=year,
            date__month=month
        ).order_by('date')

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="transactions_{year}_{month}.csv"'

        writer = csv.writer(response)
        writer.writerow(['Date', 'Type', 'Amount', 'Category', 'Description', 'Payment Mode'])

        for txn in txns:
            writer.writerow([
                txn.date,
                txn.transaction_type,
                txn.amount,
                txn.category.name if txn.category else '',
                txn.description,
                txn.payment_mode
            ])

        return response


class ExportPDFReportView(views.APIView):
    def get(self, request):
        report_type = request.query_params.get('type', 'monthly') # daily, weekly, monthly, yearly, custom
        
        # Default Filter
        queryset = Transaction.objects.filter(
            profile=request.user.profile
        ).order_by('date')
        title = "Financial Report"
        date_range = ""

        try:
            if report_type == 'daily':
                date_str = request.query_params.get('date', str(date.today()))
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                queryset = queryset.filter(date=target_date)
                title = f"Daily Report - {target_date}"
                date_range = str(target_date)

            elif report_type == 'monthly':
                year = int(request.query_params.get('year', datetime.now().year))
                month = int(request.query_params.get('month', datetime.now().month))
                queryset = queryset.filter(date__year=year, date__month=month)
                month_name = date(year, month, 1).strftime('%B')
                title = f"Monthly Report - {month_name} {year}"
                date_range = f"{month_name} {year}"

            elif report_type == 'yearly':
                year = int(request.query_params.get('year', datetime.now().year))
                queryset = queryset.filter(date__year=year)
                title = f"Yearly Report - {year}"
                date_range = str(year)

            elif report_type == 'custom': # Also covers weekly if start/end provided manually
                start_date_str = request.query_params.get('start_date')
                end_date_str = request.query_params.get('end_date')
                if start_date_str and end_date_str:
                    s_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                    e_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                    queryset = queryset.filter(date__range=[s_date, e_date])
                    title = f"Custom Report"
                    date_range = f"{s_date} to {e_date}"
                else:
                    return response.Response({"error": "start_date and end_date required for custom report"}, status=status.HTTP_400_BAD_REQUEST)
            
            # Helper for 'weekly' - if user passes a specific date, calculated week of that date
            elif report_type == 'weekly':
                date_str = request.query_params.get('date', str(date.today()))
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                start_week = target_date - timedelta(days=target_date.weekday())
                end_week = start_week + timedelta(days=6)
                queryset = queryset.filter(date__range=[start_week, end_week])
                title = f"Weekly Report"
                date_range = f"{start_week} to {end_week}"

            elif report_type == 'debt':
                queryset = Debt.objects.filter(
                    profile=request.user.profile
                ).order_by('-date')
                # Optional: Filter by status if needed
                status_filter = request.query_params.get('status')
                if status_filter == 'active':
                    queryset = queryset.filter(is_cleared=False)
                elif status_filter == 'cleared':
                    queryset = queryset.filter(is_cleared=True)

            elif report_type == 'event':
                event_id = request.query_params.get('event_id')
                if not event_id:
                    return response.Response({"error": "event_id is required for event report"}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    event = Event.objects.filter(
                        profile=request.user.profile
                    ).get(id=event_id)
                except Event.DoesNotExist:
                    return response.Response({"error": "event not found"}, status=status.HTTP_404_NOT_FOUND)
                queryset = Transaction.objects.filter(profile=request.user.profile, related_event=event).order_by('date')
                title = event.name
                date_range = str(event.date)


        except ValueError:
             return response.Response({"error": "Invalid date parameters"}, status=status.HTTP_400_BAD_REQUEST)

        # Generate PDF
        if report_type == 'yearly':
            year = int(request.query_params.get('year', datetime.now().year))
            pdf_buffer = generate_yearly_pdf_report(title, year, queryset)
        elif report_type == 'debt':
            pdf_buffer = generate_debt_pdf_report(queryset)
        elif report_type == 'event':
            pdf_buffer = generate_event_pdf_report(title, date_range, queryset)
        else:
            # Calculate Totals only for transaction-based standard reports
            total_income = queryset.filter(transaction_type='INCOME', related_debt__isnull=True).aggregate(Sum('amount'))['amount__sum'] or 0
            total_expense = queryset.filter(transaction_type='EXPENSE', related_debt__isnull=True).aggregate(Sum('amount'))['amount__sum'] or 0
            
             # Calculate Daily Balances if it's not yearly (though monthly is the primary user of this)
            daily_balances = {}
            report_dates = queryset.values_list('date', flat=True).distinct()
            
            for d in report_dates:
                closing_snapshot = BalanceSnapshot.objects.filter(
                    profile=request.user.profile,
                    date__lte=d
                ).order_by('-date').first()

                if closing_snapshot:
                    closing_data = {
                        'total': closing_snapshot.total_balance,
                        'cash': closing_snapshot.cash_in_hand,
                        'account': closing_snapshot.cash_in_account
                    }
                else:
                    closing_data = {'total': 0, 'cash': 0, 'account': 0}
                
                opening_snapshot = BalanceSnapshot.objects.filter(
                    profile=request.user.profile,
                    date__lt=d
                ).order_by('-date').first()

                if opening_snapshot:
                    opening_data = {
                        'total': opening_snapshot.total_balance,
                        'cash': opening_snapshot.cash_in_hand,
                        'account': opening_snapshot.cash_in_account
                    }
                else:
                    opening_data = {'total': 0, 'cash': 0, 'account': 0}
                
                daily_balances[d] = {
                    'opening': opening_data,
                    'closing': closing_data
                }
            
            pdf_buffer = generate_pdf_report(title, date_range, queryset, total_income, total_expense, daily_balances)

        resp = HttpResponse(pdf_buffer, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="report_{report_type}.pdf"'
        return resp

class AppInitView(views.APIView):
    def get(self, request):
        # Check if the app is initialized
        # We consider initialized if there is at least one transaction or debt for this profile
        profile = request.user.profile
        is_initialized = Transaction.objects.filter(profile=profile).exists() or Debt.objects.filter(profile=profile).exists()
        return response.Response({"initialized": is_initialized})

    def post(self, request):
        # Initialize the app with opening balances
        data = request.data
        initial_account = data.get('account_balance', 0)
        initial_cash = data.get('cash_balance', 0)
        initial_date_str = data.get('date', str(date.today()))
        
        try:
            initial_date = datetime.strptime(initial_date_str, '%Y-%m-%d').date()
        except ValueError:
            return response.Response({"error": "Invalid date format"}, status=status.HTTP_400_BAD_REQUEST)

        # Create Default Categories
        default_categories = [
            'Food', 'Travel', 'Medical', 'Shopping', 'Recharge / Internet',
            'Charity', 'Loan / Debt', 'Investment', 'Miscellaneous'
        ]
        profile = request.user.profile

        for cat_name in default_categories:
            Category.objects.get_or_create(name=cat_name, defaults={'profile': profile})

        misc_category, _ = Category.objects.get_or_create(name='Miscellaneous', defaults={'profile': profile})

        # Create Initial Transactions
        # We create them even if 0 to ensure the app is marked as "initialized" (Transaction.objects.exists() becomes True)
        Transaction.objects.create(
            profile=profile,
            date=initial_date,
            amount=initial_account,
            payment_mode='ACCOUNT',
            transaction_type='INCOME',
            category=misc_category,
            description='Initial Account Balance'
        )
        
        Transaction.objects.create(
            profile=profile,
            date=initial_date,
            amount=initial_cash,
            payment_mode='CASH',
            transaction_type='INCOME',
            category=misc_category,
            description='Initial Cash Balance'
        )

        return response.Response({"message": "App initialized successfully", "initialized": True})

class FundViewSet(viewsets.ModelViewSet):
    queryset = Fund.objects.all()
    serializer_class = FundSerializer

    def get_queryset(self):
        return Fund.objects.filter(profile=self.request.user.profile).order_by('-received_date', '-id')

    def perform_create(self, serializer):
        serializer.save(profile=self.request.user.profile)

    @action(detail=False, methods=['get'], url_path='reports')
    def reports(self, request):
        profile = request.user.profile
        active_funds = Fund.objects.filter(profile=profile, status='ACTIVE')
        closed_funds = Fund.objects.filter(profile=profile, status='CLOSED')

        fund_initial_sum = Fund.objects.filter(profile=profile).aggregate(Sum('initial_amount'))['initial_amount__sum'] or 0
        additions_sum = FundAddition.objects.filter(profile=profile).aggregate(Sum('amount'))['amount__sum'] or 0
        total_received = fund_initial_sum + additions_sum

        total_spent = FundExpense.objects.filter(profile=profile).aggregate(Sum('amount'))['amount__sum'] or 0
        remaining_balance = total_received - total_spent

        return response.Response({
            'summary': {
                'total_received': float(total_received),
                'total_spent': float(total_spent),
                'remaining_balance': float(remaining_balance),
                'active_count': active_funds.count(),
                'settled_count': closed_funds.count()
            },
            'active_funds': FundSerializer(active_funds, many=True, context={'request': request}).data,
            'settled_funds': FundSerializer(closed_funds, many=True, context={'request': request}).data
        })

class FundAdditionViewSet(viewsets.ModelViewSet):
    queryset = FundAddition.objects.all()
    serializer_class = FundAdditionSerializer

    def get_queryset(self):
        return FundAddition.objects.filter(profile=self.request.user.profile).order_by('-date', '-id')

    def perform_create(self, serializer):
        serializer.save(profile=self.request.user.profile)

class FundExpenseViewSet(viewsets.ModelViewSet):
    queryset = FundExpense.objects.all()
    serializer_class = FundExpenseSerializer

    def get_queryset(self):
        return FundExpense.objects.filter(profile=self.request.user.profile).order_by('-date', '-id')

    def perform_create(self, serializer):
        serializer.save(profile=self.request.user.profile)


class GlobalSearchView(views.APIView):
    def get(self, request):
        q = request.query_params.get('q', '').strip()
        if not q:
            return response.Response([])

        profile = request.user.profile

        # 1. Search Transactions
        txn_query = Q(description__icontains=q) | \
                    Q(category__name__icontains=q) | \
                    Q(related_debt__person_name__icontains=q) | \
                    Q(related_debt__ledger__name__icontains=q) | \
                    Q(related_debt__description__icontains=q) | \
                    Q(related_fund__title__icontains=q) | \
                    Q(related_fund__ledger__name__icontains=q) | \
                    Q(related_fund__notes__icontains=q) | \
                    Q(related_event__name__icontains=q)
        
        transactions = Transaction.objects.filter(profile=profile).filter(txn_query).distinct().order_by('-date', '-id')[:100]

        results = []
        seen_txn_ids = set()

        for txn in transactions:
            seen_txn_ids.add(txn.id)
            target = 'transaction'
            target_id = txn.id
            
            # Check if debt related
            debt_id = None
            if txn.transaction_type in ['DEBT_TAKEN', 'DEBT_GIVEN'] and hasattr(txn, 'debt_entry'):
                debt_id = txn.debt_entry.id
            elif txn.related_debt:
                debt_id = txn.related_debt.id
                
            if debt_id:
                target = 'debt'
                target_id = debt_id
            elif txn.related_fund_id:
                target = 'fund'
                target_id = txn.related_fund_id
                
            results.append({
                'id': txn.id,
                'model': 'transaction',
                'date': txn.date.strftime('%Y-%m-%d'),
                'amount': float(txn.amount),
                'type': txn.transaction_type,
                'description': txn.description,
                'category': txn.category.name if txn.category else '',
                'target': target,
                'target_id': target_id,
                'target_date': txn.date.strftime('%Y-%m-%d')
            })

        # 2. Search Debts (standalone or unmatched)
        debt_query = Q(person_name__icontains=q) | Q(description__icontains=q) | Q(ledger__name__icontains=q)
        debts = Debt.objects.filter(profile=profile).filter(debt_query).distinct()[:50]
        
        for debt in debts:
            if debt.transaction and debt.transaction.id in seen_txn_ids:
                continue
            
            results.append({
                'id': debt.id,
                'model': 'debt',
                'date': debt.date.strftime('%Y-%m-%d'),
                'amount': float(debt.amount),
                'type': f"DEBT_{debt.debt_type}",
                'description': f"Debt: {debt.person_name} - {debt.description}" if debt.description else f"Debt: {debt.person_name}",
                'category': 'Loan / Debt',
                'target': 'debt',
                'target_id': debt.id,
                'target_date': debt.date.strftime('%Y-%m-%d')
            })

        fund_query = Q(title__icontains=q) | Q(ledger__name__icontains=q) | Q(notes__icontains=q)
        funds = Fund.objects.filter(profile=profile).filter(fund_query).distinct()[:50]
        
        for fund in funds:
            if fund.transaction and fund.transaction.id in seen_txn_ids:
                continue
            
            provider_name = fund.ledger.name if fund.ledger else "Unknown"
            results.append({
                'id': fund.id,
                'model': 'fund',
                'date': fund.received_date.strftime('%Y-%m-%d'),
                'amount': float(fund.initial_amount),
                'type': 'FUND_MANAGEMENT_INC',
                'description': f"Fund: {fund.title} (Provider: {provider_name})",
                'category': 'Fund Management',
                'target': 'fund',
                'target_id': fund.id,
                'target_date': fund.received_date.strftime('%Y-%m-%d')
            })

        # Sort combined results by date descending, then id descending
        results.sort(key=lambda x: (x['date'], x['id']), reverse=True)

        return response.Response(results)

