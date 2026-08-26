from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in _TRUE_VALUES


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, default).split(",") if value.strip())


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables.

    Development defaults are intentionally convenient for local SQLite testing. A production
    process must provide strong secrets, PostgreSQL, Redis coordination, and a non-synthetic
    provider before it can start.
    """

    app_name: str = os.getenv("APP_NAME", "Lead Generation API")
    environment: str = os.getenv("APP_ENV", "development").strip().lower()
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./lead_generation.db")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-change-me")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "30"))
    api_key_pepper: str = os.getenv("API_KEY_PEPPER", "dev-only-api-key-pepper")
    default_credits: int = int(os.getenv("DEFAULT_CREDITS", "100"))
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    rate_limit_backend: str = os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower()
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    cors_origins: tuple[str, ...] = _csv("CORS_ORIGINS", "http://localhost:3000")
    auto_create_schema: bool = _bool("AUTO_CREATE_SCHEMA", True)
    idempotency_backend: str = os.getenv("IDEMPOTENCY_BACKEND", "memory").strip().lower()
    idempotency_ttl_seconds: int = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400"))
    queue_backend: str = os.getenv("QUEUE_BACKEND", "redis").strip().lower()
    queue_stream: str = os.getenv("QUEUE_STREAM", "lead-generation-jobs")
    queue_group: str = os.getenv("QUEUE_GROUP", "lead-generation-workers")
    queue_consumer: str = os.getenv("QUEUE_CONSUMER", os.getenv("HOSTNAME", "worker"))
    queue_block_ms: int = int(os.getenv("QUEUE_BLOCK_MS", "5000"))
    provider_mode: str = os.getenv("PROVIDER_MODE", "synthetic").strip().lower()
    provider_url: str = os.getenv("PROVIDER_URL", "")
    provider_token: str = os.getenv("PROVIDER_TOKEN", "")
    provider_timeout_seconds: float = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "15"))
    billing_webhook_secret: str = os.getenv("BILLING_WEBHOOK_SECRET", "")
    allow_synthetic_in_production: bool = _bool("ALLOW_SYNTHETIC_IN_PRODUCTION", False)
    metrics_enabled: bool = _bool("METRICS_ENABLED", True)
    metrics_token: str = os.getenv("METRICS_TOKEN", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    data_retention_days: int = int(os.getenv("DATA_RETENTION_DAYS", "365"))
    zyla_enabled: bool = _bool("ZYLA_ENABLED", False)
    zyla_auth_mode: str = os.getenv("ZYLA_AUTH_MODE", "shared_token").strip().lower()
    zyla_shared_token: str = os.getenv("ZYLA_SHARED_TOKEN", "")
    zyla_tenant_id: str = os.getenv("ZYLA_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    zyla_tenant_name: str = os.getenv("ZYLA_TENANT_NAME", "Zyla Marketplace Consumers")
    zyla_tenant_slug: str = os.getenv("ZYLA_TENANT_SLUG", "zyla-marketplace")
    zyla_default_credits: int = int(os.getenv("ZYLA_DEFAULT_CREDITS", "1000000"))
    zyla_max_limit: int = int(os.getenv("ZYLA_MAX_LIMIT", "25"))
    zyla_allow_synthetic: bool = _bool("ZYLA_ALLOW_SYNTHETIC", False)

    def validate(self) -> None:
        """Fail fast on unsafe production settings instead of silently starting an MVP."""
        if self.access_token_minutes <= 0:
            raise ValueError("ACCESS_TOKEN_MINUTES must be positive")
        if self.default_credits < 0 or self.rate_limit_per_minute <= 0:
            raise ValueError("DEFAULT_CREDITS and RATE_LIMIT_PER_MINUTE are invalid")
        if self.idempotency_ttl_seconds < 60:
            raise ValueError("IDEMPOTENCY_TTL_SECONDS must be at least 60")
        if self.zyla_enabled:
            if self.zyla_auth_mode not in {"shared_token", "public"}:
                raise ValueError("ZYLA_AUTH_MODE must be shared_token or public")
            if not self.zyla_tenant_id or len(self.zyla_tenant_id) > 36:
                raise ValueError("ZYLA_TENANT_ID must be a non-empty value of at most 36 characters")
            if not self.zyla_tenant_slug or len(self.zyla_tenant_slug) > 120:
                raise ValueError("ZYLA_TENANT_SLUG must be a non-empty value of at most 120 characters")
            if self.zyla_default_credits <= 0 or self.zyla_max_limit <= 0 or self.zyla_max_limit > 100:
                raise ValueError("ZYLA_DEFAULT_CREDITS and ZYLA_MAX_LIMIT are invalid")
            if self.environment in {"production", "staging"}:
                if self.zyla_auth_mode != "shared_token" or len(self.zyla_shared_token) < 32:
                    raise ValueError("enabled hosted Zyla APIs require ZYLA_AUTH_MODE=shared_token and a strong ZYLA_SHARED_TOKEN")
                if self.provider_mode == "synthetic" and not self.zyla_allow_synthetic:
                    raise ValueError("hosted Zyla APIs require a licensed non-synthetic provider")
        if self.environment in {"production", "staging"}:
            forbidden = {"dev-only-change-me", "dev-only-api-key-pepper", "replace-with-a-long-random-secret", "replace-with-a-long-random-pepper"}
            if self.jwt_secret in forbidden or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be a strong production secret")
            if self.api_key_pepper in forbidden or len(self.api_key_pepper) < 32:
                raise ValueError("API_KEY_PEPPER must be a strong production secret")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("production requires PostgreSQL")
            if self.rate_limit_backend != "redis":
                raise ValueError("production requires RATE_LIMIT_BACKEND=redis")
            if self.idempotency_backend != "redis":
                raise ValueError("production requires IDEMPOTENCY_BACKEND=redis")
            if self.queue_backend != "redis":
                raise ValueError("production requires QUEUE_BACKEND=redis")
            if self.auto_create_schema:
                raise ValueError("production requires AUTO_CREATE_SCHEMA=false and an explicit migration step")
            if "*" in self.cors_origins:
                raise ValueError("production does not allow wildcard CORS")
            if self.metrics_enabled and len(self.metrics_token) < 32:
                raise ValueError("production metrics require METRICS_TOKEN with at least 32 characters")
            if self.provider_mode == "synthetic" and not self.allow_synthetic_in_production:
                raise ValueError("production requires an approved non-synthetic provider")
            if self.provider_mode == "http" and (not self.provider_url or not self.provider_token):
                raise ValueError("http provider mode requires PROVIDER_URL and PROVIDER_TOKEN")
            if len(self.billing_webhook_secret) < 32:
                raise ValueError("production requires BILLING_WEBHOOK_SECRET with at least 32 characters")


settings = Settings()
settings.validate()
