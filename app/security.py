from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import ApiKey, User


password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    actor_id: str | None
    role: str
    scopes: frozenset[str]
    api_key_id: str | None = None


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(user: User) -> tuple[str, int]:
    expires_in = settings.access_token_minutes * 60
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user.id,
        "tenant_id": user.tenant_id,
        "role": user.role,
        "aud": "lead-generation-api",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def _digest_api_key(secret: str) -> str:
    return hashlib.sha256(f"{settings.api_key_pepper}:{secret}".encode()).hexdigest()


def create_api_key(environment: str) -> tuple[str, str, str]:
    secret = secrets.token_urlsafe(32)
    prefix = "lg_live_" if environment == "production" else "lg_test_"
    visible_prefix = prefix + secret[:8]
    raw_key = prefix + secret
    return raw_key, visible_prefix, _digest_api_key(raw_key)


def _require_scope(context: AuthContext, scope: str) -> None:
    if context.api_key_id is not None and scope not in context.scopes:
        raise AuthorizationError(f"missing required scope: {scope}")


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("authentication required")
    token = credentials.credentials
    if token.startswith(("lg_test_", "lg_live_")):
        digest = _digest_api_key(token)
        key = db.scalar(select(ApiKey).where(ApiKey.secret_hash == digest, ApiKey.status == "ACTIVE"))
        if key is None:
            raise AuthenticationError("invalid API key")
        key.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return AuthContext(
            tenant_id=key.tenant_id,
            actor_id=None,
            role="API_CLIENT",
            scopes=frozenset(key.scopes or []),
            api_key_id=key.id,
        )
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience="lead-generation-api",
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError("invalid or expired access token") from exc
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not isinstance(user_id, str) or not isinstance(tenant_id, str):
        raise AuthenticationError("invalid access token claims")
    user = db.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    if user is None:
        raise AuthenticationError("user not found")
    return AuthContext(
        tenant_id=tenant_id,
        actor_id=user.id,
        role=user.role,
        scopes=frozenset({"*"}),
    )


def require_scope(context: AuthContext, scope: str) -> AuthContext:
    _require_scope(context, scope)
    return context


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
