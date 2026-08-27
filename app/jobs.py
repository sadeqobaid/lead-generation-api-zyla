from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import BackgroundJob, CreditLedger, IdempotencyRecord, Search, UsageRecord
from .queue import RedisJobQueue


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _create_job_record(kind: str, tenant_id: str | None, payload: dict[str, Any]) -> BackgroundJob:
    with SessionLocal() as db:
        job = BackgroundJob(tenant_id=tenant_id, kind=kind, payload=payload, status="QUEUED")
        db.add(job)
        db.commit()
        db.refresh(job)
        return job


def _mark_job_failed(job_id: str, error_type: str) -> None:
    with SessionLocal() as db:
        job = db.get(BackgroundJob, job_id)
        if job is not None:
            job.status = "FAILED"
            job.last_error = f"queue submission failed: {error_type}"
            db.commit()


async def submit_job(queue: RedisJobQueue, kind: str, tenant_id: str | None, payload: dict[str, Any]) -> BackgroundJob:
    """Persist a job before enqueueing it without blocking the async request loop."""
    job = await run_in_threadpool(_create_job_record, kind, tenant_id, payload)
    try:
        await queue.enqueue(job.id, job.kind)
    except Exception as exc:
        await run_in_threadpool(_mark_job_failed, job.id, type(exc).__name__)
        raise
    return job


def run_retention_cleanup(db: Session) -> dict[str, int]:
    """Delete only operational records beyond the configured retention window."""
    cutoff = utcnow() - timedelta(days=settings.data_retention_days)
    usage_deleted = db.execute(delete(UsageRecord).where(UsageRecord.created_at < cutoff)).rowcount or 0
    searches_deleted = db.execute(delete(Search).where(Search.created_at < cutoff)).rowcount or 0
    idempotency_deleted = db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < utcnow())).rowcount or 0
    db.commit()
    return {
        "usage_records_deleted": int(usage_deleted),
        "searches_deleted": int(searches_deleted),
        "idempotency_records_deleted": int(idempotency_deleted),
    }


def run_credit_reconciliation(db: Session) -> dict[str, int]:
    """Find tenants whose balance differs from their immutable credit-ledger sum."""
    mismatches = 0
    tenants_checked = 0
    rows = db.execute(select(CreditLedger.tenant_id, func.coalesce(func.sum(CreditLedger.amount), 0)).group_by(CreditLedger.tenant_id)).all()
    for tenant_id, ledger_balance in rows:
        tenants_checked += 1
        from .models import Tenant

        tenant = db.get(Tenant, tenant_id)
        if tenant is not None and tenant.credits_balance != int(ledger_balance):
            mismatches += 1
    return {"tenants_checked": tenants_checked, "mismatches": mismatches}


def execute_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    """Execute allow-listed job kinds; unknown kinds fail closed."""
    if job.kind == "retention_cleanup":
        return run_retention_cleanup(db)
    if job.kind == "credit_reconciliation":
        return run_credit_reconciliation(db)
    raise ValueError(f"unsupported job kind: {job.kind}")
