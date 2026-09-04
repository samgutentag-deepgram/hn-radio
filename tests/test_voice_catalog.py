"""Pin the Flux voice catalog to the published catalog.

Source of truth: https://developers.deepgram.com/docs/flux-tts/voices

This exists because the catalog was once expanded to ~33 voices by probing which flux-* ids
happened to render on this project's key. They did render, but most are not in the published
catalog, so a public demo would have been showing off voices no customer can rely on. Rendering
successfully is not the same as being shipped.

The mirror of that rule bit when Flux TTS went GA and the published catalog went
from twelve voices to thirty-six. Renee was dropped, and this table pinned her, so three tests
agreed with the code and all four were wrong about the world. She is gone from both now. The
other twenty-five GA voices are deliberately NOT added here yet: this table is transcribed by
hand and the migration regenerates it from voices.mdx, so adding them by hand would recreate
exactly the guesswork this file exists to prevent. Eleven of thirty-six, on purpose.
"""

from __future__ import annotations

from hn_radio import config
from hn_radio.cast import DEFAULT_CAST

# Transcribed from the docs table: model string -> (display name, accent, gender, age band).
DOCUMENTED = {
    "flux-alexis-en":   ("Alexis", "American", "F", "Adult"),
    "flux-bree-en":     ("Bree", "American", "F", "Mature"),
    "flux-brittany-en": ("Brittany", "American", "F", "Mature"),
    "flux-brooke-en":   ("Brooke", "American", "F", "Young"),
    "flux-bruce-en":    ("Bruce", "American", "M", "Adult"),
    "flux-cliff-en":    ("Cliff", "American", "M", "Mature"),
    "flux-cole-en":     ("Cole", "American", "M", "Young"),
    "flux-colin-en":    ("Colin", "British", "M", "Adult"),
    "flux-conor-en":    ("Conor", "British", "M", "Mature"),
    "flux-donovan-en":  ("Donovan", "American", "M", "Adult"),
    "flux-drew-en":     ("Drew", "American", "M", "Adult"),
    "flux-elise-en":    ("Elise", "American", "F", "Adult"),
    "flux-gemma-en":    ("Gemma", "British", "F", "Young"),
    "flux-haley-en":    ("Haley", "American", "F", "Young Adult"),
    "flux-hannah-en":   ("Hannah", "American", "F", "Young"),
    "flux-heather-en":  ("Heather", "American", "F", "Young"),
    "flux-jack-en":     ("Jack", "British", "M", "Adult"),
    "flux-kai-en":      ("Kai", "Singaporean", "M", "Young Adult"),
    "flux-kelsey-en":   ("Kelsey", "American", "F", "Young Adult"),
    "flux-kit-en":      ("Kit", "British", "M", "Young Adult"),
    "flux-maeve-en":    ("Maeve", "Irish", "F", "Adult"),
    "flux-marcelo-en":  ("Marcelo", "Filipino", "M", "Young Adult"),
    "flux-marcus-en":   ("Marcus", "American", "M", "Adult"),
    "flux-meena-en":    ("Meena", "Indian", "F", "Adult"),
    "flux-meghan-en":   ("Meghan", "American", "F", "Adult"),
    "flux-miles-en":    ("Miles", "American", "M", "Adult"),
    "flux-naveen-en":   ("Naveen", "Indian", "M", "Adult"),
    "flux-paige-en":    ("Paige", "American", "F", "Young Adult"),
    "flux-priya-en":    ("Priya", "Indian", "F", "Adult"),
    "flux-rufus-en":    ("Rufus", "British", "M", "Adult"),
    "flux-sean-en":     ("Sean", "British", "M", "Mature"),
    "flux-sharon-en":   ("Sharon", "Australian", "F", "Young"),
    "flux-sienna-en":   ("Sienna", "American", "F", "Young Adult"),
    "flux-tanner-en":   ("Tanner", "British", "M", "Adult"),
    "flux-wade-en":     ("Wade", "American", "M", "Adult"),
    "flux-wes-en":      ("Wes", "American", "M", "Adult"),
}


def test_catalog_is_exactly_the_documented_catalog_minus_the_retired():
    shipped = set(DOCUMENTED) - set(config.RETIRED_VOICES)
    extra = set(config.VOICE_CATALOG) - shipped
    missing = shipped - set(config.VOICE_CATALOG)
    assert not extra, (
        f"voices not in the published catalog: {sorted(extra)}. A flux-* id that renders on our "
        "key is not necessarily shipped; only list what the docs list."
    )
    assert not missing, f"documented voices absent from the catalog: {sorted(missing)}"


def test_every_voice_keeps_its_documented_name_accent_gender_and_age():
    for vid, (name, accent, gender, age) in DOCUMENTED.items():
        if vid in config.RETIRED_VOICES:
            continue  # transcribed from the docs, deliberately not shipped
        got_name, note = config.VOICE_CATALOG[vid]
        assert got_name == name, f"{vid} should be named {name}, got {got_name}"
        for field in (accent, gender, age):
            assert field in note, f"{vid} note {note!r} is missing {field!r} from the docs"


def test_model_strings_follow_the_documented_format():
    """flux-{voice}-{language}, per the docs. A typo here is a 400 at render time."""
    for vid in config.VOICE_CATALOG:
        assert vid.startswith("flux-") and vid.endswith("-en"), vid
        assert config.voice_family(vid) == "flux"


def test_suggested_expressivity_only_covers_catalog_voices():
    """The map is trimmed to the catalog; a stale entry means a voice was removed and this was not."""
    stray = set(config.FLUX_SUGGESTED_EXPRESSIVITY) - set(config.VOICE_CATALOG)
    assert not stray, f"suggested-expressivity entries for non-catalog voices: {sorted(stray)}"
    assert all(-5.0 <= v <= 5.0 for v in config.FLUX_SUGGESTED_EXPRESSIVITY.values()), \
        "expressivity is documented as a -5..5 range"


def test_desk_and_guest_voices_all_come_from_the_catalog():
    """A desk pointed at a voice we do not ship would 400 on every episode that routes to it."""
    referenced = {config.HOST_VOICE, *config.COMMENTER_VOICES, *config.GUEST_VOICES}
    unknown = referenced - set(config.VOICE_CATALOG)
    assert not unknown, f"referenced but not in the catalog: {sorted(unknown)}"


# Voices the published catalog carried before Flux TTS GA (2026-08-12) and does not carry after.
# Add to this set, do not edit it: it is the record of what the docs took away.
REMOVED_AT_GA = {"flux-renee-en"}

# Retired by ear. Both are STILL in the published docs, so unlike Renee they would come back on
# the next catalog regeneration if nothing filtered them out. config.RETIRED_VOICES is that filter
# and this pins it.
#   flux-marcus-en  2026-08-19
#   flux-priya-en   2026-08-20
RETIRED_BY_DECISION = {"flux-marcus-en", "flux-priya-en"}


def test_the_retired_set_matches_what_the_config_declares():
    """config.RETIRED_VOICES is the filter; this is the list it must hold.

    Marcus is the reason this is separate from REMOVED_AT_GA. Renee is gone from the published
    docs, so a catalog regenerated from voices.mdx simply will not contain her. Marcus is still
    IN those docs, so a regeneration puts him straight back unless something removes him after
    the fact. That is what RETIRED_VOICES does, and a filter nothing tests is a filter that gets
    deleted as dead code.
    """
    assert set(config.RETIRED_VOICES) == REMOVED_AT_GA | RETIRED_BY_DECISION


def test_a_regenerated_catalog_still_drops_the_retired_voices():
    """Belt and braces: the shipped catalog, whatever produced it, contains neither."""
    for vid in config.RETIRED_VOICES:
        assert vid not in config.VOICE_CATALOG, f"{vid} is retired but present in the catalog"


def test_every_cast_role_can_be_filled_from_the_production_catalog(monkeypatch):
    """Retiring a voice that holds a seat is how you crash the nightly show.

    `episode_cast` does not catch RoleUnavailable, so a seat whose every candidate is missing
    takes the whole episode down. Retiring Priya would have done exactly that under
    the old desk model, where she was the AI desk's only real candidate and any AI-heavy day
    picked it. Under the two-person show the co-host draws from the whole catalog, which is most
    of why a by-ear retirement is now cheap.

    Both seats are checked, and the co-host has to be checked through `cohost_candidates` rather
    than through ROLE_VOICES: it deliberately has no fixed preference list.
    """
    from hn_radio import cast as cast_mod

    for role in cast_mod.ROLE_VOICES:
        desk, _ = cast_mod.resolve_role(role)
        assert desk.voice_id in config.VOICE_CATALOG
        assert desk.voice_id not in config.RETIRED_VOICES

    host = cast_mod.resolve_role("anchor")[0].voice_id
    pool = cast_mod.cohost_candidates(recent_voices=[], host_voice=host)
    assert pool, "no voice can take the second chair, so every episode would raise"
    assert host not in pool
    assert not set(pool) & set(config.RETIRED_VOICES)
    assert set(pool) <= set(config.VOICE_CATALOG)
    # The reason the window can be as wide as it is. If this floor ever fails, the rotation is
    # falling back on recently-used voices most nights and COHOST_RECENCY_WINDOW is a fiction.
    assert len(pool) > cast_mod.COHOST_RECENCY_WINDOW, (
        f"{len(pool)} eligible co-hosts against a window of "
        f"{cast_mod.COHOST_RECENCY_WINDOW}; the no-repeat rule cannot hold"
    )


def test_voices_removed_at_ga_are_not_referenced_anywhere():
    """A removed id in a production pool fails some episodes and not others.

    Renee is the reason this test exists. She was a plain id in COMMENTER_VOICES, GUEST_VOICES
    and FLUX_SUGGESTED_EXPRESSIVITY, and the catalog simply stopped carrying her at GA, so
    nothing guarded her. `guest_voice_for` hashes usernames into that pool: the show would have
    rendered fine until some commenter's name hashed onto her seat.
    """
    surfaces = {
        "HOST_VOICE": {config.HOST_VOICE},
        "COMMENTER_VOICES": set(config.COMMENTER_VOICES),
        "GUEST_VOICES": set(config.GUEST_VOICES),
        "VOICE_CATALOG": set(config.VOICE_CATALOG),
        "FLUX_SUGGESTED_EXPRESSIVITY": set(config.FLUX_SUGGESTED_EXPRESSIVITY),
        "DEFAULT_CAST": {DEFAULT_CAST.anchor.voice_id,
                         *(d.voice_id for d in DEFAULT_CAST.desks)},
    }
    for surface, ids in sorted(surfaces.items()):
        dead = ids & REMOVED_AT_GA
        assert not dead, f"{surface} references voices removed at GA: {sorted(dead)}"


def test_the_two_regulars_can_still_cast_a_full_episodes_comments_distinctly():
    """`N_COMMENTS` performed commenters must not all land on one voice, or comment theater
    reads as one person arguing with themselves.

    The show performs comments with the host and the co-host alternating, so the requirement
    that used to fall on the guest pool's SIZE now falls on there being two regulars. Two
    voices covers N_COMMENTS = 2 exactly; a larger N_COMMENTS would start repeating a voice
    within the segment, which is worth knowing before anyone raises it.
    """
    assert config.N_COMMENTS <= 2, (
        f"N_COMMENTS is {config.N_COMMENTS} but only two regulars perform comments, so a voice "
        "would read two consecutive quotes"
    )




def test_the_guest_pool_no_longer_has_to_avoid_the_cast():
    """DELIBERATELY INVERTED, and the inversion is the point.

    This used to assert that no GUEST_VOICES entry could also hold a desk. The reason was real:
    `taken_guests` in writers.py started empty rather than seeded with the episode's cast, so a
    guest voice that also held a desk could perform a comment in the episode it anchored -- the
    same "one voice, two characters" failure `episode_cast` raises over, except silent.

    That cannot happen any more, because the show no longer casts a guest voice at all: the host
    and the co-host read the comments themselves. So the OVERLAP IS NOW EXPECTED -- the co-host
    is drawn from the whole catalog, which is very nearly the guest pool -- and asserting against
    it would fail for a reason that no longer describes a bug.

    What the pool is still FOR, and why it is not deleted: `manifest.build_voices_json` publishes
    it as the recast preset's "guest" voice, `scripts/voice_preview.py` renders previews from it,
    and a build-your-own edition can still put a distinct voice on comment lines. None of those
    can seat one voice twice in one daily episode.
    """
    from hn_radio import cast as cast_mod

    assert set(config.GUEST_VOICES) <= set(config.VOICE_CATALOG)
    host = cast_mod.resolve_role("anchor")[0].voice_id
    # The one separation that still matters: the HOST is not in the guest pool, so a recast that
    # maps the guest slot cannot accidentally reproduce her voice on a quoted comment.
    assert host not in config.GUEST_VOICES


def test_every_guest_voice_is_real():
    """A dead id here is a hard failure, unlike a dead id in ROLE_VOICES.

    `resolve_role` skips a voice missing from the active catalog and announces a substitution.
    `guest_voice_for` does no such check: it hashes into GUEST_VOICES and returns whatever it
    lands on, so an id that no longer renders takes the episode down at 3am.
    """
    unknown = set(config.GUEST_VOICES) - set(config.VOICE_CATALOG)
    assert not unknown, f"guest voices absent from the catalog: {sorted(unknown)}"
    assert not set(config.GUEST_VOICES) & set(config.RETIRED_VOICES)


def test_the_guest_pool_is_still_wide_enough_to_be_worth_publishing():
    """The pool is no longer on the show path, but it is still published in voices.json and
    previewed by scripts/voice_preview.py, so a pool that shrank to a handful would make the
    recast picker's guest option pointless. Floor kept, reason changed."""
    assert len(config.GUEST_VOICES) >= 8, (
        f"guest pool is {len(config.GUEST_VOICES)}; too narrow to offer as a recast choice"
    )


# --- PUBLISHED_VOICES: the 36 the cast page grids ---------------------------------------------
#
# Added 2026-08-21 with the all-voices cast page. The literal counts here are deliberate: this set
# has to match the official orb SVGs the team supplied, one file per voice, and those files are not
# in the repo (vendor brand assets, and this repo is headed for a public Show HN post). So the
# thing that would catch a drift is a count and a composition rule, pinned here, rather than a
# directory listing that only exists on one laptop.


def test_published_voices_is_the_catalog_plus_the_two_retired_by_ear():
    """The 36 = the 34 this show can seat + Marcus and Priya.

    Renee must NOT be in it. She was removed from the published docs at GA and has no official orb,
    so a page that offered her would be advertising a voice the catalog does not carry. That is the
    exact failure the ledger records for the production commenter pool, and this is the guard.
    """
    assert set(config.PUBLISHED_VOICES) == set(config.VOICE_CATALOG) | RETIRED_BY_DECISION
    assert set(config.PUBLISHED_VOICES) & REMOVED_AT_GA == set()
    assert len(config.PUBLISHED_VOICES) == 36, (
        f"the published catalog is {len(config.PUBLISHED_VOICES)}, not 36; if the docs really "
        f"changed, update this number and re-export the orb SVGs to match"
    )


def test_the_config_owns_the_retirement_split_this_module_pins():
    """The two halves of RETIRED_VOICES moved into config.

    They used to live only here. PUBLISHED_VOICES needs the distinction at runtime, and a
    definition the code cannot reach is a definition that drifts, so config is now the owner and
    this asserts the test module and the config still agree.
    """
    assert set(config.REMOVED_AT_GA) == REMOVED_AT_GA
    assert set(config.RETIRED_BY_DECISION) == RETIRED_BY_DECISION


def test_every_published_voice_carries_a_name_and_a_note():
    """The cast page prints both on every card, and prints them INSTEAD of relying on colour.

    An entry with a blank name renders a card identified by hue alone, which is the one thing
    brand.css block 1 forbids outright.
    """
    for vid, (name, note) in config.PUBLISHED_VOICES.items():
        assert name and name.strip(), f"{vid} has no display name"
        assert note and note.strip(), f"{vid} has no character note"


def test_is_retired_agrees_with_the_retired_set():
    for vid in config.PUBLISHED_VOICES:
        assert config.is_retired(vid) == (vid in config.RETIRED_VOICES)


def test_the_build_picker_can_only_offer_voices_the_build_endpoint_accepts(tmp_path):
    """`voices.json.buildable` feeds web/build.html's selects; `POST /api/build` validates against
    config.ALL_VOICES. An option the endpoint refuses is a build that fails after the reader has
    already chosen a cast, so the published list must be a strict subset, and it must not offer a
    retired voice. Read off the generated file rather than the constant, because the file is what
    the browser actually gets.
    """
    import json  # noqa: PLC0415

    from hn_radio import manifest  # noqa: PLC0415

    manifest.build_voices_json(tmp_path)
    doc = json.loads((tmp_path / "voices.json").read_text())

    buildable = set(doc["buildable"])
    assert buildable, "the picker would render an empty select"
    assert buildable <= set(config.ALL_VOICES), (
        f"published but not accepted by /api/build: {sorted(buildable - set(config.ALL_VOICES))}"
    )
    assert buildable & set(config.RETIRED_VOICES) == set()

    # And the catalog the cast page grids: 36, every entry flagged, host-independent.
    catalog = {v["id"]: v for v in doc["catalog"]}
    assert set(catalog) == set(config.PUBLISHED_VOICES)
    assert {v["id"] for v in doc["catalog"] if v["retired"]} == RETIRED_BY_DECISION
