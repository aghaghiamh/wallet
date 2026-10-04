from django.urls import path

from common.views import common_urls
from wallets.views import DebitView, HistoryView, TopUpStatusView, TopUpView, WalletView

urlpatterns = [
    path("v1/wallets/<str:user_id>", WalletView.as_view()),
    path("v1/wallets/<str:user_id>/top-ups", TopUpView.as_view()),
    path("v1/wallets/<str:user_id>/top-ups/<str:top_up_id>", TopUpStatusView.as_view()),
    path("v1/wallets/<str:user_id>/debits", DebitView.as_view()),
    path("v1/wallets/<str:user_id>/entries", HistoryView.as_view()),
    *common_urls(),
]
