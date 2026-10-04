from rest_framework import serializers

from common.serializers import AccountField, AmountField, StrictSerializer
from provider.models import Transfer


class TransferRequestSerializer(StrictSerializer):
    source_account_id = AccountField()
    destination_account_id = AccountField()
    amount = AmountField()
    currency = serializers.ChoiceField(choices=["IRR"])


class TransferSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=Transfer.Status)
    source_account_id = serializers.CharField()
    destination_account_id = serializers.CharField()
    amount = AmountField()
    currency = serializers.CharField()
    failure_code = serializers.CharField(allow_null=True)
