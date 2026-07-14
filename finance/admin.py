from django.contrib import admin
from .models import Category, Event, Transaction, BalanceSnapshot, Debt, Fund, FundAddition, FundExpense

# Register your models here.
admin.site.register(Category)
admin.site.register(Event)
admin.site.register(Transaction)
admin.site.register(BalanceSnapshot)
admin.site.register(Debt)
admin.site.register(Fund)
admin.site.register(FundAddition)
admin.site.register(FundExpense)

