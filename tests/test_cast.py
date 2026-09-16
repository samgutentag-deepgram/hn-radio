import json

import pytest
from dataclasses import MISSING, fields

from hn_radio import cast, config, voices
from hn_radio.cast import DEFAULT_CAST
from hn_radio.models import ScriptSegment

# `_story` used to live here, because casting read the day's stories to score each desk's beat.
# It does not any more (the co-host rotation is a function of the episode id), so a Story fixture
# in this file would only be scenery. The one test below that still needs stories builds them
# inline, next to the pipeline wiring it is actually checking.


def test_voice_for_seat_returns_that_seats_voice():
    assert DEFAULT_CAST.voice_for("cohost") == DEFAULT_CAST.desks[0].voice_id
    assert DEFAULT_CAST.voice_for("anchor") == DEFAULT_CAST.anchor.voice_id
    # An unknown seat falls back to the host, which is what makes ClaudeWriter's out-of-cast
    # guard load-bearing: without it a line for a seat this episode lacks is silently hers.
    assert DEFAULT_CAST.voice_for("ai") == DEFAULT_CAST.anchor.voice_id


def test_assign_voices_uses_the_seat_when_cast_given():
    segs = [
        ScriptSegment(order=0, role="anchor", speaker_key="Alexis", text="hi", desk="anchor"),
        ScriptSegment(order=1, role="desk", speaker_key="Wade", text="a repo", desk="cohost"),
    ]
    voices.assign_voices(segs, cast=DEFAULT_CAST)
    assert segs[0].voice_id == DEFAULT_CAST.anchor.voice_id
    assert segs[1].voice_id == DEFAULT_CAST.desks[0].voice_id
    assert segs[0].voice_id != segs[1].voice_id


def test_assign_voices_without_cast_is_v1_compatible():
    segs = [ScriptSegment(order=0, role="host", speaker_key="host", text="hi")]
    voices.assign_voices(segs)  # no cast, no desk
    assert segs[0].voice_id  # still assigned via the v1 path


# A small, fixed catalog to resolve roles against.
#
# It plays the exact part `config.STAGING_CAST_VOICES` used to play in this file: a handful of
# known voices in a known order, so a test can assert WHICH one `resolve_role` picks without
# depending on the thirty-odd entries of the real catalog, and without breaking every time a voice
# is retired by ear.
#
# GA ids, because the staging split is GONE (recovered deploy, 2026-09-05). Flux went GA on
# 2026-08-12, the pre-GA `_studio` and `ga_candidate` ids 400 in production, and `config.on_staging`
# / `STAGING_CAST_VOICES` / `cast.staging_cast` were deleted as dead code.
#
# The first two entries are `cast.ROLE_VOICES["anchor"]` IN ORDER, which is what makes the
# preference and substitution assertions below mean anything: Alexis is the first choice, Haley is
# who the show falls back to, and that is the real rule rather than a fixture convenience.
SMALL_CATALOG = {
    "flux-alexis-en": ("Alexis", "American F. The morning host, and the first anchor preference."),
    "flux-haley-en":  ("Haley", "American F. The anchor fallback."),
    # Two voices that are NOT hosts, because `cast.cohost_candidates` subtracts
    # `host_voice_ids()` -- now Alexis AND Cole, since the afternoon edition has its own host --
    # from the pool. A catalog of nothing but host voices cannot seat a second chair at all, which
    # is a `RoleUnavailable` rather than the thing any of these tests is about.
    "flux-wade-en":   ("Wade", "American M. Eligible for the second chair."),
    "flux-meena-en":  ("Meena", "Indian F. Eligible for the second chair."),
}


# --- active_cast: one rule for the render path and the cast page --------------------------------
#
# Regression guard. These two sides disagreed: `publish` chose the staging cast while
# `pipeline.run_panel` took `cast=DEFAULT_CAST` as a DEFAULT ARGUMENT, bound at import. Staging
# serves both sets of voice ids, so the mismatch raised nothing at all -- the site simply listed
# the pre-GA cast and rendered the production one. Only an assertion can catch that.

def test_active_cast_is_the_production_seating():
    assert cast.active_cast() is cast.DEFAULT_CAST


# TWO TESTS WERE DELETED HERE, 2026-09-16, and what they guarded is worth recording because the
# guard is gone rather than satisfied.
#
#   test_active_cast_is_the_pre_ga_seating_on_staging
#   test_active_cast_follows_the_host_at_call_time_not_import_time
#
# Both were about the production/staging voice split, and their whole mechanism was that
# `active_cast()` returns DIFFERENT seating depending on `config.on_staging()`. That function, the
# `STAGING_*` tables and `cast.staging_cast` were all deleted in the work recovered off the machine
# on 2026-09-05: Flux went GA on 2026-08-12 and the pre-GA ids now 400 in production, so the
# branch they exercised cannot be constructed any more.
#
# The SECOND one is the loss worth naming. It was not really about staging; it was about a value
# captured at import time not being able to follow a later change, and staging was just the
# cheapest way to make the host change mid-test. That bug class is still live -- `active_cast` is
# still a function precisely so it is read at call time -- but with one catalog there is no second
# host to switch to, so there is nothing left to assert. If a second host ever returns (and
# `config.AFTERNOON_HOST_VOICE` means one now exists for the afternoon edition), this is the test
# to write again.


def test_run_panel_takes_no_cast_parameter_at_all():
    """Strictly stronger than the assertion this replaced, which pinned `cast`'s default to None.

    The shipped bug (`.hub/ledger.md:584`, `cast.py:222`) was `cast=DEFAULT_CAST` bound at import:
    the cast page advertised pre-GA seating while the render path used production, and it was
    silent because staging serves both id sets. A default of None could not reintroduce that, but
    the parameter itself was the invitation. It was deleted 2026-08-22 as unreachable, so pin its
    ABSENCE: no default to get wrong, and re-adding the seam fails here.
    """
    import inspect

    from hn_radio import pipeline
    assert "cast" not in inspect.signature(pipeline.run_panel).parameters


def test_pipeline_does_not_import_the_cast_constant():
    """It imported DEFAULT_CAST and never used it, which is how the import-time trap gets set.

    A name in scope is an invitation to reach for it, and reaching for it is precisely the bug
    the two tests above exist to catch. pipeline builds its cast through `active_cast()` and
    `episode_cast`, both called at run time, so the constant has no business being in scope.
    """
    from hn_radio import pipeline
    assert not hasattr(pipeline, "DEFAULT_CAST")


# `test_the_cast_page_and_the_render_path_agree` was deleted here on 2026-09-16. It asserted that
# `active_cast()` and `cast.staging_cast()` resolve the same desks, and `staging_cast` no longer
# exists. The property it was reaching for -- the page and the render path agree about seating --
# is the subject of the next two tests, which check it against `resolve_role` (what the render path
# actually calls) rather than against a second constant, and are the stronger version anyway.


def _voices_json(tmp_path):
    from hn_radio import manifest
    return json.loads(manifest.build_voices_json(tmp_path).read_text())


def test_cast_page_anchor_is_the_one_the_render_path_would_cast(tmp_path, monkeypatch):
    """voices.json must name the anchor `resolve_role` casts, not a different seating.

    These two DID disagree. The page read `active_cast()` (the five-desk constant) while the
    render path reads `episode_cast` -> `resolve_role`, and the two resolved to different people.
    Nothing failed at the time, because the staging host served both sets of ids.
    """
    data = _voices_json(tmp_path)
    seats = {s["role"]: s for s in data["seating"]}
    expected, _ = cast.resolve_role("anchor")
    assert seats["anchor"]["voice"] == expected.voice_id
    assert seats["anchor"]["name"] == expected.name


def test_cast_page_seats_the_two_the_render_path_would(tmp_path, monkeypatch):
    """Two seats, on both hosts, and the page must agree with the render path about both.

    It listed five until 2026-08-20 because the show had five desks. Now it is a host and a
    second chair.

    The `cohost_pool` key this used to assert on is gone (2026-08-22): `cast.html` was rewritten
    into a 36-voice grid and stopped reading it, so the manifest was publishing a rotation list
    with no reader. The rotation itself is still what seats the second chair, and that is what
    the assertion below now pins -- via the seated voice rather than via a published copy of the
    pool, which is the stronger check anyway.
    """
    # One pass, not one per host. This used to loop over `on_staging` False/True, which was the
    # only reason it took `monkeypatch`; there is one catalog now.
    if True:
        data = _voices_json(tmp_path)
        seats = {s["role"]: s for s in data["seating"]}
        assert list(seats) == ["anchor", "cohost"]
        assert seats["anchor"]["voice"] == cast.resolve_role("anchor")[0].voice_id
        # the co-host resolves against the same rotation the render path uses
        pool = cast.cohost_candidates(cast.recent_cohost_voices(),
                                       seats["anchor"]["voice"])
        assert "cohost_pool" not in data, "the manifest stopped publishing the pool in 2026-08-22"
        assert seats["cohost"]["voice"] == pool[0]
        assert seats["cohost"]["voice"] != seats["anchor"]["voice"]
        # `presets.flux` used to be asserted here -- the recast picker's one-click cast, which
        # had to agree with the seating it sat next to. Both the picker and the key are gone
        # (2026-09-16). The seating assertions above are what that check was protecting.


def test_voice_name_spans_both_catalogs():
    """Both catalogs means Flux and Aura now.

    It used to mean production and staging, and named two pre-GA ids that no longer exist. The
    remaining split is real, and it is why the function reads `ALL_VOICES` rather than
    `VOICE_CATALOG`: `render.py` still has a /v1/speak path, so an Aura id is a voice this code can
    be asked to name even though the site never offers one.
    """
    assert config.voice_name("flux-haley-en") == "Haley"
    assert config.voice_name("flux-alexis-en") == "Alexis"
    assert config.voice_name("aura-2-thalia-en") == "Thalia"
    assert config.voice_name("flux-not-a-voice-en") is None


# --- resolve_role: fill a role from the active catalog, reporting who was unavailable ----------


def test_resolve_role_takes_the_first_available_preference(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(SMALL_CATALOG))
    desk, substituted_for = cast.resolve_role("anchor")
    assert desk.voice_id == "flux-alexis-en"
    assert desk.name == "Alexis"
    assert substituted_for is None


def test_resolve_role_casts_the_ga_alexis_on_production_with_no_substitution(monkeypatch):
    """CORRECTED. This test used to assert production resolved to Haley substituting for Alexis.

    That encoded a false belief: flux-alexis-en has been in the production catalog the whole
    time (config.VOICE_CATALOG, "American F 18-24, friendly, intelligent, fast"). It was simply
    missing from ROLE_VOICES["anchor"], so every production episode would have opened with an
    in-fiction "Alexis is out today" while a GA voice named Alexis sat unused. Same
    same-character-two-ids case as Haley, and the existing guard now suppresses it.
    """
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(config.VOICE_CATALOG))
    desk, substituted_for = cast.resolve_role("anchor")
    assert desk.voice_id == "flux-alexis-en"
    assert desk.name == "Alexis"
    assert substituted_for is None


def test_resolve_role_substitutes_and_reports_who_is_missing(monkeypatch):
    """A REAL substitution: a catalog with neither Alexis id, so Haley genuinely stands in."""
    catalog = {k: v for k, v in config.VOICE_CATALOG.items() if "alexis" not in k}
    monkeypatch.setattr(config, "active_voice_catalog", lambda: catalog)
    desk, substituted_for = cast.resolve_role("anchor")
    assert desk.voice_id == "flux-haley-en"
    assert desk.name == "Haley"
    assert substituted_for == "Alexis"


def test_resolve_role_keeps_the_description_of_the_role(monkeypatch):
    """Who fills a seat must not change what the seat is."""
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(SMALL_CATALOG))
    desk, _ = cast.resolve_role("anchor")
    canonical = cast.DEFAULT_CAST.anchor
    assert desk.name == "Alexis"
    assert desk.beat == canonical.beat and desk.persona == canonical.persona
    # The shared-mutable-state assertions that used to live here are gone with their subject.
    # They pinned that a resolved Desk did not share `keywords`/`domains` list objects with
    # DEFAULT_CAST, so mutating one could not corrupt the module constant. Both fields were
    # deleted 2026-08-22 as unread, and every field left on Desk is an immutable str, so there is
    # no shared mutable state left to protect. Re-add the guard with any future mutable field.
    assert not [f for f in fields(desk) if f.default_factory is not MISSING], (
        "a mutable default is back on Desk; restore the DEFAULT_CAST aliasing guard above"
    )


def test_resolve_role_raises_when_nobody_can_fill_it(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog", lambda: {})
    with pytest.raises(cast.RoleUnavailable):
        cast.resolve_role("anchor")


def test_resolve_role_raises_on_an_unknown_role():
    with pytest.raises(cast.RoleUnavailable):
        cast.resolve_role("sports")


def test_resolve_role_reports_no_substitution_when_it_is_the_same_character(monkeypatch):
    """Haley under two ids is one character, not a stand-in. Guards the post-GA table."""
    monkeypatch.setitem(cast.ROLE_VOICES, "anchor",
                        ["flux-haley-en", "flux-haley-en"])
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    desk, substituted_for = cast.resolve_role("anchor")
    assert desk.voice_id == "flux-haley-en"
    assert desk.name == "Haley"
    assert substituted_for is None


# --- recent_desk_roles: derive recency from what actually aired ----------------------------------


# --- desk_of_the_day: beat fit decides it, the recency nudge only breaks near ties -------------


# --- episode_cast: the three regulars for one show ----------------------------------------------


def test_episode_cast_seats_a_host_and_one_cohost(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(SMALL_CATALOG))
    ep_cast, _ = cast.episode_cast(before="2026-08-20", recent_voices=[])
    assert [d.role for d in ep_cast.desks] == ["cohost"]
    assert ep_cast.anchor.name == "Alexis"


def test_episode_cast_announces_nothing_on_the_production_catalog(monkeypatch):
    """Production casts Alexis (flux-alexis-en) and picks the co-host from the live catalog, so
    a production episode has nobody to announce as absent. The co-host never announces one at
    all: its candidate list is built FROM the catalog, so its first choice is always available."""
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    ep_cast, subs = cast.episode_cast(before="2026-08-20", recent_voices=[])
    assert ep_cast.anchor.name == "Alexis"
    assert ep_cast.anchor.voice_id == "flux-alexis-en"
    assert subs == {}


def test_episode_cast_reports_the_substituted_anchor(monkeypatch):
    """A catalog with no Alexis at all: Haley hosts and the show says so."""
    catalog = {k: v for k, v in config.VOICE_CATALOG.items() if "alexis" not in k}
    monkeypatch.setattr(config, "active_voice_catalog", lambda: catalog)
    ep_cast, subs = cast.episode_cast(before="2026-08-20", recent_voices=[])
    assert ep_cast.anchor.name == "Haley"
    assert subs == {"anchor": "Alexis"}


# --- one voice, one desk ------------------------------------------------------------------------


def test_episode_cast_never_casts_one_voice_in_two_seats(monkeypatch):
    """The original bug: each role resolved independently, so one voice could hold two seats and
    the host would conduct a follow-up with herself, in her own voice, under two labels. Nothing
    raised and voices.json looked fine.

    Now impossible by construction rather than by luck: the co-host pool EXCLUDES the host's
    voice by id before the rotation runs, and `exclude` is the backstop underneath that. Asserted
    over every date in a month, because a single date only exercises one rotation offset.
    """
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    for n in range(1, 29):
        ep_cast, _ = cast.episode_cast(before=f"2026-09-{n:02d}", recent_voices=[])
        ids = [ep_cast.anchor.voice_id] + [d.voice_id for d in ep_cast.desks]
        assert len(set(ids)) == len(ids), ids


def test_episode_cast_voices_are_all_distinct_on_both_hosts(monkeypatch):
    for catalog in (SMALL_CATALOG, config.VOICE_CATALOG):
        monkeypatch.setattr(config, "active_voice_catalog", lambda c=catalog: dict(c))
        for day in ("2026-08-18", "2026-08-19", "2026-08-20"):
            ep_cast, _ = cast.episode_cast(before=day, recent_voices=[])
            ids = [ep_cast.anchor.voice_id] + [d.voice_id for d in ep_cast.desks]
            assert len(set(ids)) == len(ids), ids


def test_resolve_role_skips_a_voice_already_cast(monkeypatch):
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(SMALL_CATALOG))
    first, _ = cast.resolve_role("anchor")
    assert first.voice_id == "flux-alexis-en"
    second, _ = cast.resolve_role("anchor", exclude={"flux-alexis-en"})
    assert second.voice_id == "flux-haley-en"


def test_resolve_role_does_not_announce_someone_who_is_on_air_elsewhere(monkeypatch):
    """"Alexis is out today" is a lie the listener can hear if Alexis has another desk."""
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(SMALL_CATALOG))
    desk, substituted_for = cast.resolve_role("anchor", exclude={"flux-alexis-en"})
    assert desk.name == "Haley"
    assert substituted_for is None


def test_episode_cast_voice_for_resolves_both_seats(monkeypatch):
    """assign_voices calls voice_for; an unresolved seat would silently get the host."""
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(SMALL_CATALOG))
    ep_cast, _ = cast.episode_cast(before="2026-08-20", recent_voices=[])
    assert ep_cast.voice_for("anchor") == ep_cast.anchor.voice_id
    assert ep_cast.voice_for("cohost") != ep_cast.anchor.voice_id


# --- recency is relative to the episode being generated, not to the clock ----------------------


def _episode(tmp_path, ep_id, desk):
    d = tmp_path / ep_id
    d.mkdir()
    (d / "script.json").write_text(json.dumps([
        {"order": 0, "role": "desk", "speaker_key": "X", "text": "t", "desk": desk}]))


def test_run_panel_passes_the_episode_date_to_the_cast(monkeypatch, tmp_path):
    """The wiring, not just the parameter: a backfill must get episode-relative recency."""
    from hn_radio import ingest, pipeline, sources, status
    from hn_radio.models import Story

    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(SMALL_CATALOG))
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    stories = [Story(id=1, title="LLM model inference", url="https://example.com", points=99,
                     author="a", num_comments=1, rank=1, kids=[])]
    monkeypatch.setattr(ingest, "fetch_front_page_for_date", lambda d, n: list(stories))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "render_panel", lambda segments, **kw: "episode")

    seen = {}
    real = cast.episode_cast
    monkeypatch.setattr(pipeline, "episode_cast_for",
                        lambda **kw: seen.update(kw) or real(**kw))

    import datetime
    pipeline.run_panel(edition="frontpage", episode_date=datetime.date(2026, 7, 1),
                       log=lambda *a, **k: None)
    assert seen["before"] == "2026-07-01"


def test_cast_page_never_seats_one_voice_at_two_desks(tmp_path, monkeypatch):
    """The page resolves five roles independently, so it needs the same exclusion episode_cast has.

    Strip every Alexis id from staging and both "anchor" and "ai" bottom out on
    flux-haley_studio-en. Reading active_cast() made that impossible (fixed, distinct literal
    ids); resolving by role reopened it, and the symptom is a public page advertising Haley at
    two desks at once, which is a cast no episode could ever air.

    The page DEGRADES rather than raising: an unfillable desk is dropped, not fatal, because
    this runs inside the site build.
    """
    catalog = {k: v for k, v in SMALL_CATALOG.items() if "alexis" not in k}
    monkeypatch.setattr(config, "active_voice_catalog", lambda: catalog)
    # `recent_cohost_voices` reads `config.EPISODES_DIR`, so without this the rotation is decided
    # by whatever episodes are on the developer's disk. Found for real: a machine holding a pulled
    # copy of the live archive seated a different co-host than a clean checkout.
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)

    data = _voices_json(tmp_path)
    ids = [s["voice"] for s in data["seating"]]
    assert len(set(ids)) == len(ids), ids
    seats = {s["role"]: s for s in data["seating"]}
    assert seats["anchor"]["voice"] == "flux-haley-en"
    # The co-host pool excludes the host by id, so the page cannot seat her twice even here.
    assert seats["cohost"]["voice"] != seats["anchor"]["voice"]
