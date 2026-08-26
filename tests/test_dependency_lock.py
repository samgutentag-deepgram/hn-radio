"""Guard the dependency lock, the other half of local-versus-production parity.

Pinning the interpreter (see test_python_version.py) stopped the suite running on a Python three
minor versions behind the container. But the dependency set drifted the same way: with `>=` floors
and no lock, the local venv and the image each resolved on whatever day they were built, and a
rebuild on 3.12 pulled FastAPI 0.141 where the previous venv had 0.128.

pyproject.toml is now the human-edited source and uv.lock is the generated, fully pinned lock that
both `make install` and the Dockerfile install from. uv.lock is universal by construction -- one
file, correct on macOS arm64 (local) and linux amd64 (the image) -- which is what the old
`uv pip compile --universal` flag was buying by hand.

Everything here reads the two files with tomllib rather than shelling out to `uv lock --check`, so
the suite still passes anywhere Python runs, with or without uv on PATH.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"
PIN_FILE = ROOT / ".python-version"
DOCKERFILE = ROOT / "Dockerfile"

# `fastapi>=0.110`, `uvicorn[standard]>=0.29` -> the distribution name, normalized.
_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _norm(name: str) -> str:
    return name.split("[")[0].lower().replace("_", "-")


def _toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _declared() -> set[str]:
    """Every dependency a human wrote in pyproject.toml, runtime and dev alike."""
    src = _toml(SRC)
    specs = list(src["project"].get("dependencies", []))
    for group in src.get("dependency-groups", {}).values():
        specs += group
    return {_norm(_NAME.match(s).group(1)) for s in specs if _NAME.match(s)}


def _locked() -> dict[str, str]:
    """Third-party packages in the lock, name -> pinned version.

    Excludes the root entry: `hn-radio` appears in its own lock as `source = { virtual = "." }`
    because `tool.uv.package = false` makes this a virtual project. It is never installed, so
    asking importlib for its version would fail for a reason that is not a drift.
    """
    return {
        _norm(p["name"]): p["version"]
        for p in _toml(LOCK)["package"]
        if "virtual" not in p.get("source", {})
    }


def test_both_files_exist():
    assert SRC.exists(), "pyproject.toml is the human-edited source"
    assert LOCK.exists(), "uv.lock is the generated lock; run `make lock`"


def test_every_locked_package_carries_an_exact_version():
    """An entry without a version is a package that can still differ between local and the image."""
    unpinned = [p.get("name", "?") for p in _toml(LOCK)["package"] if not p.get("version")]
    assert not unpinned, f"lock entries without a version: {unpinned}"


def test_the_lock_targets_the_same_interpreter_as_everything_else():
    """A lock resolved for a different Python than the one that ships is a lock about nothing.

    This is the check that used to be a hand-written "GENERATED FILE" banner. uv manages the file,
    so nobody needs telling not to edit it; what does need pinning is that its `requires-python`
    still agrees with .python-version and the Dockerfile's base image.
    """
    pin = PIN_FILE.read_text().strip()
    assert _toml(LOCK)["requires-python"] == f"=={pin}.*", (
        f"uv.lock targets {_toml(LOCK)['requires-python']} but .python-version pins {pin}. "
        "Fix `requires-python` in pyproject.toml and re-run `make lock`."
    )
    assert _toml(SRC)["project"]["requires-python"] == f"=={pin}.*", (
        "pyproject.toml's requires-python must pin the same exact minor as .python-version"
    )


def test_every_direct_dependency_appears_in_the_lock():
    """A dep added to pyproject.toml but never re-locked would silently not be installed."""
    missing = _declared() - set(_locked())
    assert not missing, \
        f"declared in pyproject.toml but absent from the lock, run `make lock`: {sorted(missing)}"


def test_declared_dependencies_stay_unpinned_so_upgrades_remain_possible():
    """pyproject expresses intent with floors; pinning both files would defeat `make lock`."""
    src = _toml(SRC)
    specs = list(src["project"]["dependencies"])
    pinned = [s for s in specs if "==" in s]
    assert not pinned, \
        f"pyproject.toml dependencies should hold floors, not exact pins: {pinned}"


def test_the_installed_environment_matches_the_lock():
    """The venv actually running these tests must be what the lock describes.

    This is the assertion that makes the rest meaningful: a correct lock nobody installed from
    proves nothing.
    """
    from importlib.metadata import PackageNotFoundError, version

    mismatches = []
    for name, want in _locked().items():
        try:
            got = version(name)
        except PackageNotFoundError:
            continue  # a platform marker excluded it here; that is expected of a universal lock
        if got != want:
            mismatches.append(f"{name}: installed {got}, lock says {want}")
    assert not mismatches, "venv drifted from the lock, run `make install`: " + "; ".join(mismatches)


def test_the_image_installs_the_lock_without_dev_dependencies():
    """pytest and httpx have no business in production, and `--frozen` is what makes the lock bind.

    Pinning the RULE rather than the package list, on purpose. `--no-dev` keeps the dev group out
    however that group changes later, and `--frozen` makes the build fail on a stale lock instead
    of quietly re-resolving to something no one tested.
    """
    dockerfile = DOCKERFILE.read_text()
    m = re.search(r"^RUN uv sync\b(.*)$", dockerfile, re.MULTILINE)
    assert m, "the Dockerfile must install dependencies with `uv sync`"
    flags = m.group(1)
    assert "--no-dev" in flags, "uv sync in the image must pass --no-dev, or pytest ships to prod"
    assert "--frozen" in flags, (
        "uv sync in the image must pass --frozen, or a stale lock re-resolves at build time and the "
        "image stops being what the lock describes"
    )


def test_no_workflow_or_script_still_installs_from_requirements_txt():
    """The lock changed format; anything that installs the old way is broken and cannot say so.

    `.github/workflows/daily-episode.yml` is the reason this exists. It is dormant -- manual
    dispatch only -- so nothing failed when the migration deleted requirements.txt, and nothing
    would have failed until the night somebody enabled it and the install step died. A dormant
    entry point is exactly where this rots, so the check is on the text of every entry point rather
    than on running them.
    """
    entry_points = sorted(ROOT.glob(".github/workflows/*.yml")) + \
        sorted(ROOT.glob(".github/workflows/*.yaml")) + [ROOT / "Makefile", DOCKERFILE]
    offenders = []
    for path in entry_points:
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text().splitlines(), start=1):
            bare = line.split("#", 1)[0]
            if "requirements.txt" in bare or "requirements.in" in bare:
                offenders.append(f"{path.relative_to(ROOT)}:{i}")
    assert not offenders, (
        "these install from the retired requirements files; use `uv sync --frozen`: "
        + ", ".join(offenders)
    )
