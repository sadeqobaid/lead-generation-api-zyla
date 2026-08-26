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
    """Generic POST/JSON adapter for a buyer-approved provider contract.

    Expected response shape is either a list of records or ``{"data": [...]}``. Each record
    must contain a ``company`` object and may contain ``contact``, ``observed_at``, ``use_policy``,
    and ``data_status``. Field mapping, provider licensing, and lawful-use review remain
    deployment-specific and must be documented by the buyer.
    """

    code = "http_provider"

    def __init__(self, url: str, token: str, timeout_seconds: float = 15.0) -> None:
        self.url = url
        self.token = token
        self.timeout_seconds = timeout_seconds

    async def search(self, filters: dict[str, Any]) -> list[ProviderLead]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.url,
                    json={"filters": filters},
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderRequestError("configured provider request failed") from exc
        records = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ProviderRequestError("configured provider returned an invalid JSON shape")
        now = datetime.now(timezone.utc).isoformat()
        result: list[ProviderLead] = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("company"), dict):
                raise ProviderRequestError("configured provider returned a record without a company object")
            result.append(
                ProviderLead(
                    provider=self.code,
                    company=record["company"],
                    contact=record.get("contact") if isinstance(record.get("contact"), dict) else None,
                    observed_at=str(record.get("observed_at") or now),
                    use_policy=str(record.get("use_policy") or "PROVIDER_CONTRACT_REQUIRED"),
                    data_status=str(record.get("data_status") or "PROVIDER_SUPPLIED"),
                )
            )
        return result


def build_provider(mode: str, url: str = "", token: str = "", timeout_seconds: float = 15.0) -> LeadProvider:
    """Build only an explicitly selected provider and fail closed for unsupported modes."""
    if mode == "synthetic":
        return SyntheticLeadProvider()
    if mode == "http" and url and token:
        return HttpLeadProvider(url, token, timeout_seconds)
    raise ProviderConfigurationError(
        f"provider mode {mode!r} has no complete included adapter configuration; "
        "configure an approved licensed HTTP provider or install a deployment-specific adapter"
    )
