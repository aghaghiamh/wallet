import uuid

from django.db import models
from django.db.models import Q


class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(unique=True)
    balance = models.BigIntegerField(default=0)
    last_sequence = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def currency(self):
        return "IRR"

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="wallet_balance_nonnegative"),
            models.CheckConstraint(
                condition=Q(last_sequence__gte=0), name="wallet_sequence_nonnegative"
            ),
        ]


class TopUp(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        SETTLED = "SETTLED"
        FAILED = "FAILED"
        REVIEW_REQUIRED = "REVIEW_REQUIRED"

    class ProviderStatus(models.TextChoices):
        PENDING = "PENDING"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="top_ups")
    idempotency_key = models.UUIDField()
    amount = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="IRR")
    source_account_id = models.CharField(max_length=100)
    destination_account_id = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING)
    provider_status = models.CharField(max_length=10, choices=ProviderStatus, null=True)
    error_code = models.CharField(max_length=100, null=True)
    last_checked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["wallet", "idempotency_key"], name="topup_request_unique"
            ),
            models.UniqueConstraint(fields=["id", "wallet"], name="topup_id_wallet_unique"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="topup_amount_positive"),
            models.CheckConstraint(condition=Q(currency="IRR"), name="topup_currency_irr"),
            models.CheckConstraint(
                condition=Q(status__in=["PENDING", "SETTLED", "FAILED", "REVIEW_REQUIRED"]),
                name="topup_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(provider_status__isnull=True)
                | Q(provider_status__in=["PENDING", "SUCCEEDED", "FAILED"]),
                name="topup_provider_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["id"], condition=Q(status="PENDING"), name="topup_pending_idx"),
            models.Index(fields=["updated_at", "id"], name="topup_updated_idx"),
            models.Index(
                fields=["id"],
                condition=Q(status__in=["PENDING", "REVIEW_REQUIRED"]),
                name="topup_unresolved_idx",
            ),
        ]


class LedgerEntry(models.Model):
    class Kind(models.TextChoices):
        CREDIT = "credit"
        DEBIT = "debit"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    wallet_sequence = models.BigIntegerField()
    kind = models.CharField(max_length=6, choices=Kind)
    delta = models.BigIntegerField()
    balance_after = models.BigIntegerField()
    idempotency_key = models.UUIDField(null=True)
    top_up = models.OneToOneField(
        TopUp, null=True, on_delete=models.PROTECT, related_name="ledger_entry"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def amount(self):
        return abs(self.delta)

    @property
    def currency(self):
        return "IRR"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["wallet", "wallet_sequence"], name="ledger_sequence_unique"
            ),
            models.UniqueConstraint(
                fields=["wallet", "idempotency_key"], name="ledger_request_unique"
            ),
            models.CheckConstraint(
                condition=Q(balance_after__gte=0), name="ledger_balance_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(wallet_sequence__gt=0), name="ledger_sequence_positive"
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="credit",
                        delta__gt=0,
                        top_up__isnull=False,
                        idempotency_key__isnull=True,
                    )
                    | Q(
                        kind="debit",
                        delta__lt=0,
                        top_up__isnull=True,
                        idempotency_key__isnull=False,
                    )
                ),
                name="ledger_entry_shape",
            ),
        ]
        indexes = [models.Index(fields=["wallet", "-wallet_sequence"], name="ledger_history_idx")]
