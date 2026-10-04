from uuid import uuid4

import pytest
from django.test import override_settings
from drf_spectacular.generators import SchemaGenerator
from openapi_schema_validator import OAS30Validator
from openapi_spec_validator import validate

from wallets.serializers import TopUpReceiptSerializer
from wallets.services import create_top_up

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "provider"])


@pytest.mark.parametrize(
    "role,urlconf", [("wallet", "wallets.urls"), ("provider", "provider.urls")]
)
def test_openapi_and_swagger(client, role, urlconf):
    with override_settings(ROOT_URLCONF=urlconf, SERVICE_ROLE=role):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        validate(response.data)
        assert client.get("/docs").status_code == 200
        path = (
            "/v1/wallets/{user_id}/top-ups" if role == "wallet" else "/v1/transfers/{transfer_id}"
        )
        assert path in response.data["paths"]
        schema = response.data
        request = schema["paths"][path]["post" if role == "wallet" else "put"]["requestBody"][
            "content"
        ]["application/json"]["schema"]
        component = request["$ref"].split("/")[-1]
        assert (
            schema["components"]["schemas"][component]["properties"]["amount"]["type"] == "string"
        )


def test_representative_receipt_matches_openapi(wallet):
    top_up = create_top_up(wallet.user_id, uuid4(), 100, "source")
    schema = SchemaGenerator(urlconf="wallets.urls").get_schema(public=True)
    response = schema["paths"]["/v1/wallets/{user_id}/top-ups"]["post"]["responses"]["202"][
        "content"
    ]["application/json"]["schema"]
    component = response["$ref"].split("/")[-1]
    OAS30Validator(schema["components"]["schemas"][component]).validate(
        TopUpReceiptSerializer(top_up).data
    )
