from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import hmac
import logging
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, get_db
from .errors import AppError
from .jobs import submit_job
from .models import ApiKey, BackgroundJob, BillingEvent, CreditLedger, DataSubjectRequest, Search, Suppression, Tenant, UsageRecord, User
from .privacy import hash_subject
from .providers import build_provider
from .queue import RedisJobQueue
from .idempotency import (
    IdempotencyConflict,
    IdempotencyHit,
    IdempotencyInProgress,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
)
from .rate_limit import InMemoryRateLimiter, RateLimitExceeded, RedisRateLimiter
from .schemas import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    ErrorResponse,
    LeadResponse,
    LeadSearchRequest,
    LeadSearchResponse,
    LoginRequest,
    BillingWebhookResponse,
    DataSubjectRequestCreate,
    DataSubjectRequestResponse,
    JobResponse,
    SuppressionRequest,
    SuppressionResponse,
    MeResponse,
    RegisterRequest,
    TenantResponse,
    TokenResponse,
    UsageResponse,
    UsageSummaryResponse,
    UserResponse,
)
from .security import (
    AuthContext,
    AuthenticationError,
    AuthorizationError,
    create_access_token,
    constant_time_equal,
    create_api_key,
    get_auth_context,
    hash_password,
    require_scope,
    verify_password,
)
from .search_service import execute_lead_search
from .zyla import authorize_zyla_request, ensure_zyla_tenant, parse_zyla_query, query_to_payload, zyla_metadata


logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("lead_generation_api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start only approved development conveniences; production uses an explicit migration job."""
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    yield
    await limiter.close()
    await idempotency_store.close()
    await job_queue.close()


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "Hosted B2B lead discovery API edition for marketplace distribution. "
        "Provider rights, data status, and synthetic/demo restrictions are deployment-dependent."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
)

provider = build_provider(
    settings.provider_mode,
    settings.provider_url,
    settings.provider_token,
    settings.provider_timeout_seconds,
)
limiter = (
    RedisRateLimiter(settings.redis_url, settings.rate_limit_per_minute)
    if settings.rate_limit_backend == "redis"
    else InMemoryRateLimiter(settings.rate_limit_per_minute)
)
idempotency_store = (
    RedisIdempotencyStore(settings.redis_url, settings.idempotency_ttl_seconds)
    if settings.idempotency_backend == "redis"
    else InMemoryIdempotencyStore(settings.idempotency_ttl_seconds)
)
job_queue = RedisJobQueue(
    settings.redis_url,
    settings.queue_stream,
    settings.queue_group,
    settings.queue_consumer,
    settings.queue_block_ms,
)
request_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a bounded correlation ID and log failures without logging credentials or payloads."""
    supplied = request.headers.get("X-Request-Id", "")
    request_id = supplied if request_id_pattern.fullmatch(supplied) else f"req_{uuid4().hex}"
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "unhandled request failure",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        raise
    response.headers["X-Request-Id"] = request_id
    response.headers["X-API-Version"] = "v1"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Response-Time-Ms"] = str(round((time.perf_counter() - started) * 1000))
    return response


def current_request_id(request: Request) -> str:
    return request.state.request_id


def error_payload(request: Request, error: AppError) -> dict:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": current_request_id(request),
            "details": error.details,
        }
    }


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_payload(request, exc))


@app.exception_handler(AuthenticationError)
async def auth_error_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=error_payload(request, AppError("UNAUTHORIZED", str(exc), 401)),
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.exception_handler(AuthorizationError)
async def authorization_error_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content=error_payload(request, AppError("FORBIDDEN", str(exc), 403)),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = {"fields": [str(item.get("loc", [])) for item in exc.errors()]}
    return JSONResponse(
        status_code=422,
        content=error_payload(request, AppError("INVALID_REQUEST", "Request validation failed.", 422, details)),
    )


@app.get("/health/live")
def live(request: Request) -> dict[str, str]:
    return {"status": "ok", "request_id": current_request_id(request)}


@app.get("/health/ready")
def ready(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(select(1))
    except Exception as exc:  # pragma: no cover - exercised by deployment environment
        raise AppError("NOT_READY", "Required dependencies are unavailable.", 503) from exc
    return {"status": "ok", "request_id": current_request_id(request)}


@app.get("/health/zyla")
def zyla_health(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """Unauthenticated marketplace verification endpoint with no customer or secret data."""
    if not settings.zyla_enabled:
        raise AppError("NOT_FOUND", "The Zyla edition is disabled.", 404)
    try:
        db.execute(select(1))
    except Exception as exc:  # pragma: no cover - deployment dependency failure
        raise AppError("NOT_READY", "The hosted API is not ready.", 503) from exc
    return {"status": "ok", "request_id": current_request_id(request), **zyla_metadata()}


@app.get("/metrics")
def metrics(
    token: str | None = Header(default=None, alias="X-Metrics-Token"),
    db: Session = Depends(get_db),
) -> Response:
    """Expose minimal Prometheus-compatible counters behind an optional shared token."""
    if not settings.metrics_enabled:
        raise AppError("NOT_FOUND", "Metrics are disabled.", 404)
    if settings.metrics_token and (token is None or not constant_time_equal(token, settings.metrics_token)):
        raise AuthenticationError("metrics authentication required")
    total_requests = db.scalar(select(func.count(UsageRecord.id))) or 0
    total_credits = db.scalar(select(func.coalesce(func.sum(UsageRecord.credits_used), 0))) or 0
    queued_jobs = db.scalar(select(func.count(BackgroundJob.id)).where(BackgroundJob.status.in_(["QUEUED", "RUNNING"]))) or 0
    body = "\n".join(
        [
            "# HELP lead_api_requests_total Total recorded API requests.",
            "# TYPE lead_api_requests_total counter",
            f"lead_api_requests_total {int(total_requests)}",
            "# HELP lead_api_credits_used_total Total credits recorded as used.",
            "# TYPE lead_api_credits_used_total counter",
            f"lead_api_credits_used_total {int(total_credits)}",
            "# HELP lead_api_jobs_in_progress Number of queued or running jobs.",
            "# TYPE lead_api_jobs_in_progress gauge",
            f"lead_api_jobs_in_progress {int(queued_jobs)}",
            "",
        ]
    )
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.post("/api/v1/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    slug = re.sub(r"[^a-z0-9]+", "-", payload.tenant_name.lower()).strip("-") or "tenant"
    if db.scalar(select(Tenant).where(Tenant.slug == slug)) is not None:
        slug = f"{slug}-{uuid4().hex[:8]}"
    tenant = Tenant(name=payload.tenant_name, slug=slug, credits_balance=settings.default_credits)
    user = User(tenant=tenant, email=payload.email, password_hash=hash_password(payload.password), role="TENANT_ADMIN")
    db.add_all([tenant, user])
    db.flush()
    db.add(
        CreditLedger(
            tenant_id=tenant.id,
            amount=settings.default_credits,
            reason="registration_grant",
            reference_id=f"registration:{tenant.id}",
            metadata_json={"email": user.email},
        )
    )
    db.commit()
    db.refresh(user)
    access_token, expires_in = create_access_token(user)
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@app.post("/api/v1/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError("UNAUTHORIZED", "Invalid email or password.", 401)
    access_token, expires_in = create_access_token(user)
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@app.get("/api/v1/users/me", response_model=MeResponse)
def me(context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> MeResponse:
    user = db.scalar(select(User).where(User.id == context.actor_id, User.tenant_id == context.tenant_id)) if context.actor_id else None
    tenant = db.get(Tenant, context.tenant_id)
    if tenant is None:
        raise AppError("TENANT_NOT_FOUND", "Tenant was not found.", 404)
    if user is None:
        user_response = UserResponse(id="api-client", tenant_id=tenant.id, email="", role=context.role)
    else:
        user_response = UserResponse(id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role)
    return MeResponse(
        user=user_response,
        tenant=TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            status=tenant.status,
            credits_remaining=tenant.credits_balance,
        ),
    )


@app.post("/api/v1/api-keys", response_model=CreateApiKeyResponse, status_code=status.HTTP_201_CREATED)
def create_key(
    payload: CreateApiKeyRequest,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> CreateApiKeyResponse:
    require_scope(context, "keys:write")
    raw_key, visible_prefix, digest = create_api_key(payload.environment)
    key = ApiKey(
        tenant_id=context.tenant_id,
        name=payload.name,
        environment=payload.environment,
        key_prefix=visible_prefix,
        secret_hash=digest,
        scopes=payload.scopes,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return CreateApiKeyResponse(
        id=key.id,
        name=key.name,
        environment=key.environment,
        key_prefix=key.key_prefix,
        scopes=key.scopes,
        status=key.status,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        secret=raw_key,
    )


@app.get("/api/v1/api-keys", response_model=list[ApiKeyResponse])
def list_keys(context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> list[ApiKeyResponse]:
    require_scope(context, "keys:read")
    keys = db.scalars(select(ApiKey).where(ApiKey.tenant_id == context.tenant_id).order_by(ApiKey.created_at.desc())).all()
    return [ApiKeyResponse.model_validate(key, from_attributes=True) for key in keys]


@app.delete("/api/v1/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def revoke_key(key_id: str, context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> Response:
    require_scope(context, "keys:write")
    key = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == context.tenant_id))
    if key is None:
        raise AppError("NOT_FOUND", "API key was not found.", 404)
    key.status = "REVOKED"
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/v1/leads/search", response_model=LeadSearchResponse)
async def search_leads(
    request: Request,
    payload: LeadSearchRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> LeadSearchResponse:
    """Search approved provider data with replay-safe billing and tenant isolation."""
    require_scope(context, "leads:read")
    try:
        await limiter.check(f"tenant:{context.tenant_id}")
    except RateLimitExceeded as exc:
        raise AppError("RATE_LIMITED", "Rate limit exceeded.", 429, {"retry_after": exc.retry_after}) from exc

    request_id = current_request_id(request)
    request_fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    idempotency_scope = None
    if idempotency_key:
        idempotency_scope = f"{context.tenant_id}:{context.api_key_id or context.actor_id}:{idempotency_key}"
        try:
            cached = await idempotency_store.get(idempotency_scope, request_fingerprint)
        except IdempotencyConflict as exc:
            raise AppError("IDEMPOTENCY_CONFLICT", "Idempotency key was reused with a different request.", 409) from exc
        except IdempotencyInProgress as exc:
            raise AppError("IDEMPOTENCY_IN_PROGRESS", "The original request is still being completed; retry shortly.", 409) from exc
        if cached is not None:
            return LeadSearchResponse.model_validate(cached.response_body)
        try:
            reserved = await idempotency_store.reserve(idempotency_scope, request_fingerprint)
        except IdempotencyConflict as exc:
            raise AppError("IDEMPOTENCY_CONFLICT", "Idempotency key was reused with a different request.", 409) from exc
        if not reserved:
            raise AppError("IDEMPOTENCY_IN_PROGRESS", "The original request is still being completed; retry shortly.", 409)

    try:
        response = await execute_lead_search(
            db=db,
            tenant_id=context.tenant_id,
            api_key_id=context.api_key_id,
            request_id=request_id,
            request_fingerprint=request_fingerprint,
            payload=payload,
            provider=provider,
        )
        if idempotency_scope:
            await idempotency_store.put(
                idempotency_scope,
                IdempotencyHit(fingerprint=request_fingerprint, response_body=response.model_dump(mode="json")),
            )
        return response
    except Exception:
        if idempotency_scope:
            await idempotency_store.delete(idempotency_scope)
            logger.exception("lead search failed", extra={"request_id": request_id, "tenant_id": context.tenant_id})
        db.rollback()
        raise


async def _execute_zyla_search(
    request: Request,
    payload: LeadSearchRequest,
    idempotency_key: str | None,
    db: Session,
) -> LeadSearchResponse:
    """Run one marketplace request against a dedicated accounting tenant."""
    authorize_zyla_request(request)
    tenant = ensure_zyla_tenant(db)
    try:
        await limiter.check(f"zyla:{tenant.id}")
    except RateLimitExceeded as exc:
        raise AppError("RATE_LIMITED", "Rate limit exceeded.", 429, {"retry_after": exc.retry_after}) from exc

    request_id = current_request_id(request)
    request_fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    idempotency_scope = f"zyla:{tenant.id}:{idempotency_key}" if idempotency_key else None
    if idempotency_scope:
        try:
            cached = await idempotency_store.get(idempotency_scope, request_fingerprint)
        except IdempotencyConflict as exc:
            raise AppError("IDEMPOTENCY_CONFLICT", "Idempotency key was reused with a different request.", 409) from exc
        except IdempotencyInProgress as exc:
            raise AppError("IDEMPOTENCY_IN_PROGRESS", "The original request is still being completed; retry shortly.", 409) from exc
        if cached is not None:
            return LeadSearchResponse.model_validate(cached.response_body)
        try:
            reserved = await idempotency_store.reserve(idempotency_scope, request_fingerprint)
        except IdempotencyConflict as exc:
            raise AppError("IDEMPOTENCY_CONFLICT", "Idempotency key was reused with a different request.", 409) from exc
        if not reserved:
            raise AppError("IDEMPOTENCY_IN_PROGRESS", "The original request is still being completed; retry shortly.", 409)

    try:
        response = await execute_lead_search(
            db=db,
            tenant_id=tenant.id,
            api_key_id=None,
            request_id=request_id,
            request_fingerprint=request_fingerprint,
            payload=payload,
            provider=provider,
        )
        if idempotency_scope:
            await idempotency_store.put(
                idempotency_scope,
                IdempotencyHit(fingerprint=request_fingerprint, response_body=response.model_dump(mode="json")),
            )
        return response
    except Exception:
        if idempotency_scope:
            await idempotency_store.delete(idempotency_scope)
        db.rollback()
        logger.exception("Zyla marketplace search failed", extra={"request_id": request_id, "tenant_id": tenant.id})
        raise


@app.get("/api/v1/zyla/leads/search", response_model=LeadSearchResponse)
async def zyla_search_get(
    request: Request,
    countries: str | None = Query(default=None, description="Comma-separated ISO country codes, for example SA,AE."),
    industries: str | None = Query(default=None, description="Comma-separated industry values."),
    job_titles: str | None = Query(default=None, description="Comma-separated job titles."),
    technologies: str | None = Query(default=None, description="Comma-separated technology names."),
    employee_min: int | None = Query(default=None, ge=0),
    employee_max: int | None = Query(default=None, ge=0),
    lead_score_min: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=10, ge=1, le=100),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> LeadSearchResponse:
    """Zyla-friendly GET contract using simple query parameters and bearer authentication."""
    query = parse_zyla_query(
        countries=countries,
        industries=industries,
        job_titles=job_titles,
        technologies=technologies,
        employee_min=employee_min,
        employee_max=employee_max,
        lead_score_min=lead_score_min,
        limit=limit,
    )
    return await _execute_zyla_search(request, query_to_payload(query), idempotency_key, db)


@app.post("/api/v1/zyla/leads/search", response_model=LeadSearchResponse)
async def zyla_search_post(
    request: Request,
    payload: LeadSearchRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> LeadSearchResponse:
    """JSON variant for Zyla configurations that support POST request bodies."""
    if payload.limit > settings.zyla_max_limit:
        raise AppError("INVALID_REQUEST", f"limit must be at most {settings.zyla_max_limit}.", 422)
    return await _execute_zyla_search(request, payload, idempotency_key, db)


@app.post("/api/v1/billing/webhook", response_model=BillingWebhookResponse)
async def billing_webhook(
    request: Request,
    signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
    db: Session = Depends(get_db),
) -> BillingWebhookResponse:
    """Verify a generic HMAC webhook and grant credits once per provider event.

    Payment processors have different signature schemes. This endpoint deliberately defines a
    small adapter contract: the caller sends `X-Webhook-Signature: sha256=<HMAC-SHA256>` and a
    JSON body containing provider, event_id, event_type, tenant_id, and credits. A real payment
    integration must adapt its processor-specific verification into this contract.
    """
    if not settings.billing_webhook_secret:
        raise AppError("BILLING_DISABLED", "Billing webhooks are not configured.", 503)
    body = await request.body()
    expected = "sha256=" + hmac.new(settings.billing_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if signature is None or not hmac.compare_digest(signature, expected):
        raise AuthenticationError("invalid webhook signature")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise AppError("INVALID_WEBHOOK", "Webhook body must be valid JSON.", 422) from exc
    if not isinstance(payload, dict):
        raise AppError("INVALID_WEBHOOK", "Webhook body must be a JSON object.", 422)
    provider_name = str(payload.get("provider") or "generic")[:80]
    event_id = str(payload.get("event_id") or "")[:200]
    event_type = str(payload.get("event_type") or "")[:100]
    tenant_id = str(payload.get("tenant_id") or "")[:36]
    try:
        credits = int(payload.get("credits") or 0)
    except (TypeError, ValueError) as exc:
        raise AppError("INVALID_WEBHOOK", "credits must be an integer.", 422) from exc
    if not event_id or not event_type or not tenant_id or credits <= 0 or credits > 1_000_000:
        raise AppError("INVALID_WEBHOOK", "Webhook event fields are invalid.", 422)
    existing = db.scalar(select(BillingEvent).where(BillingEvent.provider == provider_name, BillingEvent.event_id == event_id))
    if existing is not None:
        return BillingWebhookResponse(event_id=event_id, status="DUPLICATE", credits_granted=existing.credits_granted)
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.status != "ACTIVE":
        raise AppError("TENANT_NOT_FOUND", "Tenant was not found or is inactive.", 404)
    db.add(
        BillingEvent(
            provider=provider_name,
            event_id=event_id,
            tenant_id=tenant_id,
            event_type=event_type,
            credits_granted=credits,
        )
    )
    tenant.credits_balance += credits
    db.add(
        CreditLedger(
            tenant_id=tenant_id,
            amount=credits,
            reason="billing_grant",
            reference_id=f"billing:{provider_name}:{event_id}",
            metadata_json={"event_type": event_type},
        )
    )
    db.commit()
    return BillingWebhookResponse(event_id=event_id, status="PROCESSED", credits_granted=credits)


@app.post("/api/v1/privacy/suppressions", response_model=SuppressionResponse, status_code=status.HTTP_201_CREATED)
def create_suppression(
    payload: SuppressionRequest,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> SuppressionResponse:
    """Store a tenant-scoped suppression hash without retaining the raw subject identifier."""
    require_scope(context, "privacy:write")
    subject_hash = hash_subject(payload.subject)
    existing = db.scalar(
        select(Suppression).where(
            Suppression.tenant_id == context.tenant_id,
            Suppression.subject_type == payload.subject_type,
            Suppression.subject_hash == subject_hash,
        )
    )
    if existing is not None:
        return SuppressionResponse(id=existing.id, subject_type=existing.subject_type, created_at=existing.created_at)
    entry = Suppression(
        tenant_id=context.tenant_id,
        subject_type=payload.subject_type,
        subject_hash=subject_hash,
        reason=payload.reason,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return SuppressionResponse(id=entry.id, subject_type=entry.subject_type, created_at=entry.created_at)


@app.post("/api/v1/privacy/requests", response_model=DataSubjectRequestResponse, status_code=status.HTTP_202_ACCEPTED)
def create_privacy_request(
    payload: DataSubjectRequestCreate,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> DataSubjectRequestResponse:
    """Record an auditable privacy request for an operator/provider workflow."""
    require_scope(context, "privacy:write")
    entry = DataSubjectRequest(
        tenant_id=context.tenant_id,
        request_type=payload.request_type,
        subject_hash=hash_subject(payload.subject),
        status="RECEIVED",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return DataSubjectRequestResponse.model_validate(entry, from_attributes=True)


@app.get("/api/v1/privacy/requests", response_model=list[DataSubjectRequestResponse])
def list_privacy_requests(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[DataSubjectRequestResponse]:
    """List tenant-scoped privacy requests without returning raw subject identifiers."""
    require_scope(context, "privacy:read")
    entries = db.scalars(
        select(DataSubjectRequest).where(DataSubjectRequest.tenant_id == context.tenant_id).order_by(DataSubjectRequest.created_at.desc())
    ).all()
    return [DataSubjectRequestResponse.model_validate(entry, from_attributes=True) for entry in entries]


@app.post("/api/v1/jobs/{kind}", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    kind: str,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> JobResponse:
    """Submit an allow-listed operational job to Redis Streams."""
    require_scope(context, "jobs:write")
    if kind not in {"retention_cleanup", "credit_reconciliation"}:
        raise AppError("INVALID_JOB", "Unsupported job kind.", 422)
    try:
        job = await submit_job(db, job_queue, kind, context.tenant_id, {})
    except Exception as exc:
        raise AppError("QUEUE_UNAVAILABLE", "The background queue is unavailable.", 503) from exc
    return JobResponse.model_validate(job, from_attributes=True)


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> JobResponse:
    """Return only the authenticated tenant's job metadata."""
    require_scope(context, "jobs:read")
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id, BackgroundJob.tenant_id == context.tenant_id))
    if job is None:
        raise AppError("NOT_FOUND", "Job was not found.", 404)
    return JobResponse.model_validate(job, from_attributes=True)


@app.get("/api/v1/usage", response_model=UsageSummaryResponse)
def usage(request: Request, context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)) -> UsageSummaryResponse:
    require_scope(context, "usage:read")
    total_requests = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.tenant_id == context.tenant_id)) or 0
    total_credits = db.scalar(select(func.coalesce(func.sum(UsageRecord.credits_used), 0)).where(UsageRecord.tenant_id == context.tenant_id)) or 0
    total_leads = db.scalar(select(func.coalesce(func.sum(Search.result_count), 0)).where(Search.tenant_id == context.tenant_id)) or 0
    now = datetime.now(timezone.utc)
    return UsageSummaryResponse(
        request_id=current_request_id(request),
        period={"from": "all", "to": now.isoformat()},
        totals={"requests": int(total_requests), "credits_used": int(total_credits), "leads_returned": int(total_leads)},
    )
