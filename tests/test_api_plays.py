"""The two routes the play counter adds: the beacon that writes, and the rollup that reads.

`POST /api/plays` is the first endpoint in this app that a browser calls WITHOUT anyone clicking
anything, and the first one whose whole job is to write caller-supplied data to the volume. Both
of those change what it has to defend against, and the tests are organised around that:

  - it must not become a free write primitive. The episode id is validated against what is
    actually on disk, so nobody can fill `plays.jsonl` with strings of their choosing.
  - it must not compete with a render. The guard here is its own window, never `render_slot`,
    because that is one process-wide lock held for the five minutes a Flux render takes and a
    counter queueing behind it would hang the page it is counting.
  - it must not be able to fail loudly. A listener pressing play does not care that a counter
    broke, so a storage failure is a 202 with nothing written, not a 500.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from hn_radio import plays


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(plays.config, "EPISODES_DIR", tmp_path)
    plays.reset_cache()
    yield
    plays.reset_cache()


def _client():
    """No `with` block: entering it runs the startup hook, which rebuilds files on disk."""
    from backend.app import app
    return TestClient(app)


def _episode(tmp_path, ep_id, title=None):
    d = tmp_path / ep_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "episode.json").write_text(json.dumps({"id": ep_id, "title": title or ep_id}))


def _post(client, body, ip="203.0.113.9"):
    return client.post("/api/plays", json=body, headers={"Fly-Client-IP": ip})


# --- the beacon ----------------------------------------------------------------------------

def test_a_play_is_recorded(tmp_path):
    _episode(tmp_path, "2026-08-26")
    r = _post(_client(), {"episode_id": "2026-08-26", "event": "play"})

    assert r.status_code == 202
    assert plays.counts()["episodes"]["2026-08-26"]["plays"] == 1


def test_every_event_the_page_sends_is_accepted(tmp_path):
    _episode(tmp_path, "2026-08-26")
    client = _client()
    assert _post(client, {"episode_id": "2026-08-26", "event": "view"}).status_code == 202
    assert _post(client, {"episode_id": "2026-08-26", "event": "play"}).status_code == 202
    for pct in (25, 50, 75, 100):
        body = {"episode_id": "2026-08-26", "event": "progress", "pct": pct}
        assert _post(client, body).status_code == 202

    got = plays.counts()["episodes"]["2026-08-26"]
    assert got == {"views": 1, "plays": 1, "p25": 1, "p50": 1, "p75": 1, "completes": 1}


def test_an_episode_that_does_not_exist_is_refused(tmp_path):
    """Otherwise the endpoint is an append-anything-you-like primitive on our volume."""
    r = _post(_client(), {"episode_id": "not-an-episode", "event": "play"})
    assert r.status_code == 400
    assert not (tmp_path / "plays.jsonl").exists()


def test_a_traversal_id_is_refused(tmp_path):
    _episode(tmp_path, "2026-08-26")
    r = _post(_client(), {"episode_id": "../../etc/passwd", "event": "play"})
    assert r.status_code == 400


def test_an_unknown_event_is_refused(tmp_path):
    _episode(tmp_path, "2026-08-26")
    r = _post(_client(), {"episode_id": "2026-08-26", "event": "subscribe"})
    assert r.status_code == 400


def test_progress_needs_a_milestone_we_publish(tmp_path):
    """The page fires at four fixed marks. An arbitrary percentage is a caller inventing rows."""
    _episode(tmp_path, "2026-08-26")
    client = _client()
    assert _post(client, {"episode_id": "2026-08-26", "event": "progress"}).status_code == 400
    assert _post(client, {"episode_id": "2026-08-26", "event": "progress",
                          "pct": 33}).status_code == 400


def test_a_storage_failure_still_answers_the_browser(tmp_path, monkeypatch):
    """A dead counter is not an error the listener should ever see."""
    _episode(tmp_path, "2026-08-26")

    def _boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(plays, "_append", _boom)
    assert _post(_client(), {"episode_id": "2026-08-26", "event": "play"}).status_code == 202


def test_the_beacon_is_rate_limited_per_caller(tmp_path, monkeypatch):
    from backend import limits
    monkeypatch.setattr(limits, "MAX_BEACONS", 3)
    _episode(tmp_path, "2026-08-26")
    client = _client()

    for i in range(3):
        r = _post(client, {"episode_id": "2026-08-26", "event": "view"})
        assert r.status_code == 202, f"request {i + 1} was refused while under the limit"
    refused = _post(client, {"episode_id": "2026-08-26", "event": "view"})
    assert refused.status_code == 429
    assert refused.headers.get("Retry-After")


def test_one_caller_hitting_the_beacon_limit_cannot_block_a_render(tmp_path, monkeypatch):
    """The two windows are separate on purpose: a counter must not spend a render quota.

    They are also separate LOCKS. If the beacon took `render_slot` it would serialise behind a
    five-minute Flux render, and the page's fire-and-forget POST would sit open the whole time.
    """
    from backend import limits
    monkeypatch.setattr(limits, "MAX_BEACONS", 1)
    _episode(tmp_path, "2026-08-26")
    client = _client()

    _post(client, {"episode_id": "2026-08-26", "event": "view"})
    assert _post(client, {"episode_id": "2026-08-26", "event": "view"}).status_code == 429
    assert "203.0.113.9" not in limits._hits, "a beacon charged the render quota"


def test_a_held_render_slot_does_not_stop_the_counter(tmp_path):
    from backend import limits
    _episode(tmp_path, "2026-08-26")
    assert limits._render_slot.acquire(blocking=False)
    try:
        r = _post(_client(), {"episode_id": "2026-08-26", "event": "play"})
    finally:
        limits._render_slot.release()
    assert r.status_code == 202


# --- the rollup ----------------------------------------------------------------------------

def test_stats_reports_per_episode_rows_and_totals(tmp_path):
    _episode(tmp_path, "2026-08-26", "Wednesday's front page")
    _episode(tmp_path, "2026-08-25", "Tuesday's front page")
    client = _client()
    _post(client, {"episode_id": "2026-08-26", "event": "view"})
    _post(client, {"episode_id": "2026-08-26", "event": "play"})
    _post(client, {"episode_id": "2026-08-26", "event": "progress", "pct": 100})
    _post(client, {"episode_id": "2026-08-25", "event": "view"})

    body = client.get("/api/stats").json()
    rows = {r["id"]: r for r in body["episodes"]}
    assert rows["2026-08-26"]["title"] == "Wednesday's front page"
    assert rows["2026-08-26"]["plays"] == 1
    assert rows["2026-08-26"]["completes"] == 1
    assert body["totals"]["views"] == 2


def test_stats_is_newest_first(tmp_path):
    for ep in ("2026-08-24", "2026-08-26", "2026-08-25"):
        _episode(tmp_path, ep)
    client = _client()
    for ep in ("2026-08-24", "2026-08-26", "2026-08-25"):
        _post(client, {"episode_id": ep, "event": "view"})

    ids = [r["id"] for r in client.get("/api/stats").json()["episodes"]]
    assert ids == ["2026-08-26", "2026-08-25", "2026-08-24"]


def test_stats_on_a_cold_store_is_an_empty_board_not_a_500(tmp_path):
    body = _client().get("/api/stats").json()
    assert body["episodes"] == []
    assert body["totals"]["plays"] == 0


def test_stats_keeps_an_episode_whose_files_were_deleted(tmp_path):
    """An id in the log outlives the episode directory, and its history is still true.

    Dropping the row would make a total stop matching the sum of its parts, which is the one thing
    a stats page cannot do and stay believable. It falls back to the id as its title.
    """
    _episode(tmp_path, "2026-08-26")
    _post(_client(), {"episode_id": "2026-08-26", "event": "play"})
    for p in (tmp_path / "2026-08-26").iterdir():
        p.unlink()
    (tmp_path / "2026-08-26").rmdir()
    plays.reset_cache()

    body = _client().get("/api/stats").json()
    rows = {r["id"]: r for r in body["episodes"]}
    assert rows["2026-08-26"]["plays"] == 1
    assert rows["2026-08-26"]["title"] == "2026-08-26"
    assert body["totals"]["plays"] == 1
