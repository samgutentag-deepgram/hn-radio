"""The play counter's storage layer: an append-only event log, and the rollup read off it.

Two things this file is really guarding.

**A counter must never break a page.** Every write path in `plays.py` is best-effort in the same
sense `status.py` is: a read-only volume, a full disk, a permission change, a half-written last
line from a machine that died mid-append. None of those may raise into a request handler, because
the thing they would break is playback, and playback is the product. Several tests below do
nothing but prove a failure stays swallowed.

**The rollup is derived, never stored.** `counts()` is a pure function of the log plus the
rotation baseline. Nothing increments a number in place, so there is no counter to drift, and a
bad deploy cannot corrupt a total that a re-read would have fixed.
"""

from __future__ import annotations

import json

import pytest

from hn_radio import plays


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    """A temp episode store, and a cold cache on both sides of every test.

    The cache lives in module state and the suite shares one process, so without the reset a test
    that reads a rollup decides what an unrelated test below it sees.
    """
    monkeypatch.setattr(plays.config, "EPISODES_DIR", tmp_path)
    plays.reset_cache()
    yield
    plays.reset_cache()


def _episode(tmp_path, ep_id):
    """The minimum on disk that makes an id real: a directory with an episode.json in it."""
    d = tmp_path / ep_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "episode.json").write_text(json.dumps({"id": ep_id, "title": ep_id}))


def _lines(tmp_path):
    return [json.loads(l) for l in (tmp_path / "plays.jsonl").read_text().splitlines() if l.strip()]


# --- what gets written ---------------------------------------------------------------------

def test_record_appends_one_line_per_event(tmp_path):
    plays.record("2026-08-26", "view")
    plays.record("2026-08-26", "play")
    plays.record("2026-08-26", "progress", 50)

    rows = _lines(tmp_path)
    assert [r["e"] for r in rows] == ["view", "play", "progress"]
    assert rows[2]["pct"] == 50
    assert all(r["ep"] == "2026-08-26" for r in rows)


def test_a_line_carries_a_timestamp_and_nothing_identifying(tmp_path):
    """The whole privacy claim of this feature is checkable here, so check it.

    The page says it stores no visitor identity. That is only true if the row itself cannot carry
    one, so the row is a closed set of four keys rather than a dict we pass through. A future
    caller that adds `ip=` gets a test failure instead of a quiet retention change.
    """
    plays.record("2026-08-26", "play")

    row = _lines(tmp_path)[0]
    assert set(row) <= {"t", "ep", "e", "pct"}
    assert row["t"].endswith("Z")


def test_progress_without_a_percentage_is_not_written(tmp_path):
    """`progress` means "reached this far". A row that does not say how far is not a fact."""
    plays.record("2026-08-26", "progress")
    assert not (tmp_path / "plays.jsonl").exists()


def test_an_unknown_event_name_is_not_written(tmp_path):
    plays.record("2026-08-26", "purchase")
    assert not (tmp_path / "plays.jsonl").exists()


# --- the rollup ----------------------------------------------------------------------------

def test_counts_rolls_events_up_per_episode(tmp_path):
    for _ in range(4):
        plays.record("2026-08-26", "view")
    plays.record("2026-08-26", "play")
    plays.record("2026-08-26", "play")
    plays.record("2026-08-26", "progress", 25)
    plays.record("2026-08-26", "progress", 50)
    plays.record("2026-08-26", "progress", 100)
    plays.record("2026-08-25", "view")

    got = plays.counts()
    assert got["episodes"]["2026-08-26"] == {
        "views": 4, "plays": 2, "p25": 1, "p50": 1, "p75": 0, "completes": 1,
    }
    assert got["episodes"]["2026-08-25"]["views"] == 1
    assert got["totals"] == {"views": 5, "plays": 2, "p25": 1, "p50": 1, "p75": 0, "completes": 1}


def test_counts_on_an_empty_store_is_zeroes_not_an_error(tmp_path):
    """The first request after a deploy reads a log that does not exist yet."""
    got = plays.counts()
    assert got["episodes"] == {}
    assert got["totals"]["plays"] == 0
    assert got["daily"] == []


def test_the_daily_series_is_one_row_per_day_ascending(tmp_path):
    (tmp_path / "plays.jsonl").write_text("\n".join([
        json.dumps({"t": "2026-08-24T10:00:00Z", "ep": "a", "e": "play"}),
        json.dumps({"t": "2026-08-26T10:00:00Z", "ep": "a", "e": "view"}),
        json.dumps({"t": "2026-08-26T11:00:00Z", "ep": "a", "e": "play"}),
        json.dumps({"t": "2026-08-26T11:30:00Z", "ep": "a", "e": "progress", "pct": 100}),
    ]) + "\n")

    daily = plays.counts()["daily"]
    assert [d["date"] for d in daily] == ["2026-08-24", "2026-08-26"]
    assert daily[1] == {"date": "2026-08-26", "views": 1, "plays": 1, "completes": 1}


def test_a_torn_last_line_does_not_lose_the_lines_above_it(tmp_path):
    """A machine killed mid-append leaves a partial line. It costs that event, not the file.

    This is the failure that makes people reach for a database. It does not need one: the reader
    skips what it cannot parse, because one unreadable row is worth exactly one event and the
    rollup is derived on every read anyway.
    """
    (tmp_path / "plays.jsonl").write_text(
        json.dumps({"t": "2026-08-26T10:00:00Z", "ep": "a", "e": "play"}) + "\n"
        + json.dumps({"t": "2026-08-26T10:01:00Z", "ep": "a", "e": "play"}) + "\n"
        + '{"t": "2026-08-26T10:02:00Z", "ep": "a", "e": "pl'
    )

    assert plays.counts()["episodes"]["a"]["plays"] == 2


def test_a_row_missing_its_episode_is_skipped(tmp_path):
    (tmp_path / "plays.jsonl").write_text("\n".join([
        json.dumps({"t": "2026-08-26T10:00:00Z", "e": "play"}),
        json.dumps({"t": "2026-08-26T10:01:00Z", "ep": "a", "e": "play"}),
    ]) + "\n")

    got = plays.counts()
    assert got["totals"]["plays"] == 1
    assert list(got["episodes"]) == ["a"]


def test_the_rollup_is_recomputed_after_the_log_grows(tmp_path):
    """The cache is keyed on the file's size and mtime, so a new event has to invalidate it."""
    plays.record("a", "play")
    assert plays.counts()["totals"]["plays"] == 1
    plays.record("a", "play")
    assert plays.counts()["totals"]["plays"] == 2


# --- rotation ------------------------------------------------------------------------------

def test_rotation_folds_the_old_log_into_a_baseline_and_totals_survive(tmp_path, monkeypatch):
    """Rotation must be invisible in the numbers. A count that resets is worse than no count."""
    monkeypatch.setattr(plays, "MAX_LOG_BYTES", 200)
    for _ in range(12):
        plays.record("a", "play")

    assert plays.counts()["episodes"]["a"]["plays"] == 12
    assert (tmp_path / "plays-totals.json").exists()
    assert list(tmp_path.glob("plays-*.jsonl")), "the rotated log should be kept, not deleted"
    # Truncated in place rather than unlinked, so the live log is always a file that exists.
    assert (tmp_path / "plays.jsonl").stat().st_size < 200


def test_rotation_keeps_accumulating_across_several_rolls(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "MAX_LOG_BYTES", 120)
    for _ in range(40):
        plays.record("a", "view")
    assert plays.counts()["episodes"]["a"]["views"] == 40


def test_a_corrupt_baseline_does_not_take_the_rollup_down(tmp_path):
    """Same rule as the log: unreadable history costs history, never the request."""
    (tmp_path / "plays-totals.json").write_text("{not json")
    plays.record("a", "play")
    assert plays.counts()["episodes"]["a"]["plays"] == 1


# --- never break a page --------------------------------------------------------------------

def test_a_write_failure_is_swallowed(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(plays, "_append", _boom)
    plays.record("a", "play")  # must not raise


def test_an_unreadable_log_reads_as_empty(tmp_path, monkeypatch):
    (tmp_path / "plays.jsonl").write_text("")

    def _boom(*a, **k):
        raise OSError("gone")

    monkeypatch.setattr(plays.Path, "read_text", _boom)
    assert plays.counts()["totals"]["plays"] == 0


# --- which ids are allowed to enter the log ------------------------------------------------

def test_known_episode_accepts_an_episode_on_disk(tmp_path):
    _episode(tmp_path, "2026-08-26")
    assert plays.known_episode("2026-08-26")


def test_known_episode_rejects_one_that_was_never_rendered(tmp_path):
    assert not plays.known_episode("2026-08-26")


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "a/b", "", ".", "..", "x" * 200, "a b", "a\n b",
])
def test_known_episode_rejects_anything_that_is_not_an_id(tmp_path, bad):
    """The id becomes a path segment and a JSON key, so it is validated before either use.

    Traversal is the obvious one. The subtler one is that an id we accept is an id that lands in
    the log forever, so the shape is pinned here rather than left to whatever the caller sent.
    """
    assert not plays.known_episode(bad)
