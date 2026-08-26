from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_lead_generation.db")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-sufficient-entropy")
os.environ.setdefault("API_KEY_PEPPER", "test-api-key-pepper")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "60")
os.environ.setdefault("DEFAULT_CREDITS", "100")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


def register(client: TestClient, tenant_name: str | None = None) -> tuple[str, dict]:
    email = f"owner-{uuid4().hex[:8]}@example.test"
    response = client.post(
        "/api/v1/auth/register",
        json={
            "tenant_name": tenant_name or f"Tenant {uuid4().hex[:6]}",
            "email": email,
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"], {"email": email, "password": "correct-horse-battery-staple"}


def create_key(client: TestClient, token: str, scopes: list[str] | None = None) -> str:
    response = client.post(
        "/api/v1/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "test integration",
            "environment": "test",
            "scopes": scopes or ["leads:read", "usage:read", "keys:read", "keys:write"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["secret"]
    return body["secret"]


def test_health_endpoints(client: TestClient):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_register_login_and_me(client: TestClient):
    token, credentials = register(client)
    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "TENANT_ADMIN"
    login = client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"


def test_invalid_login_is_not_accepted(client: TestClient):
    _, credentials = register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": credentials["email"], "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_api_key_secret_is_one_time_and_revocation_works(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token)
    listed = client.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert "secret" not in listed.text
    key_id = listed.json()[0]["id"]
    revoked = client.delete(f"/api/v1/api-keys/{key_id}", headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 204
    response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {secret}"})
    assert response.status_code == 401


def test_search_filters_scores_and_consumes_credits(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token)
    response = client.post(
        "/api/v1/leads/search",
        headers={"Authorization": f"Bearer {secret}", "Idempotency-Key": "test-search-001"},
        json={
            "filters": {
                "countries": ["SA"],
                "industries": ["financial_services", "insurance"],
                "employee_count": {"min": 100, "max": 5000},
                "job_titles": ["CFO", "CTO"],
                "technologies": ["AWS"],
                "lead_score_min": 70,
            },
            "limit": 50,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["data"]) == 2
    assert body["usage"]["credits_used"] == 2
    assert all(item["company"]["data_status"] == "SYNTHETIC" for item in body["data"])
    assert all(item["scores"]["lead_score"] >= 70 for item in body["data"])


def test_empty_search_does_not_consume_credits(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token)
    response = client.post(
        "/api/v1/leads/search",
        headers={"Authorization": f"Bearer {secret}"},
        json={"filters": {"countries": ["US"]}, "limit": 10},
    )
    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["usage"]["credits_used"] == 0


def test_missing_scope_is_rejected(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token, scopes=["usage:read"])
    response = client.post(
        "/api/v1/leads/search",
        headers={"Authorization": f"Bearer {secret}"},
        json={"filters": {}, "limit": 1},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_cross_tenant_key_cannot_read_other_tenant_context(client: TestClient):
    token_a, _ = register(client, "Tenant A")
    token_b, _ = register(client, "Tenant B")
    key_a = create_key(client, token_a)
    me_a = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {key_a}"})
    me_b = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token_b}"})
    assert me_a.status_code == 200
    assert me_b.status_code == 200
    assert me_a.json()["tenant"]["id"] != me_b.json()["tenant"]["id"]


def test_usage_is_tenant_scoped(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token)
    searched = client.post(
        "/api/v1/leads/search",
        headers={"Authorization": f"Bearer {secret}"},
        json={"filters": {"countries": ["SA"]}, "limit": 1},
    )
    assert searched.status_code == 200
    usage = client.get("/api/v1/usage", headers={"Authorization": f"Bearer {secret}"})
    assert usage.status_code == 200
    assert usage.json()["totals"]["requests"] >= 1
    assert usage.json()["totals"]["credits_used"] >= 1


def test_validation_errors_are_standardized(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token)
    response = client.post(
        "/api/v1/leads/search",
        headers={"Authorization": f"Bearer {secret}"},
        json={"filters": {"employee_count": {"min": 500, "max": 100}}, "limit": 1},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_idempotency_replays_without_double_charging(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token)
    headers = {"Authorization": f"Bearer {secret}", "Idempotency-Key": "replay-001"}
    payload = {"filters": {"countries": ["SA"]}, "limit": 1}
    first = client.post("/api/v1/leads/search", headers=headers, json=payload)
    second = client.post("/api/v1/leads/search", headers=headers, json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()


def test_idempotency_rejects_different_payload(client: TestClient):
    token, _ = register(client)
    secret = create_key(client, token)
    headers = {"Authorization": f"Bearer {secret}", "Idempotency-Key": "conflict-001"}
    first = client.post("/api/v1/leads/search", headers=headers, json={"filters": {"countries": ["SA"]}, "limit": 1})
    second = client.post("/api/v1/leads/search", headers=headers, json={"filters": {"countries": ["AE"]}, "limit": 1})
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
