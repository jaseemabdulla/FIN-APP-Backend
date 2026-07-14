from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.db.models import Sum
from finance.models import Debt, Transaction, Category
from datetime import date, timedelta

class DebtSettlePersonTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name="Loan / Debt")
        
        # Debt 1: $100, TAKEN, oldest (2 days ago)
        self.t1 = Transaction.objects.create(
            date=date.today() - timedelta(days=2),
            amount=100.00,
            payment_mode='CASH',
            transaction_type='DEBT_TAKEN',
            category=self.category,
            description="Test Person"
        )
        
        # Debt 2: $50, TAKEN, middle (1 day ago)
        self.t2 = Transaction.objects.create(
            date=date.today() - timedelta(days=1),
            amount=50.00,
            payment_mode='CASH',
            transaction_type='DEBT_TAKEN',
            category=self.category,
            description="Test Person"
        )
        
        # Debt 3: $30, TAKEN, newest (today)
        self.t3 = Transaction.objects.create(
            date=date.today(),
            amount=30.00,
            payment_mode='CASH',
            transaction_type='DEBT_TAKEN',
            category=self.category,
            description="Test Person"
        )

    def test_partial_settlement_chronological(self):
        response = self.client.post('/api/debts/settle-person/', {
            "person_name": "Test Person",
            "amount": 120.00,
            "debt_type": "TAKEN",
            "payment_mode": "CASH",
            "date": str(date.today()),
            "description": "Partial settlement test"
        }, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        self.assertEqual(float(response.data['applied_amount']), 120.00)
        self.assertEqual(float(response.data['remaining_amount']), 0.00)
        self.assertEqual(response.data['transactions_count'], 2)
        
        # Check Debt 1: Should be cleared
        debt1 = Debt.objects.get(transaction=self.t1)
        self.assertEqual(debt1.is_cleared, True)
        
        # Check Debt 2: Should be active, with $20 repaid (remaining $30)
        debt2 = Debt.objects.get(transaction=self.t2)
        self.assertEqual(debt2.is_cleared, False)
        repayments2 = debt2.repayments.all()
        self.assertEqual(repayments2.count(), 1)
        self.assertEqual(float(repayments2[0].amount), 20.00)
        
        # Check Debt 3: Should be active, no repayments
        debt3 = Debt.objects.get(transaction=self.t3)
        self.assertEqual(debt3.is_cleared, False)
        self.assertEqual(debt3.repayments.count(), 0)

    def test_full_settlement(self):
        response = self.client.post('/api/debts/settle-person/', {
            "person_name": "Test Person",
            "amount": 180.00,
            "debt_type": "TAKEN",
            "payment_mode": "CASH",
            "date": str(date.today()),
            "description": "Full settlement test"
        }, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data['applied_amount']), 180.00)
        
        # All debts should be cleared
        for t in [self.t1, self.t2, self.t3]:
            debt = Debt.objects.get(transaction=t)
            self.assertEqual(debt.is_cleared, True)

    def test_overpayment_capping(self):
        response = self.client.post('/api/debts/settle-person/', {
            "person_name": "Test Person",
            "amount": 200.00,
            "debt_type": "TAKEN",
            "payment_mode": "CASH",
            "date": str(date.today()),
            "description": "Overpayment test"
        }, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data['applied_amount']), 180.00)
        self.assertEqual(float(response.data['remaining_amount']), 20.00)
        
        # All debts should be cleared
        for t in [self.t1, self.t2, self.t3]:
            debt = Debt.objects.get(transaction=t)
            self.assertEqual(debt.is_cleared, True)

class FundTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name="Tech Supplies")

    def test_fund_creation_and_actions(self):
        # 1. Create a fund
        create_data = {
            "title": "Tech Fest 2026",
            "purpose": "Organizing annual event",
            "provider": "Alice",
            "initial_amount": "5000.00",
            "received_date": "2026-07-10",
            "notes": "Sponsor money",
            "payment_mode": "ACCOUNT"
        }
        response = self.client.post('/api/funds/', create_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        fund_id = response.data['id']
        self.assertEqual(float(response.data['total_received']), 5000.00)
        self.assertEqual(float(response.data['remaining_balance']), 5000.00)
        self.assertEqual(response.data['status'], 'ACTIVE')

        # Verify underlying Transaction was created
        self.assertEqual(Transaction.objects.count(), 1)
        t_init = Transaction.objects.first()
        self.assertEqual(t_init.transaction_type, 'FUND_MANAGEMENT_INC')
        self.assertEqual(float(t_init.amount), 5000.00)
        self.assertEqual(t_init.payment_mode, 'ACCOUNT')
        self.assertEqual(t_init.related_fund_id, fund_id)

        # 2. Add additional fund
        addition_data = {
            "fund": fund_id,
            "amount": "1500.00",
            "date": "2026-07-11",
            "notes": "Second installment",
            "payment_mode": "ACCOUNT"
        }
        res_add = self.client.post('/api/fund-additions/', addition_data, format='json')
        self.assertEqual(res_add.status_code, status.HTTP_201_CREATED)

        # Verify additional Transaction was created
        self.assertEqual(Transaction.objects.count(), 2)
        t_add = Transaction.objects.order_by('-id').first()
        self.assertEqual(t_add.transaction_type, 'FUND_MANAGEMENT_INC')
        self.assertEqual(float(t_add.amount), 1500.00)
        self.assertEqual(t_add.related_fund_id, fund_id)

        # 3. Add expense
        expense_data = {
            "fund": fund_id,
            "title": "Purchase routers",
            "category": self.category.id,
            "amount": "2000.00",
            "date": "2026-07-12",
            "description": "Router buy",
            "payment_mode": "ACCOUNT"
        }
        res_exp = self.client.post('/api/fund-expenses/', expense_data, format='json')
        self.assertEqual(res_exp.status_code, status.HTTP_201_CREATED)

        # Verify expense Transaction was created
        self.assertEqual(Transaction.objects.count(), 3)
        t_exp = Transaction.objects.order_by('-id').first()
        self.assertEqual(t_exp.transaction_type, 'FUND_MANAGEMENT_DEC')
        self.assertEqual(float(t_exp.amount), 2000.00)
        self.assertEqual(t_exp.related_fund_id, fund_id)

        # 4. Check reports and totals
        res_reports = self.client.get('/api/funds/reports/')
        self.assertEqual(res_reports.status_code, status.HTTP_200_OK)
        summary = res_reports.data['summary']
        self.assertEqual(summary['total_received'], 6500.00)
        self.assertEqual(summary['total_spent'], 2000.00)
        self.assertEqual(summary['remaining_balance'], 4500.00)
        self.assertEqual(summary['active_count'], 1)

        # 5. Check timeline sorted correctly
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.status_code, status.HTTP_200_OK)
        timeline = res_detail.data['timeline']
        self.assertEqual(len(timeline), 3) # initial fund, addition, expense
        self.assertEqual(timeline[0]['type'], 'INITIAL_FUND')
        self.assertEqual(timeline[1]['type'], 'ADDITIONAL_FUND')
        self.assertEqual(timeline[2]['type'], 'EXPENSE')

        # 6. Settle fund
        settle_data = {
            "settlement_date": "2026-07-15",
            "returned_amount": "4500.00",
            "additional_amount_required": "0.00",
            "settlement_notes": "All settled, remainder returned",
            "settlement_payment_mode": "ACCOUNT"
        }
        res_settle = self.client.post(f'/api/funds/{fund_id}/settle/', settle_data, format='json')
        self.assertEqual(res_settle.status_code, status.HTTP_200_OK)
        self.assertEqual(res_settle.data['status'], 'SETTLED')
        self.assertEqual(float(res_settle.data['returned_amount']), 4500.00)

        # Verify settlement Transaction was created
        self.assertEqual(Transaction.objects.count(), 4)
        t_settle = Transaction.objects.order_by('-id').first()
        self.assertEqual(t_settle.transaction_type, 'FUND_MANAGEMENT_DEC')
        self.assertEqual(float(t_settle.amount), 4500.00)
        self.assertEqual(t_settle.related_fund_id, fund_id)

        # 7. Check reports again
        res_reports = self.client.get('/api/funds/reports/')
        summary = res_reports.data['summary']
        self.assertEqual(summary['active_count'], 0)
        self.assertEqual(summary['settled_count'], 1)

        # 8. Reopen fund and check that settlement transactions are deleted
        res_reopen = self.client.post(f'/api/funds/{fund_id}/reopen/')
        self.assertEqual(res_reopen.status_code, status.HTTP_200_OK)
        self.assertEqual(res_reopen.data['status'], 'ACTIVE')
        self.assertEqual(Transaction.objects.count(), 3) # initial + addition + expense (settlement deleted!)

        # 9. Delete the fund and verify all linked transactions are deleted
        res_delete = self.client.delete(f'/api/funds/{fund_id}/')
        self.assertEqual(res_delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Transaction.objects.count(), 0) # all transactions deleted!


