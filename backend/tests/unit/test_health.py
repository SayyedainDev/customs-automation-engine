from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.customs_audit import factory


client = TestClient(app)


def test_root_page_points_to_api_documentation() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["documentation"] == "/docs"


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "API is running"}


def test_database_health_check() -> None:
    response = client.get("/health/database")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_extraction_cache_health_exposes_no_secret_and_all_profiles() -> None:
    response = client.get("/health/extraction-cache")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "cache_enabled",
        "application_code_version",
        "profiles",
    }
    assert payload["cache_enabled"] is True
    assert len(payload["application_code_version"]) == 64
    int(payload["application_code_version"], 16)
    assert set(payload["profiles"]) == {
        "commercial_invoice",
        "packing_list",
        "supporting_document",
    }
    for fingerprint in payload["profiles"].values():
        assert set(fingerprint) == {
            "extraction_model",
            "prompt_version",
            "schema_version",
            "ocr_settings",
            "cache_format_version",
        }
    assert "api_key" not in str(payload).casefold()
    assert "gsk_" not in str(payload)


def test_customs_audit_health_proves_runtime_profile_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory, "get_app_customs_audit_service", lambda: object())

    response = client.get("/health/customs-audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checkpoint_ready"] is True
    assert payload["checkpoint_backend"] in {"postgres", "sqlite", "memory"}
    assert isinstance(payload["live_agents_enabled"], bool)
    assert payload["broker_model"]
    assert payload["auditor_model"]
    assert payload["extraction_model"]
    assert "gsk_" not in str(payload)


def test_customs_audit_health_fails_closed_when_checkpointer_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> object:
        raise RuntimeError("database credentials must not leak")

    monkeypatch.setattr(factory, "get_app_customs_audit_service", unavailable)

    response = client.get("/health/customs-audit")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The customs-audit workflow/checkpointer is unavailable."
    }
