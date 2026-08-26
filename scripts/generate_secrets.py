#!/usr/bin/env python3
"""Generate secrets for a development or deployment environment.

Usage:
    python scripts/generate_secrets.py

The output contains two shell-compatible assignments. Copy the values into a protected
secret manager or local `.env`; never commit or publish the generated output.
"""

from __future__ import annotations

import secrets


def main() -> None:
    """Print independent high-entropy values for JWT signing and API-key peppering."""
    print("JWT_SECRET=" + secrets.token_urlsafe(48))
    print("API_KEY_PEPPER=" + secrets.token_urlsafe(48))


if __name__ == "__main__":
    main()
