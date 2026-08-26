"""Safe Zyla contract smoke test for a deployed staging or demo instance.

This test does not create marketplace accounts, submit listings, call a paid provider, or perform
payment actions. It only exercises the public HTTP contract configured by the operator.
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

import httpx


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ZYLA_SHARED_TOKEN = os.getenv("ZYLA_SHARED_TOKEN", "")


def main() -> int:
    if not ZYLA_SHARED_TOKEN:
        raise SystemExit("ZYLA_SHARED_TOKEN must be set for the smoke test")
    headers = {"Authorization": f"Bearer {ZYLA_SHARED_TOKEN}"}
    idempotency_key = f"zyla-smoke-{uuid4().hex}"
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as client:
        health = client.get("/health/zyla")
        assert health.status_code == 200, health.text
        health_body = health.json()
        assert health_body["status"] == "ok"
        assert health_body["owner"] == "Sadeq Obaid"

        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200, openapi.text
        assert "/api/v1/zyla/leads/search" in openapi.json()["paths"]

        unauthorized = client.get("/api/v1/zyla/leads/search", params={"limit": 1})
        assert unauthorized.status_code == 401, unauthorized.text

        params = {"countries": "SA", "industries": "financial_services", "limit": 1}
        first = client.get(
            "/api/v1/zyla/leads/search",
            params=params,
            headers={**headers, "Idempotency-Key": idempotency_key},
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["data"]
        assert body["data"][0]["company"]["country"] == "SA"
        assert "provenance" in body["data"][0]

        replay = client.get(
            "/api/v1/zyla/leads/search",
            params=params,
            headers={**headers, "Idempotency-Key": idempotency_key},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == body

    print("Zyla smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
