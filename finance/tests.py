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
        self.ledger = Ledger.objects.create(name="Alice", profile=self.user.profile)

    def test_fund_creation_and_automatic_status(self):
        # 1. Create a fund
        create_data = {
            "title": "Tech Fest 2026",
            "ledger": self.ledger.id,
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
        addition_id = res_add.data['id']

        # Verify status is still ACTIVE
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.data['status'], 'ACTIVE')

        # 3. Add expense of exactly 6500.00 (Total received = 6500, expenses = 6500)
        expense_data = {
            "fund": fund_id,
            "title": "Purchase routers",
            "amount": "6500.00",
            "date": "2026-07-12",
            "description": "Router buy",
            "payment_mode": "ACCOUNT"
        }
        res_exp = self.client.post('/api/fund-expenses/', expense_data, format='json')
        self.assertEqual(res_exp.status_code, status.HTTP_201_CREATED)
        expense_id = res_exp.data['id']

        # Status must automatically become CLOSED
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.data['status'], 'CLOSED')

        # 4. Add another expense of 1000.00 (Total received = 6500, expenses = 7500)
        expense2_data = {
            "fund": fund_id,
            "title": "Cables",
            "amount": "1000.00",
            "date": "2026-07-13",
            "description": "Cables buy",
            "payment_mode": "ACCOUNT"
        }
        res_exp2 = self.client.post('/api/fund-expenses/', expense2_data, format='json')
        self.assertEqual(res_exp2.status_code, status.HTTP_201_CREATED)
        expense2_id = res_exp2.data['id']

        # Status must automatically become ACTIVE
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.data['status'], 'ACTIVE')

        # 5. Add additional fund of 1000.00 (Total received = 7500, expenses = 7500)
        addition2_data = {
            "fund": fund_id,
            "amount": "1000.00",
            "date": "2026-07-14",
            "notes": "Third installment",
            "payment_mode": "ACCOUNT"
        }
        res_add2 = self.client.post('/api/fund-additions/', addition2_data, format='json')
        self.assertEqual(res_add2.status_code, status.HTTP_201_CREATED)

        # Status must automatically become CLOSED
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.data['status'], 'CLOSED')

        # 6. Edit addition amount from 1500 to 2000 (Total received = 8000, expenses = 7500)
        res_update_add = self.client.put(f'/api/fund-additions/{addition_id}/', {
            "fund": fund_id,
            "amount": "2000.00",
            "date": "2026-07-11",
            "notes": "Second installment edited",
            "payment_mode": "ACCOUNT"
        }, format='json')
        self.assertEqual(res_update_add.status_code, status.HTTP_200_OK)

        # Status must automatically become ACTIVE
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.data['status'], 'ACTIVE')

        # 7. Edit expense amount from 6500 to 7000 (Total received = 8000, expenses = 8000)
        res_update_exp = self.client.put(f'/api/fund-expenses/{expense_id}/', {
            "fund": fund_id,
            "title": "Purchase routers edited",
            "amount": "7000.00",
            "date": "2026-07-12",
            "description": "Router buy edited",
            "payment_mode": "ACCOUNT"
        }, format='json')
        self.assertEqual(res_update_exp.status_code, status.HTTP_200_OK)

        # Status must automatically become CLOSED
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.data['status'], 'CLOSED')

        # 8. Edit the initial fund entry: set amount to 0 (Total received = 3000, expenses = 8000)
        res_update_fund = self.client.put(f'/api/funds/{fund_id}/', {
            "title": "Tech Fest 2026",
            "ledger": self.ledger.id,
            "initial_amount": "0.00",
            "received_date": "2026-07-10",
            "notes": "Sponsor money edited to 0",
            "payment_mode": "ACCOUNT"
        }, format='json')
        self.assertEqual(res_update_fund.status_code, status.HTTP_200_OK)

        # Status must automatically become ACTIVE and initial transaction must be deleted
        res_detail = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_detail.data['status'], 'ACTIVE')
        self.assertEqual(float(res_detail.data['initial_amount']), 0.00)

        # 9. Delete addition
        res_del_add = self.client.delete(f'/api/fund-additions/{addition_id}/')
        self.assertEqual(res_del_add.status_code, status.HTTP_204_NO_CONTENT)

        # 10. Delete expense
        res_del_exp = self.client.delete(f'/api/fund-expenses/{expense_id}/')
        self.assertEqual(res_del_exp.status_code, status.HTTP_204_NO_CONTENT)

        # 11. Delete Fund
        res_delete_fund = self.client.delete(f'/api/funds/{fund_id}/')
        self.assertEqual(res_delete_fund.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_fund_to_debt_integration(self):
        # Scenario 1 - Balanced initially (amount = 0)
        # Create a fund with 0 initial amount
        create_data_zero = {
            "title": "Zero Fund",
            "ledger": self.ledger.id,
            "initial_amount": "0.00",
            "received_date": "2026-07-10",
            "notes": "Testing Zero Fund",
            "payment_mode": "ACCOUNT"
        }
        res_zero = self.client.post('/api/funds/', create_data_zero, format='json')
        self.assertEqual(res_zero.status_code, status.HTTP_201_CREATED)
        fund_zero_id = res_zero.data['id']
        
        # Expected: No outstanding Fund-related debt (debt counts = 0)
        self.assertEqual(Debt.objects.filter(related_fund_id=fund_zero_id).count(), 0)

        # Scenario 2 - Excess Fund
        # Create a fund with 10,000 initial amount
        create_data = {
            "title": "Project X",
            "ledger": self.ledger.id,
            "initial_amount": "10000.00",
            "received_date": "2026-07-10",
            "notes": "Tech Sponsor",
            "payment_mode": "ACCOUNT"
        }
        response = self.client.post('/api/funds/', create_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        fund_id = response.data['id']
        
        # Add expense of 7000 (Total credit = 10000, Total expense = 7000)
        exp1_data = {
            "fund": fund_id,
            "title": "Expense A",
            "amount": "7000.00",
            "date": "2026-07-11",
            "description": "Laptops",
            "payment_mode": "ACCOUNT"
        }
        res_exp1 = self.client.post('/api/fund-expenses/', exp1_data, format='json')
        self.assertEqual(res_exp1.status_code, status.HTTP_201_CREATED)
        exp1_id = res_exp1.data['id']
        
        # Expected: ₹3,000 Taken/Payable debt against Alice
        debts = Debt.objects.filter(related_fund_id=fund_id)
        self.assertEqual(debts.count(), 1)
        debt = debts.first()
        self.assertEqual(float(debt.amount), 3000.00)
        self.assertEqual(debt.debt_type, 'TAKEN')
        self.assertEqual(debt.ledger, self.ledger)
        self.assertEqual(debt.is_cleared, False)

        # Scenario 4 - Update: change expense 7,000 -> 8,000
        # Expected: Fund debt ₹3,000 -> ₹2,000, no duplicates
        res_update_exp = self.client.put(f'/api/fund-expenses/{exp1_id}/', {
            "fund": fund_id,
            "title": "Expense A edited",
            "amount": "8000.00",
            "date": "2026-07-11",
            "description": "Laptops upgrade",
            "payment_mode": "ACCOUNT"
        }, format='json')
        self.assertEqual(res_update_exp.status_code, status.HTTP_200_OK)
        
        debts = Debt.objects.filter(related_fund_id=fund_id)
        self.assertEqual(debts.count(), 1)
        debt = debts.first()
        self.assertEqual(float(debt.amount), 2000.00)
        self.assertEqual(debt.debt_type, 'TAKEN')

        # Scenario 3 - Excess Expense
        # Add another expense: 4000 (Total credit = 10,000, Total expense = 12,000)
        exp2_data = {
            "fund": fund_id,
            "title": "Expense B",
            "amount": "4000.00",
            "date": "2026-07-12",
            "description": "Networking",
            "payment_mode": "ACCOUNT"
        }
        res_exp2 = self.client.post('/api/fund-expenses/', exp2_data, format='json')
        self.assertEqual(res_exp2.status_code, status.HTTP_201_CREATED)
        exp2_id = res_exp2.data['id']
        
        # Expected: ₹2,000 Given/Lent debt against Alice
        debts = Debt.objects.filter(related_fund_id=fund_id)
        self.assertEqual(debts.count(), 1)
        debt = debts.first()
        self.assertEqual(float(debt.amount), 2000.00)
        self.assertEqual(debt.debt_type, 'GIVEN')

        # Scenario 5 - Delete: Delete expense B (Total credit = 10,000, Total expense = 8,000)
        res_del_exp2 = self.client.delete(f'/api/fund-expenses/{exp2_id}/')
        self.assertEqual(res_del_exp2.status_code, status.HTTP_204_NO_CONTENT)
        
        # Expected: Fund debt returns to ₹2,000 Taken/Payable
        debts = Debt.objects.filter(related_fund_id=fund_id)
        self.assertEqual(debts.count(), 1)
        debt = debts.first()
        self.assertEqual(float(debt.amount), 2000.00)
        self.assertEqual(debt.debt_type, 'TAKEN')

        # Scenario 6 - Close Again: Make credit == expense
        # Add expense of 2000 (Total credit = 10,000, Total expense = 10,000)
        exp3_data = {
            "fund": fund_id,
            "title": "Expense C",
            "amount": "2000.00",
            "date": "2026-07-13",
            "description": "Catering",
            "payment_mode": "ACCOUNT"
        }
        res_exp3 = self.client.post('/api/fund-expenses/', exp3_data, format='json')
        self.assertEqual(res_exp3.status_code, status.HTTP_201_CREATED)
        
        # Expected: Debt has 0 amount, is_cleared = True, and Fund is CLOSED
        debts = Debt.objects.filter(related_fund_id=fund_id)
        self.assertEqual(debts.count(), 1)
        debt = debts.first()
        self.assertEqual(float(debt.amount), 0.00)
        self.assertEqual(debt.is_cleared, True)
        
        res_fund = self.client.get(f'/api/funds/{fund_id}/')
        self.assertEqual(res_fund.data['status'], 'CLOSED')

        # Scenario 11/12/13 - Deletion Cleanup
        # Create an unrelated transaction and debt to verify they remain untouched
        unrelated_debt = Debt.objects.create(
            profile=self.user.profile,
            person_name="Bob",
            amount=500.00,
            debt_type="GIVEN",
            is_cleared=False
        )
        
        # Now delete the fund
        res_delete_fund = self.client.delete(f'/api/funds/{fund_id}/')
        self.assertEqual(res_delete_fund.status_code, status.HTTP_204_NO_CONTENT)
        
        # Verify that all Fund-related debts and transactions are deleted
        self.assertEqual(Debt.objects.filter(related_fund_id=fund_id).count(), 0)
        self.assertEqual(Transaction.objects.filter(related_fund_id=fund_id).count(), 0)
        
        # Verify that unrelated debt remains untouched
        self.assertTrue(Debt.objects.filter(id=unrelated_debt.id).exists())


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


class HomeFormIntegrationTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='homeuser', password='password123', email='home@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Loan / Debt", profile=self.user.profile)
        self.fund_category = Category.objects.create(name="Fund Management", profile=self.user.profile)
        self.ledger = Ledger.objects.create(name="Ledger A", profile=self.user.profile)

    def test_home_form_flows(self):
        # 1. Create Debt Taken from Home
        response = self.client.post('/api/transactions/', {
            "date": "2026-08-12",
            "amount": "1000.00",
            "payment_mode": "CASH",
            "transaction_type": "DEBT_TAKEN",
            "category": self.category.id,
            "description": "Ledger A",
            "ledger": self.ledger.id
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        debt_taken_txn_id = response.data['id']
        
        # Verify Debt record was created
        debt_taken = Debt.objects.get(transaction_id=debt_taken_txn_id)
        self.assertEqual(float(debt_taken.amount), 1000.00)
        self.assertEqual(debt_taken.debt_type, 'TAKEN')
        self.assertEqual(debt_taken.is_cleared, False)

        # 2. Create Debt Given from Home
        response = self.client.post('/api/transactions/', {
            "date": "2026-08-12",
            "amount": "500.00",
            "payment_mode": "CASH",
            "transaction_type": "DEBT_GIVEN",
            "category": self.category.id,
            "description": "Ledger A",
            "ledger": self.ledger.id
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        debt_given_txn_id = response.data['id']
        
        # Verify Debt record was created
        debt_given = Debt.objects.get(transaction_id=debt_given_txn_id)
        self.assertEqual(float(debt_given.amount), 500.00)
        self.assertEqual(debt_given.debt_type, 'GIVEN')
        self.assertEqual(debt_given.is_cleared, False)

        # 3. Return a Debt Taken partially (400.00 return on 1000.00 debt)
        response = self.client.post('/api/transactions/', {
            "date": "2026-08-12",
            "amount": "400.00",
            "payment_mode": "CASH",
            "transaction_type": "DEBT_TAKEN_RETURN",
            "category": self.category.id,
            "description": "Repayment: Ledger A",
            "related_debt": debt_taken.id,
            "ledger": self.ledger.id
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify remaining amount is 600.00 and debt is not cleared
        debt_taken.refresh_from_db()
        self.assertEqual(debt_taken.is_cleared, False)
        # Using serializer to get remaining_amount just like frontend does
        from finance.serializers import DebtSerializer
        serializer = DebtSerializer(debt_taken)
        self.assertEqual(float(serializer.data['remaining_amount']), 600.00)

        # 4. Return the remaining Debt Taken (600.00)
        response = self.client.post('/api/transactions/', {
            "date": "2026-08-12",
            "amount": "600.00",
            "payment_mode": "CASH",
            "transaction_type": "DEBT_TAKEN_RETURN",
            "category": self.category.id,
            "description": "Repayment: Ledger A",
            "related_debt": debt_taken.id,
            "ledger": self.ledger.id
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify debt is now cleared
        debt_taken.refresh_from_db()
        self.assertEqual(debt_taken.is_cleared, True)
        serializer = DebtSerializer(debt_taken)
        self.assertEqual(float(serializer.data['remaining_amount']), 0.00)

        # 5. Return a Debt Given partially (200.00 return on 500.00 debt)
        response = self.client.post('/api/transactions/', {
            "date": "2026-08-12",
            "amount": "200.00",
            "payment_mode": "CASH",
            "transaction_type": "DEBT_GIVEN_RETURN",
            "category": self.category.id,
            "description": "Repayment: Ledger A",
            "related_debt": debt_given.id,
            "ledger": self.ledger.id
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify remaining amount is 300.00 and debt is not cleared
        debt_given.refresh_from_db()
        self.assertEqual(debt_given.is_cleared, False)
        serializer = DebtSerializer(debt_given)
        self.assertEqual(float(serializer.data['remaining_amount']), 300.00)

        # 6. Return the remaining Debt Given (300.00)
        response = self.client.post('/api/transactions/', {
            "date": "2026-08-12",
            "amount": "300.00",
            "payment_mode": "CASH",
            "transaction_type": "DEBT_GIVEN_RETURN",
            "category": self.category.id,
            "description": "Repayment: Ledger A",
            "related_debt": debt_given.id,
            "ledger": self.ledger.id
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify debt is now cleared
        debt_given.refresh_from_db()
        self.assertEqual(debt_given.is_cleared, True)
        serializer = DebtSerializer(debt_given)
        self.assertEqual(float(serializer.data['remaining_amount']), 0.00)

        # 7. Create an active Fund first to credit/expense into
        from finance.models import Fund
        fund = Fund.objects.create(
            profile=self.user.profile,
            title="Home Fund",
            ledger=self.ledger,
            initial_amount=1000.00,
            received_date=date.today(),
            payment_mode="ACCOUNT"
        )
        self.assertEqual(fund.status, 'ACTIVE')
        
        # Verify Fund-related debt initially: difference = 1000 - 0 = 1000 (TAKEN debt created)
        fund_debt = Debt.objects.get(related_fund=fund)
        self.assertEqual(float(fund_debt.amount), 1000.00)
        self.assertEqual(fund_debt.debt_type, 'TAKEN')

        # 8. Add a Fund credit from Home (corresponds to createFundAddition)
        response = self.client.post('/api/fund-additions/', {
            "fund": fund.id,
            "amount": "500.00",
            "date": str(date.today()),
            "notes": "Home credit addition",
            "payment_mode": "ACCOUNT"
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify fund balance: received = 1000 + 500 = 1500, spent = 0, balance = 1500
        fund.refresh_from_db()
        self.assertEqual(fund.status, 'ACTIVE')
        from finance.serializers import FundSerializer
        fund_serializer = FundSerializer(fund)
        self.assertEqual(float(fund_serializer.data['remaining_balance']), 1500.00)

        # 9. Add a Fund expense from Home (corresponds to createFundExpense)
        response = self.client.post('/api/fund-expenses/', {
            "fund": fund.id,
            "title": "Home expense",
            "amount": "300.00",
            "date": str(date.today()),
            "description": "Home expense details",
            "payment_mode": "ACCOUNT"
        }, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify fund balance: received = 1500, spent = 300, balance = 1200
        fund.refresh_from_db()
        self.assertEqual(fund.status, 'ACTIVE')
        fund_serializer = FundSerializer(fund)
        self.assertEqual(float(fund_serializer.data['remaining_balance']), 1200.00)
        
        # 10. Verify Fund-related debt is recalculated correctly: difference = 1500 - 300 = 1200
        fund_debt.refresh_from_db()
        self.assertEqual(float(fund_debt.amount), 1200.00)
        self.assertEqual(fund_debt.debt_type, 'TAKEN')
        self.assertEqual(fund_debt.is_cleared, False)

        # 11. Add large expense to close the fund
        response = self.client.post('/api/fund-expenses/', {
            "fund": fund.id,
            "title": "Large expense",
            "amount": "1200.00",
            "date": str(date.today()),
            "description": "Close fund",
            "payment_mode": "ACCOUNT"
        }, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Fund status should now be CLOSED since received == spent (1500 == 1500)
        fund.refresh_from_db()
        self.assertEqual(fund.status, 'CLOSED')
        
        # Recalculated difference: 1500 - 1500 = 0. Fund-debt amount should be 0 and is_cleared should be True
        fund_debt.refresh_from_db()
        self.assertEqual(float(fund_debt.amount), 0.00)
        self.assertEqual(fund_debt.is_cleared, True)


class InvestmentTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='testuser', password='password123', email='test@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Investment", profile=self.user.profile)

    def test_investment_flow_and_validations(self):
        # 1. Create Investment Profile
        create_data = {
            "name": "Gold Investment Test",
            "investment_type": "GOLD",
            "description": "Buying gold bars",
            "date": "2026-08-20"
        }
        response = self.client.post('/api/investments/', create_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invest_id = response.data['id']
        self.assertEqual(response.data['status'], 'CLOSED') # Starts at 0 balance, so CLOSED/SETTLED
        self.assertEqual(float(response.data['remaining_balance']), 0.00)

        # 2. Add Capital (INVESTMENT transaction)
        txn1_data = {
            "amount": "5000.00",
            "payment_mode": "ACCOUNT",
            "transaction_type": "INVESTMENT",
            "category": self.category.id,
            "description": "Initial gold purchase",
            "date": "2026-08-20",
            "related_investment": invest_id
        }
        res_txn1 = self.client.post('/api/transactions/', txn1_data, format='json')
        self.assertEqual(res_txn1.status_code, status.HTTP_201_CREATED)

        # Verify investment balance updates and status is ACTIVE
        res_detail = self.client.get(f'/api/investments/{invest_id}/')
        self.assertEqual(float(res_detail.data['total_invested']), 5000.00)
        self.assertEqual(float(res_detail.data['remaining_balance']), 5000.00)
        self.assertEqual(res_detail.data['status'], 'ACTIVE')

        # 3. Add more capital
        txn2_data = {
            "amount": "3000.00",
            "payment_mode": "ACCOUNT",
            "transaction_type": "INVESTMENT",
            "category": self.category.id,
            "description": "Additional gold purchase",
            "date": "2026-08-21",
            "related_investment": invest_id
        }
        res_txn2 = self.client.post('/api/transactions/', txn2_data, format='json')
        self.assertEqual(res_txn2.status_code, status.HTTP_201_CREATED)

        # Verify balance is 8000
        res_detail = self.client.get(f'/api/investments/{invest_id}/')
        self.assertEqual(float(res_detail.data['total_invested']), 8000.00)
        self.assertEqual(float(res_detail.data['remaining_balance']), 8000.00)

        # 4. Withdraw Capital (INVESTMENT_RETURN transaction)
        txn3_data = {
            "amount": "2000.00",
            "payment_mode": "ACCOUNT",
            "transaction_type": "INVESTMENT_RETURN",
            "category": self.category.id,
            "description": "Selling some gold",
            "date": "2026-08-22",
            "related_investment": invest_id
        }
        res_txn3 = self.client.post('/api/transactions/', txn3_data, format='json')
        self.assertEqual(res_txn3.status_code, status.HTTP_201_CREATED)

        # Verify balance is 6000
        res_detail = self.client.get(f'/api/investments/{invest_id}/')
        self.assertEqual(float(res_detail.data['total_withdrawn']), 2000.00)
        self.assertEqual(float(res_detail.data['remaining_balance']), 6000.00)
        self.assertEqual(res_detail.data['status'], 'ACTIVE')

        # 5. Over-withdrawal Validation (try to withdraw 7000, remaining is 6000)
        txn4_data = {
            "amount": "7000.00",
            "payment_mode": "ACCOUNT",
            "transaction_type": "INVESTMENT_RETURN",
            "category": self.category.id,
            "description": "Over-withdrawing",
            "date": "2026-08-23",
            "related_investment": invest_id
        }
        res_txn4 = self.client.post('/api/transactions/', txn4_data, format='json')
        self.assertEqual(res_txn4.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("amount", res_txn4.data)

        # 6. Full Withdrawal (withdraw remaining 6000)
        txn5_data = {
            "amount": "6000.00",
            "payment_mode": "ACCOUNT",
            "transaction_type": "INVESTMENT_RETURN",
            "category": self.category.id,
            "description": "Liquidating gold portfolio",
            "date": "2026-08-24",
            "related_investment": invest_id
        }
        res_txn5 = self.client.post('/api/transactions/', txn5_data, format='json')
        self.assertEqual(res_txn5.status_code, status.HTTP_201_CREATED)

        # Verify balance is 0 and status is CLOSED
        res_detail = self.client.get(f'/api/investments/{invest_id}/')
        self.assertEqual(float(res_detail.data['total_withdrawn']), 8000.00)
        self.assertEqual(float(res_detail.data['remaining_balance']), 0.00)
        self.assertEqual(res_detail.data['status'], 'CLOSED')


class PersonalBalanceAndFundSeparationTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='testuser_sep', password='password123', email='sep@example.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.category = Category.objects.create(name="Miscellaneous", profile=self.user.profile)
        self.ledger = Ledger.objects.create(name="Sponsor Org", profile=self.user.profile)

    def test_personal_balance_isolated_from_fund_management(self):
        today_str = str(date.today())

        # 1. Create personal income: ₹10,000 ACCOUNT, ₹2,000 CASH
        Transaction.objects.create(
            profile=self.user.profile,
            date=date.today(),
            amount=10000.00,
            payment_mode='ACCOUNT',
            transaction_type='INCOME',
            category=self.category,
            description='Salary'
        )
        Transaction.objects.create(
            profile=self.user.profile,
            date=date.today(),
            amount=2000.00,
            payment_mode='CASH',
            transaction_type='INCOME',
            category=self.category,
            description='Cash Bonus'
        )

        res = self.client.get(f'/api/reports/daily/?date={today_str}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(float(res.data['closing_balance']['account']), 10000.00)
        self.assertEqual(float(res.data['closing_balance']['cash']), 2000.00)
        self.assertEqual(float(res.data['closing_balance']['total']), 12000.00)
        self.assertEqual(float(res.data['fund_summary']['remaining_balance']), 0.00)

        # 2. Create a Fund (receipt of ₹50,000 in ACCOUNT mode)
        fund_res = self.client.post('/api/funds/', {
            "title": "Annual Event Fund",
            "ledger": self.ledger.id,
            "initial_amount": "50000.00",
            "received_date": today_str,
            "notes": "Sponsorship received",
            "payment_mode": "ACCOUNT"
        }, format='json')
        self.assertEqual(fund_res.status_code, status.HTTP_201_CREATED)
        fund_id = fund_res.data['id']

        # Verify Personal balances did NOT change, but Fund Summary updated
        res = self.client.get(f'/api/reports/daily/?date={today_str}')
        self.assertEqual(float(res.data['closing_balance']['account']), 10000.00)
        self.assertEqual(float(res.data['closing_balance']['cash']), 2000.00)
        self.assertEqual(float(res.data['closing_balance']['total']), 12000.00)
        self.assertEqual(float(res.data['fund_summary']['remaining_balance']), 50000.00)
        self.assertEqual(float(res.data['fund_summary']['total_received']), 50000.00)

        # 3. Add Fund Addition of ₹10,000 in CASH mode
        add_res = self.client.post('/api/fund-additions/', {
            "fund": fund_id,
            "amount": "10000.00",
            "date": today_str,
            "notes": "Additional cash contribution",
            "payment_mode": "CASH"
        }, format='json')
        self.assertEqual(add_res.status_code, status.HTTP_201_CREATED)
        addition_id = add_res.data['id']

        # Verify Personal balances still unchanged, Fund Cash & Total updated
        res = self.client.get(f'/api/reports/daily/?date={today_str}')
        self.assertEqual(float(res.data['closing_balance']['account']), 10000.00)
        self.assertEqual(float(res.data['closing_balance']['cash']), 2000.00)
        self.assertEqual(float(res.data['fund_summary']['remaining_balance']), 60000.00)
        self.assertEqual(float(res.data['fund_summary']['cash_balance']), 10000.00)
        self.assertEqual(float(res.data['fund_summary']['account_balance']), 50000.00)

        # 4. Record Fund Expense of ₹15,000 in ACCOUNT mode
        exp_res = self.client.post('/api/fund-expenses/', {
            "fund": fund_id,
            "title": "Venue Booking",
            "amount": "15000.00",
            "date": today_str,
            "description": "Hall deposit",
            "payment_mode": "ACCOUNT"
        }, format='json')
        self.assertEqual(exp_res.status_code, status.HTTP_201_CREATED)

        res = self.client.get(f'/api/reports/daily/?date={today_str}')
        self.assertEqual(float(res.data['closing_balance']['account']), 10000.00)
        self.assertEqual(float(res.data['closing_balance']['cash']), 2000.00)
        self.assertEqual(float(res.data['fund_summary']['total_spent']), 15000.00)
        self.assertEqual(float(res.data['fund_summary']['remaining_balance']), 45000.00)

        # 5. Delete Fund Addition
        del_add_res = self.client.delete(f'/api/fund-additions/{addition_id}/')
        self.assertEqual(del_add_res.status_code, status.HTTP_204_NO_CONTENT)

        res = self.client.get(f'/api/reports/daily/?date={today_str}')
        self.assertEqual(float(res.data['closing_balance']['account']), 10000.00)
        self.assertEqual(float(res.data['closing_balance']['cash']), 2000.00)
        self.assertEqual(float(res.data['fund_summary']['remaining_balance']), 35000.00)

        # 6. Add Personal Expense: ₹3,000 ACCOUNT
        Transaction.objects.create(
            profile=self.user.profile,
            date=date.today(),
            amount=3000.00,
            payment_mode='ACCOUNT',
            transaction_type='EXPENSE',
            category=self.category,
            description='Groceries'
        )

        res = self.client.get(f'/api/reports/daily/?date={today_str}')
        self.assertEqual(float(res.data['closing_balance']['account']), 7000.00)
        self.assertEqual(float(res.data['closing_balance']['cash']), 2000.00)
        self.assertEqual(float(res.data['closing_balance']['total']), 9000.00)
        self.assertEqual(float(res.data['fund_summary']['remaining_balance']), 35000.00)






