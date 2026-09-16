"""The recast picker's rules, after it became two roles over the whole Flux catalog (2026-08-20).

Why this file is separate from tests/test_recast.py: that one characterizes the slot-level
primitives (`apply_mapping`, `rewrite_names`) and must keep passing for the legacy episodes
already on disk. This one pins the RULES the picker and the endpoint enforce, which are a
different kind of claim:

  - two roles, Showrunner and Guest host, mapped onto the `anchor` and `cohost` slots,
  - Flux voices only, because the demo exists to show the Flux range,
  - never the same voice on both roles, enforced server-side because a UI rule is bypassable,
  - retired voices are never offered, whatever the published catalog says.

The endpoint tests went through `POST /api/recast` on purpose. Validating in `hn_radio.recast` and
trusting the route to call it is exactly the shape of bug this file exists to catch, so the
assertions are made where a browser (or curl) actually lands.
"""

import json

import pytest
from fastapi.testclient import TestClient

from hn_radio import config, manifest, pipeline, recast
from hn_radio.cast import DEFAULT_CAST
from hn_radio.models import ScriptSegment

# The GA id, not the pre-GA `flux-alexis_studio-en` this used to be. `rewrite_role_names`
# resolves a voice id to a display name through `config.ALL_VOICES`, and the staging ids were
# deleted from the catalog when Flux went GA -- so with the old constant no name resolved, every
# rename was silently skipped, and `test_swapping_the_two_voices_does_not_collapse_both_names`
# passed its rename through unchanged while asserting it had happened.
ANCHOR = "flux-alexis-en"
COHOST = "flux-wade-en"
OTHER = "flux-cole-en"
AURA = "aura-2-thalia-en"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Fresh rate-limit state and an episode store that is not the real one."""
    from backend import limits
    limits.reset()
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    yield
    limits.reset()


def _client():
    from backend.app import app
    return TestClient(app)


def _post(mapping, episode_id="2026-08-20", ip="203.0.113.11"):
    return _client().post("/api/recast", json={"episode_id": episode_id, "mapping": mapping},
                          headers={"Fly-Client-IP": ip})


# --- what the picker may offer ------------------------------------------------------------------

def test_the_picker_has_exactly_two_roles_with_the_authors_labels():
    assert recast.ROLES == ("anchor", "cohost"), "the show is two-person; the picker must say so"
    assert recast.ROLE_LABELS == {"anchor": "Showrunner", "cohost": "Guest host"}


def test_every_offered_voice_is_flux():
    offered = recast.selectable_voices()
    assert offered, "the picker cannot offer an empty catalog"
    stray = [vid for vid in offered if config.voice_family(vid) != "flux"]
    assert not stray, f"non-Flux voices offered by the recast picker: {stray}"


def test_no_retired_voice_is_offered():
    offered = recast.selectable_voices()
    stray = [vid for vid in config.RETIRED_VOICES if vid in offered]
    assert not stray, f"retired voices offered by the recast picker: {stray}"


# --- what a mapping must satisfy ---------------------------------------------------------------
#
# THESE USED TO GO THROUGH `POST /api/recast`, and the file header explained why: the endpoint
# held the Deepgram key, anyone could curl it, and every rule the picker showed had to be true on
# the server as well. That endpoint was deleted on 2026-09-16 with the rest of the recast feature,
# so there is no HTTP surface left to protect and the seven tests that posted to it are gone.
#
# The RULES are not gone, because `recast.validate_mapping` is not: `scripts/recast_archive.py`
# and the CLI still call it, and it is still the single definition. So each rule is asserted
# against the function directly. What is genuinely no longer covered, and is worth naming rather
# than pretending otherwise: that a bad mapping produces a 400 and not a 500, and that the
# endpoint validated independently of the page. Both were properties of a route that does not
# exist.

def _refuses(mapping, match):
    with pytest.raises(ValueError, match=match):
        recast.validate_mapping(mapping)


def test_the_same_voice_on_both_roles_is_refused():
    """One voice in two seats puts the host in conversation with herself, in her own voice."""
    _refuses({"anchor": ANCHOR, "cohost": ANCHOR}, "(?i)same|both|twice|distinct")


def test_an_aura_voice_is_refused():
    """Recast exists to show the Flux range. An Aura id renders, and is not what this offers."""
    _refuses({"anchor": "aura-2-thalia-en"}, "(?i)flux")


def test_a_retired_voice_is_refused_as_retired():
    """Marcus and Priya are in the published docs and were pulled by ear. The refusal says which
    of the two reasons applies, because "no such voice" would be wrong and confusing."""
    _refuses({"anchor": "flux-priya-en"}, "(?i)retired")


def test_a_slot_that_is_not_one_of_the_two_roles_is_refused():
    """The show has an anchor and a co-host. A `maker` key is a caller describing a show that
    stopped existing on 2026-08-20."""
    _refuses({"maker": ANCHOR}, "(?i)role|anchor|cohost")


def test_an_empty_mapping_is_refused_rather_than_rendering_a_copy():
    """An empty mapping re-renders the episode unchanged: a full Flux bill for a byte-identical
    show. That is a caller mistake, not a no-op."""
    _refuses({}, "(?i)empty|at least|no ")


# --- what the published catalog offers ---------------------------------------------------------

def test_voices_json_offers_flux_only(tmp_path):
    """Filtered at GENERATION, so a stale published file cannot reintroduce the Aura voices."""
    manifest.build_voices_json(tmp_path)
    data = json.loads((tmp_path / "voices.json").read_text())
    families = {v["family"] for v in data["voices"]}
    assert families == {"flux"}, f"voices.json offers non-Flux families: {families - {'flux'}}"
    assert [f["id"] for f in data["families"]] == ["flux"]
    # `presets` used to be checked for an "aura" key here, on the grounds that an Aura preset is
    # a picker route back to the Aura voices. The key is gone with the picker (2026-09-16), so
    # there is no route to close. The filter that matters is still the one above: `voices` is
    # built Flux-only AT GENERATION, so a stale published file cannot reintroduce them either.
    assert "presets" not in data, "the recast picker's preset key should not have come back"


def test_voices_json_offers_no_retired_voice(tmp_path):
    manifest.build_voices_json(tmp_path)
    data = json.loads((tmp_path / "voices.json").read_text())
    ids = {v["id"] for v in data["voices"]}
    stray = [vid for vid in config.RETIRED_VOICES if vid in ids]
    assert not stray, f"voices.json publishes retired voices: {stray}"


# --- coverage: what the two roles actually reach ------------------------------------------------

def _two_person_script():
    """A current-format script: host, co-host, and comments PINNED to a regular's voice.

    That pinning is the whole reason comment lines need their own rule. `_slot_of` calls them
    "guest" because they carry no desk, but the voice on them belongs to whoever read the quote.
    """
    return [
        ScriptSegment(order=0, role="anchor", speaker_key="Alexis", desk="anchor",
                      text="Hi, this is Alexis. Today I have Wade with me.", voice_id=ANCHOR),
        ScriptSegment(order=1, role="desk", speaker_key="Wade", desk="cohost",
                      text="Good to be here.", voice_id=COHOST),
        ScriptSegment(order=2, role="commenter", speaker_key="dang", desk=None,
                      text="A quote the co-host reads.", voice_id=COHOST),
        ScriptSegment(order=3, role="commenter", speaker_key="pg", desk=None,
                      text="A quote the host reads.", voice_id=ANCHOR),
        ScriptSegment(order=4, role="anchor", speaker_key="Alexis", desk="anchor",
                      text="From me and Wade, we'll talk to you tomorrow.", voice_id=ANCHOR),
    ]


def test_a_two_person_recast_leaves_no_line_on_an_old_voice():
    """Both roles remapped must leave ZERO segments on a pre-recast voice.

    The bug this pins: comment lines sit in the `guest` slot, so a {anchor, cohost} mapping used
    to skip them and a "recast" episode came out with four voices in it.
    """
    segs = _two_person_script()
    recast.apply_roles(segs, {"anchor": OTHER, "cohost": "flux-heather-en"})
    left = [s.voice_id for s in segs if s.voice_id in (ANCHOR, COHOST)]
    assert not left, f"segments still on a pre-recast voice: {left}"
    assert [s.voice_id for s in segs] == [
        OTHER, "flux-heather-en", "flux-heather-en", OTHER, OTHER]


def test_a_comment_line_follows_the_role_that_read_it():
    segs = _two_person_script()
    recast.apply_roles(segs, {"cohost": OTHER})
    assert segs[2].voice_id == OTHER, "the co-host's quote must follow the co-host"
    assert segs[3].voice_id == ANCHOR, "the host's quote must not move when only the co-host does"


def _legacy_script():
    """A pre-two-person script: themed desks and a separate guest voice for the comments."""
    return [
        ScriptSegment(order=0, role="anchor", speaker_key="Haley", desk="anchor",
                      text="Over to Meena at the AI desk.", voice_id="flux-haley-en"),
        ScriptSegment(order=1, role="desk", speaker_key="Meena", desk="ai",
                      text="An AI take.", voice_id="flux-meena-en"),
        ScriptSegment(order=2, role="commenter", speaker_key="dang", desk=None,
                      text="A quote.", voice_id="flux-drew-en"),
    ]


def test_the_guest_host_absorbs_every_non_host_seat_in_a_legacy_episode():
    """Every episode on disk predates the two-person format, so this is not an edge case.

    Matching the Guest host to a `cohost` slot by name would leave its picker dead on the whole
    archive. It takes over the themed desks and the quoted comments instead, and `role_coverage`
    is what the page reads to SAY so before anyone clicks Recast.
    """
    cover = recast.role_coverage(_legacy_script())
    assert cover["anchor"] == [("anchor", "flux-haley-en")]
    assert cover["cohost"] == [("ai", "flux-meena-en"), ("guest", "flux-drew-en")]


def test_a_legacy_recast_leaves_no_line_on_an_old_voice_either():
    segs = _legacy_script()
    recast.apply_roles(segs, {"anchor": OTHER, "cohost": "flux-heather-en"})
    assert [s.voice_id for s in segs] == [OTHER, "flux-heather-en", "flux-heather-en"]


def test_a_legacy_recast_renames_every_desk_it_absorbed():
    """Keying the rename on the SLOT would have left "Over to Meena at the AI desk" in a script
    that Heather now reads, which is the exact incoherence rewrite_names exists to prevent."""
    segs = _legacy_script()
    mapping = {"cohost": "flux-heather-en"}
    absorbed = recast.apply_roles(segs, mapping)
    assert absorbed["cohost"] == ["flux-meena-en", "flux-drew-en"]
    recast.rewrite_role_names(segs, mapping, absorbed)
    assert segs[0].text == "Over to Heather at the AI desk."
    assert segs[1].speaker_key == "Heather"
    assert segs[2].text == "A quote.", "a real quote is never rewritten"


def test_a_current_format_episode_needs_no_takeover_notice():
    cover = recast.role_coverage(_two_person_script())
    assert cover["anchor"] == [("anchor", ANCHOR), ("guest", ANCHOR)]
    assert cover["cohost"] == [("cohost", COHOST), ("guest", COHOST)]


def test_swapping_the_two_voices_does_not_collapse_both_names():
    """"Make the co-host the host" is an obvious thing to try on a two-role picker. Substituting
    the renames one after another turned Alexis->Cole then Cole->Alexis into one name for both."""
    segs = _two_person_script()
    mapping = {"anchor": COHOST, "cohost": ANCHOR}
    absorbed = recast.apply_roles(segs, mapping)
    recast.rewrite_role_names(segs, mapping, absorbed)
    assert segs[0].text == "Hi, this is Wade. Today I have Alexis with me."


# --- the fixed intro and outro -----------------------------------------------------------------

def _script_with_the_real_intro_and_outro():
    """The pipeline's own fixed intro and outro, wrapped around one co-host line.

    Those two lines are the only copy in the show the WRITERS do not own, and both say names out
    loud: the host introduces herself and today's co-host, and the sign-off names the co-host
    again. They are `desk="anchor"` segments, so a rename driven by the slot would have caught the
    host's name and left the co-host's.
    """
    from datetime import date
    segs = (pipeline._intro_segments(DEFAULT_CAST, date(2026, 8, 20))
            + [ScriptSegment(order=1, role="desk", speaker_key=DEFAULT_CAST.cohost.name,
                             desk="cohost", text="Good to be here.",
                             voice_id=DEFAULT_CAST.cohost.voice_id)]
            + pipeline._outro_segments(DEFAULT_CAST))
    for seg in segs:
        if seg.voice_id is None:
            seg.voice_id = DEFAULT_CAST.anchor.voice_id
    return segs


def test_the_fixed_intro_and_outro_are_renamed_by_a_recast():
    """A recast that missed them would introduce Wade and have somebody else answer."""
    segs = _script_with_the_real_intro_and_outro()
    assert "Alexis" in segs[0].text and "Wade" in segs[0].text, "fixture no longer names both"
    assert "Wade" in segs[-1].text, "fixture no longer names the co-host in the sign-off"

    mapping = {"anchor": OTHER, "cohost": "flux-heather-en"}
    absorbed = recast.apply_roles(segs, mapping)
    recast.rewrite_role_names(segs, mapping, absorbed)

    joined = " ".join(s.text for s in segs)
    assert "Alexis" not in joined and "Wade" not in joined, f"old names survived: {joined}"
    assert "Cole" in joined and "Heather" in joined, joined


def test_recasting_only_the_showrunner_leaves_the_co_hosts_name_alone():
    """The intro names both people. Renaming one must not touch the other."""
    segs = _script_with_the_real_intro_and_outro()
    mapping = {"anchor": OTHER}
    recast.rewrite_role_names(segs, mapping, recast.apply_roles(segs, mapping))
    joined = " ".join(s.text for s in segs)
    assert "Cole" in joined and "Wade" in joined and "Alexis" not in joined, joined


def test_a_takeover_is_a_seat_the_two_person_show_does_not_have():
    """The host reading some of the quotes is the format working, not a takeover.

    `role_coverage` counts slots, so the current format gives the Showrunner two of them (`anchor`
    plus `guest`, both on her own voice). Reporting that as a takeover put a "this episode predates
    the two-person show" notice on an episode in the current format.
    """
    assert recast.role_takeovers(_two_person_script()) == {}
    legacy = recast.role_takeovers(_legacy_script())
    assert list(legacy) == ["cohost"]
    assert legacy["cohost"] == [("ai", "flux-meena-en"), ("guest", "flux-drew-en")]
