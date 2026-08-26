from __future__ import annotations

import asyncio

import pytest

from app.providers import ProviderLead
from app.rate_limit import InMemoryRateLimiter, RateLimitExceeded
from app.services import normalize_domain, quality_for, score_provider_lead


def test_normalize_domain():
    assert normalize_domain("https://Example.com/path") == "example.com"
    assert normalize_domain("http://EXAMPLE.com/") == "example.com"
    assert normalize_domain(None) is None


def test_score_provider_lead_is_bounded_and_explainable():
    record = ProviderLead(
        provider="synthetic_provider",
        company={
            "name": "SYNTHETIC Example",
            "domain": "example.invalid",
            "industry": "financial_services",
            "country": "SA",
            "employee_count": 850,
            "technologies": ["AWS"],
        },
        contact={"job_title": "CFO"},
        observed_at="2026-01-01T00:00:00+00:00",
    )
    scored = score_provider_lead(
        record,
        {
            "industries": ["financial_services"],
            "countries": ["SA"],
            "employee_count": {"min": 100, "max": 5000},
            "job_titles": ["CFO"],
            "technologies": ["AWS"],
        },
    )
    assert 0 <= scored.lead_score <= 100
    assert scored.lead_score == sum(scored.breakdown.values())
    assert scored.breakdown["industry"] == 20


def test_quality_marks_missing_values_without_inventing_them():
    record = ProviderLead(
        provider="synthetic_provider",
        company={"name": "SYNTHETIC Example", "domain": None, "industry": None, "country": "SA", "employee_count": None},
        contact=None,
        observed_at="2026-01-01T00:00:00+00:00",
    )
    quality = quality_for(record)
    assert quality["completeness"] < 100
    assert quality["accuracy_estimate"] is None


def test_rate_limiter_rejects_after_limit():
    async def run():
        limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
        await limiter.check("tenant:test")
        with pytest.raises(RateLimitExceeded):
            await limiter.check("tenant:test")

    asyncio.run(run())
