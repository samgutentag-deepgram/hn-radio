import json
from datetime import date

import pytest

from hn_radio import custom
from hn_radio.custom import ConfigError
from hn_radio.models import Story


def _story(hn_id, title, points=100, url=None):
    return Story(id=hn_id, title=title, url=url, points=points, author="someone",
                 num_comments=3, rank=1)


def _write_episode(root, ep_id, source_items, segments, with_pcm=()):
    """Create a fake rendered episode on disk. `with_pcm` lists segment orders that have audio."""
    d = root / ep_id
    (d / "segments").mkdir(parents=True)
    (d / "episode.json").write_text(json.dumps({"id": ep_id, "title": ep_id,
                                                "source_items": source_items}))
    (d / "script.json").write_text(json.dumps(segments))
    for order in with_pcm:
        (d / "segments" / f"{order}.pcm").write_bytes(b"\x00\x00" * 10)
    return d


def _seg(order, desk, hn_id, voice, text="line"):
    return {"order": order, "role": "desk", "speaker_key": desk, "text": text,
            "source_hn_id": hn_id, "voice_id": voice, "desk": desk}


# --- validate / config_id -------------------------------------------------------------------

def test_validate_fills_defaults_and_sorts_desks():
    cfg = custom.validate({"desks": {"maker": "flux-wade-en", "ai": "flux-meena-en"}})
    assert cfg["anchor"] == "flux-alexis-en"         # config.HOST_VOICE, Alexis since 2026-08-20
    assert list(cfg["desks"]) == ["ai", "maker"]     # sorted, so the hash is order-independent
    assert cfg["drama"] is None
    assert cfg["days"] == custom.DEFAULT_DAYS


def test_validate_rejects_bad_input():
    with pytest.raises(ConfigError, match="at least one desk"):
        custom.validate({"desks": {}})
    with pytest.raises(ConfigError, match="Unknown desk"):
        custom.validate({"desks": {"anchor": "flux-haley-en"}})   # anchor is not selectable
    with pytest.raises(ConfigError, match="Unknown desk"):
        custom.validate({"desks": {"drama": "flux-cole-en"}})      # drama covers comments only
    with pytest.raises(ConfigError, match="Unknown voice"):
        custom.validate({"desks": {"ai": "flux-nobody-en"}})
    with pytest.raises(ConfigError, match="Unknown anchor"):
        custom.validate({"anchor": "nope", "desks": {"ai": "flux-meena-en"}})
    with pytest.raises(ConfigError, match="between 1 and 7"):
        custom.validate({"desks": {"ai": "flux-meena-en"}, "days": 99})


def test_config_id_is_stable_and_order_independent():
    a = custom.config_id({"desks": {"ai": "flux-meena-en", "maker": "flux-wade-en"}})
    b = custom.config_id({"desks": {"maker": "flux-wade-en", "ai": "flux-meena-en"}})
    assert a == b and len(a) == 8
    # a different voice is a different feed, so it must be a different id
    assert custom.config_id({"desks": {"ai": "flux-heather-en"}}) != \
        custom.config_id({"desks": {"ai": "flux-meena-en"}})


# --- pools ----------------------------------------------------------------------------------

def test_pool_from_episodes_reads_the_owning_desk_from_the_script(tmp_path):
    _write_episode(
        tmp_path, "2026-08-05",
        [{"hn_id": 1, "title": "A model thing", "url": "u1"},
         {"hn_id": 2, "title": "A repo thing", "url": "u2"}],
        [{"order": 0, "role": "anchor", "desk": "anchor", "text": "intro", "source_hn_id": None,
          "voice_id": "flux-haley-en", "speaker_key": "Haley"},
         # the anchor also has a line tagged with story 1; it must not decide the owning desk
         {"order": 1, "role": "anchor", "desk": "anchor", "text": "over to Meena",
          "source_hn_id": 1, "voice_id": "flux-haley-en", "speaker_key": "Haley"},
         _seg(2, "ai", 1, "flux-meena-en"),
         _seg(3, "maker", 2, "flux-wade-en")],
    )
    pool = custom.pool_from_episodes(days=3, today=date(2026, 8, 5), episodes_dir=tmp_path)
    by_id = {p["hn_id"]: p for p in pool}
    assert by_id[1]["desk"] == "ai"
    assert by_id[2]["desk"] == "maker"
    assert by_id[1]["source"] == "episode" and by_id[1]["episode_id"] == "2026-08-05"


def test_pool_from_episodes_skips_derived_and_out_of_window(tmp_path):
    items = [{"hn_id": 7, "title": "T", "url": None}]
    segs = [_seg(0, "ai", 7, "flux-meena-en")]
    _write_episode(tmp_path, "2026-08-05", items, segs)
    _write_episode(tmp_path, "2026-08-05-recast", [{"hn_id": 8, "title": "R"}],
                   [_seg(0, "ai", 8, "flux-meena-en")])
    _write_episode(tmp_path, "2026-08-05-custom-abc12345", [{"hn_id": 9, "title": "C"}],
                   [_seg(0, "ai", 9, "flux-meena-en")])
    _write_episode(tmp_path, "2026-07-01", [{"hn_id": 10, "title": "Old"}],
                   [_seg(0, "ai", 10, "flux-meena-en")])

    pool = custom.pool_from_episodes(days=3, today=date(2026, 8, 5), episodes_dir=tmp_path)
    assert [p["hn_id"] for p in pool] == [7], "recasts, customs and out-of-window days are excluded"


def test_pool_from_live_routes_and_drops_unroutable():
    pool = custom.pool_from_live([_story(1, "New LLM inference benchmark"),
                                 _story(2, "Show HN: I built a thing in Rust")],
                                today=date(2026, 8, 6))
    desks = {p["hn_id"]: p["desk"] for p in pool}
    assert desks[1] == "ai"
    assert desks[2] == "maker"
    assert all(p["source"] == "live" and p["episode_id"] is None for p in pool)


def test_build_pool_prefers_the_already_rendered_copy(tmp_path):
    _write_episode(tmp_path, "2026-08-06", [{"hn_id": 42, "title": "Rendered", "url": None}],
                   [_seg(0, "ai", 42, "flux-meena-en")])
    pool = custom.build_pool(days=1, live_stories=[_story(42, "Same story, live")],
                             today=date(2026, 8, 6), episodes_dir=tmp_path)
    assert len(pool) == 1
    assert pool[0]["source"] == "episode", "the rendered copy is free to reuse, so it wins"


# --- select / plan --------------------------------------------------------------------------

def test_select_filters_by_desk_and_caps_by_points():
    pool = [{"hn_id": i, "desk": "ai", "points": i, "day": "2026-08-05", "source": "episode",
             "episode_id": "e", "title": f"t{i}", "url": None} for i in range(1, 10)]
    pool.append({"hn_id": 99, "desk": "security", "points": 1000, "day": "2026-08-05",
                 "source": "episode", "episode_id": "e", "title": "sec", "url": None})

    chosen = custom.select({"desks": {"ai": "flux-meena-en"}}, pool, cap=3)
    assert [c["hn_id"] for c in chosen] == [9, 8, 7], "highest points survive the cap"
    assert all(c["desk"] == "ai" for c in chosen), "an unpicked desk is filtered out entirely"


def test_plan_counts_reuse_rerender_and_new(tmp_path):
    _write_episode(
        tmp_path, "2026-08-05",
        [{"hn_id": 1, "title": "A", "url": None}, {"hn_id": 2, "title": "B", "url": None}],
        [{"order": 0, "role": "anchor", "desk": "anchor", "text": "hand off", "source_hn_id": 1,
          "voice_id": "flux-haley-en", "speaker_key": "Haley"},
         _seg(1, "ai", 1, "flux-meena-en"),      # keeps Meena -> reuse
         _seg(2, "ai", 1, "flux-meena-en"),      # cached too  -> reuse
         _seg(3, "maker", 2, "flux-wade-en")],  # maker recast to another voice -> rerender
        with_pcm=(1, 2, 3),
    )
    chosen = [
        {"hn_id": 1, "desk": "ai", "source": "episode", "episode_id": "2026-08-05",
         "points": 10, "day": "2026-08-05", "title": "A", "url": None},
        {"hn_id": 2, "desk": "maker", "source": "episode", "episode_id": "2026-08-05",
         "points": 9, "day": "2026-08-05", "title": "B", "url": None},
        {"hn_id": 3, "desk": "ai", "source": "live", "episode_id": None,
         "points": 8, "day": "2026-08-06", "title": "Live", "url": None},
    ]
    cfg = {"desks": {"ai": "flux-meena-en", "maker": "flux-heather-en"}}
    got = custom.plan(cfg, chosen, episodes_dir=tmp_path)

    assert got["reuse"] == 2, "unchanged words + unchanged voice + cached pcm"
    assert got["rerender"] == 1, "maker's voice changed, so its line needs new audio"
    assert got["new"] == 2 + 1, "intro and sign-off are always new, plus the live story"


def test_plan_counts_a_missing_pcm_cache_as_rerender(tmp_path):
    _write_episode(tmp_path, "2026-08-05", [{"hn_id": 1, "title": "A", "url": None}],
                   [_seg(1, "ai", 1, "flux-meena-en")], with_pcm=())   # no cached audio
    chosen = [{"hn_id": 1, "desk": "ai", "source": "episode", "episode_id": "2026-08-05",
               "points": 1, "day": "2026-08-05", "title": "A", "url": None}]
    got = custom.plan({"desks": {"ai": "flux-meena-en"}}, chosen, episodes_dir=tmp_path)
    assert got["reuse"] == 0 and got["rerender"] == 1


# --- collect_segments -----------------------------------------------------------------------

def test_collect_segments_renumbers_and_records_provenance(tmp_path):
    _write_episode(
        tmp_path, "2026-08-04", [{"hn_id": 1, "title": "A", "url": None}],
        [{"order": 5, "role": "anchor", "desk": "anchor", "text": "hand off", "source_hn_id": 1,
          "voice_id": "flux-haley-en", "speaker_key": "Haley"},
         _seg(6, "ai", 1, "flux-meena-en", text="ai line"),
         _seg(7, "security", 1, "flux-jack-en", text="security line")],
        with_pcm=(6, 7),
    )
    chosen = [{"hn_id": 1, "desk": "ai", "source": "episode", "episode_id": "2026-08-04",
               "points": 1, "day": "2026-08-04", "title": "A", "url": None}]

    segments, provenance, orig_voice = custom.collect_segments(
        {"desks": {"ai": "flux-heather-en"}}, chosen, episodes_dir=tmp_path)

    assert [s.order for s in segments] == [1], "order 0 is reserved for the intro"
    assert segments[0].text == "ai line"
    assert segments[0].voice_id == "flux-heather-en", "the chosen voice overrides the original"
    assert provenance == {1: ("2026-08-04", 6)}, "provenance points back at the source episode"
    # the source voice is kept so a name rewrite can find the old correspondent's name
    assert orig_voice == {"ai": "flux-meena-en"}
    # the anchor hand-off and the unpicked security desk are both dropped
    assert all(s.desk == "ai" for s in segments)


def test_collect_segments_skips_live_stories(tmp_path):
    chosen = [{"hn_id": 1, "desk": "ai", "source": "live", "episode_id": None,
               "points": 1, "day": "2026-08-06", "title": "Live", "url": None}]
    segments, provenance, orig_voice = custom.collect_segments(
        {"desks": {"ai": "flux-meena-en"}}, chosen, episodes_dir=tmp_path)
    assert segments == [] and provenance == {} and orig_voice == {}, \
        "a live story has no script to collect yet"


# --- cast_from_config -----------------------------------------------------------------------

def test_cast_from_config_carries_chosen_voices_and_drops_unpicked_desks():
    cast = custom.cast_from_config({
        "anchor": "flux-cole-en",
        "desks": {"ai": "flux-heather-en"},
        "drama": "flux-sharon-en",
    })
    assert cast.anchor.voice_id == "flux-cole-en"
    assert cast.anchor.name == "Cole", "the anchor's name follows the chosen voice"

    roles = {d.role: d for d in cast.desks}
    assert set(roles) == {"ai", "drama"}, "maker and security were not picked, so they are absent"
    assert roles["ai"].voice_id == "flux-heather-en" and roles["ai"].name == "Heather"
    # The assertion here used to be `roles["ai"].keywords` with the message "routing keywords are
    # inherited so routing still works", which said the opposite of what `custom.py` says: fresh
    # lines have not been routed by keyword since `Cast.route` was removed. The field went with it
    # on 2026-08-22. What actually matters for a picked desk is the beat and persona it speaks
    # with, so pin those.
    assert roles["ai"].beat and roles["ai"].persona, "a picked desk must carry its topic's brief"
    assert cast.default_role in roles, "the fallback desk must be one that is actually present"


def test_cast_from_config_omits_drama_when_not_requested():
    cast = custom.cast_from_config({"desks": {"security": "flux-jack-en"}})
    assert [d.role for d in cast.desks] == ["security"]
    assert cast.default_role == "security"


# --- assemble -------------------------------------------------------------------------------

def _one_story_episode(tmp_path, ep_id="2026-08-05", voice="flux-meena-en"):
    _write_episode(
        tmp_path, ep_id, [{"hn_id": 1, "title": "A model thing", "url": None}],
        [{"order": 0, "role": "anchor", "desk": "anchor", "text": "Over to you, Meena.",
          "source_hn_id": 1, "voice_id": "flux-haley-en", "speaker_key": "Haley"},
         _seg(1, "ai", 1, voice, text="Meena here with the details."),
         _seg(2, "ai", 1, voice, text="And that is the model story.")],
        with_pcm=(1, 2),
    )
    return [{"hn_id": 1, "desk": "ai", "source": "episode", "episode_id": ep_id,
             "points": 10, "day": "2026-08-05", "title": "A model thing", "url": None}]


def test_assemble_wraps_intro_and_outro_and_renumbers(tmp_path):
    chosen = _one_story_episode(tmp_path)
    a = custom.assemble({"desks": {"ai": "flux-meena-en"}}, chosen,
                        today=date(2026, 8, 6), episodes_dir=tmp_path)
    segs = a["segments"]

    assert [s.order for s in segs] == [0, 1, 2, 3], "orders are sequential from 0"
    assert segs[0].desk == "anchor" and segs[-1].desk == "anchor"
    assert "custom edition" in segs[0].text.lower()
    assert "AI desk with Meena" in segs[0].text, "the intro names what is actually in the episode"
    assert "3 days" in segs[0].text
    assert a["episode_id"] == "2026-08-06-custom-" + a["config_id"]


def test_assemble_rekeys_provenance_to_new_positions(tmp_path):
    chosen = _one_story_episode(tmp_path)
    a = custom.assemble({"desks": {"ai": "flux-meena-en"}}, chosen,
                        today=date(2026, 8, 6), episodes_dir=tmp_path)
    # the two reused lines sit at positions 1 and 2, after the intro
    assert a["provenance"] == {1: ("2026-08-05", 1), 2: ("2026-08-05", 2)}


def test_assemble_rewrites_correspondent_names_only_when_the_voice_changed(tmp_path):
    chosen = _one_story_episode(tmp_path)
    a = custom.assemble({"desks": {"ai": "flux-heather-en"}}, chosen,
                        today=date(2026, 8, 6), episodes_dir=tmp_path)
    body = [s for s in a["segments"] if s.desk == "ai"]
    assert body, "the AI lines survived"
    assert all("Meena" not in s.text for s in body), "the old correspondent name is gone"
    assert any("Heather" in s.text for s in body), "replaced with the newly chosen voice's name"
    assert all(s.speaker_key == "Heather" for s in body)


def test_assemble_keeps_words_intact_when_the_voice_is_unchanged(tmp_path):
    chosen = _one_story_episode(tmp_path)
    a = custom.assemble({"desks": {"ai": "flux-meena-en"}}, chosen,
                        today=date(2026, 8, 6), episodes_dir=tmp_path)
    body = [s for s in a["segments"] if s.desk == "ai"]
    assert body[0].text == "Meena here with the details.", \
        "an unchanged voice must keep its exact words, or its cached audio stops being reusable"


def test_assemble_tolerates_a_live_pick_with_no_matching_story(tmp_path):
    """A live candidate whose Story object was not passed in must be skipped, not crash."""
    chosen = _one_story_episode(tmp_path)
    chosen.append({"hn_id": 999, "desk": "ai", "source": "live", "episode_id": None,
                   "points": 5, "day": "2026-08-06", "title": "Live one", "url": None})
    a = custom.assemble({"desks": {"ai": "flux-meena-en"}}, chosen,
                        today=date(2026, 8, 6), episodes_dir=tmp_path)
    assert len(a["segments"]) == 4, "intro + 2 reused + outro; the unresolvable live pick is dropped"


def test_select_reserves_room_for_cached_stories(tmp_path):
    """A plain points sort gave the whole episode to live stories, wasting the segment cache.

    Older episodes never recorded `points` in source_items, so their stories score 0 and lose
    every comparison against a live story. The cap must therefore be filled from both sources.
    """
    cached = [{"hn_id": i, "desk": "ai", "points": 0, "day": "2026-08-05", "source": "episode",
               "episode_id": "2026-08-05", "title": f"cached {i}", "url": None} for i in range(1, 6)]
    live = [{"hn_id": 100 + i, "desk": "ai", "points": 900, "day": "2026-08-06", "source": "live",
             "episode_id": None, "title": f"live {i}", "url": None} for i in range(1, 8)]

    chosen = custom.select({"desks": {"ai": "flux-meena-en"}}, cached + live, cap=6)
    kinds = [c["source"] for c in chosen]
    assert len(chosen) == 6
    assert kinds.count("episode") >= 3, "at least half the cap is reserved for reusable material"
    assert kinds.count("live") >= 1, "today still gets in"


def test_select_fills_entirely_from_cache_when_nothing_is_live():
    cached = [{"hn_id": i, "desk": "ai", "points": 0, "day": "2026-08-05", "source": "episode",
               "episode_id": "e", "title": f"c{i}", "url": None} for i in range(1, 10)]
    chosen = custom.select({"desks": {"ai": "flux-meena-en"}}, cached, cap=6)
    assert len(chosen) == 6, "the live half is absorbed when there is nothing live to put in it"


def test_select_fills_entirely_from_live_when_nothing_is_cached():
    live = [{"hn_id": i, "desk": "ai", "points": i, "day": "2026-08-06", "source": "live",
             "episode_id": None, "title": f"l{i}", "url": None} for i in range(1, 10)]
    chosen = custom.select({"desks": {"ai": "flux-meena-en"}}, live, cap=6)
    assert len(chosen) == 6 and all(c["source"] == "live" for c in chosen)

def test_the_routable_desks_are_exactly_the_three_topics():
    """RELOCATED from `tests/test_api_build.py` on 2026-08-22, and it had to be.

    That test asserted the membership through `/api/build/pool`'s `desks` key, which was the
    suite's ONLY pin on it. The key was deleted as unread by the picker, so without moving the
    assertion here the set could have changed silently -- and `custom.py:86-87` rejects an unknown
    desk with a 400, so a drift shows up as a listener's build failing rather than as a test.
    """
    assert custom.ROUTABLE_DESKS == ("ai", "maker", "security")
