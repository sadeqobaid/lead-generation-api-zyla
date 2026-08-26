from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import AppError
from .models import Company, Contact, CreditLedger, Lead, Search, Suppression, Tenant, UsageRecord
from .privacy import hash_subject
from .providers import LeadProvider
from .schemas import LeadResponse, LeadSearchRequest, LeadSearchResponse
from .services import normalize_domain, quality_for, score_provider_lead


def _get_or_create_company(db: Session, tenant_id: str, record: dict[str, Any]) -> Company:
    domain = normalize_domain(record.get("domain"))
    company = db.scalar(select(Company).where(Company.tenant_id == tenant_id, Company.canonical_domain == domain)) if domain else None
    if company is None:
        company = Company(
            tenant_id=tenant_id,
            name=record["name"],
            website=record.get("website"),
            canonical_domain=domain,
            industry=record.get("industry"),
            country=record.get("country"),
            employee_count=record.get("employee_count"),
            data_status=record.get("data_status", "SYNTHETIC"),
        )
        db.add(company)
        db.flush()
    return company


def _get_or_create_contact(db: Session, tenant_id: str, company_id: str, record: dict[str, Any] | None) -> Contact | None:
    if not record:
        return None
    contact = db.scalar(
        select(Contact).where(
            Contact.tenant_id == tenant_id,
            Contact.company_id == company_id,
            Contact.job_title == record.get("job_title"),
        )
    )
    if contact is None:
        contact = Contact(
            tenant_id=tenant_id,
            company_id=company_id,
            first_name=record.get("first_name"),
            last_name=record.get("last_name"),
            job_title=record.get("job_title"),
            email=record.get("email"),
            data_status=record.get("data_status", "SYNTHETIC"),
        )
        db.add(contact)
        db.flush()
    return contact


async def execute_lead_search(
    db: Session,
    tenant_id: str,
    api_key_id: str | None,
    request_id: str,
    request_fingerprint: str,
    payload: LeadSearchRequest,
    provider: LeadProvider,
) -> LeadSearchResponse:
    """Execute one search in a single database transaction.

    The tenant row is locked before checking and consuming credits. A negative credit ledger
    entry is written with the persisted search ID as its idempotent reference. This prevents
    concurrent requests from overspending a tenant balance when PostgreSQL is used.
    """
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.status != "ACTIVE":
        raise AppError("TENANT_SUSPENDED", "Tenant is not active.", 403)

    filters = payload.filters.model_dump(exclude_none=True)
    records = await provider.search(filters)
    def is_suppressed(record: Any) -> bool:
        candidates = [
            ("domain", record.company.get("domain")),
            ("company", record.company.get("name")),
        ]
        if record.contact:
            candidates.extend(
                [
                    ("email", record.contact.get("email")),
                    ("contact", " ".join(filter(None, [record.contact.get("first_name"), record.contact.get("last_name")]))),
                ]
            )
        for subject_type, subject in candidates:
            if not subject:
                continue
            subject_hash = hash_subject(subject)
            if db.scalar(
                select(Suppression.id).where(
                    Suppression.tenant_id == tenant_id,
                    Suppression.subject_type == subject_type,
                    Suppression.subject_hash == subject_hash,
                )
            ):
                return True
        return False

    records = [record for record in records if not is_suppressed(record)]
    scored = [score_provider_lead(record, filters) for record in records]
    min_score = payload.filters.lead_score_min
    if min_score is not None:
        scored = [item for item in scored if item.lead_score >= min_score]
    if payload.sort.field == "lead_score":
        scored.sort(key=lambda item: item.lead_score, reverse=payload.sort.direction == "desc")
    scored = scored[: payload.limit]
    credits_used = len(scored)
    if tenant.credits_balance < credits_used:
        raise AppError(
            "INSUFFICIENT_CREDITS",
            "The account does not have enough credits for this operation.",
            403,
            {"required": credits_used, "available": tenant.credits_balance},
        )

    started = time.perf_counter()
    response_data: list[LeadResponse] = []
    for item in scored:
        company = _get_or_create_company(db, tenant_id, item.source.company)
        contact = _get_or_create_contact(db, tenant_id, company.id, item.source.contact)
        lead = Lead(
            tenant_id=tenant_id,
            company_id=company.id,
            contact_id=contact.id if contact else None,
            lead_score=item.lead_score,
            icp_score=item.icp_score,
            score_breakdown=item.breakdown,
        )
        db.add(lead)
        db.flush()
        response_data.append(
            LeadResponse(
                id=lead.id,
                record_status=lead.status,
                company={
                    "id": company.id,
                    "name": company.name,
                    "website": company.website,
                    "domain": company.canonical_domain,
                    "industry": company.industry,
                    "country": company.country,
                    "employee_count": company.employee_count,
                    "data_status": company.data_status,
                },
                contact=(
                    {
                        "id": contact.id,
                        "first_name": contact.first_name,
                        "last_name": contact.last_name,
                        "job_title": contact.job_title,
                        "email": contact.email,
                        "data_status": contact.data_status,
                        "email_verification": None,
                    }
                    if contact
                    else None
                ),
                scores={
                    "lead_score": item.lead_score,
                    "score_category": "HIGH" if item.lead_score >= 70 else ("MEDIUM" if item.lead_score >= 40 else "LOW"),
                    "icp_score": item.icp_score,
                    "icp_match": item.icp_score >= 70,
                    "breakdown": item.breakdown,
                    "rule_set_version": "mvp_v1",
                },
                quality=quality_for(item.source),
                provenance=[
                    {
                        "provider": item.source.provider,
                        "observed_at": datetime.fromisoformat(item.source.observed_at),
                        "use_policy": item.source.use_policy,
                        "data_status": item.source.data_status,
                    }
                ],
            )
        )

    tenant.credits_balance -= credits_used
    search = Search(
        tenant_id=tenant_id,
        request_hash=request_fingerprint,
        filters=filters,
        result_count=len(response_data),
        credits_used=credits_used,
    )
    db.add(search)
    db.flush()
    if credits_used:
        db.add(
            CreditLedger(
                tenant_id=tenant_id,
                amount=-credits_used,
                reason="lead_search",
                reference_id=f"search:{search.id}",
                metadata_json={"request_id": request_id, "provider": provider.code},
            )
        )
    db.add(
        UsageRecord(
            tenant_id=tenant_id,
            api_key_id=api_key_id,
            endpoint="POST /api/v1/leads/search",
            request_id=request_id,
            credits_used=credits_used,
            response_status=200,
            provider=provider.code,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
    )
    db.commit()
    response = LeadSearchResponse(
        request_id=request_id,
        data=response_data,
        pagination={"limit": payload.limit, "next_cursor": None, "has_more": False},
        usage={
            "credits_reserved": credits_used,
            "credits_used": credits_used,
            "credits_remaining": tenant.credits_balance,
        },
    )
    return response
