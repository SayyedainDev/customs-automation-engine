"""Per-turn logging for the customs_chat pipeline.

Kept separate from persistence (``memory/history.py`` writes ``ChatTurn``
rows) because telemetry must never raise: a logging failure must not turn
into a failed chat turn.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("app.services.customs_chat")


def log_stage(stage: str, **fields: Any) -> None:
    try:
        rendered = " ".join(f"{key}={value!r}" for key, value in fields.items())
        logger.info("customs_chat.%s %s", stage, rendered)
    except Exception:  # pragma: no cover - logging must never break a turn
        logger.exception("customs_chat telemetry failed for stage %s", stage)


def log_guard_violation(stage: str, violations: tuple[str, ...], **fields: Any) -> None:
    if not violations:
        return
    try:
        rendered = " ".join(f"{key}={value!r}" for key, value in fields.items())
        logger.warning(
            "customs_chat.%s violations=%s %s", stage, list(violations), rendered
        )
    except Exception:  # pragma: no cover
        logger.exception("customs_chat violation logging failed for stage %s", stage)
