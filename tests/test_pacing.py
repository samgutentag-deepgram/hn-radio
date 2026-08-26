"""Pacing: per-boundary gaps, edge normalization, and the offsets-match-audio invariant."""

from __future__ import annotations

import array

import pytest

from hn_radio import config, pacing, stitch
from hn_radio.models import ScriptSegment


def seg(order, role="desk", speaker="Marcus", desk="maker", hn=101):
    return ScriptSegment(order=order, role=role, speaker_key=speaker, text="words",
                         desk=desk, source_hn_id=hn)


def tone(seconds, amplitude=8000):
    """`seconds` of loud, obviously-not-silence PCM."""
    n = int(config.SAMPLE_RATE * seconds)
    return array.array("h", [amplitude if i % 2 else -amplitude for i in range(n)]).tobytes()


def quiet(seconds):
    return b"\x00" * (int(config.SAMPLE_RATE * seconds) * config.SAMPLE_WIDTH)


# --- gap_list: the single reader of a gap spec -------------------------------------------------

def test_gap_list_expands_a_scalar_to_one_value_per_boundary():
    assert stitch.gap_list(4, 0.5) == [0.5, 0.5, 0.5]


def test_gap_list_passes_a_matching_sequence_through():
    assert stitch.gap_list(3, [0.1, 0.9]) == [0.1, 0.9]


def test_gap_list_rejects_a_wrong_length_sequence():
    # Silently padding or truncating here would desync the audio from the seek buttons.
    with pytest.raises(ValueError):
        stitch.gap_list(4, [0.1, 0.2])


def test_gap_list_handles_a_single_segment():
    assert stitch.gap_list(1, 0.5) == []


# --- the invariant: offsets must describe the audio that was written ---------------------------

def _duration(pcm):
    return len(pcm) / config.SAMPLE_WIDTH / config.CHANNELS / config.SAMPLE_RATE


@pytest.mark.parametrize("gaps", [0.45, [0.1, 0.85, 0.2], [0.0, 0.0, 0.0]])
def test_start_times_match_the_concatenated_audio(gaps):
    pcm = [tone(0.5), tone(0.3), tone(0.7), tone(0.2)]
    starts = stitch.segment_start_times(pcm, gaps)
    joined = stitch.concat_pcm(pcm, gaps)

    # Each reported start must be where that segment actually begins in the joined buffer.
    expanded = stitch.gap_list(len(pcm), gaps)
    offset = 0
    for i, p in enumerate(pcm):
        assert starts[i] == pytest.approx(offset / (config.SAMPLE_RATE * config.SAMPLE_WIDTH),
                                          abs=0.002)
        assert joined[offset:offset + len(p)] == p
        offset += len(p)
        if i < len(pcm) - 1:
            offset += len(stitch.silence(expanded[i]))
    assert len(joined) == offset


def test_variable_gaps_change_offsets_but_not_segment_audio(tmp_path):
    pcm = [tone(0.4), tone(0.4), tone(0.4)]
    flat = stitch.segment_start_times(pcm, 0.45)
    varied = stitch.segment_start_times(pcm, [0.1, 1.0])
    assert flat[0] == varied[0] == 0.0
    assert varied[1] < flat[1]      # tighter first boundary pulls segment 2 earlier
    assert varied[2] > flat[2]      # the long second boundary pushes segment 3 later

    d = stitch.stitch(pcm, tmp_path / "e.wav", [0.1, 1.0])
    assert d == pytest.approx(0.4 * 3 + 1.1, abs=0.01)


def test_stitch_and_rebuild_agree_on_a_variable_gap_plan(tmp_path):
    seg_dir = tmp_path / "segments"
    seg_dir.mkdir()
    pcm = [tone(0.3), tone(0.3), tone(0.3)]
    for i, p in enumerate(pcm):
        (seg_dir / f"{i}.pcm").write_bytes(p)
    direct = stitch.stitch(pcm, tmp_path / "a.wav", [0.2, 0.8])
    rebuilt = stitch.stitch(stitch.load_cached_segments(seg_dir, [0, 1, 2]),
                            tmp_path / "b.wav", [0.2, 0.8])
    assert direct == rebuilt
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()


# --- boundary classification -------------------------------------------------------------------

def test_two_speakers_on_one_story_is_an_exchange():
    a = seg(1, speaker="Haley", desk="anchor")
    b = seg(2, speaker="Marcus")
    assert pacing.boundary_kind(a, b, {101}) == "exchange"


def test_one_speaker_continuing_is_not_a_turn():
    assert pacing.boundary_kind(seg(1), seg(2), {101}) == "same_speaker"


def test_a_different_story_id_is_a_story_change():
    assert pacing.boundary_kind(seg(1, hn=101), seg(2, hn=202), {101, 202}) == "story_change"


def test_a_comment_id_is_not_a_story_change():
    # Comment ids differ from the story id but stay inside the same segment of the show.
    a = seg(1, speaker="Cole", desk="drama", hn=101)
    b = seg(2, role="commenter", speaker="dang", desk=None, hn=9999)
    assert pacing.boundary_kind(a, b, {101}) == "into_comment"


def test_landing_out_of_a_comment_is_its_own_boundary():
    a = seg(1, role="commenter", speaker="dang", desk=None, hn=9999)
    b = seg(2, speaker="Cole", desk="drama", hn=101)
    assert pacing.boundary_kind(a, b, {101}) == "out_of_comment"


# --- gap_plan ----------------------------------------------------------------------------------

def _episode():
    return [
        seg(0, role="anchor", speaker="Haley", desk="anchor", hn=None),   # fixed intro
        seg(1, role="anchor", speaker="Haley", desk="anchor", hn=101),
        seg(2, speaker="Marcus", hn=101),
        seg(3, speaker="Marcus", hn=101),
        seg(4, role="anchor", speaker="Haley", desk="anchor", hn=202),
        seg(5, speaker="Priya", desk="ai", hn=202),
        seg(6, role="commenter", speaker="dang", desk=None, hn=9999),
        seg(7, role="anchor", speaker="Haley", desk="anchor", hn=None),   # fixed outro
    ]


def test_gap_plan_returns_one_gap_per_boundary():
    plan = pacing.gap_plan(_episode(), pacing.CONVERSATIONAL, {101, 202})
    assert len(plan) == len(_episode()) - 1


def test_gap_plan_is_accepted_by_stitch_unchanged():
    segs = _episode()
    plan = pacing.gap_plan(segs, pacing.CONVERSATIONAL, {101, 202})
    assert stitch.gap_list(len(segs), plan) == plan


def test_gap_plan_brackets_the_show_with_the_intro_and_outro_beat():
    p = pacing.CONVERSATIONAL
    plan = pacing.gap_plan(_episode(), p, {101, 202})
    assert plan[0] == p.show_boundary
    assert plan[-1] == p.show_boundary


def test_gap_plan_gives_a_story_change_more_air_than_a_turn():
    p = pacing.CONVERSATIONAL
    plan = pacing.gap_plan(_episode(), p, {101, 202})
    assert plan[1] == p.exchange        # Haley -> Marcus, same story
    assert plan[2] == p.same_speaker    # Marcus -> Marcus
    assert plan[3] == p.story_change    # Marcus -> Haley, new story
    assert plan[3] > plan[1]


def test_uniform_policy_reproduces_the_current_fixed_gap():
    plan = pacing.gap_plan(_episode(), pacing.UNIFORM, {101, 202})
    assert set(plan) == {config.GAP_SECONDS}
    assert pacing.UNIFORM.normalize_edges is False


def test_gap_plan_on_a_script_too_short_to_have_boundaries():
    assert pacing.gap_plan([seg(0)], pacing.TIGHT) == []
    assert pacing.gap_plan([], pacing.TIGHT) == []


# --- edge normalization ------------------------------------------------------------------------

def test_normalize_edges_replaces_renderer_silence_with_a_known_amount():
    body = tone(0.5)
    raw = quiet(0.31) + body + quiet(0.27)
    out = pacing.normalize_edges(raw, edge_seconds=0.06)
    expected_edge = len(quiet(0.06))
    assert len(out) == expected_edge * 2 + len(body)
    assert out[expected_edge:expected_edge + len(body)] == body


def test_normalize_edges_pads_a_segment_that_starts_hot():
    out = pacing.normalize_edges(tone(0.4), edge_seconds=0.06)
    assert out.startswith(quiet(0.06))
    assert out.endswith(quiet(0.06))


def test_normalize_edges_never_eats_audible_audio():
    body = tone(0.4)
    out = pacing.normalize_edges(quiet(0.4) + body + quiet(0.4))
    assert body in out


def test_normalize_edges_leaves_an_all_silent_segment_alone():
    allquiet = quiet(0.5)
    assert pacing.normalize_edges(allquiet) == allquiet


def test_normalize_edges_leaves_an_implausibly_short_segment_alone():
    # Below MIN_KEEP_SECONDS of body: trust the original rather than the silence detector.
    tiny = quiet(0.2) + tone(0.02) + quiet(0.2)
    assert pacing.normalize_edges(tiny) == tiny


def test_normalize_edges_makes_every_segment_the_same_at_the_edges():
    a = pacing.normalize_edges(quiet(0.0) + tone(0.3) + quiet(0.37))
    b = pacing.normalize_edges(quiet(0.43) + tone(0.3) + quiet(0.13))
    edge = len(quiet(pacing.EDGE_SECONDS))
    assert a[:edge] == b[:edge] == quiet(pacing.EDGE_SECONDS)
    assert a[-edge:] == b[-edge:] == quiet(pacing.EDGE_SECONDS)


# --- apply: the one call that keeps the audio and its offsets in agreement ----------------------

def test_apply_returns_gaps_aligned_to_the_pcm_it_returns():
    segs = [seg(i) for i in range(5)]
    raw = [quiet(0.3) + tone(0.5) + quiet(0.25) for _ in segs]
    paced, gaps = pacing.apply(segs, raw, pacing.CONVERSATIONAL)
    assert len(paced) == len(segs)
    assert len(gaps) == len(segs) - 1
    # The invariant the whole module exists to protect: the last segment's start plus its own
    # length is the total, i.e. the offsets describe exactly the audio that gets written.
    starts = stitch.segment_start_times(paced, gaps)
    total = len(stitch.concat_pcm(paced, gaps)) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)
    last = len(paced[-1]) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)
    assert starts[-1] + last == pytest.approx(total, abs=0.01)


def test_apply_does_not_mutate_the_raw_pcm_it_was_given():
    """The caller caches the raw list after calling apply; aliasing it would poison the archive."""
    segs = [seg(i) for i in range(3)]
    raw = [quiet(0.3) + tone(0.5) + quiet(0.25) for _ in segs]
    before = [bytes(p) for p in raw]
    paced, _ = pacing.apply(segs, raw, pacing.CONVERSATIONAL)
    assert raw == before
    assert paced[0] != raw[0]  # and it really did normalize, so the check above means something


def test_apply_skips_normalization_when_the_policy_says_so():
    segs = [seg(i) for i in range(3)]
    raw = [quiet(0.3) + tone(0.5) + quiet(0.25) for _ in segs]
    paced, gaps = pacing.apply(segs, raw, pacing.UNIFORM)
    assert paced == raw
    assert set(gaps) == {config.GAP_SECONDS}


def test_apply_defaults_to_the_shipping_show_policy():
    segs = [seg(i) for i in range(4)]
    raw = [tone(0.4) for _ in segs]
    assert pacing.apply(segs, raw) == pacing.apply(segs, raw, pacing.SHOW_POLICY)


def test_story_ids_from_keeps_only_real_ids():
    items = [{"hn_id": 1}, {"hn_id": None}, {"title": "no id"}, {"hn_id": 2}]
    assert pacing.story_ids_from(items) == {1, 2}
    assert pacing.story_ids_from(None) == set()


def test_a_comment_boundary_is_not_read_as_a_story_change():
    """Comments carry an hn id too; without the story set they look like a new story."""
    desk = seg(1, role="desk", speaker="Marcus", hn=100)
    commenter = seg(2, role="commenter", speaker="pg", hn=999)
    assert pacing.boundary_kind(desk, commenter, {100}) == "into_comment"
    assert pacing.boundary_kind(commenter, desk, {100}) == "out_of_comment"
