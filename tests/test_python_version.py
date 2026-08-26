"""Guard the one thing that makes a green local suite mean anything: the interpreter.

A stock Mac's `python3` is Xcode's 3.9, while the container is python:3.12-slim. A venv built from
the system python therefore runs the suite on an interpreter three minor versions behind
production. That is how `str | None` in a request model passed in the container and crashed
locally, and it would just as easily hide the reverse.

Two invariants:
  1. The interpreter running the tests matches .python-version.
  2. .python-version matches the Dockerfile's base image, so the pin cannot silently drift from
     what actually ships.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN_FILE = ROOT / ".python-version"
DOCKERFILE = ROOT / "Dockerfile"


def _pinned() -> str:
    return PIN_FILE.read_text().strip()


def test_pin_file_exists_and_is_a_major_minor():
    assert PIN_FILE.exists(), ".python-version is the single source of truth for the interpreter"
    assert re.fullmatch(r"\d+\.\d+", _pinned()), \
        f"expected a bare major.minor like '3.12', got {_pinned()!r}"


def test_running_interpreter_matches_the_pin():
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert running == _pinned(), (
        f"tests are running on Python {running} but the project pins {_pinned()}. "
        "A pass here would not tell you anything about production. Run: make install"
    )


def test_dockerfile_base_image_matches_the_pin():
    """If these drift, local and production disagree and nothing above would catch it."""
    m = re.search(r"^FROM python:(\d+\.\d+)", DOCKERFILE.read_text(), re.MULTILINE)
    assert m, "could not find a `FROM python:<version>` line in the Dockerfile"
    assert m.group(1) == _pinned(), (
        f"Dockerfile ships Python {m.group(1)} but .python-version pins {_pinned()}. "
        "Update both together."
    )
