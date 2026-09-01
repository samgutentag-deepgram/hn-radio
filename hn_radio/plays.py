"""Play instrumentation: an append-only event log on the volume, and the rollup read off it.

WHAT THIS CAN AND CANNOT SEE. It counts plays that happen ON THE SITE. A podcast client that
pulled `feed.xml` and played the MP3 is invisible here and always will be, because the only thing
that would catch it is byte-range hits on `episode.mp3` in an access log, and the Fly proxy does
not retain one. So every number this module produces is "on the dashboard", never "in the world",
and the stats page says so rather than letting someone read it as total listenership.

WHY A FILE AND NOT A DATABASE. `backend/limits.py` already argued this for rate limiting: a
persistent store is a new dependency, a new failure mode, and a new thing to explain in a post
whose argument is that this repo is simple enough to read. The same holds here with one addition
in its favour -- a JSONL log is greppable. Answering "what happened the night the feed 404'd" is
`grep <date> plays.jsonl`, with no query language and no client.

THREE RULES THE REST OF THE FILE FOLLOWS.

1. **Best-effort, exactly like `status.py`.** Every write is wrapped and every read falls back to
   empty. A counter that raises would take down playback, and playback is the product. A number
   that is briefly wrong is a rounding error; a page that will not load is an outage.

2. **The rollup is DERIVED, never stored.** Nothing anywhere increments a counter in place. Totals
   are recomputed from the log on every read, so there is no number that can drift out of sync
   with the events behind it, and a bad deploy cannot corrupt a figure that a re-read would fix.
   The one file that does hold numbers, `plays-totals.json`, is a fold of log lines that were
   rotated away, and it is only ever written by that fold.

3. **A row cannot carry an identity.** `_row` builds a closed set of four keys rather than passing
   a dict through, so there is no seam where an IP or a user agent gets added without someone
   editing this line and failing `test_a_line_carries_a_timestamp_and_nothing_identifying`. The
   "one play per session" de-duplication is done in the browser with `sessionStorage` precisely so
   that the server never needs a visitor id to do it.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

# The events the page is allowed to report. `view` is the episode page loading, `play` is the
# first playback start in a session, `progress` is one of the four marks below.
EVENTS = ("view", "play", "progress")

# Fixed marks rather than a free percentage. Four numbers make a funnel that can be read at a
# glance; an arbitrary integer makes a histogram nobody asked for and lets a caller invent rows.
MILESTONES = (25, 50, 75, 100)

# Roll the live log over at roughly this size. Five megabytes is on the order of 60k events, which
# at this show's traffic is years, so rotation is a safety valve rather than a routine event.
MAX_LOG_BYTES = 5 * 1024 * 1024

# How much day-by-day history the baseline keeps after a rotation. Per-episode totals are kept
# forever because they are one small row each; the daily series is capped because it grows without
# bound and nothing on the stats page looks back further than a few weeks.
BASELINE_DAILY_DAYS = 180

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# One process writes this file, but FastAPI dispatches these sync handlers to a threadpool, so two
# beacons genuinely can land at once. The lock keeps an append atomic against itself; the `"a"`
# mode plus a single small write is what keeps it atomic against the nightly cron in the same
# container, which is a different process and cannot see this lock.
_write_guard = threading.Lock()

_cache_lock = threading.Lock()
_cache_key = None
_cache_value = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log_path() -> Path:
    return config.EPISODES_DIR / "plays.jsonl"


def totals_path() -> Path:
    return config.EPISODES_DIR / "plays-totals.json"


def _empty_counters() -> dict:
    return {"views": 0, "plays": 0, "p25": 0, "p50": 0, "p75": 0, "completes": 0}


def known_episode(episode_id: str) -> bool:
    """True when `episode_id` names an episode that actually exists on disk.

    This is the anti-junk guard, and it is the reason the endpoint is not a free write primitive.
    The id becomes both a path segment and a permanent JSON key, so it is checked twice: the regex
    rejects anything that is not an id at all (traversal, whitespace, an empty string, a novel),
    and the existence check rejects a well-formed id for an episode nobody ever rendered.

    Order matters. The regex runs FIRST, so a traversal string never reaches the filesystem.
    """
    if not isinstance(episode_id, str) or not _ID_RE.match(episode_id):
        return False
    return (config.EPISODES_DIR / episode_id / "episode.json").exists()


def _row(episode_id: str, event: str, pct=None) -> dict | None:
    """One log line, or None if the caller described something that is not an event.

    Short keys because this is the only file in the repo where the key names are a meaningful
    fraction of the bytes on disk, and it is append-only forever.
    """
    if event not in EVENTS:
        return None
    row = {"t": _now(), "ep": episode_id, "e": event}
    if event == "progress":
        if pct not in MILESTONES:
            return None
        row["pct"] = pct
    return row


def _append(line: str) -> None:
    """The single write. Isolated so a test can make storage fail without faking a filesystem."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_guard:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def record(episode_id: str, event: str, pct=None) -> None:
    """Append one event. Never raises, never blocks on anything but a local write.

    Callers do NOT check a return value, because there is no useful thing for a request handler to
    do about a counter that failed. That is the whole contract: fire it and move on.
    """
    row = _row(episode_id, event, pct)
    if row is None:
        return
    try:
        _append(json.dumps(row, separators=(",", ":")))
        _rotate_if_needed()
    except Exception:
        pass  # a counter must never break the page it is counting


def _read_lines() -> list:
    """Parsed rows from the live log, skipping anything unreadable.

    A torn final line is the expected failure, not an exotic one: a machine killed mid-append
    leaves exactly that. It costs one event, so it is skipped rather than escalated. The whole
    read is wrapped too, so a permissions change or a vanished volume reads as "no events".
    """
    path = log_path()
    try:
        if not path.exists():
            return []
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return []
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # torn or hand-edited; worth one event, not the file
        if isinstance(row, dict) and isinstance(row.get("ep"), str) and row.get("e") in EVENTS:
            rows.append(row)
    return rows


def _read_baseline() -> dict:
    try:
        raw = totals_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return {"episodes": {}, "daily": {}}
    episodes = data.get("episodes")
    daily = data.get("daily")
    return {
        "episodes": episodes if isinstance(episodes, dict) else {},
        "daily": daily if isinstance(daily, dict) else {},
    }


def _fold(rows: list, into: dict) -> dict:
    """Fold parsed rows into an `{episodes, daily}` accumulator, in place."""
    episodes = into["episodes"]
    daily = into["daily"]
    for row in rows:
        ep = episodes.setdefault(row["ep"], _empty_counters())
        day = str(row.get("t", ""))[:10]
        bucket = daily.setdefault(day, {"views": 0, "plays": 0, "completes": 0}) if day else None
        event = row["e"]
        if event == "view":
            ep["views"] += 1
            if bucket:
                bucket["views"] += 1
        elif event == "play":
            ep["plays"] += 1
            if bucket:
                bucket["plays"] += 1
        elif event == "progress":
            pct = row.get("pct")
            if pct == 100:
                ep["completes"] += 1
                if bucket:
                    bucket["completes"] += 1
            elif pct in MILESTONES:
                ep[f"p{pct}"] += 1
    return into


def _cache_stamp():
    """Identity of the files the rollup is derived from: existence, size and mtime of each.

    Cheap enough to do per request (two stats), and it means the rollup is recomputed exactly when
    something changed rather than on a timer that is either stale or wasteful.
    """
    stamp = []
    for path in (log_path(), totals_path()):
        try:
            st = path.stat()
            stamp.append((str(path), st.st_size, st.st_mtime_ns))
        except OSError:
            stamp.append((str(path), -1, -1))
    return tuple(stamp)


def counts() -> dict:
    """The whole rollup: per-episode counters, grand totals, and a per-day series.

    `daily` carries only days that actually have events, ascending. Filling the gaps is the
    caller's job, because the chart knows what range it wants to draw and this module does not.
    """
    global _cache_key, _cache_value
    stamp = _cache_stamp()
    with _cache_lock:
        if _cache_key == stamp and _cache_value is not None:
            return _cache_value

    acc = _fold(_read_lines(), _read_baseline())
    totals = _empty_counters()
    for counters in acc["episodes"].values():
        for key in totals:
            totals[key] += counters.get(key, 0)
    daily = [dict(date=day, **acc["daily"][day]) for day in sorted(acc["daily"])]
    value = {"episodes": acc["episodes"], "totals": totals, "daily": daily}

    with _cache_lock:
        _cache_key = stamp
        _cache_value = value
    return value


def reset_cache() -> None:
    """Drop the memoised rollup. For tests, and for a caller that just rotated the log."""
    global _cache_key, _cache_value
    with _cache_lock:
        _cache_key = None
        _cache_value = None


def _rotated_name(stamp: str) -> Path:
    """A free filename for the archive copy.

    The suffix loop is not paranoia: the timestamp has second resolution and a rotation triggered
    by a burst can fire twice inside one second, which without this would overwrite the archive it
    just wrote.
    """
    base = config.EPISODES_DIR / f"plays-{stamp}.jsonl"
    if not base.exists():
        return base
    for n in range(1, 1000):
        candidate = config.EPISODES_DIR / f"plays-{stamp}-{n}.jsonl"
        if not candidate.exists():
            return candidate
    return base


def _rotate_if_needed() -> None:
    """Fold the live log into the baseline and start a fresh one, once it gets large.

    The order is chosen so that no crash can double-count or lose a total:

      1. read and fold everything (baseline + live log) into the new baseline
      2. write the new baseline
      3. copy the live log aside for archaeology
      4. truncate the live log

    A crash between 2 and 4 leaves events counted in the baseline AND still present in the log,
    which double-counts until the next rotation completes -- visibly wrong but recoverable. The
    opposite order loses events outright. Given the choice, be wrong loudly rather than short
    quietly.

    Truncate rather than unlink, so `plays.jsonl` is always a file that exists.
    """
    path = log_path()
    try:
        if not path.exists() or path.stat().st_size <= MAX_LOG_BYTES:
            return
    except OSError:
        return

    acc = _fold(_read_lines(), _read_baseline())
    days = sorted(acc["daily"])[-BASELINE_DAILY_DAYS:]
    baseline = {"episodes": acc["episodes"], "daily": {d: acc["daily"][d] for d in days},
                "rotated_at": _now()}

    with _write_guard:
        tmp = totals_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(baseline), encoding="utf-8")
        os.replace(tmp, totals_path())
        _rotated_name(_now().replace(":", "").replace("-", "")).write_bytes(path.read_bytes())
        path.write_text("", encoding="utf-8")
    reset_cache()
