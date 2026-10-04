from django.utils import timezone

from common.db import transactional
from common.errors import DomainError
from common.serializers import MAX_INT
from wallets.models import TopUp, Wallet
from wallets.provider_client import ProviderClient, ProviderIntegrityError, ProviderUnavailable
from wallets.services import post_entry

TERMINAL_PROVIDER_STATES = {"SUCCEEDED", "FAILED"}


def matches(top_up, observation):
    expected = {
        "id": str(top_up.id),
        "amount": str(top_up.amount),
        "currency": top_up.currency,
        "source_account_id": top_up.source_account_id,
        "destination_account_id": top_up.destination_account_id,
    }
    return all(observation.get(key) == value for key, value in expected.items())


@transactional()
def apply_observation(top_up_id, observation=None, *, error_code=None, review=False):
    reference = TopUp.objects.only("wallet_id").get(pk=top_up_id)
    wallet = Wallet.objects.select_for_update().get(pk=reference.wallet_id)
    top_up = TopUp.objects.select_for_update().get(pk=top_up_id)
    if top_up.status != TopUp.Status.PENDING:
        return top_up.status

    top_up.last_checked_at = timezone.now()
    top_up.error_code = error_code
    if review:
        top_up.status = TopUp.Status.REVIEW_REQUIRED
    elif observation is not None:
        if not matches(top_up, observation):
            top_up.status = TopUp.Status.REVIEW_REQUIRED
            top_up.error_code = "transfer_mismatch"
        else:
            provider_status = observation["status"]
            if top_up.provider_status in TERMINAL_PROVIDER_STATES:
                if provider_status == "PENDING":
                    return top_up.status  # An older observation cannot undo confirmed funds.
                if provider_status != top_up.provider_status:
                    top_up.status = TopUp.Status.REVIEW_REQUIRED
                    top_up.error_code = "provider_status_conflict"
            if top_up.status == TopUp.Status.PENDING:
                top_up.provider_status = provider_status
                if provider_status == "FAILED":
                    top_up.status = TopUp.Status.FAILED
                    code = observation.get("failure_code")
                    top_up.error_code = (
                        code if isinstance(code, str) and len(code) <= 100 else "transfer_failed"
                    )
                elif provider_status == "SUCCEEDED":
                    if wallet.balance > MAX_INT - top_up.amount:
                        top_up.status = TopUp.Status.REVIEW_REQUIRED
                        top_up.error_code = "balance_limit"
                    elif wallet.last_sequence == MAX_INT:
                        top_up.status = TopUp.Status.REVIEW_REQUIRED
                        top_up.error_code = "sequence_limit"
                    else:
                        post_entry(wallet, "credit", top_up.amount, top_up=top_up)
                        top_up.status = TopUp.Status.SETTLED
    top_up.save(
        update_fields=["status", "provider_status", "error_code", "last_checked_at", "updated_at"]
    )
    return top_up.status


def process_top_up(top_up_id, client=None):
    """One recoverable attempt. Never call this inside a database transaction."""
    top_up = TopUp.objects.filter(pk=top_up_id).first()
    if top_up is None:
        return "MISSING"
    if top_up.status != TopUp.Status.PENDING:
        return top_up.status
    client = client or ProviderClient()
    try:
        observation = client.get(top_up.id)
        if observation is None:
            if top_up.provider_status in TERMINAL_PROVIDER_STATES:
                return apply_observation(
                    top_up.id, error_code="provider_record_missing", review=True
                )
            observation = client.create(top_up)
        return apply_observation(top_up.id, observation)
    except ProviderUnavailable:
        return apply_observation(top_up.id, error_code="provider_unavailable")
    except ProviderIntegrityError as exc:
        return apply_observation(top_up.id, error_code=exc.code, review=True)


@transactional()
def reprocess_top_up(top_up_id):
    reference = TopUp.objects.only("wallet_id").get(pk=top_up_id)
    Wallet.objects.select_for_update().get(pk=reference.wallet_id)
    top_up = TopUp.objects.select_for_update().get(pk=top_up_id)
    if top_up.status != TopUp.Status.REVIEW_REQUIRED:
        raise DomainError("invalid_state", "Only a reviewed intent can be reprocessed.", 409)
    top_up.status = TopUp.Status.PENDING
    top_up.error_code = None
    top_up.save(update_fields=["status", "error_code", "updated_at"])
    return top_up
