from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .providers import ProviderLead


@dataclass(frozen=True)
class ScoredLead:
    source: ProviderLead
    lead_score: int
    icp_score: int
    breakdown: dict[str, float]


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    domain = value.strip().lower()
    domain = domain.removeprefix("https://").removeprefix("http://")
    domain = domain.split("/", 1)[0]
    return domain or None


def score_provider_lead(record: ProviderLead, filters: dict[str, Any]) -> ScoredLead:
    company = record.company
    contact = record.contact or {}
    breakdown: dict[str, float] = {}
    industries = set(filters.get("industries", []))
    countries = set(filters.get("countries", []))
    titles = {title.lower() for title in filters.get("job_titles", [])}
    technologies = {technology.lower() for technology in filters.get("technologies", [])}
    employee_range = filters.get("employee_count") or {}

    breakdown["industry"] = 20 if industries and company["industry"] in industries else (10 if not industries else 0)
    breakdown["country"] = 10 if countries and company["country"] in countries else (5 if not countries else 0)
    title = str(contact.get("job_title", "")).lower()
    breakdown["job_title"] = 20 if titles and title in titles else (10 if not titles else 0)
    technology_set = {str(value).lower() for value in company.get("technologies", [])}
    breakdown["technology"] = 10 if technologies and technologies.intersection(technology_set) else (5 if not technologies else 0)
    count = company.get("employee_count")
    in_range = (
        count is not None
        and (employee_range.get("min") is None or count >= employee_range["min"])
        and (employee_range.get("max") is None or count <= employee_range["max"])
    )
    breakdown["company_size"] = 15 if employee_range and in_range else (8 if not employee_range else 0)
    breakdown["intent"] = 15 if company.get("intent_signal") else 0

    lead_score = max(0, min(100, round(sum(breakdown.values()))))
    icp_score = lead_score
    return ScoredLead(record, lead_score, icp_score, breakdown)


def quality_for(record: ProviderLead) -> dict[str, Any]:
    company_fields = [record.company.get("name"), record.company.get("domain"), record.company.get("industry"), record.company.get("country"), record.company.get("employee_count")]
    contact_fields = list((record.contact or {}).values())
    all_fields = company_fields + contact_fields
    completeness = round(100 * sum(value is not None for value in all_fields) / max(1, len(all_fields)))
    return {
        "completeness": completeness,
        "accuracy_estimate": None,
        "freshness": 70,
        "confidence": 95,
        "source_count": 1,
        "provenance_available": True,
    }
