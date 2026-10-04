from django.db import migrations

FORWARD = """
ALTER TABLE wallets_ledgerentry
    ADD CONSTRAINT ledger_funding_wallet_fk
    FOREIGN KEY (top_up_id, wallet_id)
    REFERENCES wallets_topup (id, wallet_id)
    ON DELETE RESTRICT;

CREATE FUNCTION wallet_ledger_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Ledger entries are append-only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER ledger_immutable BEFORE UPDATE OR DELETE ON wallets_ledgerentry
FOR EACH ROW EXECUTE FUNCTION wallet_ledger_immutable();

CREATE FUNCTION wallet_topup_immutable_intent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id, NEW.wallet_id, NEW.idempotency_key, NEW.amount, NEW.currency,
           NEW.source_account_id, NEW.destination_account_id)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.wallet_id, OLD.idempotency_key, OLD.amount, OLD.currency,
           OLD.source_account_id, OLD.destination_account_id) THEN
        RAISE EXCEPTION 'Top-up intent parameters are immutable' USING ERRCODE = '55000';
    END IF;
    IF OLD.status IN ('SETTLED', 'FAILED') AND
       ROW(NEW.status, NEW.provider_status) IS DISTINCT FROM ROW(OLD.status, OLD.provider_status) THEN
        RAISE EXCEPTION 'Completed top-up state is terminal' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER topup_immutable_intent BEFORE UPDATE ON wallets_topup
FOR EACH ROW EXECUTE FUNCTION wallet_topup_immutable_intent();
"""

REVERSE = """
DROP TRIGGER topup_immutable_intent ON wallets_topup;
DROP FUNCTION wallet_topup_immutable_intent();
DROP TRIGGER ledger_immutable ON wallets_ledgerentry;
DROP FUNCTION wallet_ledger_immutable();
ALTER TABLE wallets_ledgerentry DROP CONSTRAINT ledger_funding_wallet_fk;
"""


class Migration(migrations.Migration):
    dependencies = [("wallets", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
