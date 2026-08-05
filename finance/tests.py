from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.db.models import Sum
from finance.models import Debt, Transaction, Category, Ledger
from datetime import date, timedelta
from django.contrib.auth import get_user_model

class DebtSettlePersonTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='testuser', password='password123', email='test@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Loan / Debt", profile=self.user.profile)
        
        # Debt 1: $100, TAKEN, oldest (2 days ago)
        self.t1 = Transaction.objects.create(
            profile=self.user.profile,
            date=date.today() - timedelta(days=2),
            amount=100.00,
            payment_mode='CASH',
            transaction_type='DEBT_TAKEN',
            category=self.category,
            description="Test Person"
        )
        
        # Debt 2: $50, TAKEN, middle (1 day ago)
        self.t2 = Transaction.objects.create(
            profile=self.user.profile,
            date=date.today() - timedelta(days=1),
            amount=50.00,
            payment_mode='CASH',
            transaction_type='DEBT_TAKEN',
            category=self.category,
            description="Test Person"
        )
        
        # Debt 3: $30, TAKEN, newest (today)
        self.t3 = Transaction.objects.create(
            profile=self.user.profile,
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
        self.user = get_user_model().objects.create_user(username='testuser', password='password123', email='test@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Tech Supplies", profile=self.user.profile)

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


class GlobalSearchTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='testuser', password='password123', email='test@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category_food = Category.objects.create(name="Food", profile=self.user.profile)
        self.category_loan = Category.objects.create(name="Loan / Debt", profile=self.user.profile)

        # 1. Income transaction
        self.t1 = Transaction.objects.create(
            profile=self.user.profile,
            date=date.today(),
            amount=5000.00,
            payment_mode='ACCOUNT',
            transaction_type='INCOME',
            description="Salary payment from Acme Corp"
        )

        # 2. Expense transaction
        self.t2 = Transaction.objects.create(
            profile=self.user.profile,
            date=date.today(),
            amount=15.50,
            payment_mode='CASH',
            transaction_type='EXPENSE',
            category=self.category_food,
            description="Sushi lunch with friends"
        )

        # 3. Debt transaction
        self.t3 = Transaction.objects.create(
            profile=self.user.profile,
            date=date.today(),
            amount=100.00,
            payment_mode='CASH',
            transaction_type='DEBT_TAKEN',
            category=self.category_loan,
            description="Borrow from John Doe"
        )

    def test_search_by_description(self):
        # Search for "Sushi"
        response = self.client.get('/api/search/?q=Sushi')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['description'], "Sushi lunch with friends")
        self.assertEqual(response.data[0]['type'], "EXPENSE")

    def test_search_by_person_name(self):
        # Search for "John Doe" (who is linked to Debt/Transaction)
        response = self.client.get('/api/search/?q=John')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['description'], "Borrow from John Doe")

    def test_search_by_category(self):
        # Search for "Food" category
        response = self.client.get('/api/search/?q=Food')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['category'], "Food")

    def test_search_empty_query(self):
        # Empty query should return empty list
        response = self.client.get('/api/search/?q=')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])


class LedgerTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='testuser', password='password123', email='test@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Loan / Debt", profile=self.user.profile)

    def test_ledger_crud(self):
        # 1. Create Ledger
        create_data = {
            "name": "Jane Smith",
            "phone": "+1234567890",
            "email": "jane@example.com"
        }
        res_create = self.client.post('/api/ledgers/', create_data, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        ledger_id = res_create.data['id']
        self.assertEqual(res_create.data['name'], "Jane Smith")

        # 2. Read Ledgers
        res_list = self.client.get('/api/ledgers/')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_list.data), 1)

        # 3. Update Ledger
        update_data = {
            "name": "Jane Doe",
            "phone": "+1987654321",
            "email": "jane.doe@example.com"
        }
        res_update = self.client.put(f'/api/ledgers/{ledger_id}/', update_data, format='json')
        self.assertEqual(res_update.status_code, status.HTTP_200_OK)
        self.assertEqual(res_update.data['name'], "Jane Doe")

        # 4. Delete Ledger (empty ledger, should succeed)
        res_delete = self.client.delete(f'/api/ledgers/{ledger_id}/')
        self.assertEqual(res_delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Ledger.objects.count(), 0)

    def test_debt_linking_and_compatibility(self):
        ledger = Ledger.objects.create(name="Bob Johnson", phone="555-0199", profile=self.user.profile)
        
        # Create Debt linked to ledger
        debt_data = {
            "ledger": ledger.id,
            "amount": "250.00",
            "debt_type": "TAKEN",
            "date": "2026-07-24",
            "payment_mode": "CASH",
            "description": "Borrow for tools"
        }
        response = self.client.post('/api/debts/', debt_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['ledger'], ledger.id)
        
        # Verify compatibility: person_name must be populated with ledger name
        self.assertEqual(response.data['person_name'], "Bob Johnson")
        
        debt = Debt.objects.get(id=response.data['id'])
        self.assertEqual(debt.person_name, "Bob Johnson")
        self.assertEqual(debt.ledger, ledger)

    def test_ledger_deletion_protection(self):
        ledger = Ledger.objects.create(name="Charlie Brown", profile=self.user.profile)
        Debt.objects.create(
            profile=self.user.profile,
            ledger=ledger,
            person_name=ledger.name,
            amount=50.00,
            debt_type="GIVEN",
            date=date.today(),
            payment_mode="CASH"
        )
        
        # Attempting to delete ledger should fail with validation error (400 Bad Request)
        response = self.client.delete(f'/api/ledgers/{ledger.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot delete ledger", response.data['detail'])
        
        # Verify ledger still exists
        self.assertTrue(Ledger.objects.filter(id=ledger.id).exists())

    def test_global_search_by_ledger_name(self):
        ledger = Ledger.objects.create(name="Unique Ledger Person Name", profile=self.user.profile)
        Debt.objects.create(
            profile=self.user.profile,
            ledger=ledger,
            amount=10.00,
            debt_type="TAKEN",
            date=date.today(),
            payment_mode="CASH"
        )
        
        response = self.client.get('/api/search/?q=Unique')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data) > 0)
        self.assertIn("Unique Ledger Person Name", response.data[0]['description'])

    def test_transaction_ledger_linking_and_sync(self):
        ledger1 = Ledger.objects.create(name="Ledger One", profile=self.user.profile)
        ledger2 = Ledger.objects.create(name="Ledger Two", profile=self.user.profile)
        
        # 1. Create a transaction with type DEBT_TAKEN and a ledger
        tx_data = {
            "date": "2026-08-01",
            "amount": "100.00",
            "payment_mode": "CASH",
            "transaction_type": "DEBT_TAKEN",
            "category": self.category.id,
            "description": "Borrow cash",
            "ledger": ledger1.id
        }
        response = self.client.post('/api/transactions/', tx_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['ledger'], ledger1.id)
        
        # Verify Debt was automatically created and linked to ledger1
        debt = Debt.objects.get(transaction_id=response.data['id'])
        self.assertEqual(debt.ledger, ledger1)
        self.assertEqual(debt.person_name, "Ledger One")
        self.assertEqual(float(debt.amount), 100.00)
        
        # 2. Update the transaction's ledger to ledger2 and change some values
        update_data = {
            "date": "2026-08-02",
            "amount": "150.00",
            "payment_mode": "ACCOUNT",
            "transaction_type": "DEBT_TAKEN",
            "category": self.category.id,
            "description": "Borrow cash edited",
            "ledger": ledger2.id
        }
        res_update = self.client.put(f"/api/transactions/{response.data['id']}/", update_data, format='json')
        self.assertEqual(res_update.status_code, status.HTTP_200_OK)
        
        # Verify the linked Debt was updated in sync
        debt.refresh_from_db()
        self.assertEqual(debt.ledger, ledger2)
        self.assertEqual(debt.person_name, "Ledger Two")
        self.assertEqual(float(debt.amount), 150.00)
        self.assertEqual(str(debt.date), "2026-08-02")
        self.assertEqual(debt.payment_mode, "ACCOUNT")


class ReportsTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='reportuser', password='password123', email='report@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Miscellaneous", profile=self.user.profile)

    def test_daily_report_with_profile(self):
        # Create a transaction for our profile
        Transaction.objects.create(
            profile=self.user.profile,
            date=date.today(),
            amount=50.00,
            payment_mode='CASH',
            transaction_type='INCOME',
            category=self.category,
            description="Profile income"
        )
        
        # Create a transaction for other user's profile
        other_user = get_user_model().objects.create_user(username='otheruser', password='password123', email='other@example.com')
        Transaction.objects.create(
            profile=other_user.profile,
            date=date.today(),
            amount=1000.00,
            payment_mode='CASH',
            transaction_type='INCOME',
            category=self.category,
            description="Other user income"
        )

        response = self.client.get(f'/api/reports/daily/?date={date.today()}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify only the profile's snapshot is returned as closing balance (which was recalculated dynamically)
        self.assertEqual(float(response.data['closing_balance']['cash']), 50.00)
        self.assertEqual(float(response.data['closing_balance']['account']), 0.00)
        self.assertEqual(float(response.data['closing_balance']['total']), 50.00)
        
        # Verify that only the profile's transactions are returned
        self.assertEqual(len(response.data['transactions']), 1)

    def test_monthly_report_with_profile(self):
        from finance.models import BalanceSnapshot
        # Create snapshot
        BalanceSnapshot.objects.create(
            profile=self.user.profile,
            date=date.today(),
            cash_in_hand=150.00,
            cash_in_account=250.00
        )
        
        response = self.client.get(f'/api/reports/monthly/?year={date.today().year}&month={date.today().month}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data['remaining_balance']), 400.00)



