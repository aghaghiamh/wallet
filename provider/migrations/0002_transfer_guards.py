from django.db import migrations

FORWARD = """
CREATE FUNCTION provider_transfer_immutable_intent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id, NEW.source_account_id, NEW.destination_account_id, NEW.amount, NEW.currency)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.source_account_id, OLD.destination_account_id, OLD.amount, OLD.currency) THEN
        RAISE EXCEPTION 'Transfer intent parameters are immutable' USING ERRCODE = '55000';
    END IF;
    IF OLD.status IN ('SUCCEEDED', 'FAILED') AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'Provider transfer state is terminal' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER transfer_immutable_intent BEFORE UPDATE ON provider_transfer
FOR EACH ROW EXECUTE FUNCTION provider_transfer_immutable_intent();
"""


class Migration(migrations.Migration):
    dependencies = [("provider", "0001_initial")]
    operations = [
        migrations.RunSQL(
            FORWARD,
            "DROP TRIGGER transfer_immutable_intent ON provider_transfer; DROP FUNCTION provider_transfer_immutable_intent();",
        )
    ]
