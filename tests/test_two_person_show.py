"""The two-person show: one consistent host, one co-host who changes every episode.

Written before the implementation, so every assertion here failed against
`feat/comments-with-stories` first. What each group pins:

  cast      the co-host is never the host, is drawn from the whole catalog, and does not
            repeat inside the recency window
  writers   the cold open is ONE segment (one batch TTS call), and the two regulars perform
            the comments themselves
  record    a performed comment keeps the real HN username and the real comment id even
            though the voice is now a regular's. That is the data the transcript, the cast
            page and the chapter metadata all read
  catalog   Priya is retired, and retiring her removes her from casting everywhere

Deliberately behavioral, not textual. The handoff wording is meant to be rewritten by ear
without breaking a test, so nothing below asserts on a spoken line except where the prompt
itself is the product (see tests/test_writers_prompt.py for that half).
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from hn_radio import cast, config, voices
from hn_radio.models import Comment, Story
from hn_radio.writers import ClaudeWriter, PanelWriter


def _story(id, title, url=None, summary=None):
    s = Story(id=id, title=title, url=url, points=100, author="a", num_comments=5,
              rank=id, kids=[])
    s.summary, s.source_kind = summary, "article"
    return s


def _stories(n=3):
    return [_story(i, f"Story number {i}", url="https://example.com") for i in range(1, n + 1)]


def _comments(story_id, n=2):
    return [Comment(id=100 + i, author=f"hnuser{i}", text=f"<p>Comment number {i}.</p>") for i in range(n)]


def _production(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))


def _episode_on_disk(root, ep_id, cohost_voice):
    """A rendered episode whose co-host was `cohost_voice`, as `recent_cohost_voices` reads it."""
    d = root / ep_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "script.json").write_text(json.dumps([
        {"order": 0, "role": "anchor", "speaker_key": "Alexis", "text": "hi",
         "desk": "anchor", "voice_id": "flux-alexis-en"},
        {"order": 1, "role": "desk", "speaker_key": "X", "text": "a take",
         "desk": "cohost", "voice_id": cohost_voice},
    ]))


# --- item 1 + 3: two people, and the second chair rotates --------------------------------------

def test_the_episode_cast_is_exactly_two_people(monkeypatch):
    _production(monkeypatch)
    ep_cast, _ = cast.episode_cast(before="2026-08-20")
    assert [d.role for d in ep_cast.desks] == ["cohost"]
    assert ep_cast.by_role("cohost") is not None


def test_the_cohost_is_never_the_host(monkeypatch):
    """One voice cannot hold both chairs, or the host interviews herself in her own voice."""
    _production(monkeypatch)
    for day in ("2026-08-18", "2026-08-19", "2026-08-20", "2026-09-01", "2026-12-25"):
        ep_cast, _ = cast.episode_cast(before=day)
        assert ep_cast.by_role("cohost").voice_id != ep_cast.anchor.voice_id


def test_the_cohost_comes_from_the_whole_catalog_not_a_fixed_pool(monkeypatch):
    """The reason the desks went: three fixed personas showed three voices, the catalog has 34."""
    _production(monkeypatch)
    seen = set()
    for day in [f"2026-09-{n:02d}" for n in range(1, 29)]:
        ep_cast, _ = cast.episode_cast(before=day, recent_voices=[])
        seen.add(ep_cast.by_role("cohost").voice_id)
    assert len(seen) >= 5, f"the second chair barely moves: {sorted(seen)}"
    assert seen <= set(config.VOICE_CATALOG)


def test_a_voice_used_as_cohost_recently_is_not_reselected(tmp_path, monkeypatch):
    """The whole point of the window. Derived from what aired, like the desk rule it replaces."""
    _production(monkeypatch)
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)

    # Cast a co-host with an empty history, then record that episode and re-cast the SAME day.
    first, _ = cast.episode_cast(before="2026-09-10", recent_voices=[])
    _episode_on_disk(tmp_path, "2026-09-09", first.by_role("cohost").voice_id)
    second, _ = cast.episode_cast(before="2026-09-10")
    assert second.by_role("cohost").voice_id != first.by_role("cohost").voice_id


def test_the_recency_window_covers_more_than_one_episode(tmp_path, monkeypatch):
    """A window of 1 would let two voices alternate forever, which is a two-voice rotation."""
    _production(monkeypatch)
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    assert cast.COHOST_RECENCY_WINDOW > 3, (
        "the catalog is far larger than the old three-episode desk window; a co-host that can "
        "return within three days is not being drawn from the catalog in any real sense"
    )

    used = []
    for n in range(1, cast.COHOST_RECENCY_WINDOW + 1):
        ep_id = f"2026-09-{n:02d}"
        ep_cast, _ = cast.episode_cast(before=ep_id)
        vid = ep_cast.by_role("cohost").voice_id
        assert vid not in used, f"{vid} came back inside the window: {used}"
        used.append(vid)
        _episode_on_disk(tmp_path, ep_id, vid)


def test_the_rotation_reuses_a_recent_voice_rather_than_failing(monkeypatch):
    """The recency window is a PREFERENCE, and it has to degrade rather than raise.

    Feed it a catalog where every eligible voice is already "recent" -- any catalog smaller than
    the window does this. A strict filter would empty the candidate list and raise
    RoleUnavailable on a show that could perfectly well go out. Same trade `guest_voice_for`
    makes: a repeated co-host is a worse episode, no episode is worse than that.
    """
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    everyone = [v for v in config.VOICE_CATALOG if v != config.HOST_VOICE]
    pool = cast.cohost_candidates(recent_voices=everyone, host_voice=config.HOST_VOICE)
    assert sorted(pool) == sorted(everyone), "a fully-recent pool must still offer everyone"
    ep_cast, _ = cast.episode_cast(before="2026-08-20", recent_voices=everyone)
    assert ep_cast.by_role("cohost").voice_id in everyone


def test_the_rotation_puts_fresh_voices_ahead_of_recent_ones(monkeypatch):
    """The ordering IS the rule, so assert on it directly rather than only on the outcome."""
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    stale = ["flux-wade-en", "flux-cole-en", "flux-jack-en"]
    pool = cast.cohost_candidates(recent_voices=stale, host_voice=config.HOST_VOICE)
    first_stale = min(pool.index(v) for v in stale)
    assert first_stale == len(pool) - len(stale), (
        "every recent voice must sit behind every fresh one, not merely be shuffled down"
    )


def test_recent_cohost_voices_is_relative_to_the_episode_being_made(tmp_path, monkeypatch):
    """`before`-aware, exactly like `recent_desk_roles` was: a backfill must not read the future,
    and a re-render of today must not read its own previous attempt and change its mind."""
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    _episode_on_disk(tmp_path, "2026-08-18", "flux-wade-en")
    _episode_on_disk(tmp_path, "2026-08-19", "flux-cole-en")
    _episode_on_disk(tmp_path, "2026-08-20", "flux-jack-en")   # this run's own first attempt

    assert cast.recent_cohost_voices() == ["flux-jack-en", "flux-cole-en", "flux-wade-en"]
    assert cast.recent_cohost_voices(before="2026-08-20") == ["flux-cole-en", "flux-wade-en"]
    assert cast.recent_cohost_voices(before="2026-08-19") == ["flux-wade-en"]


def test_the_cohost_is_stable_for_a_re_render_of_the_same_day(monkeypatch):
    """Same episode id, same co-host. A rotation that moved on every retry would make the second
    render of a failed episode a different show."""
    _production(monkeypatch)
    a, _ = cast.episode_cast(before="2026-08-20", recent_voices=[])
    b, _ = cast.episode_cast(before="2026-08-20", recent_voices=[])
    assert a.by_role("cohost").voice_id == b.by_role("cohost").voice_id


def test_a_retired_voice_can_never_take_the_second_chair(monkeypatch):
    """RETIRED_VOICES is the by-ear veto, and the co-host pool is the widest surface it guards."""
    _production(monkeypatch)
    for day in [f"2026-10-{n:02d}" for n in range(1, 29)]:
        ep_cast, _ = cast.episode_cast(before=day, recent_voices=[])
        assert ep_cast.by_role("cohost").voice_id not in config.RETIRED_VOICES


def test_an_uncastable_episode_still_fails_loudly(monkeypatch):
    """Preserved from the desk era: an episode nobody can cast must raise, not ship two
    characters sharing one voice."""
    monkeypatch.setattr(config, "active_voice_catalog", lambda: {})
    with pytest.raises(cast.RoleUnavailable):
        cast.episode_cast(before="2026-08-20")


def test_a_catalog_with_only_the_host_in_it_cannot_seat_a_cohost(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: {"flux-alexis-en": ("Alexis", "American F Adult, Clear")})
    with pytest.raises(cast.RoleUnavailable):
        cast.episode_cast(before="2026-08-20")


def test_the_desk_machinery_is_gone(monkeypatch):
    """Sam's explicit choice, made after being shown the cost. Beat routing and desk
    substitution are removed, so the names that implemented them must not linger as dead code
    someone later wires back up."""
    for gone in ("desk_of_the_day", "recent_desk_roles", "_desk_score", "_haystack",
                 "TIE_TOLERANCE"):
        assert not hasattr(cast, gone), f"cast.{gone} still exists"
    assert not hasattr(cast.Cast, "route")
    assert not hasattr(cast.Cast, "score_for")
    assert set(cast.ROLE_VOICES) == {"anchor"}, (
        "the co-host has no fixed preference list; that is the feature. Its candidates come "
        "from cohost_candidates() against the live catalog."
    )


# --- item 2: the host is Alexis, consistently --------------------------------------------------

def test_the_configured_host_voice_is_alexis():
    """HOST_VOICE said Haley while the show cast Alexis for weeks. One host, one id."""
    assert config.HOST_VOICE == "flux-alexis-en"
    assert config.voice_name(config.HOST_VOICE) == "Alexis"


def test_the_host_the_show_casts_is_the_host_the_config_names(monkeypatch):
    _production(monkeypatch)
    ep_cast, _ = cast.episode_cast(before="2026-08-20")
    assert ep_cast.anchor.voice_id == config.HOST_VOICE
    assert ep_cast.anchor.name == "Alexis"


# --- item 4: Priya is retired ------------------------------------------------------------------

def test_priya_is_retired():
    assert "flux-priya-en" in config.RETIRED_VOICES


def test_priya_is_absent_from_every_castable_surface():
    """One line in RETIRED_VOICES has to be enough, or the retirement is not reversible."""
    assert "flux-priya-en" not in config.VOICE_CATALOG
    assert "flux-priya-en" not in config.ALL_VOICES
    assert "flux-priya-en" not in config.COMMENTER_VOICES
    assert "flux-priya-en" not in config.GUEST_VOICES
    assert "flux-priya-en" not in config.FLUX_SUGGESTED_EXPRESSIVITY
    for prefs in cast.ROLE_VOICES.values():
        assert "flux-priya-en" not in prefs


def test_priya_can_never_be_cast_as_the_cohost(monkeypatch):
    _production(monkeypatch)
    pool = cast.cohost_candidates(recent_voices=[], host_voice=config.HOST_VOICE)
    assert "flux-priya-en" not in pool


# --- item 5: the host introduces herself -------------------------------------------------------

def test_the_cold_open_names_the_host(monkeypatch):
    """The show named Deepgram from day one but the host never named herself."""
    from hn_radio import pipeline
    _production(monkeypatch)
    ep_cast, _ = cast.episode_cast(before="2026-08-20")
    intro = pipeline._intro_segments(ep_cast, date(2026, 8, 20))
    assert len(intro) == 1
    assert ep_cast.anchor.name in intro[0].text
    assert "Hacker News Radio" in intro[0].text
    # August 21, not 20: the intro speaks the AIR date, which is the day after the front page it
    # covers. See `test_the_intro_speaks_the_air_date_not_the_story_date` below.
    assert "August 21" in intro[0].text


def test_the_intro_speaks_the_air_date_not_the_story_date(monkeypatch):
    """THE OFF-BY-ONE Sam caught by ear. Regression guard.

    `scripts/daily.py` renders `now(PACIFIC) - 1 day`, because a complete day of front page is the
    point, so `episode_date` is the CONTENT date. The intro used to speak that same date as if it
    were today, so the episode that landed on the 23rd opened with "It's Saturday, August 22" --
    correct about its stories, a day behind on air. Nineteen episodes shipped that way.

    Both halves are pinned: the air date is spoken, and the story date is NOT.
    """
    from hn_radio import pipeline
    _production(monkeypatch)
    ep_cast, _ = cast.episode_cast(before="2026-08-22")
    text = pipeline._intro_segments(ep_cast, date(2026, 8, 22))[0].text

    assert "August 23" in text, f"the air date must be spoken: {text}"
    assert "August 22" not in text, f"the story date must not be spoken as today: {text}"
    assert "yesterday" in text.lower(), "the stories have to be framed as yesterday's"


def test_the_air_date_is_derived_not_read_off_the_clock():
    """A re-render must not re-date the episode. Same trap `generated_at` fell into.

    The archive re-render nearly republished nineteen episodes as if they had aired
    that day, because `_finalize` stamped a fresh `generated_at` and the feed reads it as pubDate.
    Deriving the air date from the episode date means a backfill or a re-render says what the
    episode would have said had it aired on time, however long after the fact it runs.
    """
    from hn_radio import pipeline

    assert pipeline.air_date_for(date(2026, 8, 22)) == date(2026, 8, 23)
    assert pipeline.air_date_for(date(2026, 1, 31)) == date(2026, 2, 1)      # month boundary
    assert pipeline.air_date_for(date(2026, 12, 31)) == date(2027, 1, 1)     # year boundary
    assert pipeline.air_date_for(date(2028, 2, 28)) == date(2028, 2, 29)     # leap year


def test_the_outro_does_not_call_the_front_page_todays():
    """Same off-by-one, other end of the show. It used to say "the front page for today"."""
    from hn_radio import pipeline
    from hn_radio.cast import DEFAULT_CAST

    text = pipeline._outro_segments(DEFAULT_CAST)[0].text
    assert "yesterday" in text.lower()
    assert "for today" not in text.lower()


def test_the_intro_follows_whoever_is_actually_hosting(monkeypatch):
    """A hardcoded "Alexis" would lie on the day resolve_role has to substitute."""
    from hn_radio import pipeline
    catalog = {k: v for k, v in config.VOICE_CATALOG.items() if "alexis" not in k}
    monkeypatch.setattr(config, "active_voice_catalog", lambda: catalog)
    ep_cast, _ = cast.episode_cast(before="2026-08-20")
    intro = pipeline._intro_segments(ep_cast, date(2026, 8, 20))
    assert "Haley" in intro[0].text
    assert "Alexis" not in intro[0].text


# --- item 6: the cold open is ONE segment, so it is ONE batch TTS call -------------------------

def _two_person_cast():
    from hn_radio.cast import Cast, Desk
    return Cast(
        anchor=Desk(role="anchor", name="Alexis", voice_id="v-host", beat="hosts",
                    persona="warm"),
        desks=[Desk(role="cohost", name="Wade", voice_id="v-cohost", beat="second chair",
                    persona="curious")],
    )


def test_panel_writer_emits_the_cold_open_as_one_segment():
    segs = PanelWriter().write(_stories(3), None, [], _two_person_cast(), "frontpage",
                              date(2026, 8, 20))
    opens = [s for s in segs if s.desk == "anchor" and not s.source_hn_id]
    assert len(opens) == 1
    for story in _stories(3):
        assert story.title in opens[0].text


def test_claude_writer_merges_a_multi_segment_cold_open_into_one():
    """The 2026-08-20 defect. The model emitted an opener plus one segment per headline, so
    every beat boundary picked up an inter-segment pacing gap: measured starts 7.16, 15.407,
    21.847, 26.263, about 2.9s of dead air between the first two beats for a line that is
    roughly 3.5s of speech. The pauses have to come from punctuation inside one read.
    """
    raw = [
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Three things today.",
         "source_hn_id": 0},
        {"role": "anchor", "desk": "anchor", "speaker_key": "",
         "text": "A court fined Meta another half a billion dollars.", "source_hn_id": 0},
        {"role": "anchor", "desk": "anchor", "speaker_key": "",
         "text": "A new Rust parser reads faster than C.", "source_hn_id": 0},
        {"role": "anchor", "desk": "anchor", "speaker_key": "",
         "text": "And someone rebuilt git in a weekend.", "source_hn_id": 0},
        {"role": "desk", "desk": "cohost", "speaker_key": "", "text": "Start with Meta.",
         "source_hn_id": 42},
    ]
    segs = ClaudeWriter()._to_segments(raw, _two_person_cast())
    opens = [s for s in segs if s.desk == "anchor" and not s.source_hn_id]
    assert len(opens) == 1, [s.text for s in segs]
    for piece in ("Three things today.", "half a billion", "Rust parser",
                  "And someone rebuilt git"):
        assert piece in opens[0].text
    # and it must not swallow the story coverage that follows it
    assert segs[-1].source_hn_id == 42 and segs[-1].desk == "cohost"


def test_the_merge_does_not_join_an_anchor_line_that_covers_a_story():
    """Only the cold open merges. An anchor throw carries a story id and opens that story's
    chapter, so folding it into the previous line would move the chapter mark."""
    raw = [
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Two things today.",
         "source_hn_id": 0},
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "And a git rebuild.",
         "source_hn_id": 0},
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Top story, Meta.",
         "source_hn_id": 42},
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Next up, git.",
         "source_hn_id": 43},
    ]
    segs = ClaudeWriter()._to_segments(raw, _two_person_cast())
    assert [s.source_hn_id for s in segs] == [None, 42, 43]


def test_a_later_run_of_anchor_lines_is_left_alone():
    """The merge is anchored to the TOP of the show. Two adjacent untagged anchor lines in the
    middle of the rundown are a conversation, not a cold open, and joining them would put two
    separate thoughts into one TTS read."""
    raw = [
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Two things today.",
         "source_hn_id": 0},
        {"role": "desk", "desk": "cohost", "speaker_key": "", "text": "A take.",
         "source_hn_id": 42},
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Huh.",
         "source_hn_id": 0},
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Anyway.",
         "source_hn_id": 0},
    ]
    segs = ClaudeWriter()._to_segments(raw, _two_person_cast())
    assert len(segs) == 4


# --- item 7: the two regulars perform the comments, and the record survives --------------------

def test_panel_writer_comments_keep_the_username_and_the_comment_id():
    """The audio changed; the attribution must not. transcript.build_vtt, the cast page and the
    chapter metadata all read speaker_key and source_hn_id, so collapsing speaker_key to the
    performer's name would silently destroy the record of who actually said it."""
    stories = _stories(3)
    top = stories[1]
    segs = PanelWriter().write(stories, top, _comments(top.id), _two_person_cast(),
                              "frontpage", date(2026, 8, 20))
    performed = [s for s in segs if s.role == "commenter"]
    assert [s.speaker_key for s in performed] == ["hnuser0", "hnuser1"]
    assert [s.source_hn_id for s in performed] == [100, 101]


def test_panel_writer_comments_are_voiced_by_the_two_regulars():
    stories = _stories(3)
    top = stories[1]
    ep_cast = _two_person_cast()
    segs = PanelWriter().write(stories, top, _comments(top.id, 4), ep_cast, "frontpage",
                              date(2026, 8, 20))
    regulars = {ep_cast.anchor.voice_id, ep_cast.by_role("cohost").voice_id}
    performed = [s for s in segs if s.role == "commenter"]
    assert performed
    assert all(s.voice_id in regulars for s in performed), [s.voice_id for s in performed]
    # alternating, not all piled on one person
    assert len({s.voice_id for s in performed}) == 2


def test_the_performing_regular_names_the_commenter_before_reading_it():
    """A listener has to know it is a quote and whose, now that no separate voice signals it."""
    stories = _stories(3)
    top = stories[1]
    segs = PanelWriter().write(stories, top, _comments(top.id), _two_person_cast(),
                              "frontpage", date(2026, 8, 20))
    for i, seg in enumerate(segs):
        if seg.role != "commenter":
            continue
        lead = segs[i - 1]
        assert lead.role != "commenter"
        assert seg.speaker_key in lead.text, (
            f"{lead.text!r} does not name {seg.speaker_key} before the quote is read")
        assert lead.voice_id == seg.voice_id or lead.desk in ("anchor", "cohost")


def test_claude_writer_comments_keep_the_username_and_get_a_regulars_voice():
    ep_cast = _two_person_cast()
    raw = [
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "One thing today.",
         "source_hn_id": 0},
        {"role": "commenter", "desk": "", "speaker_key": "dang", "text": "Please be kind.",
         "source_hn_id": 7},
        {"role": "commenter", "desk": "", "speaker_key": "patio11", "text": "Charge more.",
         "source_hn_id": 8},
    ]
    segs = ClaudeWriter()._to_segments(raw, ep_cast)
    performed = [s for s in segs if s.role == "commenter"]
    regulars = {ep_cast.anchor.voice_id, ep_cast.by_role("cohost").voice_id}
    assert [s.speaker_key for s in performed] == ["dang", "patio11"]
    assert [s.source_hn_id for s in performed] == [7, 8]
    assert all(s.voice_id in regulars for s in performed)
    assert performed[0].voice_id != performed[1].voice_id
    # A performed comment carries no desk tag: recast calls that slot "guest", custom.py reads
    # `desk` to decide which lines are a story's coverage, and the web page colours it as a
    # guest row. Tagging it with a regular's desk would break all three.
    assert all(s.desk is None for s in performed)


# `test_the_show_path_no_longer_calls_guest_voice_for` was deleted. It monkeypatched
# `voices.guest_voice_for` to raise, proving the show never reached it. The function is now gone
# outright, which is a stronger guarantee than a guard: there is nothing left to call. The
# monkeypatch also passed no `raising=False`, so it would have errored on the missing attribute.
#
# Sam's reasoning for the original simplification, kept because the docstring quoted it: "we're
# kind of removing some functionality from the hashing of usernames, but we're just trying to
# simplify things."


# --- item 8: it has to read like two people talking -------------------------------------------

def _system(cast_=None, stories=None):
    stories = stories or _stories(3)
    system, _ = ClaudeWriter()._build_prompt(stories, stories[0], [], cast_ or _two_person_cast(),
                                             "frontpage", date(2026, 8, 20))
    return system


def test_the_prompt_frames_the_show_as_a_two_hander():
    s = _system()
    assert "TWO-HANDER" in s
    assert "correspondent" not in s.lower(), (
        "the desks are gone; describing the second chair as a correspondent puts the anchor "
        "back in charge of a beat reporter"
    )


def test_the_prompt_permits_names_without_mandating_them():
    """Already litigated on this show. A revision that REQUIRED a named hand-off on every
    exchange was removed for reading stilted, so this is permission and taste, never a count.
    """
    s = _system()
    assert "Do NOT use a formal hand-off on every exchange" in s
    assert "sounds like a relay" in s
    # No rule that fires per turn. These are the shapes a mandate takes.
    for mandate in ("every exchange must", "on each turn", "in every turn",
                    "use their name each", "always use a name"):
        assert mandate not in s.lower(), f"{mandate!r} turns names back into a rule"
    assert "when it feels natural" in s


def test_the_prompt_still_forbids_a_spoken_self_introduction():
    """The fixed intro already names the host and the co-host, so a writer-written "I'm Alexis"
    would be the second one in twenty seconds."""
    assert "Never have anyone introduce themselves" in _system()


def test_panel_writer_uses_the_cohosts_name_but_not_on_every_line():
    """Deterministic, so it cannot exercise taste: names land at a couple of fixed points."""
    stories = _stories(3)
    top = stories[1]
    ep_cast = _two_person_cast()
    segs = PanelWriter().write(stories, top, _comments(top.id), ep_cast, "frontpage",
                              date(2026, 8, 20))
    host, cohost = ep_cast.anchor.name, ep_cast.by_role("cohost").name
    host_lines = [s for s in segs if s.desk == "anchor"]
    named = [s for s in host_lines if cohost in s.text]
    assert named, "the host never says the co-host's name at all"
    assert len(named) < len(host_lines), (
        "the host names the co-host on every single line, which is the relay this was "
        "supposed to stop reading like"
    )
    assert any(host in s.text for s in segs if s.desk == "cohost"), \
        "the co-host never says the host's name, so only one of them is in a conversation"


# --- item 9: the host introduces and signs off the co-host ------------------------------------

def test_the_intro_introduces_todays_cohost_by_name():
    from hn_radio import pipeline
    ep_cast = _two_person_cast()
    intro = pipeline._intro_segments(ep_cast, date(2026, 8, 20))
    assert len(intro) == 1, "a second segment here reintroduces the gap item 6 removes"
    assert ep_cast.anchor.name in intro[0].text
    assert ep_cast.by_role("cohost").name in intro[0].text


def test_the_outro_signs_off_both_of_them_on_deepgram_flux():
    from hn_radio import pipeline
    ep_cast = _two_person_cast()
    outro = pipeline._outro_segments(ep_cast)
    assert len(outro) == 1
    assert ep_cast.by_role("cohost").name in outro[0].text
    assert "Deepgram Flux" in outro[0].text
    assert "tomorrow" in outro[0].text.lower()


def test_the_signature_lines_are_owned_by_the_pipeline_not_the_writers():
    """Both the intro and the sign-off name today's co-host, so they need the episode cast. The
    writers are told NOT to write a greeting or a sign-off; if either one started, the show
    would open twice."""
    from hn_radio import pipeline
    ep_cast = _two_person_cast()
    written = PanelWriter().write(_stories(3), None, [], ep_cast, "frontpage",
                                 date(2026, 8, 20))
    assert not any("we'll talk to you tomorrow" in s.text.lower() for s in written)
    s = _system()
    assert "do NOT write a greeting or a sign-off" in s
    # and the pipeline's two lines are the only place the show's signature lives
    assert "Hacker News Radio" in pipeline._intro_segments(ep_cast, date(2026, 8, 20))[0].text


def test_the_top_of_the_show_is_two_segments_so_the_bed_still_fits():
    """`music.BED_SEGMENTS = 2` is documented as "the fixed intro plus the writer's cold open".
    Merging the cold open makes that true again; it was the FRAGMENTED cold open that put the
    bed under one third of the opening. Asserted here so a later change to either layer has to
    notice the music coupling.
    """
    from hn_radio import music, pipeline

    ep_cast = _two_person_cast()
    segs = (pipeline._intro_segments(ep_cast, date(2026, 8, 20))
            + PanelWriter().write(_stories(3), None, [], ep_cast, "frontpage",
                                  date(2026, 8, 20)))
    assert music.BED_SEGMENTS == 2
    assert segs[0].desk == "anchor" and segs[0].source_hn_id is None      # fixed intro
    assert segs[1].desk == "anchor" and segs[1].source_hn_id is None      # the whole cold open
    assert segs[2].source_hn_id is not None, "segment 2 is story coverage; the bed must stop"


def test_assign_voices_never_overwrites_a_performed_comments_pinned_voice():
    """The pin is what carries the co-host's voice onto a segment whose desk is None."""
    stories = _stories(3)
    top = stories[1]
    ep_cast = _two_person_cast()
    segs = PanelWriter().write(stories, top, _comments(top.id), ep_cast, "frontpage",
                              date(2026, 8, 20))
    before = {s.order: s.voice_id for s in segs if s.role == "commenter"}
    voices.assign_voices(segs, ep_cast)
    after = {s.order: s.voice_id for s in segs if s.role == "commenter"}
    assert before == after and all(after.values())


# --- the seam a later spacing pass hooks into --------------------------------------------------

def test_cold_open_index_finds_the_one_merged_segment():
    """`writers.cold_open_index` is the hook for the per-sentence spacing work that is being
    consolidated separately. Pinned here so the merge and that pass cannot drift apart: if the
    cold open ever stops being findable this way, the spacing pass silently paces the wrong
    segment, and nothing else would notice."""
    from hn_radio import pipeline, writers

    ep_cast = _two_person_cast()
    segs = (pipeline._intro_segments(ep_cast, date(2026, 8, 20))
            + PanelWriter().write(_stories(3), None, [], ep_cast, "frontpage",
                                  date(2026, 8, 20)))
    i = writers.cold_open_index(segs)
    assert i == 1, "index 0 is the fixed intro, so the cold open is the one after it"
    for story in _stories(3):
        assert story.title in segs[i].text


def test_cold_open_index_is_none_for_an_episode_with_no_stories():
    """custom.py can reach the writer with an empty pick when its source pool has rotated, and
    then there is no cold open to space out."""
    from hn_radio import pipeline, writers

    ep_cast = _two_person_cast()
    segs = (pipeline._intro_segments(ep_cast, date(2026, 8, 20))
            + PanelWriter().write([], None, [], ep_cast, "custom", date(2026, 8, 20)))
    assert writers.cold_open_index(segs) is None


# --- the legacy single-narrator path -----------------------------------------------------------

def test_the_legacy_assembler_narrator_also_names_herself():
    """`--legacy` is a different show, not a different product. The self-introduction is a
    property of HN Radio, so the v1 path says it too -- read from the configured host voice,
    since that path has no Cast to ask."""
    from hn_radio.script_assembly import TemplateAssembler

    segs = TemplateAssembler().assemble(_stories(2), None, [], date(2026, 8, 20))
    assert config.voice_name(config.host_voice()) in segs[0].text
    assert "Hacker News front page" in segs[0].text
