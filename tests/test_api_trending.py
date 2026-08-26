import pytest
from fastapi.testclient import TestClient

from hn_radio import trending
from hn_radio.models import Story


@pytest.fixture(autouse=True)
def _clean_cache():
    trending.reset_cache()
    yield
    trending.reset_cache()


def _client():
    """Build a client WITHOUT a `with` block on purpose.

    Entering TestClient as a context manager runs the app's startup event, which rebuilds the feed
    and the landing page on disk. These tests only need routing, so we skip lifespan entirely.
    """
    from backend.app import app
    return TestClient(app)


def test_trending_returns_snapshot(monkeypatch):
    monkeypatch.setattr(trending.ingest, "fetch_front_page",
                        lambda n=10: [Story(id=42, title="A story", url=None, points=310,
                                            author="someone", num_comments=9, rank=1)])
    body = _client().get("/api/trending").json()

    assert body["total"] == 1
    assert body["stories"][0]["title"] == "A story"
    assert body["stories"][0]["points"] == 310
    assert body["stories"][0]["url"] == "https://news.ycombinator.com/item?id=42"


def test_trending_is_200_even_when_hn_fails(monkeypatch):
    def boom(n=10):
        raise RuntimeError("HN unreachable")

    monkeypatch.setattr(trending.ingest, "fetch_front_page", boom)
    r = _client().get("/api/trending")

    assert r.status_code == 200, "the board must get one code path, not an error branch"
    assert r.json()["stories"] == []
