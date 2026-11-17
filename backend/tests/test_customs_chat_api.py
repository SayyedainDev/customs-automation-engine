"""API-level smoke tests for the general Customs Assistant endpoints.

Runs through ``TestClient`` against the app's real dependency wiring, using
the autouse fixtures in ``tests/conftest.py`` (isolated in-memory database,
fake embedder/reranker, blanked Groq key). The blanked key means any
question that reaches generation exercises the honest
"temporarily unavailable" path rather than a live network call - see
``generation/answerer.py`` for why that is the deliberate fallback instead
of the old written templates.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.customs_chat import config as chat_config

client = TestClient(app)


def test_out_of_scope_question_non_streaming(isolated_database):
    resp = client.post(
        "/api/v1/customs-chat/messages",
        json={"message": "Who won the FIFA World Cup 2026?", "stream": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "OUT_OF_SCOPE"
    assert data["answer"] == chat_config.OUT_OF_SCOPE_MESSAGE
    assert data["sources"] == []


def test_in_scope_question_without_groq_key_degrades_honestly(isolated_database):
    resp = client.post(
        "/api/v1/customs-chat/messages",
        json={"message": "What is a bill of lading?", "stream": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "REFUSAL"
    assert data["answer"] == chat_config.GENERATION_UNAVAILABLE_MESSAGE


def test_streaming_response_emits_sse_events(isolated_database):
    with client.stream(
        "POST",
        "/api/v1/customs-chat/messages",
        json={"message": "Who won the FIFA World Cup 2026?", "stream": True},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: meta" in body
    assert "event: token" in body
    assert "event: sources" in body
    assert "event: done" in body


def test_suggestions_endpoint():
    resp = client.get("/api/v1/customs-chat/suggestions")
    assert resp.status_code == 200
    assert len(resp.json()["suggestions"]) > 0


def test_coverage_endpoint(isolated_database):
    resp = client.get("/api/v1/customs-chat/coverage")
    assert resp.status_code == 200
    data = resp.json()
    assert "known_gaps" in data
    assert isinstance(data["known_gaps"], list)


def test_conversation_lookup_and_feedback_roundtrip(isolated_database):
    first = client.post(
        "/api/v1/customs-chat/messages",
        json={"message": "Who won the FIFA World Cup 2026?", "stream": False},
    ).json()
    conv_id = first["conversation_id"]
    message_id = first["message_id"]

    history_resp = client.get(f"/api/v1/customs-chat/conversations/{conv_id}")
    assert history_resp.status_code == 200
    turns = history_resp.json()["turns"]
    assert len(turns) == 2

    feedback_resp = client.post(
        "/api/v1/customs-chat/feedback",
        json={"message_id": message_id, "rating": "down", "comment": "not helpful"},
    )
    assert feedback_resp.status_code == 204


def test_feedback_for_unknown_message_is_404(isolated_database):
    resp = client.post(
        "/api/v1/customs-chat/feedback",
        json={"message_id": str(uuid4()), "rating": "up"},
    )
    assert resp.status_code == 404


def test_unknown_conversation_is_404(isolated_database):
    resp = client.get(f"/api/v1/customs-chat/conversations/{uuid4()}")
    assert resp.status_code == 404


def test_shipment_scoped_conversation_persists_in_chat_history_and_feedback(isolated_database):
    shipment_id = str(uuid4())
    first = client.post(
        "/api/v1/customs-chat/messages",
        json={
            "message": "What is the audit status of this shipment?",
            "shipment_id": shipment_id,
            "stream": False,
        },
    )
    assert first.status_code == 200
    data = first.json()
    conv_id = data["conversation_id"]
    msg_id = data["message_id"]

    history_resp = client.get(f"/api/v1/customs-chat/conversations/{conv_id}")
    assert history_resp.status_code == 200
    history_json = history_resp.json()
    assert history_json["shipment_id"] == shipment_id
    assert len(history_json["turns"]) == 2

    feedback_resp = client.post(
        "/api/v1/customs-chat/feedback",
        json={"message_id": msg_id, "rating": "up"},
    )
    assert feedback_resp.status_code == 204


def test_observability_headers_and_stage_sse_events(isolated_database):
    with client.stream(
        "POST",
        "/api/v1/customs-chat/messages",
        headers={"X-Request-ID": "req-custom-123"},
        json={"message": "Who won the FIFA World Cup 2026?", "stream": True},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == "req-custom-123"
        assert "X-Process-Time-Ms" in resp.headers
        body = "".join(resp.iter_text())
    assert "event: stage" in body
    assert "input_guard" in body
