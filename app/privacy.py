from __future__ import annotations

import hashlib
import hmac

from .config import settings


def normalize_subject(value: str) -> str:
    """Normalize a suppression/request identifier before hashing it."""
    return " ".join(value.strip().lower().split())


def hash_subject(value: str) -> str:
    """Create a one-way tenant-independent digest without persisting the raw identifier."""
    normalized = normalize_subject(value)
    return hmac.new(settings.api_key_pepper.encode(), normalized.encode(), hashlib.sha256).hexdigest()
