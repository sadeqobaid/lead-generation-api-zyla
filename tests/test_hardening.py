from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from uuid import uuid4

from app.idempotency import IdempotencyConflict, IdempotencyHit, IdempotencyInProgress, InMemoryIdempotencyStore
from app.privacy import hash_subject
from app.models import CreditLedger
from app.main import app
from app.db import SessionLocal
from fastapi.testclient import TestClient


def test_in_memory_idempotency_reservation_replay_and_conflict():
    async def scenario():
        store = InMemoryIdempotencyStore(ttl_seconds=60)
        assert await store.reserve("tenant:key", "fingerprint") is True
        try:
            await store.get("tenant:key", "fingerprint")
        except IdempotencyInProgress:
            pass
        else:
            raise AssertionError("pending reservation was not reported")
        await store.put("tenant:key", IdempotencyHit("fingerprint", {"ok": True}))
        hit = await store.get("tenant:key", "fingerprint")
        assert hit is not None and hit.response_body == {"ok": True}
        try:
            await store.get("tenant:key", "other")
        except IdempotencyConflict:
            pass
        else:
            raise AssertionError("fingerprint conflict was not reported")

    asyncio.run(scenario())


def test_subject_hash_is_deterministic_without_raw_value():
    assert hash_subject("  Person@example.com ") == hash_subject("person@example.com")
    assert "person@example.com" not in hash_subject("person@example.com")


def test_credit_ledger_is_written_for_registration():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "Ledger Tenant", "email": "ledger@example.test", "password": "correct-horse-battery-staple"},
        )
        assert response.status_code == 201
    with SessionLocal() as db:
        entry = db.query(CreditLedger).order_by(CreditLedger.created_at.desc()).first()
        assert entry is not None
        assert entry.reason == "registration_grant"
        assert entry.amount >= 0


def test_metrics_requires_configured_token_when_set(monkeypatch):
    from app import main

    previous = main.settings.metrics_token
    object.__setattr__(main.settings, "metrics_token", "metrics-secret")
    try:
        with TestClient(app) as client:
            assert client.get("/metrics").status_code == 401
            assert client.get("/metrics", headers={"X-Metrics-Token": "metrics-secret"}).status_code == 200
    finally:
        object.__setattr__(main.settings, "metrics_token", previous)


def test_signed_billing_webhook_grants_credits_once(monkeypatch):
    from app import main

    previous = main.settings.billing_webhook_secret
    secret = "billing-test-secret-with-sufficient-entropy"
    object.__setattr__(main.settings, "billing_webhook_secret", secret)
    try:
        with TestClient(app) as client:
            registration = client.post(
                "/api/v1/auth/register",
                json={"tenant_name": "Billing Tenant", "email": "billing@example.test", "password": "correct-horse-battery-staple"},
            )
            token = registration.json()["access_token"]
            tenant_id = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant"]["id"]
            event_id = f"evt_{uuid4().hex}"
            payload = {
                "provider": "test_processor",
                "event_id": event_id,
                "event_type": "credits.purchased",
                "tenant_id": tenant_id,
                "credits": 25,
            }
            body = json.dumps(payload, separators=(",", ":")).encode()
            signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers = {"Content-Type": "application/json", "X-Webhook-Signature": signature}
            first = client.post("/api/v1/billing/webhook", content=body, headers=headers)
            second = client.post("/api/v1/billing/webhook", content=body, headers=headers)
            assert first.json() == {"event_id": event_id, "status": "PROCESSED", "credits_granted": 25}
            assert second.json() == {"event_id": event_id, "status": "DUPLICATE", "credits_granted": 25}
    finally:
        object.__setattr__(main.settings, "billing_webhook_secret", previous)


def test_suppression_prevents_provider_record_from_returning():
    with TestClient(app) as client:
        registration = client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "Privacy Tenant", "email": "privacy@example.test", "password": "correct-horse-battery-staple"},
        )
        token = registration.json()["access_token"]
        key_response = client.post(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "privacy-test", "environment": "test", "scopes": ["leads:read"]},
        )
        key = key_response.json()["secret"]
        suppressed = client.post(
            "/api/v1/privacy/suppressions",
            headers={"Authorization": f"Bearer {token}"},
            json={"subject_type": "domain", "subject": "finance.example.invalid", "reason": "test opt out"},
        )
        assert suppressed.status_code == 201
        search = client.post(
            "/api/v1/leads/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"filters": {"countries": ["SA"]}, "limit": 50},
        )
        assert search.status_code == 200
        assert all(item["company"]["domain"] != "finance.example.invalid" for item in search.json()["data"])
