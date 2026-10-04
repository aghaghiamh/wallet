# Auditable Wallet

A small interview project: wallet funding through a polling payment provider, internal spending, an append-only ledger, and reconciliation. The stack is Django 5.2, PostgreSQL, Celery, and Redis. All money is whole IRR, encoded as decimal strings in JSON.

## Run the demo

Prerequisite: Docker with Compose.

```sh
docker compose up -d --build
docker compose ps
python3 scripts/demo.py
```

If port 8000 is occupied, start with `WALLET_PORT=18000 docker compose up -d --build` and run `python3 scripts/demo.py --api http://localhost:18000`. `PROVIDER_PORT` similarly overrides port 8001.

The demo creates a wallet, submits a top-up, waits for confirmation, spends part of it, and repeats both requests to verify idempotency. Normal funding takes about two polling cycles. Seeded provider accounts are created without resetting their existing balances.

- Wallet Swagger: http://localhost:8000/docs
- Provider Swagger: http://localhost:8001/docs
- Live OpenAPI JSON: /openapi.json on either service
- [Committed wallet schema](docs/openapi/wallet.yaml)
- [Committed provider schema](docs/openapi/provider.yaml)
- [Technical specification and diagrams](docs/technical-spec.md)

The example source account is **demo-customer-1**. **demo-failure** rejects transfers, and **demo-pending** leaves them pending. Account behavior is simulator configuration, never a field in the public funding request.

## API example

UUIDs identify users and idempotency keys. A key is scoped to a wallet and operation; keep it when retrying an uncertain request.

```sh
curl -X PUT http://localhost:8000/v1/wallets/11111111-1111-4111-8111-111111111111

curl -X POST http://localhost:8000/v1/wallets/11111111-1111-4111-8111-111111111111/top-ups \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 22222222-2222-4222-8222-222222222222' \
  -d '{"amount":"1000000","source_account_id":"demo-customer-1"}'
```

Top-up creation returns **202** and a Location header. Follow that URL to observe PENDING, SETTLED, FAILED, or REVIEW_REQUIRED. Retrying creation returns the same receipt even after settlement. A pending top-up adds no spendable funds.

After settlement:

```sh
curl -X POST http://localhost:8000/v1/wallets/11111111-1111-4111-8111-111111111111/debits \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 33333333-3333-4333-8333-333333333333' \
  -d '{"amount":"250000"}'

curl http://localhost:8000/v1/wallets/11111111-1111-4111-8111-111111111111
curl 'http://localhost:8000/v1/wallets/11111111-1111-4111-8111-111111111111/entries?limit=50'
```

History is newest first. Following next_cursor yields the original history range even while newer entries are added.

## Consistency and recovery

Each debit and confirmed top-up settlement locks its wallet row. Ledger append, balance update, and payment settlement commit together. Database checks reject negative balances and inconsistent funding references; triggers reject ledger updates/deletes and changes to transfer intent parameters.

Two small Celery tasks handle polling. Beat dispatches checks every 10 seconds; workers call the provider outside database transactions. The provider deduplicates the persisted top-up UUID, and a unique ledger reference prevents a duplicate credit. An overlapping or redelivered task is safe.

The API only persists a top-up; it does not need to publish a message before responding. A periodic database scan rediscovers pending intents after broker/task loss. Redis outages delay funding while wallet reads, spending, and intent creation can still use PostgreSQL. Run **one Beat scheduler**, and scale API/worker processes as needed.

A network timeout remains uncertain. The service reuses the same provider transfer ID and polls for a definitive result. Confirmed funds that exceed local balance capacity remain visible as REVIEW_REQUIRED.

## Reconciliation and operator commands

```sh
docker compose exec api python manage.py reconcile_known_transfers --once
docker compose exec api python manage.py reprocess_top_up TOP_UP_UUID
docker compose exec provider-api python manage.py provider_advance --once --force
docker compose logs worker beat provider-processor
```

Reconciliation checks known provider transfers and local ledger projections, reporting matched, pending, unverified, review-required, and mismatched records. Integrity mismatches cause a nonzero command exit. It does not change settled history or discover unknown external transfers.

Reprocess only a reviewed intent after resolving its cause. It preserves the transfer ID and immutable parameters and returns the intent to polling. Failed transfers stay terminal; a new funding attempt needs a new client key.

## Tests and checks

Run the full suite, including a real Celery worker, Redis, and HTTP failure injection:

```sh
docker compose --profile test run --rm tests
```

Tests use separate PostgreSQL test databases. They cover competing debits, overlapping settlement, duplicate transfers, rollback, deadlock retry, lost provider responses, external success before a local crash, reconciliation, BIGINT overflow, and schema validation. The HTTP failure test closes the socket after the provider commits; the worker integration test recovers an unpublished intent and executes duplicate notifications.

For development with Python 3.12 on the host:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
docker compose up -d postgres redis
.venv/bin/python manage.py migrate
.venv/bin/python manage.py migrate --database provider
.venv/bin/pytest -q --integration
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Environment defaults target the Compose database at port 55432 and Redis at 56379; .env.example lists overrides. Settings read process environment variables. All wallet pods share SECRET_KEY to verify signed history cursors.

Regenerate committed schemas with:

```sh
.venv/bin/python manage.py spectacular --file docs/openapi/wallet.yaml --validate --fail-on-warn
SERVICE_ROLE=provider .venv/bin/python manage.py spectacular --file docs/openapi/provider.yaml --validate --fail-on-warn
```

## Code map

| Location | Responsibility |
| --- | --- |
| wallets/services.py | Wallet creation, durable top-up creation, debits |
| wallets/topups.py | Provider checks and atomic settlement |
| wallets/tasks.py | Celery scheduling wrappers |
| wallets/reconciliation.py | Known-transfer audit and consistent ledger checks |
| provider/services.py | Independent, idempotent provider simulation |
| common/ | Validation, error responses, transaction retry |
| tests/ | PostgreSQL and HTTP/Celery verification |

## Scope

This delivery uses demo user IDs and preauthorized simulated accounts, with no authentication or live bank credentials. Debits are internal spending; there are no bank withdrawals, refunds, fees, or wallet transfers. The ledger traces wallet changes and their funding sources; full double-entry accounting and provider statements are outside the exercise.

Database restoration and failover require an explicit procedure. Losing local intents can leave external transfers undiscoverable through an API that only supports lookup by ID.

Stop services with **docker compose down**. The PostgreSQL volume preserves data.
