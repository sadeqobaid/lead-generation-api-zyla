from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    tenant_id: str
    email: str
    role: str


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    credits_remaining: int


class MeResponse(BaseModel):
    user: UserResponse
    tenant: TenantResponse


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    environment: Literal["test", "production"] = "test"
    scopes: list[str] = Field(min_length=1, max_length=20)

    @field_validator("scopes")
    @classmethod
    def clean_scopes(cls, values: list[str]) -> list[str]:
        cleaned = sorted({item.strip() for item in values if item.strip()})
        if not cleaned:
            raise ValueError("at least one non-empty scope is required")
        return cleaned


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    environment: str
    key_prefix: str
    scopes: list[str]
    status: str
    created_at: datetime
    last_used_at: datetime | None


class CreateApiKeyResponse(ApiKeyResponse):
    secret: str


class EmployeeRange(BaseModel):
    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "EmployeeRange":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("employee_count.min must be less than or equal to max")
        return self


class LeadFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countries: list[str] = Field(default_factory=list, max_length=20)
    industries: list[str] = Field(default_factory=list, max_length=20)
    employee_count: EmployeeRange | None = None
    job_titles: list[str] = Field(default_factory=list, max_length=50)
    technologies: list[str] = Field(default_factory=list, max_length=50)
    lead_score_min: int | None = Field(default=None, ge=0, le=100)


class SortRequest(BaseModel):
    field: Literal["lead_score", "company_name", "updated_at"] = "lead_score"
    direction: Literal["asc", "desc"] = "desc"


class LeadSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: LeadFilters
    include: list[Literal["company", "contact", "scores", "quality", "provenance"]] = Field(
        default_factory=lambda: ["company", "contact", "scores", "quality"], max_length=5
    )
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None
    sort: SortRequest = Field(default_factory=SortRequest)


class CompanyResponse(BaseModel):
    id: str
    name: str
    website: str | None
    domain: str | None
    industry: str | None
    country: str | None
    employee_count: int | None
    data_status: str


class EmailVerificationResponse(BaseModel):
    status: str
    checked_at: datetime | None = None


class ContactResponse(BaseModel):
    id: str
    first_name: str | None
    last_name: str | None
    job_title: str | None
    email: str | None
    data_status: str
    email_verification: EmailVerificationResponse | None = None


class ScoresResponse(BaseModel):
    lead_score: int
    score_category: str
    icp_score: int
    icp_match: bool
    breakdown: dict[str, float]
    rule_set_version: str


class QualityResponse(BaseModel):
    completeness: float
    accuracy_estimate: float | None
    freshness: float
    confidence: float
    source_count: int
    provenance_available: bool


class ProvenanceResponse(BaseModel):
    provider: str
    observed_at: datetime
    use_policy: str
    data_status: str
    data_source: str | None = None


class LeadResponse(BaseModel):
    id: str
    record_status: str
    company: CompanyResponse
    contact: ContactResponse | None
    scores: ScoresResponse
    quality: QualityResponse
    provenance: list[ProvenanceResponse]


class PaginationResponse(BaseModel):
    limit: int
    next_cursor: str | None
    has_more: bool


class UsageResponse(BaseModel):
    credits_reserved: int
    credits_used: int
    credits_remaining: int


class LeadSearchResponse(BaseModel):
    request_id: str
    data: list[LeadResponse]
    pagination: PaginationResponse
    usage: UsageResponse


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class UsageSummaryResponse(BaseModel):
    request_id: str
    period: dict[str, str]
    totals: dict[str, int]


class SuppressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["email", "domain", "company", "contact"]
    subject: str = Field(min_length=1, max_length=320)
    reason: str = Field(min_length=2, max_length=255)


class SuppressionResponse(BaseModel):
    id: str
    subject_type: str
    status: Literal["ACTIVE"] = "ACTIVE"
    created_at: datetime


class DataSubjectRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: Literal["access", "deletion", "rectification", "suppression"]
    subject: str = Field(min_length=1, max_length=320)


class DataSubjectRequestResponse(BaseModel):
    id: str
    request_type: str
    status: str
    created_at: datetime
    completed_at: datetime | None


class JobResponse(BaseModel):
    id: str
    kind: str
    status: str
    attempts: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    last_error: str | None


class BillingWebhookResponse(BaseModel):
    event_id: str
    status: Literal["PROCESSED", "DUPLICATE"]
    credits_granted: int
