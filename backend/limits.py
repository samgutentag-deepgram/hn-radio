"""Guards on the two endpoints that render audio: /api/recast and /api/build.

(There were three until 2026-08-22, when /api/generate was deleted as callerless.)

Why this exists, in the order the risks actually bite.

**Availability, first.** Each of those endpoints runs a full pipeline synchronously: one Claude call
plus roughly twenty Flux batch calls, 16-20 segments, about five minutes of audio. It holds the raw
PCM plus the paced and musicked copies in memory and then shells out to ffmpeg. The Fly machine is
`shared-cpu-1x` with 1024mb, and it is the same machine serving the site. Nothing rejected a second
concurrent run, so two curious people clicking Build at once were two full pipelines on one shared
vCPU, and a handful was an out-of-memory kill of the page everyone had come to look at.

**Cost, second.** The deployed app holds the Deepgram and Anthropic keys server-side, on purpose:
that is what makes the build page one click instead of a signup. Someone who clones the repo spends
their own key, but everyone hitting the public host spends ours. The ledger already recorded the
decision that "rate limiting plus config-hash cache reuse is the only cost control". The cache reuse
was built. This is the other half.

Two separate mechanisms, because they answer different questions:

  - ONE render slot, process-wide, shared by all three routes. The scarce thing is the machine, not
    the route, so per-route locks would not have helped.
  - A per-IP sliding window, so one person cannot queue up twenty renders back to back even though
    each one waits politely for the last.

Deliberately in-process, with no Redis and no database. State resets on deploy, which for a demo is
the right trade: a persistent store is a new dependency, a new failure mode, and a new thing to
explain in a post whose argument is that this repo is simple enough to read.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict

from fastapi import HTTPException, Request


def _positive_int_env(name: str, default: int) -> int:
    """Read an int from the environment, falling back on anything unusable.

    A malformed limit must not take the app down at import, and a zero or negative one must not
    silently disable the guard: both fall back to the default. Tunable at deploy time so a spike
    can be ridden out with `fly secrets set` rather than a code change.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_RENDERS = _positive_int_env("HN_RADIO_RENDER_LIMIT", 3)
WINDOW_SECONDS = _positive_int_env("HN_RADIO_RENDER_WINDOW", 3600)

# What to tell a caller who arrived while a render was already running. A render takes minutes, so
# a short number here would just invite a tight retry loop against the thing we are protecting.
BUSY_RETRY_AFTER = 120

# Bound on how many distinct IPs we remember. Unbounded would itself be the attack: a dict keyed by
# caller-controlled addresses on a 1GB machine. When we cross it, drop the entries that have aged
# out anyway; if that frees nothing, the window is genuinely full of real callers and the oldest go.
MAX_TRACKED_IPS = 10_000

_render_slot = threading.Lock()
_hits_guard = threading.Lock()
_hits: Dict[str, Deque[float]] = {}


def client_ip(request: Request) -> str:
    """The caller's address, as far as it can be trusted.

    `Fly-Client-IP` is set by Fly's proxy and cannot be forged by the client, so it is the answer in
    production. `X-Forwarded-For` is deliberately NOT consulted: any caller can send it, so trusting
    it would let one person present as unlimited addresses and walk straight through a per-IP limit.
    Falls back to the socket peer, which is what a local `make start` sees.
    """
    fly = request.headers.get("fly-client-ip")
    if fly and fly.strip():
        return fly.strip()
    return request.client.host if request.client else "unknown"


def _prune_locked(now: float) -> None:
    """Drop aged-out timestamps and any IP left with none. Caller holds `_hits_guard`."""
    for ip in list(_hits):
        q = _hits[ip]
        while q and now - q[0] >= WINDOW_SECONDS:
            q.popleft()
        if not q:
            del _hits[ip]


def _claim(ip: str) -> float:
    """Record one render against `ip`. Returns 0.0 if allowed, else seconds until a slot frees.

    A sliding window rather than a fixed one, so the limit cannot be doubled by straddling a
    boundary: three at 12:59 and three at 13:01 is six renders in two minutes under a fixed window.
    """
    now = time.monotonic()
    with _hits_guard:
        if len(_hits) >= MAX_TRACKED_IPS:
            _prune_locked(now)
            if len(_hits) >= MAX_TRACKED_IPS:
                for stale in sorted(_hits, key=lambda k: _hits[k][0])[:MAX_TRACKED_IPS // 10]:
                    del _hits[stale]
        q = _hits.setdefault(ip, deque())
        while q and now - q[0] >= WINDOW_SECONDS:
            q.popleft()
        if len(q) >= MAX_RENDERS:
            return max(1.0, WINDOW_SECONDS - (now - q[0]))
        q.append(now)
        return 0.0


def render_slot(request: Request):
    """FastAPI dependency: admit one render, or refuse with 429.

    Attach with `dependencies=[Depends(render_slot)]` so handler signatures stay unchanged.

    The slot is taken BEFORE the quota is charged, and that order is the point: a caller turned away
    because someone else was mid-render has not had a render, so it would be wrong to spend one of
    their three on it. Being refused for a reason that is not your fault should cost nothing.

    The `yield` is what holds the slot for the whole render. FastAPI runs the teardown of a
    generator dependency after the response, and the `finally` runs on an exception too, so a
    pipeline that raises releases the slot rather than wedging the endpoint until the next deploy.
    """
    if not _render_slot.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="A render is already in progress. This demo renders one episode at a time.",
            headers={"Retry-After": str(BUSY_RETRY_AFTER)},
        )
    try:
        wait = _claim(client_ip(request))
        if wait:
            raise HTTPException(
                status_code=429,
                detail=(f"Rate limit: {MAX_RENDERS} renders per "
                        f"{WINDOW_SECONDS // 60} minutes. Each one spends real TTS."),
                headers={"Retry-After": str(int(wait) + 1)},
            )
        yield
    finally:
        _render_slot.release()


def reset() -> None:
    """Forget every quota and force the slot free. For tests, and only tests.

    The suite shares one process, so without this a test that renders three times would decide
    whether an unrelated test later sees a 429. `tests/conftest.py` calls it before every test.
    """
    with _hits_guard:
        _hits.clear()
    if _render_slot.locked():
        try:
            _render_slot.release()
        except RuntimeError:
            pass  # released concurrently; the slot is free either way, which is all we wanted
