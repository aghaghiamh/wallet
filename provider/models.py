from django.db import models
from django.db.models import Q


class Account(models.Model):
    class Behavior(models.TextChoices):
        NORMAL = "NORMAL"
        FAIL = "FAIL"
        PENDING = "PENDING"

    id = models.CharField(primary_key=True, max_length=100)
    balance = models.BigIntegerField(default=0)
    behavior = models.CharField(max_length=10, choices=Behavior, default=Behavior.NORMAL)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(balance__gte=0), name="provider_balance_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(behavior__in=["NORMAL", "FAIL", "PENDING"]),
                name="provider_behavior_valid",
            ),
        ]


class Transfer(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"

    id = models.UUIDField(primary_key=True)
    source_account_id = models.CharField(max_length=100)
    destination_account_id = models.CharField(max_length=100)
    amount = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    failure_code = models.CharField(max_length=100, null=True)
    ready_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="provider_amount_positive"),
            models.CheckConstraint(condition=Q(currency="IRR"), name="provider_currency_irr"),
            models.CheckConstraint(
                condition=Q(status__in=["PENDING", "SUCCEEDED", "FAILED"]),
                name="provider_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["ready_at"], condition=Q(status="PENDING"), name="provider_due_idx"
            )
        ]
