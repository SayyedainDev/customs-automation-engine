"""Deployment bootstrap remains aligned with the documented database setup."""

from pathlib import Path


def test_docker_bootstrap_packages_and_runs_migrations_before_uvicorn() -> None:
    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    source = dockerfile.read_text(encoding="utf-8")

    assert (
        "COPY backend/scripts/apply_migrations.py "
        "./scripts/apply_migrations.py"
    ) in source
    command = next(line for line in source.splitlines() if line.startswith("CMD "))
    assert command.index("python -m app.core.init_db") < command.index(
        "python -m scripts.apply_migrations"
    )
    assert command.index("python -m scripts.apply_migrations") < command.index(
        "python -m uvicorn"
    )
