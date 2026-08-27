# Zyla Marketplace Listing Draft — Lead Generation API

## Listing status

This listing draft is for the hosted Zyla edition. Publish it only after the service is deployed at a public HTTPS URL, a licensed non-synthetic provider is configured, and the owner has verified the provider’s rights, permitted uses, geographic coverage, freshness, retention, suppression requirements, and commercial terms.

## Title

**B2B Lead Discovery and Scoring API**

## Short description

Search permitted provider-backed B2B lead records using company, geography, industry, employee count, job title, technology, and lead-score filters. Responses include company and optional contact information, scoring, quality metadata, usage accounting, request identifiers, and explicit provider provenance.

## Data source statement

The API returns records from the deployment-configured licensed provider named by `PROVIDER_NAME`. The exact data source, rights, permitted uses, coverage, accuracy, freshness, and retention rules are deployment-dependent and must be stated in `PROVIDER_DATA_SOURCE` and the applicable provider/customer agreement. This source-code release includes a vendor-neutral HTTP adapter but does not include commercial provider credentials or grant data rights.

The included synthetic provider is for local integration testing and controlled demonstrations only. A synthetic deployment must not be marketed as a source of real leads, commercial contact data, enrichment, or verified company information.

## Pricing and free trial

The default accounting rule is one credit per returned lead. The marketplace price is configured through `PRICING_MODEL` and must be replaced with the approved currency and credit offer before publication. An optional free allocation is configured through `FREE_TRIAL_CREDITS`. Example:

```dotenv
PRICING_MODEL=One credit per returned lead; see the approved marketplace offer for currency pricing.
FREE_TRIAL_CREDITS=25
```

## Authentication

The recommended endpoint uses a bearer credential in the `Authorization` header. The hosted deployment should use a strong shared token stored in a secret manager and should keep provider credentials separate from marketplace credentials.

```bash
curl --request GET \\
  --url 'https://YOUR_PUBLIC_API_DOMAIN/api/v1/zyla/leads/search?countries=SA&industries=financial_services&limit=2' \\
  --header 'Authorization: Bearer YOUR_ZYLA_CONFIGURED_API_KEY' \\
  --header 'Idempotency-Key: unique-request-key-001'
```

## Endpoint contract

The Zyla-friendly search endpoint is available as both `GET` and `POST`:

- `GET /api/v1/zyla/leads/search` accepts simple query parameters.
- `POST /api/v1/zyla/leads/search` accepts the JSON `LeadSearchRequest` body.

Supported filters include countries, industries, job titles, technologies, employee ranges, minimum lead score, and result limit. Each response includes `request_id`, `data`, `pagination`, `usage`, and per-record provenance with provider, data status, use policy, observed time, and data-source statement.

## Error and operational behavior

Errors use the stable envelope `{"error":{"code":"...","message":"...","request_id":"...","details":{}}}`. Provider failures return `502 PROVIDER_UNAVAILABLE`. The service also emits `X-Request-Id`, `X-API-Version: v1`, and `X-Response-Time-Ms` response headers. When metrics authentication is configured, `/metrics` requires the `X-Metrics-Token` header.
