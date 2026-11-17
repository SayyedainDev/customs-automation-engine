"""Review thumbs-down feedback on Customs Assistant answers.

docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 14.4: "Thumbs-down messages
land in chat_turns.user_feedback. Review weekly; every genuine miss becomes
either a new golden-set case, a curated guidance entry, or a data
acquisition request." This is that weekly-review tool - it does not fix
anything itself, only surfaces what needs a human look.

Requires a live CUSTOMS_DATABASE_URL (unlike the other scripts in this
directory, there is no offline mode: feedback only exists once real
conversations have happened against a real database).

Usage::

    python -m scripts.review_feedback
    python -m scripts.review_feedback --since 2026-08-01
    python -m scripts.review_feedback --rating down --limit 50
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import get_engine  # noqa: E402
from app.models.customs_chat import ChatTurn  # noqa: E402


def fetch_feedback(
    session: Session, *, rating: str, since: datetime | None, limit: int
) -> list[ChatTurn]:
    query = select(ChatTurn).where(ChatTurn.user_feedback == rating).order_by(ChatTurn.created_at.desc())
    if since is not None:
        query = query.where(ChatTurn.created_at >= since)
    query = query.limit(limit)
    return list(session.execute(query).scalars())


def _preceding_question(session: Session, turn: ChatTurn) -> str | None:
    prior = session.execute(
        select(ChatTurn)
        .where(
            ChatTurn.conversation_id == turn.conversation_id,
            ChatTurn.role == "user",
            ChatTurn.created_at <= turn.created_at,
        )
        .order_by(ChatTurn.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return prior.content if prior else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rating", choices=["up", "down"], default="down")
    parser.add_argument("--since", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    with Session(get_engine()) as session:
        turns = fetch_feedback(session, rating=args.rating, since=since, limit=args.limit)

        if not turns:
            print(f"No '{args.rating}' feedback found" + (f" since {args.since}" if since else "") + ".")
            return 0

        print(f"{len(turns)} '{args.rating}'-rated turn(s):\n")
        for turn in turns:
            question = _preceding_question(session, turn)
            print("=" * 72)
            print(f"turn_id: {turn.id}")
            print(f"conversation_id: {turn.conversation_id}")
            print(f"created_at: {turn.created_at}")
            print(f"mode: {turn.mode}  evidence_band: {turn.evidence_band}  scope_intent: {turn.scope_intent}")
            if turn.guard_violations:
                print(f"guard_violations: {turn.guard_violations}")
            print(f"prompt_version: {turn.prompt_version}  model: {turn.model}")
            print()
            print(f"Q: {question or '(not found)'}")
            print(f"A: {turn.content}")
            print()

        print("=" * 72)
        print(
            "Next step for each of these: add it to tests/eval/golden_questions.yaml "
            "(if it's a scope miss), write a curated guidance entry (if it's a "
            "concept gap), or note it as a data acquisition need - see "
            "docs/CUSTOMS_CHATBOT_ARCHITECTURE.md section 14.4."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
