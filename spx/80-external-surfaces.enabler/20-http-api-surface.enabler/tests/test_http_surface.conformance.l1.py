"""Conformance evidence for HTTP API routers, middleware headers, and OpenAPI schema."""

from __future__ import annotations

from http import HTTPStatus

from cace_testing.harnesses.database import CaceRuntime


def test_observability_middleware_health_and_root_conform(
    cace_runtime: CaceRuntime,
) -> None:
    auto_header_resp = cace_runtime.client.get("/health")
    assert auto_header_resp.status_code == HTTPStatus.OK
    assert auto_header_resp.json()["status"] == "ok"
    assert len(auto_header_resp.headers["X-Request-ID"]) > 0
    assert float(auto_header_resp.headers["X-Process-Time-Ms"]) >= 0.0

    custom_request_id = "cace-trace-001"
    echoed_resp = cace_runtime.client.get(
        "/health/database",
        headers={"X-Request-ID": custom_request_id},
    )
    assert echoed_resp.status_code == HTTPStatus.OK
    assert echoed_resp.headers["X-Request-ID"] == custom_request_id
    assert echoed_resp.json()["database"] == "connected"

    cache_resp = cace_runtime.client.get("/health/extraction-cache")
    assert cache_resp.status_code == HTTPStatus.OK

    root_resp = cace_runtime.client.get("/")
    assert root_resp.status_code == HTTPStatus.OK
    assert root_resp.json()["status"] == "API is running"


def test_openapi_schema_exposes_public_routes_and_excludes_hidden_routers(
    cace_runtime: CaceRuntime,
) -> None:
    openapi_resp = cace_runtime.client.get("/openapi.json")
    assert openapi_resp.status_code == HTTPStatus.OK
    paths = openapi_resp.json()["paths"]

    expected_paths = (
        "/health",
        "/documents/upload",
        "/api/v1/compliance/calculate-duties",
        "/api/v1/customs-audit/workflows",
        "/api/v1/customs-chat/messages",
        "/api/v1/regulatory-evidence/search",
        "/api/v1/shipment-search/search",
    )
    for route_path in expected_paths:
        assert route_path in paths

    assert not any(path.startswith("/shipments") for path in paths)
