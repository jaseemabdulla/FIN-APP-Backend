from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TransactionViewSet, DebtViewSet, DailyReportView, MonthlyReportView, CategoryViewSet, EventViewSet,
    FundViewSet, FundAdditionViewSet, FundExpenseViewSet, ExportMonthlyCSVView, ExportPDFReportView, AppInitView,
    GlobalSearchView
)

router = DefaultRouter()
router.register(r'transactions', TransactionViewSet)
router.register(r'categories', CategoryViewSet)
router.register(r'debts', DebtViewSet)
router.register(r'events', EventViewSet)
router.register(r'funds', FundViewSet)
router.register(r'fund-additions', FundAdditionViewSet)
router.register(r'fund-expenses', FundExpenseViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('search/', GlobalSearchView.as_view(), name='global-search'),
    path('reports/daily/', DailyReportView.as_view(), name='daily-report'),
    path('reports/monthly/', MonthlyReportView.as_view(), name='monthly-report'),
    path('reports/export/', ExportMonthlyCSVView.as_view(), name='export-report'),
    path('reports/export-pdf/', ExportPDFReportView.as_view(), name='export-pdf-report'),
    path('init/', AppInitView.as_view(), name='app-init'),
]

