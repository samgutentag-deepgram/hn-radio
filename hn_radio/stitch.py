"""Stage 4 - Stitch. Concatenate rendered PCM into one episode WAV.

Because segments are raw linear16 at one fixed rate, joining them is `a + silence + b`, and
silence is just zero samples. We write exactly one WAV header, at the end, via stdlib `wave`,
so the file reports an honest duration (unlike the batch container=wav placeholder header).
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import List, Sequence, Union

from . import config

# A gap spec is either one value for every boundary, or one value per boundary. Per-boundary
# pacing (see `pacing.gap_plan`) is why this is not just a float: a fixed gap everywhere is a
# monologue tell. Whatever is passed here MUST also be passed to `segment_start_times`, or the
# page's seek buttons, the chapter marks, and the VTT drift away from the audio.
GapSpec = Union[float, Sequence[float]]


def silence(seconds: float) -> bytes:
    """`seconds` of 16-bit mono silence at the configured sample rate."""
    n_samples = int(config.SAMPLE_RATE * seconds)
    return b"\x00" * (n_samples * config.SAMPLE_WIDTH)


def gap_list(n_segments: int, gap_seconds: GapSpec = config.GAP_SECONDS) -> List[float]:
    """Normalize a gap spec to exactly `n_segments - 1` values, one per boundary.

    Single source of truth for how a spec is read, so the concatenator and the offset calculator
    can never interpret the same spec two different ways.
    """
    n_gaps = max(n_segments - 1, 0)
    if isinstance(gap_seconds, (int, float)):
        return [float(gap_seconds)] * n_gaps
    gaps = [float(g) for g in gap_seconds]
    if len(gaps) != n_gaps:
        raise ValueError(
            f"gap list has {len(gaps)} values but {n_segments} segments need {n_gaps}"
        )
    return gaps


def concat_pcm(segments: List[bytes], gap_seconds: GapSpec = config.GAP_SECONDS) -> bytes:
    """Join PCM segments with a silence gap between each (no leading/trailing gap)."""
    if not segments:
        return b""
    gaps = gap_list(len(segments), gap_seconds)
    out = bytearray(segments[0])
    for seg, gap in zip(segments[1:], gaps):
        out += silence(gap)
        out += seg
    return bytes(out)


def _duration_seconds(pcm: bytes) -> float:
    frames = len(pcm) / config.SAMPLE_WIDTH / config.CHANNELS
    return frames / config.SAMPLE_RATE


def segment_start_times(pcm_segments: List[bytes],
                        gap_seconds: GapSpec = config.GAP_SECONDS) -> List[float]:
    """Start offset (seconds) of each segment in the stitched episode, matching `concat_pcm`.

    Used to place per-segment play buttons and, later, chapter markers. Accounts for the
    silence gap inserted between segments. Pass the SAME `gap_seconds` given to `stitch`: these
    offsets are what the page seeks to, so a mismatch is silently wrong audio, not an error.
    """
    gaps = gap_list(len(pcm_segments), gap_seconds)
    starts: List[float] = []
    t = 0.0
    for i, pcm in enumerate(pcm_segments):
        starts.append(round(t, 3))
        t += _duration_seconds(pcm)
        if i < len(pcm_segments) - 1:
            t += gaps[i]
    return starts


def stitch(segments: List[bytes], out_path: Union[str, Path],
           gap_seconds: GapSpec = config.GAP_SECONDS) -> float:
    """Concatenate PCM segments and write a single WAV. Returns duration in seconds."""
    pcm = concat_pcm(segments, gap_seconds)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(config.CHANNELS)
        w.setsampwidth(config.SAMPLE_WIDTH)
        w.setframerate(config.SAMPLE_RATE)
        w.writeframes(pcm)
    return round(_duration_seconds(pcm), 2)


def load_cached_segments(seg_dir: Union[str, Path], orders: List[int]) -> List[bytes]:
    """Read the cached per-segment PCM for `orders`, in that order.

    The cache holds RAW renderer output, so this is the archive, not the show: a caller that
    wants what the episode actually sounds like must run it through `pacing.apply` and then
    `music.apply` before it reaches `stitch`. Split out from the old `rebuild_from_segments` so a
    paced-and-musicked rebuild can reuse the reading (and its two guards) without `stitch`
    needing to know what a script segment is.

    `orders` must come from the episode's script.json, in script order. Do NOT glob the directory:
    a re-render can leave orphaned .pcm files with higher indices behind (one episode on disk has 23
    files for a 19-segment script), and globbing would splice those strangers into the audio.
    """
    seg_dir = Path(seg_dir)
    pcm = []
    missing = []
    for order in orders:
        p = seg_dir / f"{order}.pcm"
        if not p.exists():
            missing.append(p.name)
            continue
        pcm.append(p.read_bytes())
    if missing:
        raise FileNotFoundError(
            f"cannot rebuild the WAV: {len(missing)} cached segment(s) absent from {seg_dir} "
            f"({', '.join(missing[:5])}{'...' if len(missing) > 5 else ''})"
        )
    if not pcm:
        raise FileNotFoundError(f"no cached segments in {seg_dir}, nothing to rebuild from")
    return pcm


# `rebuild_from_segments` was deleted 2026-08-22. It was a one-line wrapper with no caller, and
# its own docstring told you to do the thing the real production rebuild already does:
# `load_cached_segments` -> `pacing.apply` -> `music.apply` -> `stitch`, which is
# `scripts/add_chapters.py:80`.
#
# It was NOT given a Makefile target instead, and that is the interesting half. It writes an
# unpaced, unmusicked WAV whose length disagrees with the chapter marks by seconds, which is the
# exact trap `add_chapters.py`'s comment says has already fired twice. A convenient entry point to
# a rebuild that sounds wrong is worse than no entry point.
#
# What it was FOR is still true and still handled: the deployed image excludes
# episodes/**/episode.wav (.dockerignore) but keeps the per-segment cache, so an episode seeded
# from the image has no source WAV on the volume. `load_cached_segments` below is how you get it
# back, and its three guards are covered by the tests that used to reach it through this wrapper.
