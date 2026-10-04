from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from common.errors import DomainError
from common.serializers import ERROR_RESPONSES, uuid_value
from provider.models import Transfer
from provider.serializers import TransferRequestSerializer, TransferSerializer
from provider.services import create_transfer


class TransferView(APIView):
    @extend_schema(responses={200: TransferSerializer, **ERROR_RESPONSES})
    def get(self, request, transfer_id):
        transfer = Transfer.objects.filter(pk=uuid_value(transfer_id)).first()
        if transfer is None:
            raise DomainError("transfer_not_found", "No transfer with this ID.", 404)
        return Response(TransferSerializer(transfer).data)

    @extend_schema(
        request=TransferRequestSerializer,
        responses={200: TransferSerializer, 202: TransferSerializer, **ERROR_RESPONSES},
        description="The caller supplies a permanent transfer ID. Identical repeats never move funds twice.",
    )
    def put(self, request, transfer_id):
        data = TransferRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        transfer, created = create_transfer(uuid_value(transfer_id), **data.validated_data)
        return Response(TransferSerializer(transfer).data, status=202 if created else 200)
