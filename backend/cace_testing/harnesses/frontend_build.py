"""Frontend production bundle build harness for CACE L2 verification."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cace_testing.harnesses.fixtures_paths import frontend_dir

DEFAULT_BUILD_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class FrontendBuildObservation:
    """Raw build process observation returned without pass/fail judgment."""

    returncode: int
    stdout: str
    stderr: str
    index_html_path: Path
    index_html_exists: bool


def run_frontend_build() -> FrontendBuildObservation:
    """Run ``npm run build`` in ``frontend/`` and return build observations."""
    npm_bin = shutil.which("npm")
    if npm_bin is None:
        raise FileNotFoundError("npm binary not found on PATH")

    target_dir = frontend_dir()
    completed = subprocess.run(  # noqa: S603  # fixed argv on resolved npm executable
        [npm_bin, "--prefix", str(target_dir), "run", "build"],
        check=False,
        capture_output=True,
        text=True,
        timeout=DEFAULT_BUILD_TIMEOUT_SECONDS,
    )
    index_path = target_dir / "dist" / "index.html"
    return FrontendBuildObservation(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        index_html_path=index_path,
        index_html_exists=index_path.is_file(),
    )
