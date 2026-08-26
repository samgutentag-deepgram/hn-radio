"""Static assets must be revalidated, not heuristically cached.

Starlette's StaticFiles sends etag and last-modified but no Cache-Control. With no directive a
browser applies heuristic freshness and can reuse a cached copy without asking. That shipped a
stale brand.css to a live page after a deploy: the new HTML rendered against the old stylesheet,
so every ticker rule went missing at once and the page looked broken.

These tests pin the fix and, just as importantly, pin the property that makes it cheap: the etag
survives, so revalidation is a bodiless 304 rather than a re-download.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    """No `with` block on purpose: entering it runs the startup hook, which rewrites files on disk."""
    from backend.app import app
    return TestClient(app)


def test_stylesheet_is_served_with_no_cache():
    r = _client().get("/brand.css")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache", (
        "without this a browser may reuse a cached stylesheet without revalidating, which is how a "
        "deploy served new HTML against an old brand.css"
    )


def test_revalidation_is_cheap_because_the_etag_survives():
    """`no-cache` only costs a conditional request if the etag still allows a 304."""
    client = _client()
    first = client.get("/brand.css")
    etag = first.headers.get("etag")
    assert etag, "no etag means every revalidation re-downloads the whole file"

    second = client.get("/brand.css", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert not second.content, "a 304 must carry no body, or the header buys us nothing"


def test_the_html_pages_revalidate_too():
    """The pages change as often as the stylesheet, and a stale pair is the same bug."""
    for path in ("/", "/build.html"):
        r = _client().get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache", path


def test_episode_audio_revalidates_as_well(tmp_path, monkeypatch):
    """Audio is rewritten in place at a stable URL, so it must not be pinned by a long max-age.

    scripts/add_chapters.py rebuilds episode.mp3 for existing episodes. That is exactly what
    re-encoded every episode from 24 kHz to 44.1 kHz, and a cached copy would have kept listeners
    on the unplayable one.
    """
    from hn_radio import config

    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    (tmp_path / "2026-08-04").mkdir()
    (tmp_path / "2026-08-04" / "episode.mp3").write_bytes(b"\xff\xfb\x00" * 32)

    # The mount captured the real directory at import time, so mount a fresh app for the temp dir.
    from fastapi import FastAPI
    from backend.app import RevalidatingStatic

    app = FastAPI()
    app.mount("/episodes", RevalidatingStatic(directory=str(tmp_path)), name="episodes")
    r = TestClient(app).get("/episodes/2026-08-04/episode.mp3")

    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_api_routes_are_untouched_by_the_static_header():
    """The header belongs to the file mounts; JSON routes set their own policy (currently none).

    Repointed from `/api/health` to `/api/status` on 2026-08-22. `/api/status` has real consumers
    (the feed page's status board), so a leak here would be a leak onto a route someone reads.
    """
    r = _client().get("/api/status")
    assert r.status_code == 200
    assert "cache-control" not in {k.lower() for k in r.headers}, \
        "the static subclass must not leak its policy onto API responses"


def test_health_answers(monkeypatch):
    """The route kept by judgment call 15 should at least be exercised.

    It was the only thing `test_api_routes_are_untouched_by_the_static_header` touched, and that
    test moved to `/api/status`, so without this the route we deliberately kept would have had no
    coverage at all and a typo in it would ship.
    """
    r = _client().get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
