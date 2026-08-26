#!/usr/bin/env python3
"""Run a safe end-to-end smoke test against a running API.

Usage:
    BASE_URL=http://127.0.0.1:8000 python scripts/smoke_test.py

The test creates an isolated synthetic tenant, creates a short-lived test key, checks liveness
and readiness, performs an idempotent search, verifies synthetic provenance, and reads usage.
It does not call a real provider or require a payment account. Use it against staging after each
migration-gated deployment; do not use a customer production tenant for test data.
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")


def main() -> None:
    """Execute the smoke flow and raise on any failed HTTP status or assertion."""
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.test"
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        live = client.get("/health/live")
        live.raise_for_status()
        ready = client.get("/health/ready")
        ready.raise_for_status()

        registered = client.post(
            "/api/v1/auth/register",
            json={
                "tenant_name": f"Smoke Tenant {uuid.uuid4().hex[:6]}",
                "email": email,
                "password": "correct-horse-battery-staple",
            },
        )
        registered.raise_for_status()
        access_token = registered.json()["access_token"]
        human_headers = {"Authorization": f"Bearer {access_token}"}

        created = client.post(
            "/api/v1/api-keys",
            headers=human_headers,
            json={
                "name": "smoke key",
                "environment": "test",
                "scopes": ["leads:read", "usage:read"],
            },
        )
        created.raise_for_status()
        api_key = created.json()["secret"]

        search_headers = {"Authorization": f"Bearer {api_key}", "Idempotency-Key": f"smoke-{uuid.uuid4().hex}"}
        search_payload = {"filters": {"countries": ["SA"]}, "limit": 10}
        searched = client.post("/api/v1/leads/search", headers=search_headers, json=search_payload)
        searched.raise_for_status()
        replayed = client.post("/api/v1/leads/search", headers=search_headers, json=search_payload)
        replayed.raise_for_status()
        assert searched.json() == replayed.json(), "idempotent replay changed the response"
        body = searched.json()
        assert body["data"], "expected at least one synthetic lead"
        assert all(item["company"]["data_status"] == "SYNTHETIC" for item in body["data"])

        usage = client.get("/api/v1/usage", headers={"Authorization": f"Bearer {api_key}"})
        usage.raise_for_status()
        assert usage.json()["totals"]["requests"] >= 1

    print("smoke test passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        raise
