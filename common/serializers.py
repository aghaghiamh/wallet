import re
from collections.abc import Mapping
from uuid import UUID

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.errors import DomainError

MAX_INT = 2**63 - 1
INTEGER_SCHEMA = {"type": "string", "pattern": r"^-?(0|[1-9][0-9]*)$"}
AMOUNT_SCHEMA = {
    "type": "string",
    "pattern": r"^[1-9][0-9]*$",
    "description": "Whole IRR, 1..9223372036854775807, encoded as a decimal string.",
    "example": "1000000",
}


def uuid_value(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise DomainError("invalid_request", "A valid UUID is required.") from None


def idempotency_key(request):
    return uuid_value(request.headers.get("Idempotency-Key", ""))


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({"non_field_errors": ["Expected a JSON object."]})
        unknown = set(data) - {name for name, field in self.fields.items() if not field.read_only}
        if unknown:
            raise serializers.ValidationError(
                {"non_field_errors": [f"Unknown fields: {', '.join(sorted(unknown))}."]}
            )
        return super().to_internal_value(data)


@extend_schema_field(AMOUNT_SCHEMA)
class AmountField(serializers.Field):
    def to_internal_value(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
            raise serializers.ValidationError(
                "Use a positive decimal string without leading zeros."
            )
        if len(value) > 19 or (len(value) == 19 and value > str(MAX_INT)):
            raise DomainError("amount_out_of_range", "Amount exceeds BIGINT capacity.", 422)
        return int(value)

    def to_representation(self, value):
        return str(value)


@extend_schema_field(INTEGER_SCHEMA)
class IntegerStringField(serializers.Field):
    def to_representation(self, value):
        return str(value)


class AccountField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=100, trim_whitespace=False, **kwargs)

    def to_internal_value(self, value):
        if not isinstance(value, str) or not value.strip():
            raise serializers.ValidationError("Use a nonempty account identifier.")
        return super().to_internal_value(value)


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class ErrorSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()


ERROR_RESPONSES = {code: ErrorSerializer for code in (400, 404, 409, 422, 500, 503)}
