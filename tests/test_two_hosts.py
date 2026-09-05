"""Two editions, two hosts. Alexis opens the Morning Edition, Cole opens the Afternoon Edition.

Sam's pick. The reasoning: a listener who hears both shows in a day should hear two people, not the
same person twice, and the afternoon host is a fixed character with one job rather than a voice
that also turns up as somebody's guest at 3am.
"""

from datetime import datetime

import pytest

from hn_radio import cast, config, pipeline
from hn_radio.window import AFTERNOON, MORNING, EpisodeWindow


def _production(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))


def _at(y, m, d, hour):
    return datetime(y, m, d, hour, 0, tzinfo=config.PACIFIC)


def test_the_afternoon_host_is_cole(monkeypatch):
    _production(monkeypatch)
    ep_cast, subs = cast.episode_cast(before="2026-09-05-pm")
    assert ep_cast.anchor.voice_id == config.AFTERNOON_HOST_VOICE == "flux-cole-en"
    assert ep_cast.anchor.name == "Cole"
    assert subs == {}, "Cole is in the catalog, so nothing is announced"


def test_the_morning_host_is_still_alexis(monkeypatch):
    _production(monkeypatch)
    for before in ("2026-09-05-am", "2026-09-05"):  # scheduled morning, and the calendar shape
        ep_cast, _ = cast.episode_cast(before=before)
        assert ep_cast.anchor.voice_id == config.HOST_VOICE
        assert ep_cast.anchor.name == "Alexis"


def test_an_explicit_slot_beats_the_id(monkeypatch):
    _production(monkeypatch)
    assert cast.episode_cast(before="2026-09-05", slot=AFTERNOON)[0].anchor.name == "Cole"
    assert cast.episode_cast(before="2026-09-05-pm", slot=MORNING)[0].anchor.name == "Alexis"


def test_slot_is_read_off_the_episode_id():
    assert cast.slot_of_episode_id("2026-09-05-am") == "am"
    assert cast.slot_of_episode_id("2026-09-05-pm") == "pm"
    assert cast.slot_of_episode_id("2026-09-05") is None
    assert cast.slot_of_episode_id("2026-09-05-pm-recast") is None
    assert cast.slot_of_episode_id(None) is None


def test_neither_host_ever_takes_the_second_chair(monkeypatch):
    """Cole is never the morning co-host and Alexis is never the afternoon co-host."""
    _production(monkeypatch)
    for n in range(1, 29):
        for slot in ("am", "pm"):
            ep_cast, _ = cast.episode_cast(before=f"2026-09-{n:02d}-{slot}", recent_voices=[])
            assert ep_cast.cohost.voice_id not in {config.HOST_VOICE, config.AFTERNOON_HOST_VOICE}
            assert ep_cast.cohost.voice_id != ep_cast.anchor.voice_id


def test_jack_stands_in_when_cole_is_missing_and_the_show_says_so(monkeypatch):
    catalog = {k: v for k, v in config.VOICE_CATALOG.items() if "cole" not in k}
    monkeypatch.setattr(config, "active_voice_catalog", lambda: catalog)
    ep_cast, subs = cast.episode_cast(before="2026-09-05-pm")
    assert ep_cast.anchor.voice_id == "flux-jack-en"
    assert subs == {"anchor": "Cole"}


def test_run_panel_hands_the_slot_to_the_cast(monkeypatch, tmp_path):
    from hn_radio import ingest, sources, status
    from hn_radio.models import Story
    _production(monkeypatch)
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    stories = [Story(id=1, title="A story", url="https://example.com", points=99, author="a",
                     num_comments=1, rank=1, kids=[])]
    monkeypatch.setattr(ingest, "fetch_stories_between", lambda a, b, n, label=None: list(stories))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    for name in ("begin", "stage"):
        monkeypatch.setattr(status, name, lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(pipeline, "render_panel", lambda segments, **kw: seen.update(kw) or "ep")
    real = cast.episode_cast
    monkeypatch.setattr(pipeline, "episode_cast_for", lambda **kw: seen.update(kw) or real(**kw))

    pipeline.run_panel(edition="frontpage", window=EpisodeWindow.ending_at(_at(2026, 9, 5, 15)),
                       log=lambda *a, **k: None)
    assert seen["slot"] == AFTERNOON and seen["before"] == "2026-09-05-pm"


# --- the fixed copy names the edition and hands off to the other host ---------------------------

def test_the_afternoon_intro_is_coles_and_names_the_edition(monkeypatch):
    _production(monkeypatch)
    w = EpisodeWindow.ending_at(_at(2026, 9, 5, 15))
    ep_cast, _ = cast.episode_cast(before=w.episode_id("frontpage"))
    intro = pipeline._intro_segments(ep_cast, w)[0].text
    assert intro.startswith("Hi, this is Cole.")
    assert "the Afternoon Edition of Hacker News Radio" in intro


def test_each_host_hands_off_to_the_other_by_name(monkeypatch):
    _production(monkeypatch)
    am = EpisodeWindow.ending_at(_at(2026, 9, 5, 3))
    pm = EpisodeWindow.ending_at(_at(2026, 9, 5, 15))
    am_cast, _ = cast.episode_cast(before=am.episode_id("frontpage"))
    pm_cast, _ = cast.episode_cast(before=pm.episode_id("frontpage"))
    am_outro = pipeline._outro_segments(am_cast, am)[0].text
    pm_outro = pipeline._outro_segments(pm_cast, pm)[0].text
    assert "Cole has the Afternoon Edition for you later today." in am_outro
    assert "Alexis has the Morning Edition for you tomorrow." in pm_outro
    # Each host promises THEIR OWN next show.
    assert "tomorrow morning" in am_outro and "tomorrow afternoon" in pm_outro
    assert len(pipeline._outro_segments(am_cast, am)) == 1, "the outro stays one segment"


def test_the_handoff_is_dropped_when_it_would_name_the_same_person(monkeypatch):
    """A catalog where both hosts collapse onto one voice must not say "Alexis has the afternoon"
    in Alexis's own voice."""
    catalog = {k: v for k, v in config.VOICE_CATALOG.items() if "cole" not in k and "jack" not in k}
    catalog = dict(catalog)
    monkeypatch.setattr(config, "active_voice_catalog", lambda: catalog)
    monkeypatch.setattr(cast, "AFTERNOON_HOST_VOICES", ["flux-cole-en", "flux-jack-en", "flux-alexis-en"])
    am = EpisodeWindow.ending_at(_at(2026, 9, 5, 3))
    am_cast, _ = cast.episode_cast(before=am.episode_id("frontpage"))
    outro = pipeline._outro_segments(am_cast, am)[0].text
    assert "has the Afternoon Edition" not in outro
    assert outro.startswith("That's the Morning Edition. From me and")


def test_the_calendar_shape_is_untouched(monkeypatch):
    """The archive re-render path: plain show name, no edition, no hand-off."""
    from datetime import date
    _production(monkeypatch)
    ep_cast, _ = cast.episode_cast(before="2026-08-22")
    intro = pipeline._intro_segments(ep_cast, date(2026, 8, 22))[0].text
    outro = pipeline._outro_segments(ep_cast)[0].text
    assert "listening to Hacker News Radio, read by" in intro
    assert "Edition" not in intro and "Edition" not in outro


# --- the cast page knows there are two hosts ------------------------------------------------------

def test_voices_json_names_both_hosts_by_edition(tmp_path, monkeypatch):
    import json
    from hn_radio import manifest
    _production(monkeypatch)
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    data = json.loads(manifest.build_voices_json(tmp_path).read_text())
    assert data["hosts"] == {
        "am": {"voice": "flux-alexis-en", "name": "Alexis", "edition": "Morning Edition"},
        "pm": {"voice": "flux-cole-en", "name": "Cole", "edition": "Afternoon Edition"},
    }
    assert data["host_voice"] == "flux-alexis-en", "the old key stays for older readers"
    # Neither host is offered as the catalog view's co-host.
    cohost = [s for s in data["seating"] if s["role"] == "cohost"][0]
    assert cohost["voice"] not in {"flux-alexis-en", "flux-cole-en"}
