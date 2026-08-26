"""The cold open's internal pauses: one read, evenly spaced.

The defect this file protects against is the second half of the cold-open fix. The first half
merged the cold open into ONE segment and one TTS call, which killed the 2.9s holes between
headlines (see `writers._merge_cold_open` and the note above it). That left the opposite problem:
Flux runs the sentences together. Measured on the real 2026-08-20 cold-open renders joined into
one read, the runs of silence inside it are 0.09 / 0.13 / 0.22s, so the largest pause anywhere
between two headlines is 0.22s while a mid-headline breath is 0.13s. The list does not read as a
list, and Sam's verdict on the merged read was "maybe a touch too fast".

The synthetic reads below use those measured numbers rather than round ones, so a test that
passes here describes audio the show has actually produced.

What must NOT be "fixed" by anything in here: splitting the cold open back into several segments.
Separate renders are the documented cause of the stark jumps between headlines, so the only lever
is the silence inside the single utterance.
"""

from __future__ import annotations

import array
import shutil
import wave
from datetime import date

import pytest

from hn_radio import config, pacing, pipeline, writers
from hn_radio.cast import Cast, Desk
from hn_radio.models import ScriptSegment, Story

# The measured shape of a real merged cold open: an intra-sentence breath, then the two headline
# boundaries. Deliberately close together (0.13 vs 0.22), because that is what the real render
# looks like and it is what makes "the longest N runs" a claim worth testing.
BREATH, BOUNDARY_1, BOUNDARY_2 = 0.09, 0.13, 0.22


def tone(seconds, amplitude=8000):
    """`seconds` of unmistakable non-silence: every sample is at full swing, so nothing inside a
    word can be mistaken for a pause by the silence detector."""
    n = int(config.SAMPLE_RATE * seconds)
    return array.array("h", [amplitude if i % 2 else -amplitude for i in range(n)]).tobytes()


def quiet(seconds):
    return b"\x00" * (int(config.SAMPLE_RATE * seconds) * config.SAMPLE_WIDTH)


def ragged_read():
    """One utterance, three headlines, spaced the way Flux actually spaces them."""
    return (tone(1.0) + quiet(BREATH) + tone(0.8) + quiet(BOUNDARY_1)
            + tone(1.0) + quiet(BOUNDARY_2) + tone(0.9))


def internal_runs(pcm, min_run=0.05):
    """Lengths in seconds of every silence run inside `pcm`, edges excluded.

    An independent re-implementation of the scan on purpose. Measuring the result with the same
    helper the implementation uses would only prove the helper agrees with itself.
    """
    a = array.array("h")
    a.frombytes(pcm)
    n, thr, out, i = len(a), pacing.SILENCE_THRESHOLD, [], 0
    while i < n:
        if abs(a[i]) > thr:
            i += 1
            continue
        j = i
        while j < n and abs(a[j]) <= thr:
            j += 1
        if i != 0 and j < n and (j - i) / config.SAMPLE_RATE >= min_run:
            out.append((j - i) / config.SAMPLE_RATE)
        i = j
    return out


def _secs(pcm):
    return len(pcm) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)


# --- the mechanism: set N pauses to one fixed length -------------------------------------------

@pytest.mark.parametrize("seconds", [0.35, 0.55, 0.80])
def test_the_headline_boundaries_all_come_out_at_the_requested_length(seconds):
    """The property, not the constant: whatever length is asked for is the length that lands, and
    every targeted pause lands on the SAME one. Evenness is most of what reads as matter-of-fact;
    a floor alone would leave 0.13 and 0.22 ragged, just less ragged."""
    spaced = pacing.set_internal_pauses(ragged_read(), 2, seconds)
    boundaries = sorted(internal_runs(spaced))[-2:]
    for got in boundaries:
        assert got == pytest.approx(seconds, abs=0.01)
    assert boundaries[0] == pytest.approx(boundaries[1], abs=0.005), "the pauses must be even"


def test_a_mid_headline_breath_is_left_exactly_as_rendered():
    """Only the boundaries are targeted. Padding every run would open a hole inside a headline,
    which is a worse defect than the one being fixed."""
    spaced = pacing.set_internal_pauses(ragged_read(), 2, 0.55)
    assert min(internal_runs(spaced)) == pytest.approx(BREATH, abs=0.01)


def test_the_read_grows_by_exactly_the_silence_that_was_added():
    """The cold open getting longer is the point, and it has to be an accountable amount: the show
    is a fixed-length format and the bed over the cold open is cut to its measured duration."""
    spaced = pacing.set_internal_pauses(ragged_read(), 2, 0.55)
    added = (0.55 - BOUNDARY_1) + (0.55 - BOUNDARY_2)
    assert _secs(spaced) == pytest.approx(_secs(ragged_read()) + added, abs=0.01)


def test_the_speech_itself_is_untouched():
    """Nothing here re-times a word. The spoken runs survive whole, which is the difference
    between spacing a read and re-cutting it."""
    spaced = pacing.set_internal_pauses(ragged_read(), 2, 0.55)
    for spoken in (tone(0.8), tone(0.9), tone(1.0)):
        assert spoken in spaced


def test_running_it_twice_changes_nothing():
    """Idempotent, so a rebuild from the per-segment cache cannot stack a second pass on audio
    that already has one. The archive holds the RAW render, but nothing should depend on that."""
    once = pacing.set_internal_pauses(ragged_read(), 2, 0.55)
    assert pacing.set_internal_pauses(once, 2, 0.55) == once


def test_a_one_sentence_cold_open_is_left_alone():
    """A single-story episode has no boundary to space, and `custom.py` can produce one."""
    raw = ragged_read()
    assert pacing.set_internal_pauses(raw, 0, 0.55) == raw


def test_asking_for_more_pauses_than_the_read_has_does_not_invent_any():
    """A miscount must degrade to "space what is there", never to padding silence that is not a
    pause. There are three runs in this read; asking for ten cannot produce more than three."""
    spaced = pacing.set_internal_pauses(ragged_read(), 10, 0.55)
    assert len(internal_runs(spaced)) == 3


def test_silence_end_to_end_is_returned_unchanged():
    """A dead render is a rendering failure, not something to pad. Same posture as
    `normalize_edges`: below the plausibility floor, trust the input over the detector."""
    dead = quiet(2.0)
    assert pacing.set_internal_pauses(dead, 2, 0.55) == dead
    assert pacing.set_internal_pauses(b"", 2, 0.55) == b""


def test_the_cold_open_pause_sits_between_a_paragraph_break_and_a_story_change():
    """Why the shipped length is what it is, expressed as the property that chose it.

    A headline boundary inside the cold open is a bigger event than one speaker's paragraph break
    (`same_speaker`) and a smaller one than the show moving to a different story
    (`story_change`), which gets a sting as well. Anything outside that window is either
    inaudible against the read or promotes the preview into the show's biggest structural beat.
    """
    policy = pacing.SHOW_POLICY
    assert policy.same_speaker < pacing.COLD_OPEN_PAUSE_SECONDS < policy.story_change


def test_the_cold_open_pause_is_clearly_longer_than_flux_leaves_on_its_own():
    """It has to be audibly different from the defect. The largest pause on the real merged read
    is 0.22s, so a value near it would ship the same "touch too fast" read with extra code."""
    assert pacing.COLD_OPEN_PAUSE_SECONDS >= 2 * BOUNDARY_2


# --- how many pauses a cold open has ----------------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("One thing broke. Another thing shipped. And a third thing happened.", 2),
    ("Today: A thing broke. Another shipped. And a third happened.", 2),
    ("Only one story today.", 0),
    ("", 0),
    ("A question? An exclamation! And a full stop.", 2),
    ('He called it "done." Nobody agreed. And the thread ran to four hundred.', 2),
])
def test_cold_open_pause_count_counts_the_gaps_between_sentences(text, want):
    """N sentences have N-1 gaps between them. The trailing full stop is not a pause: it is where
    the read ends, and the pacing gap after the segment owns that boundary. PanelWriter's
    "Today:" lead-in is part of its first sentence, not a sentence of its own.
    """
    assert writers.cold_open_pause_count(text) == want


def test_the_panel_writers_real_cold_open_is_counted_correctly():
    """Against the writer rather than a hand-written string, so a rewritten cold open cannot
    quietly change the count this depends on. Three headlines, so two pauses."""
    cast = Cast(anchor=Desk(role="anchor", name="Alexis", voice_id="v1", beat="hosts",
                            persona="warm"),
                desks=[Desk(role="cohost", name="Wade", voice_id="v2", beat="second chair",
                            persona="curious")],
                default_role="cohost")
    stories = [Story(id=i, title=f"Story number {i}", url="https://example.com", points=10 * i,
                     author="a", num_comments=1, rank=i, kids=[]) for i in (1, 2, 3)]
    segs = (pipeline._intro_segments(cast, date(2026, 8, 20))
            + writers.PanelWriter().write(stories, None, [], cast, "frontpage",
                                          date(2026, 8, 20)))
    i = writers.cold_open_index(segs)
    assert writers.cold_open_pause_count(segs[i].text) == len(stories) - 1


# --- the seam: only the cold open, found the one supported way ---------------------------------

def _script():
    """The top of a real show: fixed intro, merged cold open, then coverage."""
    return [
        ScriptSegment(order=0, role="anchor", speaker_key="Haley", text="Welcome.",
                      desk="anchor", source_hn_id=None),
        # source_hn_id 0 is the placeholder the prompt asks for: the cold open previews every
        # story and is the coverage of none, and 0 is falsy so `cold_open_index` still sees it
        # as untagged.
        ScriptSegment(order=1, role="anchor", speaker_key="Haley",
                      text="A thing broke. Another shipped. And a third happened.",
                      desk="anchor", source_hn_id=0),
        ScriptSegment(order=2, role="desk", speaker_key="Priya", text="So the thing.",
                      desk="ai", source_hn_id=101),
    ]


def test_only_the_cold_open_is_spaced():
    segs = _script()
    raw = [ragged_read(), ragged_read(), ragged_read()]
    out = pipeline._space_cold_open(segs, raw, log=lambda *a: None)

    assert out[0] == raw[0], "the fixed intro is one sentence of signature copy, not a list"
    assert out[2] == raw[2], "a desk take's breathing is not the cold open's problem"
    assert sorted(internal_runs(out[1]))[-2:] == [
        pytest.approx(pacing.COLD_OPEN_PAUSE_SECONDS, abs=0.01),
        pytest.approx(pacing.COLD_OPEN_PAUSE_SECONDS, abs=0.01)]


def test_the_raw_render_is_not_mutated():
    """`_finalize` caches the raw PCM as the episode's archive before this runs, and a later
    re-pace has to start from what the renderer actually returned."""
    segs, raw = _script(), [ragged_read(), ragged_read(), ragged_read()]
    before = list(raw)
    pipeline._space_cold_open(segs, raw, log=lambda *a: None)
    assert raw == before


def test_an_episode_with_no_cold_open_passes_straight_through():
    """`custom.py` can reach the render with an empty story pick, and then there is no cold open
    at all. `cold_open_index` returns None and this must not guess at index 1."""
    segs = [_script()[0], _script()[2]]
    raw = [ragged_read(), ragged_read()]
    assert pipeline._space_cold_open(segs, raw, log=lambda *a: None) == raw


# --- the whole chain: what actually lands in the published audio -------------------------------

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@needs_ffmpeg
def test_finalize_publishes_the_spaced_cold_open_and_archives_the_raw_one(tmp_path, monkeypatch):
    """The seam, end to end. Two things have to be true at once, and they pull against each other:
    the audio that ships is spaced, and the per-segment cache still holds exactly what Flux
    returned. The cache is the archive every later re-pace and recast starts from, so a spaced
    render baked into it could never be undone."""
    from hn_radio import config as cfg, status

    monkeypatch.setattr(cfg, "EPISODES_DIR", tmp_path)
    for name in ("begin", "stage", "done"):
        monkeypatch.setattr(status, name, lambda *a, **k: None)

    segs = _script()
    for s in segs:
        s.voice_id = "flux-haley-en"
    raw = [tone(1.0), ragged_read(), tone(1.5)]
    episode = pipeline._finalize(
        segs, raw, episode_id="test-ep", title="t",
        source_items=[{"hn_id": 101, "title": "s", "url": "u"}],
        edition="frontpage", with_music=False, log=lambda *a, **k: None)

    with wave.open(str(tmp_path / "test-ep" / "episode.wav"), "rb") as w:
        written = w.readframes(w.getnframes())
    spaced = pacing.set_internal_pauses(raw[1], 2)
    assert pacing.normalize_edges(spaced) in written, \
        "the cold open that shipped is not the spaced one"
    assert pacing.normalize_edges(raw[1]) not in written, \
        "the as-rendered cold open still ships"
    assert (tmp_path / "test-ep" / "segments" / "1.pcm").read_bytes() == raw[1]
    assert episode.duration_seconds > 0
