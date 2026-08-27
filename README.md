# Lead Generation API — Zyla Hosted Edition

## Introduction

This project is a multi-tenant B2B lead-discovery API designed for controlled marketplace distribution. It exposes authenticated tenant routes and a Zyla-friendly hosted contract, applies tenant isolation and suppression rules, scores returned records, records usage, and charges one credit per returned lead by default. The API is intended to return **licensed provider-backed data** in staging and production. Synthetic records are retained only for local development and controlled demonstrations and are explicitly labelled in responses.

The system is composed of a FastAPI/Uvicorn HTTP service, a Python worker for Redis Streams jobs, PostgreSQL for durable relational state, Redis for shared coordination, and Alembic for explicit database migrations. The default local configuration is convenient for testing; the production configuration is deliberately fail-closed.

## Technology stack

| Technology | Role in this project |
| --- | --- |
| Python 3.11+ | Application and operational-script runtime. |
| FastAPI | HTTP routing, request validation integration, dependency injection, and interactive API documentation. |
| Uvicorn | ASGI server used to run the API process. |
| Pydantic | Typed request/response models and environment-driven validation. |
| SQLAlchemy | ORM and transaction management for tenant, lead, usage, billing, privacy, and job data. |
| PostgreSQL | Production relational database for durable application state. |
| SQLite | Lightweight local/test database option. |
| Alembic | Versioned schema migrations applied explicitly during deployment. |
| Redis | Shared rate limiting, idempotency, and queue coordination state. |
| Redis Streams | Background job queue with consumer groups, acknowledgement, and pending-message recovery. |
| HTTPX | Asynchronous outbound calls to the configured licensed lead-data provider. |
| PyJWT and Argon2 | JWT access tokens and memory-hard password hashing. |
| Docker and Docker Compose | Repeatable container build and local multi-service orchestration. |
| Pytest and pytest-asyncio | Automated API, service, async, hardening, and provider-adapter tests. |
| GitHub Actions | Continuous-integration checks for repository changes. |

## Quick start for local development

1. Copy the environment template to `.env` and keep `APP_ENV=development`.
2. Leave `PROVIDER_MODE=synthetic` for local tests only, or configure the HTTP provider contract described below.
3. Start the stack with `docker compose up --build`.
4. The API is available at `http://localhost:8000`, with interactive documentation at `/docs` and the marketplace contract at `/openapi.json`.
5. Run the checks with `bash scripts/run_checks.sh`.

For a direct Python run, install `requirements.txt`, configure a local environment, apply migrations with `python scripts/migrate.py`, and start the service with `uvicorn app.main:app --reload`.

## Licensed provider configuration

The included `HttpLeadProvider` is a vendor-neutral adapter because the project archive does not contain a commercial contract or credentials for a specific vendor such as Apollo, ZoomInfo, or Crustdata. Before marketplace launch, select a vendor whose terms permit the intended use, configure the vendor endpoint and credential, and confirm that the response mapping is authorized and accurate.

Set the following variables in the deployment environment:

```dotenv
PROVIDER_MODE=http
PROVIDER_NAME=your_licensed_vendor
PROVIDER_URL=https://vendor.example/api/search
PROVIDER_TOKEN=load-from-a-secret-manager
PROVIDER_AUTH_SCHEME=bearer
PROVIDER_DATA_SOURCE=Licensed data supplied by Your Vendor under the applicable customer agreement.
PROVIDER_USE_POLICY=Use permitted under the applicable provider and customer agreement.
PROVIDER_TIMEOUT_SECONDS=15
```

The adapter sends:

```json
{"filters": {"countries": ["US"], "industries": ["software"]}}
```

It accepts a JSON list, `{"data": [...]}`, or `{"results": [...]}`. Each record must contain a non-empty `company.name`. Provider records must not be labelled `SYNTHETIC`; the adapter rejects such records. The API response includes provider name, data status, use policy, observed time, and data-source provenance.

The application does not include vendor credentials. A deployment is not commercially ready merely because `PROVIDER_MODE=http` is set: the operator must verify the vendor contract, lawful-use basis, field mapping, freshness, suppression process, and data-source statement before exposing the endpoint to customers.

## Authentication examples

Register a tenant administrator:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \\
  -H 'Content-Type: application/json' \\
  -d '{"tenant_name":"Example Company","email":"owner@example.com","password":"correct-horse-battery-staple"}'
```

Use the returned JWT with `/api/v1/users/me`, or create a tenant API key for programmatic lead searches. Zyla marketplace calls use the configured shared bearer token:

```bash
curl 'http://localhost:8000/api/v1/zyla/leads/search?countries=US&industries=software&limit=10' \\
  -H 'Authorization: Bearer YOUR_ZYLA_SHARED_TOKEN' \\
  -H 'Idempotency-Key: example-search-001'
```

## Pricing and free trial

The service records usage as credits. The default accounting rule is one credit per returned lead. The marketplace price is deployment-defined and is advertised through `PRICING_MODEL`; an optional free allocation is advertised through `FREE_TRIAL_CREDITS`. Configure billing and pricing only after the commercial offer is approved.

```dotenv
PRICING_MODEL=One credit per returned lead; see the marketplace offer for currency pricing.
FREE_TRIAL_CREDITS=25
```

The signed billing webhook expects `X-Webhook-Signature: sha256=<HMAC-SHA256>` and grants credits once per provider event. Use a secret manager for `BILLING_WEBHOOK_SECRET`.

## Production checklist

Production and staging validation require strong JWT and API-key secrets, PostgreSQL, Redis-backed rate limiting/idempotency/queueing, an explicit Alembic migration step, non-wildcard CORS, a metrics token when metrics are enabled, a signed billing-webhook secret, and a licensed non-synthetic provider unless the explicit synthetic override is being used for a controlled test. Database pooling is configured with `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE_SECONDS`, and `DB_POOL_TIMEOUT_SECONDS`.

The `/metrics` endpoint returns Prometheus-compatible text. If `METRICS_TOKEN` is configured, callers must send the exact value in the `X-Metrics-Token` header. All framework and application errors are returned in the documented envelope:

```json
{"error":{"code":"...","message":"...","request_id":"...","details":{}}}
```

## API documentation

The checked-in `openapi.yaml` documents the Zyla-facing and internal routes, including registration, login, the authenticated-user route, metrics-token requirements, provider-unavailable responses, idempotency, privacy, jobs, billing, and usage. The running FastAPI application also serves generated documentation at `/docs` and `/redoc`.
