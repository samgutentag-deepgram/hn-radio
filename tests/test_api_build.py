import json

import pytest
from fastapi.testclient import TestClient

from hn_radio import custom
from hn_radio.models import Story


def _story(hn_id, title, points=100, url=None):
    return Story(id=hn_id, title=title, url=url, points=points, author="someone",
                 num_comments=3, rank=1)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Point the episode store at a temp dir and keep every test off the network."""
    monkeypatch.setattr(custom.config, "EPISODES_DIR", tmp_path)
    custom.reset_live_cache()
    yield
    custom.reset_live_cache()


def _client():
    """No `with` block on purpose: entering it runs the startup hook, which rebuilds files on disk."""
    from backend.app import app
    return TestClient(app)


def _episode(tmp_path, ep_id, hn_id, desk, voice, with_pcm=True):
    d = tmp_path / ep_id
    (d / "segments").mkdir(parents=True)
    (d / "episode.json").write_text(json.dumps(
        {"id": ep_id, "title": ep_id,
         "source_items": [{"hn_id": hn_id, "title": f"story {hn_id}", "url": None}]}))
    (d / "script.json").write_text(json.dumps(
        [{"order": 1, "role": "desk", "speaker_key": desk, "text": "a line",
          "source_hn_id": hn_id, "voice_id": voice, "desk": desk}]))
    if with_pcm:
        (d / "segments" / "1.pcm").write_bytes(b"\x00\x00" * 10)


def test_pool_lists_rendered_and_live_stories(monkeypatch, tmp_path):
    from datetime import date
    _episode(tmp_path, date.today().isoformat(), 1, "ai", "flux-meena-en")
    monkeypatch.setattr(custom, "live_stories_cached",
                        lambda: [_story(2, "Show HN: I built a thing in Rust")])

    body = _client().get("/api/build/pool?days=3").json()

    assert body["days"] == 3 and body["live_available"] is True
    assert "desks" not in body, "the pool stopped publishing a list nothing read (2026-08-22)"
    sources = {s["hn_id"]: s["source"] for s in body["stories"]}
    assert sources == {1: "episode", 2: "live"}


def test_pool_clamps_days_to_the_supported_range(monkeypatch):
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])
    assert _client().get("/api/build/pool?days=999").json()["days"] == 7
    assert _client().get("/api/build/pool?days=0").json()["days"] == 1


def test_pool_still_answers_when_hacker_news_is_unreachable(monkeypatch):
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])
    r = _client().get("/api/build/pool")
    assert r.status_code == 200
    assert r.json()["live_available"] is False, "the picker degrades to rendered episodes only"


def test_plan_prices_a_build_without_rendering(monkeypatch, tmp_path):
    from datetime import date
    _episode(tmp_path, date.today().isoformat(), 1, "ai", "flux-meena-en")
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])

    body = _client().post("/api/build/plan",
                          json={"desks": {"ai": "flux-meena-en"}, "days": 3}).json()

    assert body["reuse"] == 1, "the AI line keeps its voice, so its cached audio is reusable"
    assert body["rerender"] == 0
    assert body["new"] == 2, "intro and sign-off are always new"
    assert len(body["config_id"]) == 8


def test_plan_counts_a_voice_change_as_a_rerender(monkeypatch, tmp_path):
    from datetime import date
    _episode(tmp_path, date.today().isoformat(), 1, "ai", "flux-meena-en")
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])

    body = _client().post("/api/build/plan",
                          json={"desks": {"ai": "flux-heather-en"}}).json()
    assert body["reuse"] == 0 and body["rerender"] == 1


def test_plan_rejects_a_bad_config_with_a_readable_message(monkeypatch):
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])
    r = _client().post("/api/build/plan", json={"desks": {}})
    assert r.status_code == 400
    assert "at least one desk" in r.json()["detail"], "the message is shown to the user as-is"

    r = _client().post("/api/build/plan", json={"desks": {"ai": "flux-nobody-en"}})
    assert r.status_code == 400 and "Unknown voice" in r.json()["detail"]


def test_build_returns_400_when_no_story_matches(monkeypatch, tmp_path):
    """An empty result is a user-fixable situation, not a server error."""
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])
    r = _client().post("/api/build", json={"desks": {"security": "flux-jack-en"}})
    assert r.status_code == 400
    assert "matched those desks" in r.json()["detail"]


def test_build_surfaces_a_render_failure_as_502(monkeypatch, tmp_path):
    from datetime import date
    _episode(tmp_path, date.today().isoformat(), 1, "ai", "flux-meena-en")
    monkeypatch.setattr(custom, "live_stories_cached", lambda: [])

    def boom(*a, **k):
        raise RuntimeError("DEEPGRAM_API_KEY not found")

    monkeypatch.setattr(custom, "build", boom)
    r = _client().post("/api/build", json={"desks": {"ai": "flux-meena-en"}})
    assert r.status_code == 502
    assert "DEEPGRAM_API_KEY" in r.json()["detail"]
