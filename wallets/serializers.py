from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.serializers import AccountField, AmountField, IntegerStringField, StrictSerializer
from wallets.models import LedgerEntry, TopUp


class DebitRequestSerializer(StrictSerializer):
    amount = AmountField()


class TopUpRequestSerializer(DebitRequestSerializer):
    source_account_id = AccountField()


class WalletSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    currency = serializers.CharField()
    balance = IntegerStringField()
    last_sequence = IntegerStringField()
    created_at = serializers.DateTimeField()


class LedgerSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    wallet_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=LedgerEntry.Kind)
    amount = AmountField()
    delta = IntegerStringField()
    balance_after = IntegerStringField()
    wallet_sequence = IntegerStringField()
    created_at = serializers.DateTimeField()
    currency = serializers.CharField()
    top_up_id = serializers.UUIDField(allow_null=True)


class TopUpReceiptSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    wallet_id = serializers.UUIDField()
    amount = AmountField()
    currency = serializers.CharField()


class TopUpStatusSerializer(TopUpReceiptSerializer):
    status = serializers.ChoiceField(choices=TopUp.Status)
    provider_status = serializers.ChoiceField(choices=TopUp.ProviderStatus, allow_null=True)
    source_account_id = serializers.CharField()
    destination_account_id = serializers.CharField()
    error_code = serializers.CharField(allow_null=True)
    last_checked_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    ledger_entry_id = serializers.SerializerMethodField()

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_ledger_entry_id(self, obj):
        try:
            return str(obj.ledger_entry.id)
        except LedgerEntry.DoesNotExist:
            return None


class HistorySerializer(serializers.Serializer):
    entries = LedgerSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
