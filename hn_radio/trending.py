"""A live Hacker News front-page snapshot for the feed page's idle state.

Wraps ingest.fetch_front_page and owns a small in-process TTL cache, so the status board can show
what is on the front page right now without a cron job or a write to the episodes volume.

Best effort, following the same rule as status.py: a snapshot must never raise at the caller. A
failed fetch serves the last good snapshot, or an empty one if there has never been a good fetch.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Optional

from . import ingest

TTL_SECONDS = 60 * 60   # the front page does not move fast enough to justify anything shorter
FAILURE_TTL_SECONDS = 60   # suppress refetch attempts for this long after a failed fetch

_cache: Optional[dict] = None
_cached_at: float = 0.0   # time.monotonic(), so a system clock change cannot wedge the cache
_failed_at: float = 0.0   # time.monotonic() of the last failed fetch; 0.0 means no failure on record

# Single-flight guard. At most one fetch runs at a time; anyone arriving while it is in progress is
# served the existing snapshot immediately instead of starting a second fetch.
#
# This is the real fix for thread occupancy, and it is worth being precise about why. The route is a
# sync `def`, so FastAPI runs it in anyio's threadpool (40 tokens by default). Rewriting it as
# `async def` with `to_thread.run_sync` would draw from that *same* pool, so it would change nothing.
# What actually threatened availability was concurrency: with a cold or stale cache, N simultaneous
# requests each began their own fetch_front_page, and each of those walks the ranked id list with
# retries at a 20s timeout. Enough of them and the shared pool is exhausted, which stalls
# /api/status and static file serving too. Capping in-flight fetches at one bounds that to a single
# occupied thread regardless of traffic.
_fetch_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _copy_cache(cache: dict) -> dict:
    """Return a deep copy of the cache so callers cannot mutate the live module state.

    Callers may sort, filter, or annotate the returned list in place before rendering. If we
    handed out a reference to the shared cache, those mutations would corrupt the cache for
    every subsequent call until the next successful refetch. Copying is the defense.
    """
    return {
        "stories": [dict(row) for row in cache["stories"]],
        "fetched_at": cache["fetched_at"],
        "total": cache["total"]
    }


def _empty() -> dict:
    return {"stories": [], "fetched_at": None, "total": 0}


def _fresh() -> bool:
    return _cache is not None and (time.monotonic() - _cached_at) < TTL_SECONDS


def _suppressed() -> bool:
    """True while a recent failure should stop us attempting another fetch."""
    return bool(_failed_at) and (time.monotonic() - _failed_at) < FAILURE_TTL_SECONDS


def _stale_or_empty() -> dict:
    """The best we can do without fetching: the last good snapshot, or nothing."""
    return _copy_cache(_cache) if _cache is not None else _empty()


def reset_cache() -> None:
    """Drop the cache, including the failure stamp. Used by tests so each one controls its own
    fetch behavior; without clearing the failure stamp too, a boom-then-reset sequence in one test
    would leak a suppressed refetch window into the next."""
    global _cache, _cached_at, _failed_at
    _cache = None
    _cached_at = 0.0
    _failed_at = 0.0


def snapshot(limit: int = 10) -> dict:
    """Return the current front page, from cache when it is still fresh.

    `total` is len(stories), not the size of the whole HN front page, so callers must not present
    it as a count of everything on HN.

    A failed fetch is cached too, negatively: `_failed_at` is a separate timestamp from
    `_cached_at`, so a failure can never look like a fresh success and extend a warm snapshot's
    TTL. It only suppresses the next fetch attempt for FAILURE_TTL_SECONDS, which keeps an HN
    outage from re-running the full retrying fetch on every request.

    At most one fetch is in flight at a time. Concurrent callers get the current snapshot rather
    than queueing behind it, because waiting would hold a threadpool thread for no benefit: they
    would all end up with the same answer the winner is about to store.
    """
    global _cache, _cached_at, _failed_at

    if _fresh():
        return _copy_cache(_cache)

    if _suppressed():
        return _stale_or_empty()

    # Do not queue. Another caller is already fetching and will publish the same result.
    if not _fetch_lock.acquire(blocking=False):
        return _stale_or_empty()
    try:
        # Re-check under the lock: while we were acquiring it, the previous holder may have just
        # published a fresh snapshot, or recorded a failure we should now respect.
        if _fresh():
            return _copy_cache(_cache)
        if _suppressed():
            return _stale_or_empty()

        try:
            stories = ingest.fetch_front_page(n=limit)
        except Exception:
            _failed_at = time.monotonic()
            return _stale_or_empty()

        rows = [{"rank": s.rank, "hn_id": s.id, "title": s.title, "author": s.author,
                 "points": s.points, "url": s.hn_url} for s in stories]
        _cache = {"stories": rows, "fetched_at": _now_iso(), "total": len(rows)}
        _cached_at = time.monotonic()
        # Clear the failure stamp. It was only ever harmless because TTL_SECONDS (3600) dwarfs
        # FAILURE_TTL_SECONDS (60), so the freshness check always won before a stale failure stamp
        # could matter. Lower TTL_SECONDS under 60 and a recovered HN would have stayed suppressed.
        _failed_at = 0.0
        return _copy_cache(_cache)
    finally:
        _fetch_lock.release()
