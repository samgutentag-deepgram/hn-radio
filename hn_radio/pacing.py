"""Pacing - how much air sits between two spoken segments.

Why this exists: the show inserted one fixed `GAP_SECONDS` at every boundary, which is a
monologue tell. Two things were wrong with that, and only one of them was the number.

1. Flux bakes its own silence into every rendered segment, and it is NOT symmetric. Measured at
   SILENCE_THRESHOLD across the 2026-08-03 and 2026-08-04 caches (n=38 segments), the tail runs a
   median 0.25s, p10-p90 0.11-0.32. The lead-in runs a median 0.02s, p90 0.08. The renderer pads
   the END of a line and barely touches the start. So a 0.45s configured gap was really
   tail + 0.45 + the next segment's lead: a median 0.73s of dead air per boundary, ranging 0.50 to
   1.16, and swinging up to 0.52s from one boundary to the next. Conversational turn transitions in
   real speech sit nearer 0.20s, so the show was running about 3.6x a natural turn.

   CORRECTED 2026-08-25. This block used to claim 0.22-0.29s of lead-in, 0.98s of total dead air,
   and 0.00-0.43s of segment-to-segment variation. The tail figure held up. The lead-in was over
   ten times too high, the 0.43 does not reproduce as any measure of this cache, and both numbers
   had already been copied into a corporate blog draft before anyone re-derived them. The asymmetry
   is the interesting part and the original wording hid it. Re-measure from the cache, not from
   this comment.
2. A single value cannot be right everywhere. The pause a listener wants between two people
   trading a thought is not the pause they want when the show changes topic.

So pacing has two halves, and both are opt-in. `normalize_edges` makes each segment's own silence
a known quantity, so the inserted gap IS the gap. `gap_plan` then picks a gap per boundary from
the script's own structure (who is speaking, about what) rather than using one constant.

This module deliberately knows about segments and nothing about WAV files; `stitch` knows about
audio and nothing about scripts. The list of per-boundary gaps is the only thing that crosses,
and it must be computed ONCE and handed to both `stitch.stitch` and `stitch.segment_start_times`,
or the page's seek buttons, the chapter marks, and the VTT will drift out of sync with the audio.
"""

from __future__ import annotations

import array
from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import config

# |sample| at or below this counts as silence. 250/32767 is about -42 dBFS, comfortably above
# the renderer's noise floor and well below anything audible as speech.
SILENCE_THRESHOLD = 250

# Silence left at each edge after normalizing. Not zero: a hard cut at the first audible sample
# clips the onset of a plosive and sounds like a dropped word.
EDGE_SECONDS = 0.06

# A segment must keep at least this much audio; below it, assume the detector is wrong and do
# nothing rather than trim a short line down to nothing.
MIN_KEEP_SECONDS = 0.10


@dataclass(frozen=True)
class GapPolicy:
    """Gap in seconds for each kind of boundary between two consecutive segments."""

    name: str
    note: str
    exchange: float          # two different speakers, same topic: the conversational turn
    same_speaker: float      # one speaker continuing; a paragraph break, not a turn
    into_comment: float      # a desk hands into a performed HN comment
    out_of_comment: float    # a performed comment lands and someone reacts
    story_change: float      # the show moves to a different story
    show_boundary: float     # after the fixed intro, before the fixed outro
    normalize_edges: bool = True  # False reproduces today's raw, unmanaged segment edges


def boundary_kind(a, b, story_ids: Optional[set] = None) -> str:
    """Classify the boundary between segments `a` and `b`.

    `story_ids` is the set of hn ids that are stories (not comments), from the episode's
    source_items. Without it, any change in `source_hn_id` between two non-comment segments is
    treated as a story change, which is close enough for scripts that tag lines consistently.
    """
    if a.role == "commenter":
        return "out_of_comment"
    if b.role == "commenter":
        return "into_comment"
    if _is_story_change(a, b, story_ids):
        return "story_change"
    if a.speaker_key == b.speaker_key:
        return "same_speaker"
    return "exchange"


def _is_story_change(a, b, story_ids: Optional[set]) -> bool:
    ida, idb = a.source_hn_id, b.source_hn_id
    if ida is None or idb is None:
        return False  # an untagged line (the fixed intro/outro) is handled as a show boundary
    if story_ids is not None and not (ida in story_ids and idb in story_ids):
        return False
    return ida != idb


def gap_plan(segments: Sequence, policy: GapPolicy,
             story_ids: Optional[set] = None) -> List[float]:
    """One gap per boundary: `len(segments) - 1` values, aligned to the gaps `stitch` inserts.

    The first and last boundaries are show boundaries (the fixed intro and outro are the show's
    signature, not part of the conversation, so they get a beat of their own regardless of who
    speaks on either side).
    """
    n = len(segments)
    if n < 2:
        return []
    gaps: List[float] = []
    for i in range(n - 1):
        a, b = segments[i], segments[i + 1]
        if i == 0 or i == n - 2:
            gaps.append(policy.show_boundary)
            continue
        gaps.append(getattr(policy, boundary_kind(a, b, story_ids)))
    return gaps


def _silence_run(samples: array.array, reverse: bool = False) -> int:
    """Count leading (or trailing) samples at or below the silence threshold."""
    seq = reversed(samples) if reverse else samples
    n = 0
    for v in seq:
        if abs(v) > SILENCE_THRESHOLD:
            break
        n += 1
    return n


def normalize_edges(pcm: bytes, edge_seconds: float = EDGE_SECONDS) -> bytes:
    """Make a segment start and end with exactly `edge_seconds` of silence.

    Trims whatever the renderer baked in and pads back to a fixed amount, so the gap the stitcher
    inserts is the whole of the pause a listener hears. Returns `pcm` unchanged if the segment is
    too short or reads as silence end to end.
    """
    width = config.SAMPLE_WIDTH
    usable = len(pcm) - (len(pcm) % width)
    if usable <= 0:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[:usable])

    lead = _silence_run(samples)
    if lead == len(samples):
        return pcm  # all silence: nothing to align to
    tail = _silence_run(samples, reverse=True)
    body = samples[lead: len(samples) - tail]
    if len(body) < int(config.SAMPLE_RATE * MIN_KEEP_SECONDS):
        return pcm  # implausibly short; trust the original over the detector

    edge = b"\x00" * (int(config.SAMPLE_RATE * edge_seconds) * width)
    return edge + body.tobytes() + edge


# --- the silence INSIDE one rendered segment ---------------------------------------------------
#
# `normalize_edges` above manages a segment's edges and `gap_plan` manages the air between
# segments. Neither can reach the pauses Flux leaves between the SENTENCES of a single render,
# and the cold open is one render on purpose: it is one TTS call, so the whole preview is a
# single continuous read with no per-headline onset or sentence-final fall (see the note above
# `writers.cold_open_index` for why re-splitting it is the one repair known not to work).
#
# The cost of that merge is that Flux runs the headlines together. Measured on the real
# 2026-08-20 cold-open renders joined into one read: the silence runs inside it are 0.09, 0.13
# and 0.22 seconds, so the largest gap between two headlines is 0.22s while a mid-headline breath
# is 0.13s. Sam's verdict on the merged read was "maybe a touch too fast", and separately that the
# cold open should be "as matter-of-fact as possible. We are just reporting information."

# A run shorter than this is a zero crossing between two phonemes, not a pause. Without the guard
# the sample-level scan finds hundreds of "runs" in one line and stretching any of them destroys
# the audio. Set well under the shortest real inter-sentence pause measured above (0.13s).
MIN_INTERNAL_RUN_SECONDS = 0.08

# What each headline boundary inside the cold open is set to.
#
# Chosen against the show's own gap policy rather than by taste. A headline boundary is a bigger
# event than one speaker's paragraph break (`CONVERSATIONAL.same_speaker`, 0.22) and a smaller one
# than the show moving to a different story (`CONVERSATIONAL.story_change`, 0.85, which also gets
# a sting): the cold open is a list being read, not the show changing subject. 0.55 is within
# 0.015 of the midpoint of those two, and it is 2.5x the largest pause Flux leaves on its own, so
# the change is unmistakable rather than a refinement of the defect.
#
# The cost is accountable: two boundaries at 0.55 add 0.75s to an 11.84s cold open (0.22 -> 0.55
# and 0.13 -> 0.55), about 6% of the preview and 0.2% of a 5.5 minute show.
#
# 0.40 and 0.70 were the other two candidates auditioned by `scripts/coldopen_spacing.py`. 0.40
# only adds 0.18s to the largest pause Flux already leaves, and it merely matches `into_comment`
# for an event that is structurally larger than one; 0.70 is within 0.15 of `story_change`, close
# enough that the preview starts to sound like three separate items rather than one list. Neither
# was recorded as the pick, so this is the reasoned middle: a by-ear call to overturn, not an
# arithmetic one.
COLD_OPEN_PAUSE_SECONDS = 0.55


def internal_silence_runs(pcm: bytes,
                          min_run_seconds: float = MIN_INTERNAL_RUN_SECONDS) -> List[tuple]:
    """`(start, end)` sample indices of every silence run INSIDE `pcm`, longest-run order aside.

    A run touching either edge is excluded: those belong to `normalize_edges`, and a segment that
    reads as silence end to end therefore yields nothing at all rather than one enormous "pause".
    """
    width = config.SAMPLE_WIDTH
    usable = len(pcm) - (len(pcm) % width)
    if usable <= 0:
        return []
    samples = array.array("h")
    samples.frombytes(pcm[:usable])

    n = len(samples)
    floor = min_run_seconds * config.SAMPLE_RATE
    out, i = [], 0
    while i < n:
        if abs(samples[i]) > SILENCE_THRESHOLD:
            i += 1
            continue
        j = i
        while j < n and abs(samples[j]) <= SILENCE_THRESHOLD:
            j += 1
        if i != 0 and j < n and (j - i) >= floor:
            out.append((i, j))
        i = j
    return out


def set_internal_pauses(pcm: bytes, count: int,
                        seconds: float = COLD_OPEN_PAUSE_SECONDS) -> bytes:
    """Set the `count` LONGEST silence runs inside `pcm` to exactly `seconds` each.

    The exact inverse of a ceiling on internal silence: this makes pauses one fixed, even length
    rather than trimming the long ones. Evenness is most of what reads as matter-of-fact, because
    an even pause makes a list scan as a list; a floor alone would leave 0.13 and 0.22 ragged,
    just less ragged. Ported from `scripts/coldopen_spacing.space_boundaries`, which is where the
    approach was established by ear.

    Targeting the longest runs rather than every run is what keeps this honest. A three-headline
    cold open has two sentence boundaries plus a handful of shorter intra-sentence breaths, and
    padding all of them would open a hole in the middle of a headline. Verified against the
    2026-08-07 merged render: the two longest runs (0.22s at 3.21s, 0.13s at 8.25s) sit within a
    half second of the boundaries predicted from word counts (3.67s, 8.27s), while every other
    run is intra-sentence. The margin is thin by construction (0.13 boundary vs a 0.12 breath on
    2026-08-20), so `count` must come from the read's own sentence count and not from a threshold.

    Separator punctuation was tried first and does not work: across five separators (space,
    newline, blank line, ellipsis, dash) the boundary pauses ranged 0.08-0.40s with no separator
    producing two even ones. Flux does not take pause direction from punctuation here, which is
    why this happens on rendered PCM instead of in the script.

    Idempotent, and returns `pcm` untouched when there is nothing to space: `count` of zero (a
    one-sentence cold open), a segment with no internal runs, or an empty buffer.
    """
    if count < 1:
        return pcm
    runs = internal_silence_runs(pcm)
    if not runs:
        return pcm

    # Ties do not matter: two runs of the same length become the same length either way.
    targets = {start for start, _ in sorted(runs, key=lambda r: r[1] - r[0], reverse=True)[:count]}
    want = array.array("h", [0]) * int(seconds * config.SAMPLE_RATE)

    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) - (len(pcm) % config.SAMPLE_WIDTH)])
    out, prev = array.array("h"), 0
    for start, end in runs:
        out.extend(samples[prev:start])
        out.extend(want if start in targets else samples[start:end])
        prev = end
    out.extend(samples[prev:])
    return out.tobytes()


# The control: exactly what the show does today. Kept as a named policy so a comparison render
# of the current sound goes through the same code path as the candidates it is judged against.
UNIFORM = GapPolicy(
    name="uniform",
    note="Today's shipping sound: one fixed 0.45s gap, renderer silence left as-is.",
    exchange=config.GAP_SECONDS, same_speaker=config.GAP_SECONDS,
    into_comment=config.GAP_SECONDS, out_of_comment=config.GAP_SECONDS,
    story_change=config.GAP_SECONDS, show_boundary=config.GAP_SECONDS,
    normalize_edges=False,
)

# Isolates one variable against UNIFORM: same flat rhythm everywhere, just with the renderer's
# unmanaged silence removed and the total pause cut to roughly 0.40s. If this alone reads as
# conversational, the problem was air, not sameness.
TIGHT = GapPolicy(
    name="tight",
    note="Flat rhythm, managed edges, ~0.40s of real pause everywhere.",
    exchange=0.28, same_speaker=0.28, into_comment=0.28, out_of_comment=0.28,
    story_change=0.28, show_boundary=0.28,
)

# Isolates the second variable against TIGHT: same managed edges, but the gap now varies with
# what the boundary means. Turns inside an exchange run near natural conversational timing;
# topic changes get a real beat, so the contrast reads as structure rather than a stutter.
CONVERSATIONAL = GapPolicy(
    name="conversational",
    note="Managed edges plus a gap chosen per boundary: fast inside an exchange, "
         "a real beat at a story change.",
    exchange=0.16, same_speaker=0.22, into_comment=0.40, out_of_comment=0.30,
    story_change=0.85, show_boundary=0.90,
)

POLICIES = {p.name: p for p in (UNIFORM, TIGHT, CONVERSATIONAL)}

# The policy the show ships. One show, one rhythm: every render path reads this, so changing it
# changes how tomorrow's episode sounds AND how an old episode rebuilt from cache sounds. An
# episode rebuilt after a change will not byte-match the audio that was published under the old
# value; its chapter marks are recomputed at the same time, so it stays internally consistent.
SHOW_POLICY = CONVERSATIONAL


def story_ids_from(source_items) -> set:
    """The set of hn ids that are stories, from an episode's `source_items`.

    Comments carry an hn id too, so without this set a desk line and the comment it hands into
    look like a story change. Tolerates items with no id rather than raising, because older
    episode.json files predate the field.
    """
    return {it["hn_id"] for it in (source_items or []) if it.get("hn_id") is not None}


def apply(segments: Sequence, pcm: Sequence[bytes], policy: Optional[GapPolicy] = None,
          story_ids: Optional[set] = None):
    """Pace an episode. Returns `(pcm_to_stitch, gaps)` -- the ONLY supported way to do this.

    Both halves of pacing come out of one call on purpose. The gaps must reach `stitch.stitch`
    and `stitch.segment_start_times` identically, and the normalized PCM must be what BOTH of
    them measure, or the offsets describe audio that was never written. Two separate calls made
    that a thing a caller had to remember; one return value makes it a thing they cannot get
    wrong. `pcm` is never mutated, so the caller still holds the raw renderer output to cache.
    """
    policy = policy or SHOW_POLICY
    paced = [normalize_edges(p) for p in pcm] if policy.normalize_edges else list(pcm)
    return paced, gap_plan(segments, policy, story_ids)
