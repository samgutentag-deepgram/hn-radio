import time

import pytest

from hn_radio import trending
from hn_radio.models import Story


def _story(i, rank):
    return Story(id=i, title=f"Story {i}", url=f"https://example.test/{i}",
                 points=100 + i, author="someone", num_comments=5, rank=rank)


@pytest.fixture(autouse=True)
def _clean_cache():
    trending.reset_cache()
    yield
    trending.reset_cache()


def _patch(monkeypatch, stories=None, boom=False):
    """Patch the ingest call and return a list that records each call's `n` argument."""
    calls = []

    def fake(n=10):
        calls.append(n)
        if boom:
            raise RuntimeError("HN unreachable")
        return stories if stories is not None else [_story(1, 1), _story(2, 2)]

    monkeypatch.setattr(trending.ingest, "fetch_front_page", fake)
    return calls


def test_snapshot_shape(monkeypatch):
    _patch(monkeypatch, stories=[_story(49173165, 1), _story(49167113, 2)])
    snap = trending.snapshot(limit=2)

    assert snap["total"] == 2 == len(snap["stories"])
    assert snap["fetched_at"].endswith("Z")
    first = snap["stories"][0]
    assert first == {
        "rank": 1,
        "hn_id": 49173165,
        "title": "Story 49173165",
        "author": "someone",
        "points": 100 + 49173165,
        "url": "https://news.ycombinator.com/item?id=49173165",
    }
    # ranks ascend from 1
    assert [s["rank"] for s in snap["stories"]] == [1, 2]


def test_inside_ttl_does_not_refetch(monkeypatch):
    calls = _patch(monkeypatch)
    trending.snapshot()
    trending.snapshot()
    trending.snapshot()
    assert calls == [10], "cache must serve repeat calls inside the TTL"


def test_outside_ttl_refetches(monkeypatch):
    calls = _patch(monkeypatch)
    trending.snapshot()
    monkeypatch.setattr(trending, "TTL_SECONDS", 0)
    trending.snapshot()
    assert len(calls) == 2


def test_fetch_failure_with_cold_cache_returns_empty(monkeypatch):
    _patch(monkeypatch, boom=True)
    snap = trending.snapshot()
    assert snap == {"stories": [], "fetched_at": None, "total": 0}


def test_fetch_failure_with_warm_cache_serves_last_good(monkeypatch):
    _patch(monkeypatch, stories=[_story(7, 1)])
    good = trending.snapshot()

    monkeypatch.setattr(trending, "TTL_SECONDS", 0)
    _patch(monkeypatch, boom=True)
    assert trending.snapshot() == good, "a failed refetch must not discard the last good snapshot"


def test_failure_inside_failure_ttl_does_not_refetch(monkeypatch):
    calls = _patch(monkeypatch, boom=True)
    trending.snapshot()
    trending.snapshot()
    assert calls == [10], "a second call inside FAILURE_TTL_SECONDS must not refetch after a failure"


def test_failure_outside_failure_ttl_refetches(monkeypatch):
    calls = _patch(monkeypatch, boom=True)
    trending.snapshot()
    monkeypatch.setattr(trending, "FAILURE_TTL_SECONDS", 0)
    trending.snapshot()
    assert len(calls) == 2


def test_returned_snapshot_is_not_the_live_cache(monkeypatch):
    """Prove that returned snapshots are copies, not references to the live cache.

    Callers may mutate returned dicts (sort, filter, annotate) before rendering. The module must
    not hand out references to the shared cache; mutations must not corrupt subsequent calls.
    """
    _patch(monkeypatch, stories=[_story(1, 1), _story(2, 2)])
    snap1 = trending.snapshot()
    original_title = snap1["stories"][0]["title"]
    original_length = len(snap1["stories"])

    # Mutate the returned snapshot.
    snap1["stories"].append({"rank": 3, "hn_id": 3, "title": "Injected", "points": 0, "url": "bad"})
    snap1["stories"][0]["title"] = "Mutated Title"
    snap1["corrupted_by_caller"] = True

    # Call snapshot() again inside the TTL; it should serve the cache unaffected.
    snap2 = trending.snapshot()

    # Assert snap2 is unaffected by snap1's mutations.
    assert len(snap2["stories"]) == original_length, "injected row must not appear"
    assert snap2["stories"][0]["title"] == original_title, "mutated title must not persist"
    assert "corrupted_by_caller" not in snap2, "new top-level key must not appear"


def test_a_successful_fetch_clears_the_failure_stamp(monkeypatch):
    """A recovered HN must not stay suppressed.

    This was only ever harmless because TTL_SECONDS (3600) dwarfs FAILURE_TTL_SECONDS (60), so the
    freshness check always won before a stale failure stamp could matter. The test proves the fix by
    removing that accident: with TTL_SECONDS shorter than FAILURE_TTL_SECONDS, an uncleared stamp
    would suppress the very next fetch.
    """
    _patch(monkeypatch, boom=True)
    assert trending.snapshot()["stories"] == []
    assert trending._failed_at, "a failure should be recorded"

    # Open the suppression window, otherwise the retry is correctly refused for 60s.
    monkeypatch.setattr(trending, "FAILURE_TTL_SECONDS", 0)
    calls = _patch(monkeypatch, stories=[_story(1, 1)])
    assert trending.snapshot()["total"] == 1, "recovery works once the window has passed"
    assert trending._failed_at == 0.0, "the failure stamp must be cleared on success"

    # Now make freshness expire faster than the failure window would.
    monkeypatch.setattr(trending, "TTL_SECONDS", 0)
    monkeypatch.setattr(trending, "FAILURE_TTL_SECONDS", 3600)
    trending.snapshot()
    assert len(calls) == 2, "a cleared stamp lets the next fetch through; a stale one would block it"


def test_only_one_fetch_runs_when_many_callers_arrive_at_once(monkeypatch):
    """Concurrent callers must not each start their own fetch.

    The route runs in anyio's shared threadpool, and fetch_front_page walks the ranked id list with
    retries at a 20s timeout. N simultaneous cold-cache requests used to mean N occupied threads,
    which is what could stall /api/status and static serving during an HN outage. Rewriting the
    route as async would not have helped: to_thread draws from the same pool.
    """
    import threading

    calls = []
    started = threading.Event()

    def slow(n=10):
        calls.append(n)
        started.set()
        time.sleep(0.4)          # hold the "fetch" open so the others pile in behind it
        return [_story(1, 1)]

    monkeypatch.setattr(trending.ingest, "fetch_front_page", slow)

    results = []
    threads = [threading.Thread(target=lambda: results.append(trending.snapshot()))
               for _ in range(8)]
    threads[0].start()
    started.wait(2)              # make sure the winner is inside the fetch before the rest arrive
    for t in threads[1:]:
        t.start()
    for t in threads:
        t.join(5)

    assert len(calls) == 1, f"expected a single fetch, got {len(calls)}"
    assert len(results) == 8, "every caller still got an answer"
    # The losers were served immediately with an empty snapshot rather than blocking on the winner.
    assert any(r["total"] == 1 for r in results), "the winner's fetch populated the cache"
