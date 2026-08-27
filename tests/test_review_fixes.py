import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_framework_http_errors_use_documented_error_envelope():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["request_id"]


def test_generated_openapi_documents_auth_routes_and_metrics_header():
    with TestClient(app) as client:
        document = client.get("/openapi.json").json()
    assert "/api/v1/auth/register" in document["paths"]
    assert "/api/v1/auth/login" in document["paths"]
    assert "/api/v1/users/me" in document["paths"]
    assert "/metrics" in document["paths"]
    metric_parameters = document["paths"]["/metrics"]["get"]["parameters"]
    assert any(parameter["name"] == "X-Metrics-Token" for parameter in metric_parameters)


def test_http_provider_requires_complete_named_configuration():
    settings = Settings(provider_mode="http", provider_name="", provider_url="", provider_token="")
    with pytest.raises(ValueError, match="PROVIDER_URL"):
        settings.validate()


def test_database_pool_defaults_are_positive():
    settings = Settings()
    assert settings.db_pool_size > 0
    assert settings.db_max_overflow >= 0
    assert settings.db_pool_recycle_seconds > 0
    assert settings.db_pool_timeout_seconds > 0
