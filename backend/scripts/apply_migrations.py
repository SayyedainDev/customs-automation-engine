"""Apply the numbered SQL migrations in ``backend/migrations`` in order.

The repository has always carried hand-written migration files, but nothing in
it applied them, so a fresh database was in practice created by calling
``Base.metadata.create_all`` - which builds tables from the ORM models and
silently diverges from the migration files it is meant to mirror. That is how a
deployment ends up with tables no migration describes.

This runner is deliberately small:

* files are applied in filename order (``001_...`` first);
* only the text above ``-- Down Migration`` is executed;
* each applied filename is recorded in ``schema_migrations``, so re-running is
  a no-op and a partially-migrated database resumes where it stopped;
* each migration runs in its own transaction, so a failure leaves the earlier
  migrations applied and the failing one rolled back.

Usage::

    python -m scripts.apply_migrations              # apply pending migrations
    python -m scripts.apply_migrations --status     # list applied/pending
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.database import get_engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
DOWN_MARKER = "-- Down Migration"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
)
"""


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def up_sql(path: Path) -> str:
    """The Up section only. Everything from the Down marker on is ignored."""
    body = path.read_text(encoding="utf-8")
    head = body.split(DOWN_MARKER, 1)[0]
    return head.replace("-- Up Migration", "", 1).strip()


def applied_filenames(connection) -> set[str]:  # type: ignore[no-untyped-def]
    rows = connection.execute(text("SELECT filename FROM schema_migrations"))
    return {row[0] for row in rows}


def apply_migrations(*, dry_run: bool = False) -> list[str]:
    """Apply every pending migration. Returns the filenames applied."""
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text(_TRACKING_TABLE))

    with engine.connect() as connection:
        already = applied_filenames(connection)

    performed: list[str] = []
    for path in migration_files():
        if path.name in already:
            continue
        statements = up_sql(path)
        if not statements:
            continue
        if dry_run:
            performed.append(path.name)
            continue
        engine = get_engine()
        with engine.begin() as connection:
            connection.execute(text(statements))
            connection.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:name)"),
                {"name": path.name},
            )
        performed.append(path.name)
    return performed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status",
        action="store_true",
        help="List applied and pending migrations without applying anything.",
    )
    args = parser.parse_args()

    if args.status:
        engine = get_engine()
        with engine.begin() as connection:
            connection.execute(text(_TRACKING_TABLE))
        with engine.connect() as connection:
            already = applied_filenames(connection)
        for path in migration_files():
            state = "applied" if path.name in already else "PENDING"
            print(f"{state:>8}  {path.name}")
        return 0

    performed = apply_migrations()
    if not performed:
        print("No pending migrations.")
    for name in performed:
        print(f"applied  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
