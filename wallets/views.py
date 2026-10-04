from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from common.errors import DomainError
from common.serializers import ERROR_RESPONSES, idempotency_key, uuid_value
from wallets import services
from wallets.models import TopUp
from wallets.queries import history
from wallets.serializers import (
    DebitRequestSerializer,
    HistorySerializer,
    LedgerSerializer,
    TopUpReceiptSerializer,
    TopUpRequestSerializer,
    TopUpStatusSerializer,
    WalletSerializer,
)

KEY = OpenApiParameter(
    "Idempotency-Key",
    OpenApiTypes.UUID,
    OpenApiParameter.HEADER,
    required=True,
    description="Retain this UUID when retrying. Scoped to wallet and operation.",
)


class WalletView(APIView):
    @extend_schema(
        operation_id="wallet_retrieve", responses={200: WalletSerializer, **ERROR_RESPONSES}
    )
    def get(self, request, user_id):
        wallet = services.get_wallet(uuid_value(user_id))
        return Response(WalletSerializer(wallet).data)

    @extend_schema(
        operation_id="wallet_create",
        request=None,
        responses={200: WalletSerializer, **ERROR_RESPONSES},
    )
    def put(self, request, user_id):
        if request.data:
            raise DomainError("invalid_request", "Wallet creation has no request body.")
        wallet = services.create_wallet(uuid_value(user_id))
        return Response(WalletSerializer(wallet).data)


class TopUpView(APIView):
    @extend_schema(
        request=TopUpRequestSerializer,
        parameters=[KEY],
        responses={202: TopUpReceiptSerializer, **ERROR_RESPONSES},
        description="Persist an intent. No credit until provider confirmation. Replays return the original receipt.",
    )
    def post(self, request, user_id):
        data = TopUpRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        top_up = services.create_top_up(
            uuid_value(user_id), idempotency_key(request), **data.validated_data
        )
        location = f"/v1/wallets/{top_up.wallet.user_id}/top-ups/{top_up.id}"
        return Response(
            TopUpReceiptSerializer(top_up).data, status=202, headers={"Location": location}
        )


class TopUpStatusView(APIView):
    @extend_schema(responses={200: TopUpStatusSerializer, **ERROR_RESPONSES})
    def get(self, request, user_id, top_up_id):
        wallet = services.get_wallet(uuid_value(user_id))
        top_up = (
            TopUp.objects.select_related("ledger_entry")
            .filter(wallet=wallet, pk=uuid_value(top_up_id))
            .first()
        )
        if top_up is None:
            raise DomainError("top_up_not_found", "Top-up does not exist for this wallet.", 404)
        return Response(TopUpStatusSerializer(top_up).data)


class DebitView(APIView):
    @extend_schema(
        request=DebitRequestSerializer,
        parameters=[KEY],
        responses={201: LedgerSerializer, **ERROR_RESPONSES},
    )
    def post(self, request, user_id):
        data = DebitRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        entry = services.debit(uuid_value(user_id), idempotency_key(request), **data.validated_data)
        return Response(LedgerSerializer(entry).data, status=201)


class HistoryView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int, description="1..100; default 50"),
            OpenApiParameter("cursor", str, description="Opaque cursor from the previous page"),
        ],
        responses={200: HistorySerializer, **ERROR_RESPONSES},
    )
    def get(self, request, user_id):
        wallet = services.get_wallet(uuid_value(user_id))
        result = history(
            wallet, request.query_params.get("limit", "50"), request.query_params.get("cursor")
        )
        return Response(HistorySerializer(result).data)
