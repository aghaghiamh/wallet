import requests
from django.conf import settings


class ProviderUnavailable(Exception):
    pass


class ProviderIntegrityError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ProviderClient:
    def __init__(self, base_url=None, timeout=None):
        self.base_url = (base_url or settings.PROVIDER_URL).rstrip("/")
        self.timeout = timeout or settings.PROVIDER_TIMEOUT

    def _request(self, method, transfer_id, body=None):
        url = f"{self.base_url}/v1/transfers/{transfer_id}"
        try:
            response = requests.request(
                method,
                url,
                json=body,
                timeout=(self.timeout, self.timeout),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ProviderUnavailable from exc
        if method == "GET" and response.status_code == 404:
            return None
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable
        if response.status_code not in {200, 202}:
            raise ProviderIntegrityError("provider_http_error")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderIntegrityError("provider_invalid_response") from exc
        if not isinstance(body, dict) or body.get("status") not in {
            "PENDING",
            "SUCCEEDED",
            "FAILED",
        }:
            raise ProviderIntegrityError("provider_invalid_response")
        return body

    def get(self, transfer_id):
        return self._request("GET", transfer_id)

    def create(self, top_up):
        return self._request(
            "PUT",
            top_up.id,
            {
                "source_account_id": top_up.source_account_id,
                "destination_account_id": top_up.destination_account_id,
                "amount": str(top_up.amount),
                "currency": top_up.currency,
            },
        )
