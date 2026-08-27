from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class ProviderLead:
    provider: str
    company: dict[str, Any]
    contact: dict[str, Any] | None
    observed_at: str
    use_policy: str = "SYNTHETIC_DEMO_ONLY"
    data_status: str = "SYNTHETIC"


class LeadProvider(Protocol):
    code: str

    async def search(self, filters: dict[str, Any]) -> list[ProviderLead]: ...


class ProviderConfigurationError(RuntimeError):
    """Raised when production has no approved provider adapter configured."""


class ProviderRequestError(RuntimeError):
    """Raised when the configured external provider cannot fulfill a request."""


class SyntheticLeadProvider:
    """Deterministic fixture provider for development and demonstrations only."""

    code = "synthetic_provider"

    def __init__(self) -> None:
        self.records = [
            ProviderLead(
                provider=self.code,
                company={
                    "name": "SYNTHETIC Example Finance",
                    "website": "https://finance.example.invalid",
                    "domain": "finance.example.invalid",
                    "industry": "financial_services",
                    "country": "SA",
                    "employee_count": 850,
                    "technologies": ["Microsoft", "AWS"],
                },
                contact={"first_name": "Synthetic", "last_name": "Finance", "job_title": "CFO", "email": None},
                observed_at="2026-01-01T00:00:00+00:00",
            ),
            ProviderLead(
                provider=self.code,
                company={
                    "name": "SYNTHETIC Example Insurance",
                    "website": "https://insurance.example.invalid",
                    "domain": "insurance.example.invalid",
                    "industry": "insurance",
                    "country": "SA",
                    "employee_count": 3200,
                    "technologies": ["AWS"],
                },
                contact={"first_name": "Synthetic", "last_name": "Insurance", "job_title": "CTO", "email": None},
                observed_at="2026-01-01T00:00:00+00:00",
            ),
            ProviderLead(
                provider=self.code,
                company={
                    "name": "SYNTHETIC Example Software",
                    "website": "https://software.example.invalid",
                    "domain": "software.example.invalid",
                    "industry": "software",
                    "country": "AE",
                    "employee_count": 180,
                    "technologies": ["AWS", "Python"],
                },
                contact={"first_name": "Synthetic", "last_name": "Software", "job_title": "CEO", "email": None},
                observed_at="2026-01-01T00:00:00+00:00",
            ),
        ]

    async def search(self, filters: dict[str, Any]) -> list[ProviderLead]:
        def matches(record: ProviderLead) -> bool:
            company = record.company
            countries = set(filters.get("countries", []))
            industries = set(filters.get("industries", []))
            titles = {title.lower() for title in filters.get("job_titles", [])}
            technologies = {technology.lower() for technology in filters.get("technologies", [])}
            employee_range = filters.get("employee_count") or {}
            if countries and company["country"] not in countries:
                return False
            if industries and company["industry"] not in industries:
                return False
            employee_count = company["employee_count"]
            if employee_range.get("min") is not None and employee_count < employee_range["min"]:
                return False
            if employee_range.get("max") is not None and employee_count > employee_range["max"]:
                return False
            if titles and (not record.contact or record.contact["job_title"].lower() not in titles):
                return False
            if technologies and not technologies.intersection({item.lower() for item in company["technologies"]}):
                return False
            return True

        return [record for record in self.records if matches(record)]


class HttpLeadProvider:
    """Configurable POST/JSON adapter for a licensed buyer-approved provider.

    The adapter deliberately uses a small stable contract instead of pretending to implement a
    vendor-specific API without that vendor's agreement. The configured provider receives
    ``{"filters": {...}}`` and must return either a list of records, ``{"data": [...]}``, or
    ``{"results": [...]}``. Each record must contain a ``company`` object with a non-empty
    ``name``. The deployment supplies the vendor name, credential scheme, data-source statement,
    and permitted-use policy so marketplace responses include provenance rather than opaque
    synthetic labels.
    """

    def __init__(
        self,
        url: str,
        token: str,
        provider_name: str,
        auth_scheme: str = "bearer",
        timeout_seconds: float = 15.0,
        data_source: str = "",
        use_policy: str = "PROVIDER_CONTRACT_REQUIRED",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.provider_name = provider_name
        self.auth_scheme = auth_scheme
        self.timeout_seconds = timeout_seconds
        self.data_source = data_source
        self.use_policy = use_policy
        self.transport = transport
        self.code = provider_name

    def _headers(self) -> dict[str, str]:
        credential_header = "X-API-Key" if self.auth_scheme == "x-api-key" else "Authorization"
        credential_value = self.token if self.auth_scheme == "x-api-key" else f"Bearer {self.token}"
        return {
            credential_header: credential_value,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "lead-generation-api/0.2.0",
        }

    async def search(self, filters: dict[str, Any]) -> list[ProviderLead]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.post(self.url, json={"filters": filters}, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderRequestError("configured provider request failed") from exc
        if isinstance(payload, dict):
            records = payload.get("data", payload.get("results"))
        else:
            records = payload
        if not isinstance(records, list):
            raise ProviderRequestError("configured provider returned an invalid JSON shape")
        now = datetime.now(timezone.utc).isoformat()
        result: list[ProviderLead] = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("company"), dict):
                raise ProviderRequestError("configured provider returned a record without a company object")
            company = record["company"]
            if not isinstance(company.get("name"), str) or not company["name"].strip():
                raise ProviderRequestError("configured provider returned a company without a name")
            provider_data_status = str(record.get("data_status") or company.get("data_status") or "PROVIDER_SUPPLIED")
            if provider_data_status.upper() == "SYNTHETIC":
                raise ProviderRequestError("configured provider returned a synthetic record")
            provenance = str(record.get("data_source") or self.data_source or self.provider_name)
            result.append(
                ProviderLead(
                    provider=self.provider_name,
                    company={**company, "data_source": provenance},
                    contact=record.get("contact") if isinstance(record.get("contact"), dict) else None,
                    observed_at=str(record.get("observed_at") or now),
                    use_policy=str(record.get("use_policy") or self.use_policy),
                    data_status=provider_data_status,
                )
            )
        return result


def build_provider(
    mode: str,
    url: str = "",
    token: str = "",
    timeout_seconds: float = 15.0,
    provider_name: str = "approved_http_provider",
    auth_scheme: str = "bearer",
    data_source: str = "",
    use_policy: str = "PROVIDER_CONTRACT_REQUIRED",
) -> LeadProvider:
    """Build only an explicitly selected provider and fail closed for unsupported modes."""
    if mode == "synthetic":
        return SyntheticLeadProvider()
    if mode == "http" and url and token and provider_name:
        return HttpLeadProvider(url, token, provider_name, auth_scheme, timeout_seconds, data_source, use_policy)
    raise ProviderConfigurationError(
        f"provider mode {mode!r} has no complete included adapter configuration; "
        "configure an approved licensed HTTP provider or install a deployment-specific adapter"
    )
