"""Music - the show's theme, placed against the episode rather than mixed over it.

Why this is its own module, and why it looks like `pacing`: inserting music changes where every
later line lands. `episode.json`'s `start_seconds` is what the chapter marks, the VTT, and the
page's seek buttons are all derived from, so audio placed one way and measured another is not an
error, it is silently wrong audio. `pacing.apply` solved that by returning the paced PCM and the
gap list from ONE call so they cannot diverge. Music has the same problem and one more on top:
the list handed to `stitch` now contains pieces that are NOT script segments (cues, and a merged
cold-open piece), so a caller can no longer read segment offsets off the list it stitches.

So `apply` returns a third thing: `starts`, one value per ORIGINAL segment, already correct.
The caller sets `seg.start_seconds` from that and never calls `stitch.segment_start_times`
itself. Those starts are computed by running `stitch.segment_start_times` over the exact pieces
and gaps that get concatenated, so the offsets are measured from the same arithmetic that writes
the file rather than from a parallel model of it.

The editorial decisions, made by ear:

* A cue at the top (intro), one at each story's FIRST segment (sting), one at the end (outro).
  First occurrence, not "the id changed": see `_sting_boundaries` for why that distinction is
  load-bearing rather than pedantic.
* A bed under the cold open ONLY. Never under a desk take. The voice is the product this demo
  sells, so nothing sits on top of it except at the very top of the show.
* Levels are relative to THIS EPISODE'S OWN SPEECH, never a fixed multiplier. Two tracks are
  never the same loudness and neither are two casts, so a gain chosen against one recording is
  meaningless against another. A sting sits `STING_DB` under the episode's speech RMS; the bed
  sits `BED_DB` under it.

A missing or unreadable track logs and returns the un-musicked audio. A missing asset must never
take the nightly show off the air.
"""

from __future__ import annotations

import array
import math
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from . import config, pacing, stitch

# The show theme. Committed; see assets/music/ATTRIBUTION.md for provenance and licence.
MUSIC_DIR = config.PROJECT_ROOT / "assets" / "music"
SHOW_TRACK = MUSIC_DIR / "sub_clair-ambient-instrumental-579510.mp3"

# --- levels, in dB relative to the episode's own speech RMS ---
STING_DB = -6.0    # a cue between stories: present, clearly quieter than a voice
BED_DB = -26.0     # under the cold open: felt, not heard, and never competing with the words

# --- cue lengths, seconds ---
INTRO_SECONDS = 7.0
STING_SECONDS = 2.0
OUTRO_SECONDS = 5.0

# --- where in the track each cue is lifted from ---
# Chosen from the track's own amplitude envelope, not picked round numbers: a cut into dense,
# near-peak material with no decrescendo starts or ends mid-phrase, and no fade length repairs
# that. The fade was papering over a bad cut point, not a bad fade length.
#
# Re-measured 2026-08-20 at a 10 ms hop over 50 ms windows. The first pass used half- and
# quarter-second buckets, which is coarser than the events being looked for, and it got the
# track's shape wrong. What is actually there, in 0.5s RMS bands:
#
#   0.0-7.0     the track's own fade-in, 11 -> 5167
#   7-79        busy: 6000-9000 throughout, with one shallow dip at 19-21.4 (5666 -> 2316)
#   79-98       a sparse BREAKDOWN: 900-4100, with three isolated hits (84.0, 86.4, 88.8) each
#               blooming to 5000-6500 and then decaying over 1.5-2s back down to ~1000
#   98-117      mid-density, 4200-6600
#   117-155.5   busy again, 8000-9000
#   156-171     a long sparse tail, 2000 down to ~200
#   171.5-177   the composer's ending, decaying to silence
#
# The previous version of this comment claimed the track had exactly two natural decays and was
# continuously busy everywhere else. That is wrong, and it is why the sting was bad: the 79-98s
# breakdown is an entire section of natural decays and was never searched.
#
# INTRO_AT = 0.0: the track fades ITSELF in over its first two seconds (RMS 11 -> 125 -> 254 ->
# 5167 at 2.0s). Starting the intro at 0.0 rides that built-in fade-in, so the show opens the
# way the track does rather than clipping into a cue already at full volume.
#
# STING_AT = 85.8, STING_SECONDS = 2.0: a swell and a decay, which is what a sting is. The
# window opens in the quiet tail of the 84.0s hit (100 ms bands: 919, 858, 1054, 1335, 1192,
# 1603), rides the 86.4s hit blooming to 5622, then decays continuously for 1.4s - 4218, 3332,
# 2671, 2155, 1901, ... 1329, 1313, 1285 - and cuts out at 87.8 into more quiet material (1125,
# 1012), so nothing is chopped on the way in or on the way out. FADE_IN_SECONDS now lands on
# material that is already soft, and STING_FADE_OUT takes over at 0.95s in, partway down that
# continuous decay, so both fades continue the music's own shape rather than overriding it.
#
# Measured on the SHAPED cue (post-fade, post-level), as the RMS of its first and last 150 ms
# over the RMS of the whole cue. That is the property
# `test_the_sting_opens_and_closes_soft_on_the_real_track` pins, because pinning the offset
# alone would only restate the constant:
#
# Both readings below are at the shipping STING_FADE_OUT of 1.05:
#
#                   head 150 ms   tail 150 ms
#   OLD 19.6 / 1.8        0.339         0.059
#   NEW 85.8 / 2.0        0.108         0.052
#
# The OLD offset, kept here as the record, did not decay at all where it started: 100 ms bands
# from 19.6 read 5416, 5516, 5022, 5390, 5906, flat at near-phrase level. That is the mid-attack
# entry a listener hears as a cue cut out of the middle of something, and the earlier comment's
# "RMS drops 5599 -> 5388 -> 5512" was a half-second bucket reading of that same flat stretch,
# not a decay. Its ending was worse: the fade-out and the music's own drop at 21.0 collided and
# took the shaped envelope 1066 -> 320 in one 50 ms step. 19.6 also cannot simply be stretched
# to 2.0s, because the next phrase hits at 21.5 (4296 -> 13047), so a 2.0s window there would
# end ON an attack. The much older offset (60.0) sat in the busy region with no decay at all.
#
# A sting-specific fade-in was measured and rejected: at 0.20s the shaped head 150 ms rises to
# 0.182 of the cue's RMS, so the shared FADE_IN_SECONDS of 0.35s is already the gentler of the
# two and splitting the constant would buy nothing. STING_FADE_OUT is a different story: it
# shipped at 0.8 with this window and Sam heard that as snapping off, so it is now 1.05. Shaped
# tail 150 ms against the cue's RMS, sweeping the constant alone: 0.5 -> 0.102, 0.8 -> 0.066,
# 1.05 -> 0.052, 1.2 -> 0.047. The test's tail bound is 0.06 precisely so that a revert to 0.8
# fails rather than passing quietly.
#
# The cost of this window, on the record: a percussive bloom has a higher crest factor than a
# flat stretch, 7.75 against the old 3.79, so the shaped sting reaches full scale once the
# episode's speech RMS passes ~8436 (-11.8 dBFS) rather than ~17233. That was not a new class of
# risk - the outro cue ships at crest 10.48 and would have reached it first, above speech RMS
# 6241 - and nothing here touches STING_DB. Both are now caught by CUE_PEAK_CEILING, which backs
# the gain off and logs it instead of clamping; see that constant for the measurements.
#
# OUTRO_AT = 171.5, OUTRO_SECONDS = 5.0: the composer's own ending. RMS decays 508 -> 428 -> 315
# -> ... -> 82 -> 38 -> 15 -> 10 from 171.5s to 176.0s, fully silent by 177s. This is the track
# actually finishing, not a cut dressed up with a fade. The OLD offset (120.0) was also in a
# busy region - same mistake as the old sting offset, for the same reason.
INTRO_AT = 0.0
BED_AT = INTRO_AT + INTRO_SECONDS
# BED_AT lands at 7.0s now that the intro is 7s, which is mid-phrase in the busy region - but
# cut points only matter for a cue that is heard on its own. The bed sits at BED_DB (-26 dB)
# under a voice; nobody will ever hear the bed's own attack, so an "unmusical" bed start point
# is a deliberate non-issue, not an oversight matching the sting/outro fix above.
STING_AT = 85.8
OUTRO_AT = 171.5

# Air on either side of a cue. Replaces the pacing gap at a boundary a sting lands on: the sting
# IS the beat there, so keeping the story-change pause as well would leave a hole around it.
# Tightened 2026-08-08 by ear (0.30 -> 0.16): a daily recap wants punctuation, not ceremony, and
# the first pass read as slow and metered. STING_SECONDS came down from 2.0 for that same reason
# and went back to 2.0 on 2026-08-20: what read as slow was the old excerpt sitting on a flat
# near-peak plateau for a full second, not the two seconds itself, and the new excerpt spends
# the extra 0.2s decaying. CUE_GAP_SECONDS stays where it is.
CUE_GAP_SECONDS = 0.16

# How much of the show the bed covers. Segment 0 is the fixed intro and the writer's cold open
# follows it (see `pipeline._intro_segments` and the COLD OPEN instruction in `writers`), so the
# opening is the first TWO segments. Detected structurally on purpose: nothing here reads the
# words, so a rewritten cold open cannot silently move the bed.
BED_SEGMENTS = 2
BED_TAIL_SECONDS = 1.2  # bed keeps playing past the last cold-open word, then fades out

# A single FADE_SECONDS served every cue's fade-in AND fade-out until 2026-08-08. That was the
# bug: a cue arriving is a different event from a cue leaving, and "leaving" is not one event
# either. The intro OPENS the show, so a fast tail feels clipped; a sting PUNCTUATES a beat
# between stories, so it wants a quick tail that gets out of the way; the outro CLOSES the
# show, so it earns the longest, gentlest tail of the three. Heard by ear: the stings "ramp
# down too sharply and quickly" at the old shared 0.35s. Fade-IN stays one shared, short value -
# a cue arriving fast reads as an entrance for all three, so nothing about arriving needed
# splitting the way leaving did.
FADE_IN_SECONDS = 0.35
INTRO_FADE_OUT = 2.0   # opens the show: give it room to breathe, not a clipped ending
# Widened 0.80 -> 1.05 on 2026-08-20 by ear: Sam, on the first two-person render, "it still feels
# like it snaps off a bit too quickly." The excerpt itself was already right, so this is the
# landing rather than the cut. The fade now starts 0.95s in instead of 1.20s, which is slightly
# ahead of where the music's own decay flattens, so it leads the decay out instead of following
# it. Measured on the shaped cue as the RMS of its last 150 ms over the whole cue: 0.066 -> 0.052,
# a fifth softer, while the head is untouched at 0.104 -> 0.108. 1.2 was also tried and reaches
# 0.047, but it starts the fade at 0.80s, before the bloom has finished, which costs the cue the
# body that makes it read as punctuation at all.
STING_FADE_OUT = 1.05  # punctuates a beat, then hands off: long enough not to snap
OUTRO_FADE_OUT = 3.0   # closes the show: the longest, gentlest tail of the three

# RMS is measured on a strided sample rather than every sample: an episode is millions of samples
# and this runs on every render. Odd stride so it cannot lock to a periodic waveform.
RMS_SAMPLE_CAP = 200_000

# The loudest sample a shaped cue is allowed to reach. Headroom protection, not a level: a cue
# that fits is not touched.
#
# Why it is needed. Cues are levelled by RMS, and RMS says nothing about peaks, so a cue's loudest
# sample lands at `target_rms * crest factor`. Measured on the committed track at the shipped
# offsets and fades:
#
#   cue      crest   full scale at speech RMS
#   intro     5.49   11909   (-8.8 dBFS)
#   sting     8.01    8164   (-12.1 dBFS)
#   outro    10.48    6241   (-14.4 dBFS)
#
# -14.4 dBFS is an ordinary loudness for a TTS master, so the outro is one hotter cast away from
# reaching full scale. Today's render measured 5174, which is why this was latent and not live.
#
# `_shape` used to clamp each sample to int16 and stop there. That is flat-topping: it squares off
# every peak that does not fit, which distorts the one cue in the show a listener hears with no
# voice over it, and it left NOTHING in the log -- the only symptom was the sound.
#
# Backing the gain off instead trades level for waveform, which is the right way round: a cue two
# dB quieter than STING_DB asked for is inaudible against a show whose own speech moves more than
# that between episodes, and nothing downstream measures a cue's level. The alternative -- lower
# STING_DB so no cast can ever clip -- was rejected: it would quieten every cue on every episode
# to insure against a level no episode has hit yet, and those constants were tuned by ear.
CUE_PEAK_CEILING = 32767


def decode(path) -> bytes:
    """Decode an audio file to the pipeline's format: mono, 16-bit, `config.SAMPLE_RATE`.

    Returns b"" rather than raising if the file is absent, ffmpeg is absent, or the decode fails.
    ffmpeg is already a hard dependency of the publish path (`chapters.to_mp3_with_chapters`), so
    this adds a subprocess, not a dependency.
    """
    path = Path(path)
    if not path.exists():
        return b""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a",
             "-ac", str(config.CHANNELS), "-ar", str(config.SAMPLE_RATE),
             "-f", "s16le", "-acodec", "pcm_s16le", "-"],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return b""
    if proc.returncode != 0:
        return b""
    return proc.stdout


def _samples(pcm: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) - (len(pcm) % config.SAMPLE_WIDTH)])
    return a


def _rms(samples: Sequence[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(v * v for v in samples) / len(samples))


def speech_rms(pcm_segments: Sequence[bytes]) -> float:
    """RMS of the episode's own speech, ignoring the silence between and inside the takes.

    The reference every music level is set against. Silence is excluded (same threshold `pacing`
    uses to find a segment's edges) because a show with long pauses is not a quieter show, and a
    reference that counted the pauses would duck the music every time the pacing changed.
    """
    total = sum(len(p) for p in pcm_segments) // config.SAMPLE_WIDTH
    if total == 0:
        return 0.0
    stride = max(1, total // RMS_SAMPLE_CAP) | 1
    loud: List[int] = []
    for p in pcm_segments:
        loud.extend(v for v in _samples(p)[::stride] if abs(v) > pacing.SILENCE_THRESHOLD)
    return _rms(loud)


def _excerpt(track_pcm: bytes, at_seconds: float, seconds: float) -> bytes:
    """`seconds` of the track starting `at_seconds` in, wrapping around if the track is short."""
    width = config.SAMPLE_WIDTH
    n = len(track_pcm) // width
    want = int(config.SAMPLE_RATE * seconds)
    if n == 0 or want <= 0:
        return b""
    i = int(config.SAMPLE_RATE * at_seconds) % n
    out = bytearray()
    while len(out) // width < want:
        take = min(n - i, want - len(out) // width)
        out += track_pcm[i * width:(i + take) * width]
        i = (i + take) % n
    return bytes(out)


def _shape(pcm: bytes, fade_in: float, fade_out: float, target_rms: float,
           *, name: str = "cue", log: Optional[Callable[[str], None]] = None) -> bytes:
    """Fade the ends, then set the whole cue's RMS to `target_rms`, backing off if it would clip.

    Level is set AFTER the fades on purpose, so "6 dB under the speech" describes the audio that
    actually airs rather than a number the fades then quietly walked away from.

    The RMS target is a REQUEST, and `CUE_PEAK_CEILING` is what it is checked against: if the
    requested gain would push this excerpt's loudest sample past full scale, the gain drops to
    whatever lands that sample on the ceiling and the cue plays quieter than `STING_DB` asked
    for. `log` is how that stops being silent, which was the actual defect -- clamping already
    kept the bytes legal, it just destroyed the waveform to do it.

    `name` is only for that message. `log=None` is silent, for the callers that are not the
    render path.

    REVIEWED 2026-08-22 and DELIBERATELY KEPT. A prune pass proposed making `log` required and
    dropping the one-line `if log:` guard below. Declined: `ad_bracket` passes `log=log` straight
    through and its own parameter is `Optional`, so `ad_bracket(rms, log=None)` is a legal call
    today and would become a TypeError. The proposal came with "must be paired with narrowing
    ad_bracket", which is the tell -- a change that needs a second change to stop it breaking a
    working path is not a simplification, it is a behaviour change that saves one line.
    """
    a = _samples(pcm)
    n = len(a)
    if n == 0:
        return b""
    n_in = min(int(config.SAMPLE_RATE * fade_in), n)
    n_out = min(int(config.SAMPLE_RATE * fade_out), n - n_in)
    for i in range(n_in):
        a[i] = int(a[i] * (i / n_in))
    for i in range(n_out):
        a[n - 1 - i] = int(a[n - 1 - i] * (i / n_out))
    current = _rms(a)
    if current <= 0 or target_rms <= 0:
        return b"\x00" * (n * config.SAMPLE_WIDTH)
    gain = target_rms / current

    # Peak measured on the FADED samples, because that is what the gain multiplies. Measuring the
    # raw excerpt would over-protect a cue whose loudest moment sits inside a fade.
    peak = max(abs(v) for v in a)
    if peak * gain > CUE_PEAK_CEILING:
        fits = CUE_PEAK_CEILING / peak
        if log:
            log(f"[music] {name} cue attenuated {20 * math.log10(fits / gain):.1f} dB below "
                f"its {20 * math.log10(gain):+.1f} dB target: at this episode's speech level it "
                f"would have peaked {peak * gain / CUE_PEAK_CEILING:.2f}x over full scale")
        gain = fits

    # The clamp stays. `fits` puts the largest sample ON the ceiling in exact arithmetic, and
    # int() of a float product can still land a unit past it; this is rounding insurance now
    # rather than the only thing standing between a hot cue and a squared-off waveform.
    for i in range(n):
        a[i] = max(-32768, min(32767, int(a[i] * gain)))
    return a.tobytes()


def _mix(base: bytes, over: bytes) -> bytes:
    """Add `over` on top of `base`, keeping `base`'s length exactly.

    Length is preserved because the bed must not move anything: it changes samples, never
    offsets, so a bed can be turned on or off without recomputing a single start time.
    """
    b = _samples(base)
    o = _samples(over)
    for i in range(min(len(b), len(o))):
        b[i] = max(-32768, min(32767, b[i] + o[i]))
    return b.tobytes()


def _seconds(n_bytes: int) -> float:
    return n_bytes / config.SAMPLE_WIDTH / config.CHANNELS / config.SAMPLE_RATE


def _sting_boundaries(segments: Sequence, story_ids: Optional[set]) -> set:
    """Boundary indices where a sting belongs.

    A sting lands at the boundary immediately BEFORE the FIRST segment that carries each story
    id in `story_ids`. First occurrence wins - the same rule `chapters.build_chapters` uses for
    its `seen_stories` set - so a later CALLBACK (a segment whose `source_hn_id` points back to a
    story already opened) can never add or move a cue: by the time the callback plays, that id is
    already in `seen` and the check that would place a sting there fails. N stories in
    `source_items` always yields exactly N stings, at the same boundaries, regardless of how many
    times the writer loops back to an earlier story. Placing a sting on every `source_hn_id`
    TRANSITION instead (the previous rule) let a callback re-open a story's id and mint a second,
    non-deterministic sting purely because the writer chose to reference it again.

    Comment ids and `0`/`None` never count, because they are never members of `story_ids`: a
    performed comment (or the cold open's placeholder id) can never win the `not in seen` check
    for a real story, so comment theater needs no special case to be excluded - it structurally
    cannot be a story's first occurrence.

    Boundary 0 (into the cold open, from the fixed intro) and the final boundary (into the fixed
    outro) are always excluded: each already has its own fixed cue, and stinging over it would
    double up on one beat rather than mark two.

    Returns the empty set if `story_ids` is falsy - no stories to key off, so no stings, rather
    than falling back to "any id change" and guessing.
    """
    if not story_ids:
        return set()
    n = len(segments)
    if n < 2:
        return set()
    last_boundary = n - 2
    seen: set = set()
    boundaries: set = set()
    for j in range(1, n):
        hn = segments[j].source_hn_id
        if hn in story_ids and hn not in seen:
            seen.add(hn)
            boundary = j - 1
            if boundary != 0 and boundary != last_boundary:
                boundaries.add(boundary)
    return boundaries


def ad_bracket(reference_rms: float, track=SHOW_TRACK,
               log: Optional[Callable[[str], None]] = print) -> Tuple[bytes, bytes]:
    """The pair of stings that would top and tail an ad slot, at `reference_rms` - `STING_DB`.

    NOT wired into the render path: the ad feature is not built. This exists so the demo can drop
    an ad in by hand and have it sound like part of the show, and so the level discipline for one
    is already decided rather than improvised on stage. Returns (b"", b"") if the track is gone.
    """
    pcm = decode(track)
    if not pcm:
        return b"", b""
    target = reference_rms * 10 ** (STING_DB / 20)
    sting = _shape(_excerpt(pcm, STING_AT, STING_SECONDS), FADE_IN_SECONDS, STING_FADE_OUT,
                   target, name="ad-bracket", log=log)
    return sting, sting


def apply(
    segments: Sequence,
    pcm: Sequence[bytes],
    gaps: Sequence[float],
    *,
    story_ids: Optional[set] = None,
    enabled: bool = True,
    track=SHOW_TRACK,
    bed_db: Optional[float] = BED_DB,
    log: Callable[[str], None] = print,
) -> Tuple[List[bytes], List[float], List[float]]:
    """Place the show's music. Returns `(pieces, piece_gaps, starts)`.

    `pieces` and `piece_gaps` go straight to `stitch.stitch`/`stitch.concat_pcm`. `starts` has
    exactly ONE value per entry in `segments` - the start time of that ORIGINAL script segment in
    the finished audio - and is the only supported way to set `seg.start_seconds` once music is
    in play. `pieces` is longer than `segments` and contains cues, so measuring it with
    `stitch.segment_start_times` would hand back offsets for the wrong things.

    Takes the ALREADY-PACED pcm and the gap plan from `pacing.apply`, because music is placed on
    the show's rhythm, not before it. Neither input is mutated.

    `bed_db=None` lays no bed while leaving the cold-open piece's structure untouched, which is
    also how the "the bed moves nothing" guarantee is testable. There was a matching
    `sting_db=None` until 2026-08-22; no caller ever passed it and silencing the cues is
    `HN_RADIO_MUSIC=0`, so the parameter was a second way to say the same thing.
    """
    pieces_in = list(pcm)
    gaps_in = [float(g) for g in gaps]
    n = len(pieces_in)
    if n == 0:
        return [], [], []

    def _plain():
        return pieces_in, gaps_in, stitch.segment_start_times(pieces_in, gaps_in)

    if not enabled:
        return _plain()

    track_pcm = decode(track)
    if not track_pcm:
        # Log, do not raise. A missing or unreadable asset costs the show its theme; raising here
        # would cost it the whole episode. Loud enough to notice in the run log, so it does not rot.
        log(f"[music] no usable track at {track}; the episode ships without music")
        return _plain()

    speech = speech_rms(pieces_in)
    sting_rms = speech * 10 ** (STING_DB / 20)
    bed_rms = speech * 10 ** (bed_db / 20) if bed_db is not None else 0.0

    def _cue(at, seconds, target, fade_out, name):
        return _shape(_excerpt(track_pcm, at, seconds), FADE_IN_SECONDS, fade_out, target,
                      name=name, log=log)

    out_pieces: List[bytes] = []
    out_gaps: List[float] = []
    # (piece index, byte offset inside that piece) for each ORIGINAL segment, in script order.
    marks: List[Tuple[int, int]] = []

    # 1. The intro cue, ahead of the first word.
    out_pieces.append(_cue(INTRO_AT, INTRO_SECONDS, sting_rms, INTRO_FADE_OUT, "intro"))

    # 2. The cold open, merged into ONE piece so a bed can lie across it and the gap inside it.
    #    A bed spans speech, so the audio it covers cannot stay split over pieces that `stitch`
    #    joins later; the gap between those lines has to exist before the mix, not after.
    n_bed = min(BED_SEGMENTS, n)
    body = bytearray()
    offsets: List[int] = []
    for i in range(n_bed):
        if i:
            body += stitch.silence(gaps_in[i - 1])
        offsets.append(len(body))
        body += pieces_in[i]
    body += stitch.silence(BED_TAIL_SECONDS)
    bed = _shape(_excerpt(track_pcm, BED_AT, _seconds(len(body))),
                 FADE_IN_SECONDS, BED_TAIL_SECONDS, bed_rms, name="bed", log=log)
    out_gaps.append(CUE_GAP_SECONDS)
    out_pieces.append(_mix(bytes(body), bed))
    for off in offsets:
        marks.append((len(out_pieces) - 1, off))

    # 3. Everything after the cold open, one piece per take, with a sting immediately before each
    #    story's FIRST segment (`_sting_boundaries`, not `pacing.boundary_kind`: a sting is an
    #    editorial cue keyed to a story's first occurrence, not to every `source_hn_id`
    #    transition, so a callback can never move or add one).
    sting_at = _sting_boundaries(segments, story_ids)
    for i in range(n_bed, n):
        gap = gaps_in[i - 1]
        if i == n_bed:
            # The bed's tail already spent part of this boundary's air, so subtract it rather
            # than stacking a full pacing gap on top of a fade that just played.
            gap = round(max(0.0, gap - BED_TAIL_SECONDS), 3)
        if (i - 1) in sting_at:
            out_gaps.append(CUE_GAP_SECONDS)
            out_pieces.append(_cue(STING_AT, STING_SECONDS, sting_rms, STING_FADE_OUT, "sting"))
            out_gaps.append(CUE_GAP_SECONDS)
        else:
            out_gaps.append(gap)
        out_pieces.append(pieces_in[i])
        marks.append((len(out_pieces) - 1, 0))

    # 4. The outro cue, after the last word.
    out_gaps.append(CUE_GAP_SECONDS)
    out_pieces.append(_cue(OUTRO_AT, OUTRO_SECONDS, sting_rms, OUTRO_FADE_OUT, "outro"))

    # Measure the pieces with the SAME function that lays them out, then step into the merged
    # cold-open piece for the segments that live inside it. Nothing here re-derives a duration.
    piece_starts = stitch.segment_start_times(out_pieces, out_gaps)
    starts = [round(piece_starts[p] + _seconds(off), 3) for p, off in marks]
    return out_pieces, out_gaps, starts
