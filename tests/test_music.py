"""Music: cues, the cold-open bed, levels set against the episode, and the offset invariant.

The invariant this file exists to protect: inserting music changes where every later line lands,
and `episode.json`'s `start_seconds` feeds the chapter marks, the VTT, and the page's seek
buttons. So the test that matters is not "the file got longer" - it is "for every ORIGINAL script
segment, the start time we report is where that segment's audio actually begins in the audio we
are about to write".
"""

from __future__ import annotations

import array
import math
import shutil
import struct
import wave

import pytest

from hn_radio import config, music, pacing, stitch
from hn_radio.models import ScriptSegment

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY_IDS = {101, 202, 303}
SILENT = (lambda *a, **k: None)


def seg(order, role="desk", speaker="Marcus", desk="maker", hn=101):
    return ScriptSegment(order=order, role=role, speaker_key=speaker, text="words",
                         desk=desk, source_hn_id=hn)


def _episode():
    """Eight segments, two real story changes, one performed comment."""
    return [
        seg(0, role="anchor", speaker="Haley", desk="anchor", hn=None),  # fixed intro
        seg(1, role="anchor", speaker="Haley", desk="anchor", hn=0),     # cold open
        seg(2, speaker="Marcus", hn=101),
        seg(3, role="anchor", speaker="Haley", desk="anchor", hn=101),
        seg(4, speaker="Marcus", hn=202),                                # story change
        seg(5, role="anchor", speaker="Haley", desk="anchor", hn=303),   # story change
        seg(6, role="commenter", speaker="dang", desk=None, hn=9999),
        seg(7, role="anchor", speaker="Haley", desk="anchor", hn=None),  # fixed outro
    ]


def tone(seconds, amplitude=8000):
    """`seconds` of loud, obviously-not-silence PCM. Durations here are whole milliseconds on
    purpose: at 24 kHz that keeps every offset exactly representable, so a byte-level assertion
    about position is testing the arithmetic and not float rounding."""
    n = int(config.SAMPLE_RATE * seconds)
    return array.array("h", [amplitude if i % 2 else -amplitude for i in range(n)]).tobytes()


def _speech(segments, base=6000):
    """One distinct-amplitude, distinct-length take per segment, so each is findable by bytes."""
    return [tone(0.2 + 0.05 * i, amplitude=base + 137 * i) for i in range(len(segments))]


def _gaps(segments):
    return pacing.gap_plan(segments, pacing.CONVERSATIONAL, STORY_IDS)


@pytest.fixture
def track(tmp_path):
    """A real audio file for ffmpeg to decode: 8s of 440 Hz, stereo, 44.1 kHz.

    Deliberately shorter than the show track and shorter than the cue offsets, so the default
    path through this fixture exercises the wrap-around in `_excerpt` rather than only the
    happy case where the track is long enough.
    """
    path = tmp_path / "fixture-track.wav"
    rate, seconds = 44100, 8
    frames = bytearray()
    for i in range(rate * seconds):
        v = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<hh", v, v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def _rms(pcm):
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    if not a:
        return 0.0
    return math.sqrt(sum(v * v for v in a) / len(a))


# --- THE invariant: reported offsets must locate the real segments -----------------------------

@needs_ffmpeg
def test_start_times_locate_every_original_segment_in_the_final_audio(track):
    """For every ORIGINAL segment index, `starts[i]` is where that segment's audio begins.

    Run with the bed's audio suppressed (`bed_db=None`) but its structure intact, so every
    segment survives byte-identical and position can be asserted exactly. The bed changes no
    offsets (pinned separately below), so this covers the real layout: intro cue, the merged
    cold-open piece, a sting at each story's first segment, the outro cue.
    """
    segs = _episode()
    pcm = _speech(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)

    joined = stitch.concat_pcm(pieces, out_gaps)
    assert len(starts) == len(segs), "one start per ORIGINAL segment, not per piece"
    bytes_per_second = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    for i, p in enumerate(pcm):
        actual = joined.find(p)
        assert actual >= 0, f"segment {i} is not in the stitched audio at all"
        assert joined.find(p, actual + 1) == -1, f"segment {i} is not uniquely findable"
        assert starts[i] == pytest.approx(actual / bytes_per_second, abs=0.002), (
            f"segment {i} reported at {starts[i]}s but its audio begins at "
            f"{actual / bytes_per_second}s"
        )
        off = int(round(starts[i] * config.SAMPLE_RATE)) * config.SAMPLE_WIDTH
        assert joined[off:off + len(p)] == p


@needs_ffmpeg
def test_start_times_still_locate_every_segment_when_a_callback_moves_the_sting_count(track):
    """Requirement 5, extended: the offset invariant must hold under the NEW placement rule too,
    including on the exact script shape that broke it (a callback and a comment-theater line
    each carrying an earlier story's id, so the number and position of stings differs from a
    naive per-boundary count)."""
    segs = _three_story_script()
    callback = seg(7, role="anchor", speaker="Haley", desk="anchor", hn=101)
    comment = seg(8, role="commenter", speaker="dang", desk=None, hn=202)
    outro = segs[7]
    segs = segs[:7] + [callback, comment, outro]

    pcm = _speech(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)
    joined = stitch.concat_pcm(pieces, out_gaps)
    bytes_per_second = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    for i, p in enumerate(pcm):
        actual = joined.find(p)
        assert actual >= 0, f"segment {i} is not in the stitched audio at all"
        assert starts[i] == pytest.approx(actual / bytes_per_second, abs=0.002), (
            f"segment {i} reported at {starts[i]}s but its audio begins at "
            f"{actual / bytes_per_second}s"
        )


@needs_ffmpeg
def test_the_bed_moves_nothing(track):
    """Mixing under the cold open must not shift a single offset; it only changes samples."""
    segs = _episode()
    pcm = _speech(segs)
    gaps = _gaps(segs)
    dry_pieces, dry_gaps, dry_starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                                   track=track, bed_db=None, log=SILENT)
    wet_pieces, wet_gaps, wet_starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                                   track=track, log=SILENT)
    assert wet_starts == dry_starts
    assert wet_gaps == dry_gaps
    assert [len(p) for p in wet_pieces] == [len(p) for p in dry_pieces]
    differing = [i for i, (a, b) in enumerate(zip(dry_pieces, wet_pieces)) if a != b]
    assert differing == [1], "only the cold-open piece carries a bed"


@needs_ffmpeg
def test_the_bed_covers_the_first_two_segments_and_nothing_after_them(track):
    segs = _episode()
    pcm = _speech(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                                           track=track, log=SILENT)
    joined = stitch.concat_pcm(pieces, out_gaps)
    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    # segments 0 and 1 have music on top, so they no longer byte-match...
    for i in (0, 1):
        off = int(round(starts[i] * config.SAMPLE_RATE)) * config.SAMPLE_WIDTH
        assert joined[off:off + len(pcm[i])] != pcm[i]
    # ...and every desk take after the cold open is untouched, which is the whole point.
    for i in range(2, len(segs)):
        off = int(round(starts[i] * config.SAMPLE_RATE)) * config.SAMPLE_WIDTH
        assert joined[off:off + len(pcm[i])] == pcm[i], f"segment {i} has music under it"
    assert starts[-1] < len(joined) / bps


@needs_ffmpeg
def test_the_bed_still_spans_a_cold_open_the_spacing_pass_stretched(track):
    """`BED_SEGMENTS = 2` lays the bed over the fixed intro plus the cold open, and the cold open
    now gets LONGER before music runs: `pipeline._space_cold_open` sets the pauses between its
    headlines to `pacing.COLD_OPEN_PAUSE_SECONDS`. The bed is cut to the measured length of the
    body it covers and `_excerpt` wraps the track, so a longer preview takes more track rather
    than running out of it.

    Pinned because the failure mode is silent rather than loud: nothing would raise, the bed would
    just stop partway through the preview and the last headline would play dry.
    """
    from hn_radio import pipeline

    segs = _episode()
    segs[1].text = "A thing broke. Another shipped. And a third happened."
    pcm = _speech(segs)
    pcm[1] = (tone(1.0) + stitch.silence(0.13) + tone(1.0) + stitch.silence(0.22) + tone(1.0))
    spaced = pipeline._space_cold_open(segs, pcm, log=SILENT)
    assert len(spaced[1]) > len(pcm[1]), "the fixture is not exercising a stretched cold open"

    gaps = _gaps(segs)
    dry, _, _ = music.apply(segs, spaced, gaps, story_ids=STORY_IDS, track=track,
                            bed_db=None, log=SILENT)
    wet, _, _ = music.apply(segs, spaced, gaps, story_ids=STORY_IDS, track=track, log=SILENT)

    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    want = len(spaced[0]) + int(gaps[0] * config.SAMPLE_RATE) * config.SAMPLE_WIDTH \
        + len(spaced[1]) + int(music.BED_TAIL_SECONDS * config.SAMPLE_RATE) * config.SAMPLE_WIDTH
    assert len(wet[1]) == pytest.approx(want, abs=bps * 0.01), \
        "the bedded piece is not the intro + cold open + tail it is supposed to cover"

    # The bed itself: what the mix added, second by second. Every window has to carry music,
    # including the last one, which is the tail that plays past the final headline.
    bed = array.array("h", [b - a for a, b in
                            zip(array.array("h", dry[1]), array.array("h", wet[1]))])
    assert len(bed) * config.SAMPLE_WIDTH == len(wet[1])
    step = config.SAMPLE_RATE
    for start in range(0, len(bed) - step, step):
        window = bed[start:start + step].tobytes()
        assert _rms(window) > 0, f"the bed goes silent {start / config.SAMPLE_RATE:.0f}s in"


# --- cue placement -----------------------------------------------------------------------------

@needs_ffmpeg
def test_a_cue_opens_the_show_and_a_cue_closes_it(track):
    segs = _episode()
    pcm = _speech(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)
    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    assert len(pieces[0]) == int(config.SAMPLE_RATE * music.INTRO_SECONDS) * config.SAMPLE_WIDTH
    assert starts[0] > 0, "the first line no longer starts at zero; music runs ahead of it"
    total = len(stitch.concat_pcm(pieces, out_gaps)) / bps
    last_end = starts[-1] + len(pcm[-1]) / bps
    assert total - last_end >= music.OUTRO_SECONDS


@needs_ffmpeg
def test_a_sting_lands_before_each_storys_first_segment_and_nowhere_else(track):
    """Stings key off first occurrence (`music._sting_boundaries`), not `boundary_kind`.

    The fixture has three real stories (101, 202, 303). Story 101's FIRST segment (index 2)
    follows the cold open (hn=0), a boundary `boundary_kind` calls "exchange", not
    "story_change" - so under the old rule that boundary never got a sting. It must get one now:
    that is the whole point of the change (the show never used to sting out of the cold open).
    """
    segs = _episode()
    pcm = _speech(segs)
    gaps = _gaps(segs)
    n_stories = 3

    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)
    # intro cue + merged cold open + (n - 2) takes + one sting per story + outro cue
    assert len(pieces) == 1 + 1 + (len(segs) - 2) + n_stories + 1

    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH

    def air(i):
        return starts[i + 1] - (starts[i] + len(pcm[i]) / bps)

    # boundary 1: seg1 (cold open, hn=0) -> seg2 (story 101's first segment) - a sting, even
    # though `boundary_kind` would call this "exchange".
    assert air(1) >= music.STING_SECONDS, "no sting out of the cold open, into story 101"
    # boundary 2: seg2 -> seg3, both story 101 - no sting, its ordinary conversational gap.
    assert air(2) == pytest.approx(gaps[2], abs=0.002)
    # boundary 3: seg3 (story 101) -> seg4 (story 202's first segment) - a sting.
    assert air(3) >= music.STING_SECONDS
    # boundary 4: seg4 (story 202) -> seg5 (story 303's first segment) - a sting.
    assert air(4) >= music.STING_SECONDS
    # boundary 5: seg5 -> seg6 (into comment theater) - no sting; comment ids are never in
    # `story_ids` so they can never be a story's first occurrence.
    assert air(5) == pytest.approx(gaps[5], abs=0.002)


# --- sting placement is keyed to first occurrence, and a callback cannot move it ----------------

def _three_story_script():
    """Intro, cold open, three stories with no callback and no comment theater at all - the
    cleanest possible case, so the callback/comment regression test below has something exact
    to compare against."""
    return [
        seg(0, role="anchor", speaker="Haley", desk="anchor", hn=None),  # fixed intro
        seg(1, role="anchor", speaker="Haley", desk="anchor", hn=0),     # cold open
        seg(2, speaker="Marcus", hn=101),                                # story one, first
        seg(3, role="anchor", speaker="Haley", desk="anchor", hn=101),   # story one, continues
        seg(4, speaker="Marcus", hn=202),                                 # story two, first
        seg(5, role="anchor", speaker="Haley", desk="anchor", hn=202),   # story two, continues
        seg(6, speaker="Marcus", hn=303),                                 # story three, first
        seg(7, role="anchor", speaker="Haley", desk="anchor", hn=None),  # fixed outro
    ]


def _sting_air(starts, pcm, i):
    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    return starts[i + 1] - (starts[i] + len(pcm[i]) / bps)


@needs_ffmpeg
def test_three_stories_no_callbacks_yields_exactly_three_stings_first_out_of_the_cold_open(track):
    """Requirement 1. Three stories, no callbacks: exactly three stings, and the FIRST one is at
    the boundary entering story one's first segment (out of the cold open) - not between story
    one and story two, which is where the old `boundary_kind`-driven rule put it."""
    segs = _three_story_script()
    pcm = _speech(segs)
    gaps = _gaps(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)

    want = int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH
    stings = [p for p in pieces if len(p) == want]
    assert len(stings) == 3, "three stories must yield exactly three stings"

    # boundary 1 (seg1 cold-open -> seg2 story one) is the FIRST sting.
    assert _sting_air(starts, pcm, 1) >= music.STING_SECONDS
    # boundary 2 (within story one) carries no sting, just its ordinary gap.
    assert _sting_air(starts, pcm, 2) == pytest.approx(gaps[2], abs=0.002)
    # boundary 3 (seg3 story one -> seg4 story two) is the second sting.
    assert _sting_air(starts, pcm, 3) >= music.STING_SECONDS
    # boundary 4 (within story two) carries no sting.
    assert _sting_air(starts, pcm, 4) == pytest.approx(gaps[4], abs=0.002)
    # boundary 5 (seg5 story two -> seg6 story three) is the third sting.
    assert _sting_air(starts, pcm, 5) >= music.STING_SECONDS


@needs_ffmpeg
def test_a_callback_or_a_comment_carrying_an_earlier_story_id_cannot_move_or_add_a_sting(track):
    """Requirement 2, THE REGRESSION TEST. Same three-story script as the test above, but with a
    later anchor line that CALLS BACK to story one's id, and a comment-theater line that also
    carries an earlier story's id (rather than a fresh comment id). This is exactly the failure
    from the real 2026-08-07 render: a callback made an old id look "new" again at the boundary
    it appeared on, so `boundary_kind` read it as a story change and minted an extra, unearned
    sting. Under first-occurrence placement, neither addition can win the `not in seen` check,
    so the sting count and the sting POSITIONS for segments 0-6 must be byte-identical to the
    callback-free script.
    """
    segs = _three_story_script()
    callback = seg(7, role="anchor", speaker="Haley", desk="anchor", hn=101)  # calls back to story one
    comment = seg(8, role="commenter", speaker="dang", desk=None, hn=202)      # "comment" tagged with story two's id
    outro = segs[7]
    outro.order = 9
    segs = segs[:7] + [callback, comment, outro]

    pcm = _speech(segs)
    gaps = _gaps(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)

    want = int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH
    stings = [p for p in pieces if len(p) == want]
    assert len(stings) == 3, "a callback and a comment carrying an old id must add zero stings"

    # Segments 0-6 are unchanged from the callback-free script and come before either addition,
    # so their reported starts - where the three real stings actually landed - must match exactly.
    baseline_segs = _three_story_script()
    baseline_pcm = _speech(baseline_segs)
    baseline_gaps = _gaps(baseline_segs)
    _, _, baseline_starts = music.apply(baseline_segs, baseline_pcm, baseline_gaps,
                                        story_ids=STORY_IDS, track=track, bed_db=None, log=SILENT)
    assert starts[:7] == baseline_starts[:7], (
        "the callback/comment must not move a single sting that was already placed"
    )
    for i in (1, 3, 5):
        assert _sting_air(starts, pcm, i) >= music.STING_SECONDS
    # And the callback line itself (index 7, hn=101, already seen) gets no sting on its way in.
    assert _sting_air(starts, pcm, 6) == pytest.approx(gaps[6], abs=0.002)


@needs_ffmpeg
def test_a_story_repeated_across_consecutive_segments_gets_exactly_one_sting_at_its_first(track):
    """Requirement 3. A story that runs across several consecutive segments must sting once, at
    its first segment, and not again at any of the repeats."""
    segs = [
        seg(0, role="anchor", speaker="Haley", desk="anchor", hn=None),
        seg(1, role="anchor", speaker="Haley", desk="anchor", hn=0),
        seg(2, speaker="Marcus", hn=101),                                 # story one, first
        seg(3, role="anchor", speaker="Haley", desk="anchor", hn=101),   # repeat
        seg(4, speaker="Marcus", hn=101),                                 # repeat
        seg(5, role="anchor", speaker="Haley", desk="anchor", hn=101),   # repeat
        seg(6, role="anchor", speaker="Haley", desk="anchor", hn=None),  # fixed outro
    ]
    pcm = _speech(segs)
    gaps = _gaps(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)
    want = int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH
    stings = [p for p in pieces if len(p) == want]
    assert len(stings) == 1

    assert _sting_air(starts, pcm, 1) >= music.STING_SECONDS, "the sting at story one's first segment"
    for i in (2, 3, 4):
        assert _sting_air(starts, pcm, i) == pytest.approx(gaps[i], abs=0.002), (
            f"boundary {i} repeats story one; it must not sting again"
        )


@needs_ffmpeg
def test_no_story_ids_means_no_stings_at_all(track):
    """Requirement 4. Without `story_ids`, place no stings rather than falling back to guessing
    from raw `source_hn_id` changes (the old default behaviour `boundary_kind` documents)."""
    segs = _three_story_script()
    pcm = _speech(segs)
    gaps = _gaps(segs)
    for story_ids in (None, set()):
        pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=story_ids,
                                               track=track, bed_db=None, log=SILENT)
        want = int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH
        stings = [p for p in pieces if len(p) == want]
        assert stings == [], f"story_ids={story_ids!r} must yield zero stings"
        for i in range(len(segs) - 1):
            if i == music.BED_SEGMENTS - 1:
                continue  # this boundary's air is dominated by the bed's own tail, not the
                          # plain pacing gap, regardless of sting placement - not what this
                          # test is checking; the sting-count assertion above already covers it
            assert _sting_air(starts, pcm, i) == pytest.approx(gaps[i], abs=0.002)


# --- levels are relative to THIS episode's speech ----------------------------------------------

@needs_ffmpeg
def test_a_sting_sits_the_named_number_of_dB_under_the_episode_speech(track):
    segs = _episode()
    pcm = _speech(segs)
    pieces, _, _ = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                               track=track, bed_db=None, log=SILENT)
    speech = music.speech_rms(pcm)
    stings = [p for p in pieces
              if len(p) == int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH]
    assert stings
    for s in stings:
        assert _rms(s) == pytest.approx(speech * 10 ** (music.STING_DB / 20), rel=0.02)


@needs_ffmpeg
def test_the_bed_sits_far_enough_under_that_the_voice_stays_the_product(track):
    segs = _episode()
    pcm = _speech(segs)
    dry, _, _ = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                            track=track, bed_db=None, log=SILENT)
    wet, _, _ = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                            track=track, log=SILENT)
    bed = bytes(array.array("h", [
        b - a for a, b in zip(array.array("h", dry[1]), array.array("h", wet[1]))]).tobytes())
    speech = music.speech_rms(pcm)
    assert _rms(bed) == pytest.approx(speech * 10 ** (music.BED_DB / 20), rel=0.05)
    assert music.BED_DB < music.STING_DB, "the bed must be quieter than a sting, it is under a voice"


@needs_ffmpeg
def test_levels_follow_the_episode_and_are_not_a_fixed_multiplier(track):
    """Two tracks are never the same loudness, so a gain chosen against one is meaningless
    against another. Halve the episode's speech and every cue must halve with it."""
    segs = _episode()
    loud = _speech(segs, base=12000)
    soft = [bytes(array.array("h", [v // 4 for v in array.array("h", p)]).tobytes())
            for p in loud]

    def _sting(pcm):
        pieces, _, _ = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                                   track=track, bed_db=None, log=SILENT)
        want = int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH
        return _rms([p for p in pieces if len(p) == want][0])

    assert _sting(soft) == pytest.approx(_sting(loud) / 4, rel=0.05)


# --- degrading safely --------------------------------------------------------------------------

def test_a_missing_track_keeps_the_show_on_the_air(tmp_path):
    """A missing asset must never take the nightly show off the air."""
    segs = _episode()
    pcm = _speech(segs)
    gaps = _gaps(segs)
    said = []
    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           track=tmp_path / "not-here.mp3", log=said.append)
    assert pieces == list(pcm)
    assert out_gaps == gaps
    assert starts == stitch.segment_start_times(pcm, gaps)
    assert any("not-here.mp3" in m for m in said), "a silent skip is how this rots unnoticed"


def test_an_undecodable_track_keeps_the_show_on_the_air(tmp_path):
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"this is not audio")
    segs = _episode()
    pcm = _speech(segs)
    gaps = _gaps(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           track=bad, log=SILENT)
    assert pieces == list(pcm)
    assert starts == stitch.segment_start_times(pcm, gaps)


def test_music_is_opt_in(track):
    segs = _episode()
    pcm = _speech(segs)
    gaps = _gaps(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           enabled=False, track=track, log=SILENT)
    assert pieces == list(pcm)
    assert out_gaps == gaps
    assert starts == stitch.segment_start_times(pcm, gaps)


@needs_ffmpeg
def test_apply_does_not_mutate_the_pcm_it_was_given(track):
    """The caller caches this list as the episode archive; aliasing it would poison the cache."""
    segs = _episode()
    pcm = _speech(segs)
    before = [bytes(p) for p in pcm]
    gaps = _gaps(segs)
    music.apply(segs, pcm, gaps, story_ids=STORY_IDS, track=track, log=SILENT)
    assert pcm == before
    assert _gaps(segs) == gaps


@needs_ffmpeg
def test_a_script_too_short_to_have_a_cold_open_still_works(track):
    segs = [seg(0, role="anchor", speaker="Haley", desk="anchor", hn=None)]
    pcm = [tone(0.4)]
    pieces, out_gaps, starts = music.apply(segs, pcm, [], story_ids=STORY_IDS,
                                           track=track, bed_db=None, log=SILENT)
    joined = stitch.concat_pcm(pieces, out_gaps)
    off = int(round(starts[0] * config.SAMPLE_RATE)) * config.SAMPLE_WIDTH
    assert joined[off:off + len(pcm[0])] == pcm[0]


def test_no_segments_at_all_is_not_an_error(track):
    assert music.apply([], [], [], track=track, log=SILENT) == ([], [], [])


# --- the ad bracket, built but deliberately not wired in ---------------------------------------

@needs_ffmpeg
def test_ad_bracket_returns_a_matched_pair_of_stings(track):
    head, tail = music.ad_bracket(4000.0, track=track)
    want = int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH
    assert len(head) == len(tail) == want
    assert _rms(head) == pytest.approx(4000.0 * 10 ** (music.STING_DB / 20), rel=0.02)


def test_ad_bracket_degrades_like_everything_else(tmp_path):
    assert music.ad_bracket(4000.0, track=tmp_path / "gone.mp3") == (b"", b"")


def test_the_ad_bracket_is_not_wired_into_the_render_path():
    """It exists so the demo can reach for it. Wiring it in is a separate, unbuilt feature.

    PARKED DELIBERATELY, not merely unfinished. Sam auditioned a five-company
    roster and ruled the reads "fine, not great", and the ad-to-show transition "mechanical":
    voice ends, pause, stinger, pause, ad starts. Four sequential events where a real break
    has one continuous one. So this stays unwired until the transition is designed rather
    than assembled, and the code stays put because the idea is good even though this take
    was not. Do not wire it up on the assumption it was just never finished.
    """
    import inspect
    from hn_radio import pipeline
    assert "ad_bracket" not in inspect.getsource(pipeline)
    assert "ad_bracket" not in inspect.getsource(music.apply)


# --- the wiring in _finalize --------------------------------------------------------------------

def _finalize_episode(tmp_path, monkeypatch, **kw):
    """Run the real `_finalize` end to end against a tmp episodes dir. No network, no renderer."""
    from hn_radio import config as cfg, pipeline, status
    monkeypatch.setattr(cfg, "EPISODES_DIR", tmp_path)
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    monkeypatch.setattr(status, "done", lambda *a, **k: None)
    segs = _episode()
    for s in segs:
        s.voice_id = "flux-haley-en"
    pcm = _speech(segs)
    episode = pipeline._finalize(
        segs, pcm, episode_id="test-ep", title="t",
        source_items=[{"hn_id": i, "title": f"s{i}", "url": "u"} for i in STORY_IDS],
        edition="frontpage", log=SILENT, **kw)
    return segs, pcm, episode, tmp_path / "test-ep"


@needs_ffmpeg
def test_finalize_start_seconds_locate_the_segments_in_the_written_wav(tmp_path, monkeypatch):
    """The end of the chain: what lands in episode.json must point at the real episode.wav."""
    segs, pcm, episode, out = _finalize_episode(tmp_path, monkeypatch)
    with wave.open(str(out / "episode.wav"), "rb") as w:
        written = w.readframes(w.getnframes())
    # What lands in the WAV is the PACED take (pacing normalizes each segment's edges), so that
    # is what to look for. Music is handed the paced audio and must not alter it further.
    paced, gaps = pacing.apply(segs, pcm, story_ids=STORY_IDS)
    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    assert episode.duration_seconds == pytest.approx(len(written) / bps, abs=0.02)
    # Every take that is NOT under the cold-open bed survives byte-identical, so its reported
    # offset can be checked against where its audio really is in the published file.
    for i in range(music.BED_SEGMENTS, len(segs)):
        actual = written.find(paced[i])
        assert actual >= 0, f"segment {i} is not in the written WAV"
        assert segs[i].start_seconds == pytest.approx(actual / bps, abs=0.002)
    # The two bedded takes have music mixed on top and so cannot be found by bytes. Pin instead
    # that _finalize took its offsets FROM music.apply and did not compute its own.
    _, _, starts = music.apply(segs, paced, gaps, story_ids=STORY_IDS, log=SILENT)
    assert [s.start_seconds for s in segs] == starts
    assert segs[0].start_seconds > 0, "the intro cue runs ahead of the first line"


@needs_ffmpeg
def test_finalize_still_caches_the_raw_unmusicked_takes(tmp_path, monkeypatch):
    """The per-segment cache is the archive. A bed baked into it could never be undone."""
    segs, pcm, _, out = _finalize_episode(tmp_path, monkeypatch)
    for i, s in enumerate(segs):
        assert (out / "segments" / f"{s.order}.pcm").read_bytes() == pcm[i]


@needs_ffmpeg
def test_finalize_can_be_asked_for_a_dry_episode(tmp_path, monkeypatch):
    segs, pcm, _, out = _finalize_episode(tmp_path, monkeypatch, with_music=False)
    assert segs[0].start_seconds == 0.0
    paced, _ = pacing.apply(segs, pcm, story_ids=STORY_IDS)
    with wave.open(str(out / "episode.wav"), "rb") as w:
        written = w.readframes(w.getnframes())
    assert written.startswith(paced[0])


@needs_ffmpeg
def test_finalize_defaults_to_a_musicked_episode(tmp_path, monkeypatch):
    """An unconfigured render has music.

    This used to assert the signature default was literally `True`. The default is `None` now,
    meaning "ask `config.music_enabled()`", so that assertion pinned a mechanism that moved while
    the behavior it cared about did not. Check the behavior instead: with nothing set anywhere,
    the intro cue still runs ahead of the first line. The switch's own wiring is in
    tests/test_music_switch.py.
    """
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    segs, _, _, _ = _finalize_episode(tmp_path, monkeypatch)
    assert segs[0].start_seconds > 0, "no intro cue, so the default rendered a dry episode"


@needs_ffmpeg
def test_chapters_and_the_vtt_follow_the_music_rather_than_the_dry_script(tmp_path, monkeypatch):
    """chapters.build_chapters and build_vtt both read start_seconds, so a stale offset there is
    a chapter mark and a subtitle that point at the wrong moment of a nine-minute file."""
    import json

    def _marks(sub, **kw):
        segs, _, _, out = _finalize_episode(tmp_path / sub, monkeypatch, **kw)
        chapters = json.loads((out / "chapters.json").read_text())["chapters"]
        return segs, chapters, (out / "transcript.vtt").read_text()

    dry_segs, dry, _ = _marks("dry", with_music=False)
    wet_segs, wet, vtt = _marks("wet")

    story = lambda cs: [c["startTime"] for c in cs if c.get("url")]
    assert story(dry), "fixture sanity: the episode has story chapters at all"
    assert len(story(wet)) == len(story(dry))
    for w, d in zip(story(wet), story(dry)):
        assert w > d, "a musicked episode's stories start later; these marks are stale"
    # and every story mark is a real segment offset, not a number invented alongside one
    starts = {s.start_seconds for s in wet_segs}
    assert all(round(t, 3) in starts for t in story(wet))
    assert f"{int(wet_segs[1].start_seconds // 60):02d}:" in vtt.splitlines()[2]


# --- the real asset ----------------------------------------------------------------------------

@needs_ffmpeg
def test_the_show_track_is_committed_and_decodes_to_the_pipeline_format():
    pcm = music.decode(music.SHOW_TRACK)
    assert music.SHOW_TRACK.exists(), "the show theme must ship with the repo"
    seconds = len(pcm) / config.SAMPLE_WIDTH / config.SAMPLE_RATE
    assert seconds > 30, f"decoded only {seconds:.1f}s of theme"


@needs_ffmpeg
def test_the_outro_cue_is_not_silent_on_the_real_track():
    """OUTRO_AT + OUTRO_SECONDS = 176.5s against a ~178.8s track, so `_excerpt` does not need to
    wrap today - but the track is fully silent by ~177s (RMS decays to single digits). If
    someone later shortens or re-encodes the committed track, OUTRO_AT would silently start
    reading that dead air instead of the ending it was tuned against, and every other assertion
    in this file checks OFFSETS and LEVELS, never "is there actually sound here" - so a mute
    outro would pass the whole suite and only show up to a listener. Pin the one thing that
    would not: the excerpt this offset produces has real signal in it."""
    pcm = music.decode(music.SHOW_TRACK)
    excerpt = music._excerpt(pcm, music.OUTRO_AT, music.OUTRO_SECONDS)
    assert _rms(excerpt) > 100, "the outro cue is reading near-silence, not the track's ending"


@needs_ffmpeg
def test_the_sting_opens_and_closes_soft_on_the_real_track():
    """The sting must not sound cut out of the middle of a phrase.

    Pinning STING_AT itself would only restate the constant, so pin the property the offset was
    chosen for: the cue the show actually airs opens quietly, swells, and decays. Measured on
    the SHAPED sting (post-fade, post-level) taken from the COMMITTED track, because the
    gentleness lives in the material, not in the fade - the old offset (19.6) had the identical
    fade lengths and still entered at 0.339 of its own RMS because the music there was flat at
    near-phrase level. The synthetic `track` fixture is a constant-amplitude sine and cannot
    fail this, which is exactly why this test does not use it.

    The swell assertion is not decoration: without it, "quiet at both ends" is satisfiable by
    an excerpt of near-silence, which would be gentle and also not a sting.
    """
    segs = _episode()
    pcm = _speech(segs)
    pieces, _, _ = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                               track=music.SHOW_TRACK, bed_db=None, log=SILENT)
    want = int(config.SAMPLE_RATE * music.STING_SECONDS) * config.SAMPLE_WIDTH
    stings = [p for p in pieces if len(p) == want]
    assert stings, "no sting of the expected length; the rest of this test would be vacuous"

    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH

    def span(sting, t0, t1):
        return _rms(sting[int(bps * t0):int(bps * t1)])

    for sting in stings:
        whole = _rms(sting)
        assert whole > 0
        end = music.STING_SECONDS
        head = span(sting, 0.0, 0.15) / whole
        tail = span(sting, end - 0.15, end) / whole
        # measured 2026-08-20: head 0.108, tail 0.052 at STING_AT = 85.8 with
        # STING_FADE_OUT = 1.05. The old 19.6 offset read head 0.339, so the head bound is what
        # the excerpt change moved and 0.15 sits between them. The tail bound is 0.06 rather
        # than 0.12 so that reverting STING_FADE_OUT to 0.80, which reads 0.066, fails here:
        # Sam heard that as snapping off, and a bound loose enough to allow it pins nothing.
        assert head < 0.15, f"the sting enters at {head:.3f} of its own RMS; that is a hard start"
        assert tail < 0.06, f"the sting leaves at {tail:.3f} of its own RMS; that is a hard stop"

        # and it is genuinely FALLING into that quiet tail, not stepping off a plateau
        last = span(sting, end - 0.3, end)
        before = span(sting, end - 0.6, end - 0.3)
        assert last < before * 0.75, (
            f"the last 300ms ({last:.0f}) is not decaying out of the 300ms before it "
            f"({before:.0f}); the fade is cutting the music off rather than following it down")

        # there is a real swell in the middle, so "gentle" cannot be met by near-silence
        middle = max(span(sting, t / 100, t / 100 + 0.2)
                     for t in range(20, int(end * 100) - 40, 5))
        assert middle > whole * 1.5, (
            f"loudest 200ms in the body is only {middle / whole:.2f}x the cue's RMS; "
            f"the sting has no swell and reads as filler, not a cue")


# --- headroom: a cue must never reach full scale ------------------------------------------------
#
# A cue is levelled by RMS and RMS says nothing about peaks, so the loudest sample in a cue is
# `target_rms * crest factor`. Measured on the committed track at the shipped offsets and fades:
# intro 5.49, sting 8.01, outro 10.48. At STING_DB = -6 the outro therefore reaches full scale
# once the episode's speech RMS passes 6241, about -14.4 dBFS, which is an unremarkable level for
# a TTS master. Today's render measured 5174, so this was latent rather than live -- and it was
# silent, because `_shape` clamped each sample to int16 and logged nothing.

def _outro_from(pieces):
    """The outro cue, found by its length. It is the last piece and the only one this long."""
    want = int(config.SAMPLE_RATE * music.OUTRO_SECONDS) * config.SAMPLE_WIDTH
    outros = [p for p in pieces if len(p) == want]
    assert len(outros) == 1, f"expected exactly one {music.OUTRO_SECONDS}s piece, got {len(outros)}"
    return outros[0]


def _crest_and_rails(pcm):
    """(peak / RMS, how many samples sit on an int16 rail). Clipping moves both."""
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    rms = math.sqrt(sum(v * v for v in a) / len(a))
    peak = max(abs(v) for v in a)
    return peak / rms, sum(1 for v in a if v >= 32767 or v <= -32768)


@needs_ffmpeg
def test_a_cue_that_would_clip_is_attenuated_rather_than_flat_topped():
    """A hot cast must cost the cue level, not waveform.

    Clamping to int16 is flat-topping: it squares off every peak that does not fit, which is
    audible as distortion on the one cue a listener hears without a voice over it, and it leaves
    no trace anywhere. Backing the gain off instead keeps the cue's SHAPE and costs it a few dB
    that nothing in the show is measured against.

    Pinned by crest factor rather than by "no sample equals 32767", because a limiter that lands
    the peak exactly on the ceiling is correct and would fail that. Crest is what clipping
    destroys: the outro excerpt's is 10.48 and it must survive the level-setting intact.
    """
    segs = _episode()
    # Loud enough that the outro's peak lands well past full scale: speech RMS here is ~9480
    # against the 6241 the outro clips above.
    pcm = _speech(segs, base=9000)
    speech = music.speech_rms(pcm)
    assert speech > 6241, f"fixture is not hot enough to exercise the guard (rms {speech:.0f})"

    lines = []
    pieces, _, _ = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                               track=music.SHOW_TRACK, bed_db=None, log=lines.append)
    crest, rails = _crest_and_rails(_outro_from(pieces))
    assert crest > 10.0, (
        f"the outro cue's crest factor collapsed to {crest:.2f} from 10.48; it is being clipped "
        "rather than attenuated")
    # A limiter that lands the loudest sample exactly on the ceiling is correct, so a couple of
    # rail samples is expected. Hundreds of them is a squared-off waveform.
    assert rails <= 4, f"{rails} samples are pinned to an int16 rail; that is flat-topping"
    assert any("outro" in line and "attenuat" in line for line in lines), (
        f"a cue was attenuated and said nothing: {lines}")


@needs_ffmpeg
def test_the_headroom_guard_does_not_touch_a_cue_that_fits():
    """The other half, and the one that makes this change shippable during a code freeze.

    Today's episode measured a speech RMS of 5174, under every cue's clip threshold, so the guard
    must be a no-op at the level the show actually runs at: the cue hits the RMS `STING_DB` asks
    for, exactly, and nothing is logged. A headroom guard that quietly ducked today's outro would
    be a mix change smuggled in as a bug fix.
    """
    segs = _episode()
    pcm = _speech(segs, base=4900)
    speech = music.speech_rms(pcm)
    assert speech < 6241, f"fixture is too hot to prove the no-op case (rms {speech:.0f})"

    lines = []
    pieces, _, _ = music.apply(segs, pcm, _gaps(segs), story_ids=STORY_IDS,
                               track=music.SHOW_TRACK, bed_db=None, log=lines.append)
    outro = _outro_from(pieces)
    assert _rms(outro) == pytest.approx(speech * 10 ** (music.STING_DB / 20), rel=0.02)
    assert not [line for line in lines if "attenuat" in line], lines


@needs_ffmpeg
def test_the_invariant_holds_on_the_real_track_and_real_rendered_speech():
    """Same assertion as the headline test, but against the committed theme and real rendered
    takes, so nothing here depends on the synthetic tone being convenient.

    The takes are COMMITTED, under `tests/golden/music/real_takes/`. This test used to read
    `episodes/2026-08-08/segments` and skip when it was absent, which made the suite total
    machine-specific: it was 484 on the laptop that happened to have that episode rendered and
    483 plus a skip on a fresh clone, so the number nobody could reproduce was the number being
    quoted. The fixture is eight takes lifted from that episode, each cut to a distinct half-
    second-ish length from 300 ms in (past the leading near-silence, which is not distinctive
    enough to locate by bytes) to keep it to 243 KB. They are asserted mutually non-containing
    at build time, so `find` below cannot match the wrong one.
    """
    seg_dir = config.PROJECT_ROOT / "tests" / "golden" / "music" / "real_takes"
    segs = _episode()
    pcm = stitch.load_cached_segments(seg_dir, list(range(len(segs))))
    gaps = _gaps(segs)
    pieces, out_gaps, starts = music.apply(segs, pcm, gaps, story_ids=STORY_IDS,
                                           bed_db=None, log=SILENT)
    joined = stitch.concat_pcm(pieces, out_gaps)
    # The renderer returns whole milliseconds -- all 405 takes on disk across every local episode
    # are exact multiples of 48 bytes -- so a millisecond-rounded offset CAN be byte-exact here,
    # and the earlier comment claiming otherwise was wrong. The tolerance stays because it guards
    # the offset arithmetic's own float rounding, not the take lengths.
    bps = config.SAMPLE_RATE * config.SAMPLE_WIDTH
    for i, p in enumerate(pcm):
        actual = joined.find(p)
        assert actual >= 0, f"segment {i} is not in the stitched audio at all"
        assert starts[i] == pytest.approx(actual / bps, abs=0.002), (
            f"segment {i} reported at {starts[i]}s but its audio begins at {actual / bps}s")


def test_the_show_track_is_shipped_in_the_docker_image():
    """The Dockerfile must COPY assets/, or production silently loses its music.

    `music.apply` degrades to un-musicked audio when the track is missing, on purpose: a missing
    asset must never take the nightly show off the air. The cost of that choice is that forgetting
    to ship the file produces no error anywhere -- the podcast just quietly has no theme. That is
    exactly what happened: the Dockerfile copied hn_radio, backend, web, scripts and episodes, and
    never assets, and it was caught by inspection minutes before the first deploy of the music
    work rather than by anything failing.
    """
    from hn_radio import config

    dockerfile = (config.PROJECT_ROOT / "Dockerfile").read_text()
    assert "COPY assets" in dockerfile, "assets/ is not copied into the image; music would vanish"

    # and the track the code reaches for is actually in there
    rel = music.SHOW_TRACK.relative_to(config.PROJECT_ROOT)
    assert str(rel).startswith("assets/"), f"SHOW_TRACK is outside assets/: {rel}"
    assert music.SHOW_TRACK.exists(), f"{rel} is missing from the repo"

    ignore = (config.PROJECT_ROOT / ".dockerignore")
    if ignore.exists():
        for line in ignore.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                assert not str(rel).startswith(line.rstrip("/*")), (
                    f".dockerignore rule {line!r} would exclude the show track"
                )


def test_the_pcm_cache_survives_a_crash_after_the_render(tmp_path, monkeypatch):
    """The rendered audio is the only irreplaceable thing in a run, so it is written first.

    This write used to sit AFTER `pacing.apply` and `music.apply`, with nothing wrapped in
    between, so one exception in either discarded every TTS call in the episode -- roughly twenty
    paid calls -- while pacing and music are pure functions of that audio and can be redone for
    free. The ordering is the fix, and this is the test that stops it drifting back.
    """
    from hn_radio import config as cfg, music as music_mod, pipeline, status
    monkeypatch.setattr(cfg, "EPISODES_DIR", tmp_path)
    for hook in ("begin", "stage", "done"):
        monkeypatch.setattr(status, hook, lambda *a, **k: None)

    def explode(*a, **k):
        raise RuntimeError("music blew up after the renderer had already been paid")
    monkeypatch.setattr(music_mod, "apply", explode)

    segs = _episode()
    for s in segs:
        s.voice_id = "flux-haley-en"
    pcm = _speech(segs)

    with pytest.raises(RuntimeError):
        pipeline._finalize(segs, pcm, episode_id="crash-ep", title="t",
                           source_items=[{"hn_id": i, "title": f"s{i}", "url": "u"}
                                         for i in STORY_IDS],
                           edition="frontpage", log=SILENT)

    seg_dir = tmp_path / "crash-ep" / "segments"
    cached = sorted(p.name for p in seg_dir.glob("*.pcm"))
    assert cached == sorted(f"{s.order}.pcm" for s in segs), (
        "the crash lost rendered audio; the cache write must happen before pacing and music"
    )
    # and it is the RAW renderer output, not a paced or musicked copy
    for seg, raw in zip(segs, pcm):
        assert (seg_dir / f"{seg.order}.pcm").read_bytes() == raw
