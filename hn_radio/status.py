"""A tiny generation-status record for the admin status board.

Every generation path (daily cron, manual run, recast) updates EPISODES_DIR/status.json as it
works; the app serves it and the feed page polls it to show "work in flight" with live progress.
Best-effort: a status write must never break generation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import config

# How long an in-flight run may go without a single status write before we call it stalled.
#
# Generous on purpose, because two legitimate phases are silent for a while: the Claude writer is
# one long call with no per-step progress, and "publishing" covers chapters, the MP3 transcode and
# the feed. Rendering updates per segment, so it is the phases either side of it that set the floor.
# Too low and the board cries wolf every night; too high and a dead run looks busy until morning.
DEFAULT_STALL_SECONDS = 900


def stall_seconds() -> int:
    raw = os.environ.get("HN_RADIO_STALL_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass  # a typo must not disable stall detection
    return DEFAULT_STALL_SECONDS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _path():
    return config.EPISODES_DIR / "status.json"


def read() -> dict:
    p = _path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"state": "idle"}


def _write(data: dict) -> None:
    data["updated_at"] = _now()
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data))
    except Exception:
        pass  # status is best-effort; never break a generation over it


def begin(episode: str, edition: str = "") -> None:
    _write({"state": "starting", "episode": episode, "edition": edition,
            "step": 0, "total": 0, "note": "starting up", "started_at": _now(),
            "last_error": None, "last_completed": read().get("last_completed")})


def stage(state: str, note: str, step: int = 0, total: int = 0) -> None:
    data = read()
    data.update({"state": state, "note": note, "step": step, "total": total})
    _write(data)


def done(episode: str, duration: float) -> None:
    _write({"state": "idle", "note": "", "step": 0, "total": 0, "last_error": None,
            "last_completed": {"episode": episode, "at": _now(), "duration_seconds": round(duration)}})


def error(message: str) -> None:
    data = read()
    data.update({"state": "error", "last_error": str(message)[:300]})
    _write(data)


def in_flight(data: dict) -> bool:
    """True while a run is supposed to be working.

    Defined as "not idle and not error" rather than by listing the stage names, so a stage added
    later is covered without anyone remembering to update this. `begin`, `stage`, `done` and
    `error` are the only writers, and the first two always leave a working state behind.
    """
    return data.get("state") not in ("idle", "error", None)


def silent_for(data: dict) -> float | None:
    """Seconds since the last status write, or None if that cannot be established.

    None is not "fine". It means the record has no parseable `updated_at`, which is itself a reason
    to look, so callers must not treat it as healthy.
    """
    raw = data.get("updated_at")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())


def is_stalled(data: dict | None = None) -> bool:
    """True when a run claims to be working but has not written anything in a long time.

    This is the half `error()` cannot cover, and the distinction is the whole point. A CRASH raises,
    so something can catch it and call `error()`. A HANG raises nothing -- a socket with no timeout,
    a wedged ffmpeg, a machine that lost its network -- so no exception ever arrives, `error()` is
    never called, and `status.json` sits at `{"state": "rendering"}` forever while the page shows a
    spinner. Nothing detects that from the inside. It can only be inferred from silence.
    """
    data = read() if data is None else data
    if not in_flight(data):
        return False
    quiet = silent_for(data)
    return quiet is None or quiet > stall_seconds()
