"""The guards on the endpoints that render: one at a time, and not many per caller.

`POST /api/recast` and `/api/build` each run a full pipeline synchronously on the
one shared-cpu-1x machine that also serves the site, spending our server-side keys. (`/api/generate`
was a third until 2026-08-22.) Before these
guards existed there was no auth, no throttle, and nothing rejecting a second concurrent run, so two
people clicking Build at once were two pipelines on one vCPU with 1GB of RAM.

Nothing here renders. Every test either trips a guard before the handler runs, or lets the handler
fail fast on an empty story pool, which is enough to prove a slot was CHARGED.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import limits
from hn_radio import custom


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """Temp episode store, no network, and an empty pool so /api/build fails fast with a 400."""
    monkeypatch.setattr(custom.config, "EPISODES_DIR", tmp_path)
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])
    custom.reset_live_cache()
    yield
    custom.reset_live_cache()


def _client():
    from backend.app import app
    return TestClient(app)


def _build(client, ip="203.0.113.7"):
    """One render request, tagged with a caller address the way Fly's proxy tags it."""
    return client.post("/api/build", json={"desks": {"ai": "flux-meena-en"}},
                       headers={"Fly-Client-IP": ip})


# --- the per-caller quota -----------------------------------------------------------------------

def test_a_caller_gets_the_limit_and_then_a_429():
    client = _client()
    for i in range(limits.MAX_RENDERS):
        r = _build(client)
        assert r.status_code != 429, f"request {i + 1} was refused while under the limit"
    refused = _build(client)
    assert refused.status_code == 429
    assert "Rate limit" in refused.json()["detail"]


def test_the_429_says_when_to_come_back():
    """A caller who cannot retry intelligently will retry stupidly, against the thing we protect."""
    client = _client()
    for _ in range(limits.MAX_RENDERS):
        _build(client)
    refused = _build(client)
    assert "retry-after" in {k.lower() for k in refused.headers}
    assert int(refused.headers["Retry-After"]) > 0


def test_one_caller_hitting_the_limit_does_not_block_everyone_else():
    """The failure this guards against is a shared counter, which turns one abuser into an outage."""
    client = _client()
    for _ in range(limits.MAX_RENDERS):
        _build(client, ip="203.0.113.7")
    assert _build(client, ip="203.0.113.7").status_code == 429
    assert _build(client, ip="198.51.100.4").status_code != 429


def test_a_forged_forwarded_header_cannot_mint_new_identities():
    """`X-Forwarded-For` is caller-controlled, so trusting it would make the per-IP limit a no-op.

    Only `Fly-Client-IP`, which Fly's proxy sets and a client cannot forge, decides identity.
    """
    client = _client()
    for _ in range(limits.MAX_RENDERS):
        _build(client, ip="203.0.113.7")
    evasion = client.post("/api/build", json={"desks": {"ai": "flux-meena-en"}},
                          headers={"Fly-Client-IP": "203.0.113.7",
                                   "X-Forwarded-For": "203.0.113.99"})
    assert evasion.status_code == 429, "X-Forwarded-For was trusted; the limit can be walked around"


# --- one render at a time -----------------------------------------------------------------------

def test_a_second_render_is_refused_while_one_is_running():
    client = _client()
    assert limits._render_slot.acquire(blocking=False), "slot should be free at test start"
    try:
        refused = _build(client)
    finally:
        limits._render_slot.release()
    assert refused.status_code == 429
    assert "already in progress" in refused.json()["detail"]


def test_being_refused_for_someone_elses_render_costs_you_nothing():
    """Charging a caller for a slot they never got would be a limit on the wrong person."""
    client = _client()
    assert limits._render_slot.acquire(blocking=False)
    try:
        for _ in range(limits.MAX_RENDERS + 2):
            assert _build(client).status_code == 429
    finally:
        limits._render_slot.release()
    # None of those consumed quota, so a full allowance is still available.
    for i in range(limits.MAX_RENDERS):
        assert _build(client).status_code != 429, f"request {i + 1} was charged for a busy slot"


def test_the_slot_is_released_when_a_render_fails():
    """An endpoint that wedges until the next deploy is worse than the crash that wedged it."""
    client = _client()

    def boom(*a, **k):
        raise RuntimeError("DEEPGRAM_API_KEY not found")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(custom, "build", boom)
        assert _build(client).status_code == 502
    assert not limits._render_slot.locked(), "a failed render left the slot held"


# --- what must NOT be guarded -------------------------------------------------------------------

def test_pricing_a_build_is_never_throttled():
    """`/api/build/plan` spends nothing. Throttling it would only stop people learning the cost."""
    client = _client()
    for _ in range(limits.MAX_RENDERS + 3):
        r = client.post("/api/build/plan", json={"desks": {"ai": "flux-meena-en"}},
                        headers={"Fly-Client-IP": "203.0.113.7"})
        assert r.status_code != 429


def test_reading_the_site_is_never_throttled():
    client = _client()
    for _ in range(limits.MAX_RENDERS + 3):
        for path in ("/api/health", "/api/status", "/episodes/index.json"):
            assert client.get(path, headers={"Fly-Client-IP": "203.0.113.7"}).status_code != 429


def test_a_read_only_visitor_is_not_even_tracked():
    """Quota is charged on renders, not on page views, so a browsing reader leaves no state."""
    client = _client()
    client.get("/api/status", headers={"Fly-Client-IP": "203.0.113.7"})
    assert "203.0.113.7" not in limits._hits


# --- configuration ------------------------------------------------------------------------------

def test_an_unusable_limit_falls_back_instead_of_disabling_the_guard(monkeypatch):
    """A typo in a Fly secret must not silently remove the protection it was meant to tune."""
    for bad in ("0", "-4", "three", ""):
        monkeypatch.setenv("HN_RADIO_RENDER_LIMIT", bad)
        assert limits._positive_int_env("HN_RADIO_RENDER_LIMIT", 3) == 3
    monkeypatch.setenv("HN_RADIO_RENDER_LIMIT", "9")
    assert limits._positive_int_env("HN_RADIO_RENDER_LIMIT", 3) == 9
