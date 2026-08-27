import asyncio
import json

import httpx
import pytest

from app.providers import HttpLeadProvider, ProviderRequestError


def test_http_provider_maps_records_and_provenance():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "company": {
                            "name": "Acme Real Data",
                            "domain": "acme.example",
                            "country": "US",
                            "employee_count": 250,
                        },
                        "contact": {"first_name": "Ada", "last_name": "Example", "job_title": "CTO"},
                        "data_status": "VERIFIED",
                        "observed_at": "2026-08-27T00:00:00+00:00",
                    }
                ]
            },
        )

    provider = HttpLeadProvider(
        "https://provider.example/search",
        "vendor-secret",
        "licensed_vendor",
        data_source="Licensed vendor dataset",
        use_policy="CONTRACTED_B2B_USE",
        transport=httpx.MockTransport(handler),
    )
    records = asyncio.run(provider.search({"countries": ["US"]}))

    assert seen["authorization"] == "Bearer vendor-secret"
    assert seen["payload"] == {"filters": {"countries": ["US"]}}
    assert len(records) == 1
    assert records[0].provider == "licensed_vendor"
    assert records[0].company["data_source"] == "Licensed vendor dataset"
    assert records[0].use_policy == "CONTRACTED_B2B_USE"
    assert records[0].data_status == "VERIFIED"


def test_http_provider_supports_x_api_key_authentication():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "vendor-secret"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"results": []})

    provider = HttpLeadProvider(
        "https://provider.example/search",
        "vendor-secret",
        "licensed_vendor",
        auth_scheme="x-api-key",
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(provider.search({})) == []


def test_http_provider_rejects_synthetic_records():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"company": {"name": "Demo"}, "data_status": "SYNTHETIC"}]})

    provider = HttpLeadProvider(
        "https://provider.example/search",
        "vendor-secret",
        "licensed_vendor",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderRequestError, match="synthetic"):
        asyncio.run(provider.search({}))
