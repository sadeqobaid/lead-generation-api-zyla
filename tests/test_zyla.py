from fastapi.testclient import TestClient

from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def _headers(key: str = "test-zyla-shared-token-with-sufficient-entropy") -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_zyla_health_is_public_and_does_not_expose_credentials():
    with _client() as client:
        response = client.get("/health/zyla")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["edition"] == "zyla-hosted-api"
    assert body["owner"] == "Sadeq Obaid"
    assert "token" not in response.text.lower()


def test_zyla_search_requires_bearer_credential():
    with _client() as client:
        response = client.get("/api/v1/zyla/leads/search", params={"limit": 1})
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_zyla_get_search_is_filterable_and_idempotent():
    headers = {**_headers(), "Idempotency-Key": "zyla-search-sa-001"}
    params = {"countries": "SA", "industries": "financial_services", "limit": 2}
    with _client() as client:
        first = client.get("/api/v1/zyla/leads/search", params=params, headers=headers)
        second = client.get("/api/v1/zyla/leads/search", params=params, headers=headers)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body == second_body
    assert len(first_body["data"]) == 1
    assert first_body["data"][0]["company"]["country"] == "SA"
    assert first_body["data"][0]["company"]["data_status"] == "SYNTHETIC"
    assert first_body["data"][0]["provenance"][0]["data_status"] == "SYNTHETIC"
    assert first_body["usage"]["credits_used"] == 1


def test_zyla_post_search_uses_same_response_contract():
    payload = {
        "filters": {"countries": ["AE"]},
        "limit": 1,
    }
    with _client() as client:
        response = client.post(
            "/api/v1/zyla/leads/search",
            headers=_headers(),
            json=payload,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"][0]["company"]["country"] == "AE"
    assert body["usage"]["credits_used"] == 1


def test_zyla_rejects_invalid_employee_range():
    with _client() as client:
        response = client.get(
            "/api/v1/zyla/leads/search",
            headers=_headers(),
            params={"employee_min": 1000, "employee_max": 10},
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
