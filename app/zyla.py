from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .errors import AppError
from .models import CreditLedger, Tenant
from .security import constant_time_equal


@dataclass(frozen=True)
class ZylaQuery:
    """Simple, marketplace-friendly representation of one lead-search request."""

    countries: list[str]
    industries: list[str]
    job_titles: list[str]
    technologies: list[str]
    employee_min: int | None
    employee_max: int | None
    lead_score_min: int | None
    limit: int


def authorize_zyla_request(request: Request) -> None:
    """Authenticate the request using the header Zyla documents for API consumers."""
    if not settings.zyla_enabled:
        raise AppError("NOT_FOUND", "The Zyla endpoint is disabled.", 404)
    if settings.zyla_auth_mode == "public":
        return
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or not settings.zyla_shared_token:
        raise AppError("UNAUTHORIZED", "A Bearer API key is required.", 401)
    if not constant_time_equal(token, settings.zyla_shared_token):
        raise AppError("UNAUTHORIZED", "The API key is invalid.", 401)


def ensure_zyla_tenant(db: Session) -> Tenant:
    """Create the isolated accounting tenant used by the marketplace adapter once."""
    tenant = db.scalar(select(Tenant).where(Tenant.id == settings.zyla_tenant_id))
    if tenant is not None:
        if tenant.slug != settings.zyla_tenant_slug:
            raise AppError("CONFIGURATION_ERROR", "ZYLA_TENANT_ID is already assigned to another tenant.", 500)
        if tenant.status != "ACTIVE":
            raise AppError("TENANT_SUSPENDED", "The marketplace tenant is not active.", 403)
        return tenant

    conflicting_slug = db.scalar(select(Tenant).where(Tenant.slug == settings.zyla_tenant_slug))
    if conflicting_slug is not None:
        raise AppError("CONFIGURATION_ERROR", "ZYLA_TENANT_SLUG is already assigned to another tenant.", 500)

    tenant = Tenant(
        id=settings.zyla_tenant_id,
        name=settings.zyla_tenant_name,
        slug=settings.zyla_tenant_slug,
        credits_balance=settings.zyla_default_credits,
    )
    db.add(tenant)
    db.flush()
    db.add(
        CreditLedger(
            tenant_id=tenant.id,
            amount=settings.zyla_default_credits,
            reason="zyla_marketplace_reserve",
            reference_id=f"zyla-reserve:{tenant.id}",
            metadata_json={"purpose": "marketplace_usage_account"},
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("CONFIGURATION_ERROR", "The marketplace tenant could not be provisioned safely.", 500) from exc
    db.refresh(tenant)
    return tenant


def ensure_zyla_tenant_id() -> str:
    """Provision or validate the marketplace accounting tenant off the async event loop."""
    with SessionLocal() as db:
        return ensure_zyla_tenant(db).id


def _csv_values(values: str | None, *, field: str, maximum: int) -> list[str]:
    if not values:
        return []
    items = [item.strip() for item in values.split(",") if item.strip()]
    if len(items) > maximum:
        raise AppError("INVALID_REQUEST", f"{field} contains too many values.", 422)
    if any(len(item) > 100 for item in items):
        raise AppError("INVALID_REQUEST", f"{field} contains a value that is too long.", 422)
    return items


def parse_zyla_query(
    *,
    countries: str | None,
    industries: str | None,
    job_titles: str | None,
    technologies: str | None,
    employee_min: int | None,
    employee_max: int | None,
    lead_score_min: int | None,
    limit: int,
) -> ZylaQuery:
    if employee_min is not None and employee_max is not None and employee_min > employee_max:
        raise AppError("INVALID_REQUEST", "employee_min must be less than or equal to employee_max.", 422)
    if employee_min is not None and employee_min < 0:
        raise AppError("INVALID_REQUEST", "employee_min must be non-negative.", 422)
    if employee_max is not None and employee_max < 0:
        raise AppError("INVALID_REQUEST", "employee_max must be non-negative.", 422)
    if lead_score_min is not None and not 0 <= lead_score_min <= 100:
        raise AppError("INVALID_REQUEST", "lead_score_min must be between 0 and 100.", 422)
    if limit < 1 or limit > settings.zyla_max_limit:
        raise AppError("INVALID_REQUEST", f"limit must be between 1 and {settings.zyla_max_limit}.", 422)
    return ZylaQuery(
        countries=_csv_values(countries, field="countries", maximum=20),
        industries=_csv_values(industries, field="industries", maximum=20),
        job_titles=_csv_values(job_titles, field="job_titles", maximum=50),
        technologies=_csv_values(technologies, field="technologies", maximum=50),
        employee_min=employee_min,
        employee_max=employee_max,
        lead_score_min=lead_score_min,
        limit=limit,
    )


def query_to_payload(query: ZylaQuery):
    """Build the internal request model after FastAPI has validated scalar query values."""
    from .schemas import LeadSearchRequest

    return LeadSearchRequest(
        filters={
            "countries": query.countries,
            "industries": query.industries,
            "job_titles": query.job_titles,
            "technologies": query.technologies,
            "employee_count": (
                {"min": query.employee_min, "max": query.employee_max}
                if query.employee_min is not None or query.employee_max is not None
                else None
            ),
            "lead_score_min": query.lead_score_min,
        },
        include=["company", "contact", "scores", "quality", "provenance"],
        limit=query.limit,
    )


def zyla_metadata() -> dict[str, str]:
    """Metadata used by the listing and health checks; it contains no credentials."""
    synthetic = settings.provider_mode == "synthetic"
    return {
        "edition": "zyla-hosted-api",
        "owner": "Sadeq Obaid",
        "provider": settings.provider_name if not synthetic else "synthetic_provider",
        "provider_mode": settings.provider_mode,
        "data_source": (
            "Built-in deterministic fixture records; development and demonstrations only."
            if synthetic
            else settings.provider_data_source
        ),
        "data_policy": "SYNTHETIC_DEMO_ONLY" if synthetic else settings.provider_use_policy,
        "pricing_model": settings.pricing_model,
        "free_trial_credits": str(settings.free_trial_credits),
        "authentication": "Authorization: Bearer <Zyla API key>",
        "endpoint_method": "GET or POST",
    }
