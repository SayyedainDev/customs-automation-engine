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

import json
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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


def _parse_sse_frames(raw_text: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            frames.append((event_name, json.loads("\n".join(data_lines))))
    return frames


def test_stream_live_turn_flushes_stages_before_generation_completes(
    isolated_database, monkeypatch
):
    from app.api.routes.customs_chat import _stream_live_turn
    from app.schemas.customs_chat import ChatMessageRequest
    from app.services.customs_chat.contracts import DraftAnswer
    from app.services.customs_chat.generation import answerer

    generation_started = threading.Event()
    release_generation = threading.Event()

    def blocked_generate_answer(**_kwargs):
        generation_started.set()
        assert release_generation.wait(timeout=5)
        return DraftAnswer(
            text="A bill of lading is a shipping transport document issued by a carrier.",
            mode="GENERAL",
            model="fake-groq",
        )

    monkeypatch.setattr(answerer, "generate_answer", blocked_generate_answer)

    with Session(isolated_database) as db:
        stream_gen = _stream_live_turn(
            db,
            ChatMessageRequest(message="What is a bill of lading?", stream=True),
        )
        early_events: list[tuple[str, dict]] = []
        for _ in range(5):
            raw_chunk = next(stream_gen)
            parsed = _parse_sse_frames(raw_chunk)
            early_events.extend(parsed)
            if any(
                ev == "stage" and payload.get("stage") == "answer_generation"
                for ev, payload in parsed
            ):
                break

        assert generation_started.wait(timeout=5)
        assert not release_generation.is_set()

        assert [payload["stage"] for ev, payload in early_events if ev == "stage"] == [
            "input_guard",
            "scope_router",
            "query_rewriter",
            "hybrid_retrieval",
            "answer_generation",
        ]
        assert all(ev == "stage" for ev, _payload in early_events)
        assert all("label" in payload and payload["label"] for _ev, payload in early_events)

        release_generation.set()
        late_raw = "".join(list(stream_gen))

    late_events = _parse_sse_frames(late_raw)
    late_stages = [payload["stage"] for ev, payload in late_events if ev == "stage"]
    assert late_stages == ["output_guard"]

    all_events = early_events + late_events
    all_stages = [payload["stage"] for ev, payload in all_events if ev == "stage"]
    assert len(all_stages) == len(set(all_stages))
    event_names = [ev for ev, _payload in late_events]
    assert "meta" in event_names
    assert "token" in event_names
    assert "sources" in event_names
    assert event_names[-1] == "done"


def test_stream_live_turn_emits_error_event_on_unexpected_exception(
    isolated_database, monkeypatch
):
    from app.api.routes.customs_chat import _stream_live_turn
    from app.schemas.customs_chat import ChatMessageRequest
    from app.services.customs_chat.guards import scope_router

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret internal database detail")

    monkeypatch.setattr(scope_router, "classify_scope", boom)

    with Session(isolated_database) as db:
        raw_output = "".join(
            list(
                _stream_live_turn(
                    db,
                    ChatMessageRequest(message="What is a bill of lading?", stream=True),
                )
            )
        )

    events = _parse_sse_frames(raw_output)
    assert events[0][0] == "stage"
    assert events[0][1]["stage"] == "input_guard"
    assert events[-1][0] == "error"
    assert "secret internal database detail" not in events[-1][1]["detail"]
    assert all(ev not in ("token", "meta", "done") for ev, _payload in events)


def test_streaming_response_sets_no_buffering_headers_and_stage_labels(
    isolated_database,
):
    with client.stream(
        "POST",
        "/api/v1/customs-chat/messages",
        json={"message": "Who won the FIFA World Cup 2026?", "stream": True},
    ) as resp:
        assert resp.status_code == 200
        assert "no-cache" in (resp.headers.get("Cache-Control") or "")
        assert resp.headers.get("X-Accel-Buffering") == "no"
        body = "".join(resp.iter_text())

    events = _parse_sse_frames(body)
    stage_events = [payload for ev, payload in events if ev == "stage"]
    assert [s["stage"] for s in stage_events] == ["input_guard", "scope_router"]
    assert all(
        isinstance(s.get("label"), str) and s["label"] and "_" not in s["label"]
        for s in stage_events
    )
