from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wallets", "0002_ledger_guards")]

    operations = [
        migrations.AddIndex(
            model_name="topup",
            index=models.Index(fields=["updated_at", "id"], name="topup_updated_idx"),
        ),
        migrations.AddIndex(
            model_name="topup",
            index=models.Index(
                fields=["id"],
                condition=models.Q(status__in=["PENDING", "REVIEW_REQUIRED"]),
                name="topup_unresolved_idx",
            ),
        ),
    ]
