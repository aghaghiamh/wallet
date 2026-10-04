from django.urls import path

from common.views import common_urls
from provider.views import TransferView

urlpatterns = [
    path("v1/transfers/<str:transfer_id>", TransferView.as_view()),
    *common_urls(),
]
