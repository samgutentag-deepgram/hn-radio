"""The twice-daily window: what an episode covers, what it is called, and what the intro says.

A `date` used to carry all of it. The cron now runs at 3am and 3pm Pacific and each run
reaches back `config.LOOKBACK_HOURS`, so the window is a first-class value (`hn_radio/window.py`)
and the old date-shaped callers are coerced at the edge. Both shapes are pinned here because both
are live: the archive and the backfill speak dates, the cron speaks windows.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from hn_radio import cast, config, ingest, pipeline
from hn_radio.window import AFTERNOON, MORNING, EpisodeWindow, coerce


def _at(y, m, d, hour, minute=0):
    return datetime(y, m, d, hour, minute, tzinfo=config.PACIFIC)


# --- the two shapes -------------------------------------------------------------------------------

def test_the_calendar_shape_is_what_a_date_always_meant():
    w = EpisodeWindow.calendar_day(date(2026, 8, 22))
    assert w.start == _at(2026, 8, 22, 0) and w.end == _at(2026, 8, 23, 0)
    assert w.air_date == date(2026, 8, 23), "aired the next morning, as before"
    assert w.slot is None
    assert w.episode_id("frontpage") == "2026-08-22", "every id already on disk keeps its name"
    assert w.episode_id("makers") == "2026-08-22-makers"
    assert w.content_date == date(2026, 8, 22)
    assert w.hours == 24


def test_the_morning_run_reaches_back_eighteen_hours_across_midnight():
    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 3))
    assert config.LOOKBACK_HOURS == 18
    assert w.start == _at(2026, 9, 4, 9) and w.end == _at(2026, 9, 5, 3)
    assert w.slot == MORNING and w.air_date == date(2026, 9, 5)
    assert w.episode_id("frontpage") == "2026-09-05-am"
    assert w.content_date == date(2026, 9, 5), "the air date is the honest date for a window that straddles two"


def test_the_afternoon_run_is_the_pm_slot():
    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 15))
    assert w.start == _at(2026, 9, 4, 21) and w.slot == AFTERNOON
    assert w.episode_id("frontpage") == "2026-09-05-pm"
    assert w.episode_id("ai") == "2026-09-05-pm-ai", "slot before edition, so a day's ids sort in air order"


def test_consecutive_windows_overlap_by_six_hours_and_no_further():
    am = EpisodeWindow.ending_at(_at(2026, 9, 5, 3))
    pm = EpisodeWindow.ending_at(_at(2026, 9, 5, 15))
    next_am = EpisodeWindow.ending_at(_at(2026, 9, 6, 3))
    assert am.end - pm.start == timedelta(hours=6)
    assert pm.end - next_am.start == timedelta(hours=6)
    assert next_am.start >= am.end, "an episode two runs back shares nothing with this one"


def test_ids_sort_in_air_order_across_both_shapes():
    ids = ["2026-09-05-pm", "2026-09-03", "2026-09-05-am", "2026-09-04"]
    assert sorted(ids) == ["2026-09-03", "2026-09-04", "2026-09-05-am", "2026-09-05-pm"]


def test_ending_at_refuses_a_naive_datetime():
    with pytest.raises(ValueError):
        EpisodeWindow.ending_at(datetime(2026, 9, 5, 3))


def test_ending_at_normalizes_to_pacific():
    """A UTC clock at 10:00 is 3am Pacific in September; the slot must be judged in Pacific."""
    from datetime import timezone
    w = EpisodeWindow.ending_at(datetime(2026, 9, 5, 10, tzinfo=timezone.utc))
    assert w.slot == MORNING and w.air_date == date(2026, 9, 5)


def test_coerce_accepts_both_shapes_and_nothing_else():
    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 3))
    assert coerce(w) is w
    assert coerce(date(2026, 8, 22)) == EpisodeWindow.calendar_day(date(2026, 8, 22))
    assert coerce(datetime(2026, 8, 22, 14)) == EpisodeWindow.calendar_day(date(2026, 8, 22))
    with pytest.raises(TypeError):
        coerce("2026-08-22")


# --- what the fixed copy says ---------------------------------------------------------------------

def _cast(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    return cast.episode_cast(before="2026-09-05-am")[0]


def test_a_date_still_gets_the_yesterday_framing(monkeypatch):
    """The archive re-render path. Unchanged wording, pinned by test_two_person_show too."""
    ep_cast = _cast(monkeypatch)
    intro = pipeline._intro_segments(ep_cast, date(2026, 8, 22))[0].text
    outro = pipeline._outro_segments(ep_cast)[0].text
    assert "August 23" in intro and "yesterday" in intro
    assert outro.startswith("That's yesterday's front page.") and "tomorrow." in outro


def test_the_morning_show_says_overnight_and_hands_to_the_afternoon(monkeypatch):
    ep_cast = _cast(monkeypatch)
    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 3))
    intro = pipeline._intro_segments(ep_cast, w)[0].text
    outro = pipeline._outro_segments(ep_cast, w)[0].text
    assert "Saturday, September 5" in intro, "the air date is the run's own date now"
    assert "overnight" in intro and "yesterday" not in intro
    assert "the Morning Edition of Hacker News Radio" in intro
    assert outro.startswith("That's the Morning Edition.")
    assert "Cole has the Afternoon Edition for you later today" in outro
    assert "tomorrow morning" in outro


def test_the_afternoon_show_says_today_and_hands_to_tomorrow_morning(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    ep_cast = cast.episode_cast(before="2026-09-05-pm")[0]  # the afternoon cast, so Cole hosts
    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 15))
    intro = pipeline._intro_segments(ep_cast, w)[0].text
    outro = pipeline._outro_segments(ep_cast, w)[0].text
    assert "today" in intro and "yesterday" not in intro
    assert "the Afternoon Edition of Hacker News Radio" in intro
    assert outro.startswith("That's the Afternoon Edition.")
    assert "Alexis has the Morning Edition for you tomorrow" in outro
    assert "tomorrow afternoon" in outro


def test_the_claude_prompt_describes_the_window_not_a_calendar_day(monkeypatch):
    from hn_radio.models import Story
    from hn_radio.writers import ClaudeWriter
    ep_cast = _cast(monkeypatch)
    story = Story(id=1, title="A story", url=None, points=10, author="a", num_comments=0, rank=1)
    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 15))
    _, user = ClaudeWriter()._build_prompt([story], None, [], ep_cast, "frontpage", w)
    assert "18 hours before this afternoon show" in user
    assert "3 pm Pacific" in user
    assert "YESTERDAY" not in user, "the calendar framing must not leak into the rolling one"
    _, legacy = ClaudeWriter()._build_prompt([story], None, [], ep_cast, "frontpage", date(2026, 8, 22))
    assert "front page of Saturday, August 22, 2026" in legacy and "YESTERDAY" in legacy


# --- casting and selection know about the new ids -------------------------------------------------

def test_am_and_pm_ids_count_as_canonical_episodes_for_cohost_recency():
    for ok in ("2026-09-05", "2026-09-05-am", "2026-09-05-pm"):
        assert cast._EPISODE_ID.match(ok), ok
    for no in ("2026-09-05-recast", "2026-09-05-am-recast", "2026-09-05-makers", "2026-09-05-pm-ai"):
        assert not cast._EPISODE_ID.match(no), no


def test_the_two_runs_of_one_day_cast_different_cohosts(monkeypatch):
    """The rotation is seeded by the episode id, so `-am` and `-pm` are different draws."""
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    monkeypatch.setattr(cast, "recent_cohost_voices", lambda **k: [])
    am = cast.episode_cast(before="2026-09-05-am")[0].cohost.voice_id
    pm = cast.episode_cast(before="2026-09-05-pm")[0].cohost.voice_id
    assert am != pm


def _episode_on_disk(root, ep_id, hn_ids):
    d = root / ep_id
    d.mkdir()
    (d / "episode.json").write_text(json.dumps({"id": ep_id, "source_items": [{"hn_id": i} for i in hn_ids]}))
    (d / "script.json").write_text("[]")


def test_the_afternoon_pool_drops_what_the_morning_covered(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    _episode_on_disk(tmp_path, "2026-09-04", [1, 2, 3])          # two runs back: irrelevant
    _episode_on_disk(tmp_path, "2026-09-05-am", [10, 11, 12])    # the previous episode
    _episode_on_disk(tmp_path, "2026-09-05-pm-recast", [99])     # a derivative: never counted
    assert pipeline._previously_covered("2026-09-05-pm") == {10, 11, 12}
    assert pipeline._previously_covered("2026-09-05-am") == {1, 2, 3}
    assert pipeline._previously_covered("2026-09-04") == set()


def test_a_half_written_previous_episode_excludes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    (tmp_path / "2026-09-05-am").mkdir()
    (tmp_path / "2026-09-05-am" / "episode.json").write_text("{not json")
    assert pipeline._previously_covered("2026-09-05-pm") == set()


def test_scheduled_titles_carry_the_slot_and_calendar_titles_do_not(monkeypatch, tmp_path):
    """Two shows a day: the feed needs to say which is which before the headline."""
    from hn_radio import sources, status
    from hn_radio.models import Story
    from hn_radio.writers import PanelWriter

    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    stories = [Story(id=1, title="A story", url="https://example.com", points=99, author="a",
                     num_comments=1, rank=1, kids=[])]
    monkeypatch.setattr(ingest, "fetch_stories_between", lambda s, e, n, label="": list(stories))
    monkeypatch.setattr(ingest, "fetch_front_page_for_date", lambda d, n: list(stories))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    monkeypatch.setattr(PanelWriter, "episode_title", lambda self: "Agents Colluding, 153M IDs")
    seen = {}
    monkeypatch.setattr(pipeline, "render_panel", lambda segments, **kw: seen.update(kw) or "ep")
    quiet = lambda *a, **k: None

    pipeline.run_panel(edition="frontpage", window=EpisodeWindow.ending_at(_at(2026, 9, 5, 3)), log=quiet)
    assert seen["title"] == "Morning Edition: Agents Colluding, 153M IDs"
    pipeline.run_panel(edition="frontpage", window=EpisodeWindow.ending_at(_at(2026, 9, 5, 15)), log=quiet)
    assert seen["title"] == "Afternoon Edition: Agents Colluding, 153M IDs"
    pipeline.run_panel(edition="frontpage", episode_date=date(2026, 8, 22), log=quiet)
    assert seen["title"] == "Agents Colluding, 153M IDs", "a calendar-day episode keeps its plain title"


def test_run_panel_with_a_window_fetches_by_window_and_names_by_slot(monkeypatch, tmp_path):
    """The wiring: a rolling window uses the between-fetch, drops covered stories, and the id
    carries the slot all the way to the cast seed and the render."""
    from hn_radio import sources, status
    from hn_radio.models import Story

    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    _episode_on_disk(tmp_path, "2026-09-05-am", [1])
    stories = [Story(id=i, title=f"story {i}", url="https://example.com", points=100 - i,
                     author="a", num_comments=1, rank=i, kids=[]) for i in range(1, 6)]
    seen = {}

    def fake_between(start, end, pool, label=""):
        seen["window"] = (start, end, label)
        return list(stories)
    monkeypatch.setattr(ingest, "fetch_stories_between", fake_between)
    monkeypatch.setattr(ingest, "fetch_front_page_for_date",
                        lambda d, n: pytest.fail("a rolling window must not use the per-day fetch"))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    begun = {}
    monkeypatch.setattr(status, "begin", lambda ep, ed="": begun.update(id=ep))
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    real_cast = pipeline.episode_cast_for
    monkeypatch.setattr(pipeline, "episode_cast_for", lambda **kw: seen.update(kw) or real_cast(**kw))
    monkeypatch.setattr(pipeline, "render_panel", lambda segments, **kw: seen.update(render=kw) or "ep")

    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 15))
    assert pipeline.run_panel(edition="frontpage", window=w, min_seconds=300,
                              log=lambda *a, **k: None) == "ep"
    assert seen["window"][:2] == (w.start, w.end)
    assert seen["before"] == "2026-09-05-pm" == begun["id"]
    assert seen["render"]["episode_id"] == "2026-09-05-pm"
    assert seen["render"]["min_seconds"] == 300
    covered = {s["hn_id"] for s in seen["render"]["source_items"]}
    assert 1 not in covered, "the morning's story must not lead the afternoon"
    assert len(covered) == 3


def test_fetch_stories_between_queries_algolia_for_exactly_the_window(monkeypatch):
    seen = {}

    def fake_search(start_ts, end_ts, tag):
        seen.update(start=start_ts, end=end_ts, tag=tag)
        return [{"objectID": "7", "title": "t", "points": 5, "author": "a", "num_comments": 1}]
    monkeypatch.setattr(ingest, "_algolia_search", fake_search)
    start, end = _at(2026, 9, 4, 21), _at(2026, 9, 5, 15)
    got = ingest.fetch_stories_between(start, end, 10)
    assert (seen["start"], seen["end"]) == (int(start.timestamp()), int(end.timestamp()))
    assert seen["tag"] == "story" and [s.id for s in got] == [7]
    with pytest.raises(ValueError):
        ingest.fetch_stories_between(end, start, 10)
    with pytest.raises(ValueError):
        ingest.fetch_stories_between(datetime(2026, 9, 4, 21), end, 10)


def test_the_per_day_fetch_is_the_between_fetch_over_a_pacific_day(monkeypatch):
    seen = {}
    monkeypatch.setattr(ingest, "fetch_stories_between",
                        lambda s, e, pool, label="": seen.update(s=s, e=e, label=label) or [])
    ingest.fetch_front_page_for_date(date(2026, 8, 22), 30)
    assert seen["s"] == _at(2026, 8, 22, 0) and seen["e"] == _at(2026, 8, 23, 0)
    assert seen["label"] == "2026-08-22"
