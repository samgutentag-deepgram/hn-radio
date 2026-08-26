"""scripts/add_chapters.py's cache rebuild path: it must reproduce a musicked episode, not a
dry one.

This is the second time this exact trap has fired. The first time, per-boundary pacing shipped
and this same rebuild path kept stitching at the old flat gap while computing chapter marks from
the paced script - caught before it published. Music re-armed the identical trap one layer up:
`pipeline._finalize` now runs `pacing.apply` -> `music.apply` -> `stitch.stitch`, and takes
`start_seconds` from what `music.apply` reports. A rebuild that stops after pacing (or skips
music) writes a shorter, un-musicked WAV while `start_seconds` and the chapter marks derived from
it describe the musicked one.

The invariant pinned here: rebuilding from the per-segment cache must be indistinguishable from a
fresh render for the same script and cache, because in production it stands in for a WAV that was
never uploaded (see `.dockerignore` excluding `episodes/**/episode.wav`). Built from synthetic
PCM; no network, no Deepgram, no Claude (the episode already carries a `summary` so `main()`
never reaches its `anthropic` import).
"""

from __future__ import annotations

import array
import importlib.util
import json
import shutil
import wave
from pathlib import Path

import pytest

from hn_radio import config as hn_config
from hn_radio import music, pacing, pipeline, status, stitch
from hn_radio.models import ScriptSegment

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

STORY_IDS = {101, 202}

_SPEC = importlib.util.spec_from_file_location(
    "add_chapters", Path(__file__).resolve().parent.parent / "scripts" / "add_chapters.py")
add_chapters = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(add_chapters)


def _segments():
    """Eight segments: fixed intro/outro, a cold open, a story change, one performed comment."""
    specs = [
        (0, "anchor", "Haley", "anchor", None),   # fixed intro
        (1, "anchor", "Haley", "anchor", 0),      # cold open
        (2, "desk", "Marcus", "maker", 101),
        (3, "anchor", "Haley", "anchor", 101),
        (4, "desk", "Marcus", "maker", 202),       # story change
        (5, "commenter", "dang", None, 9999),
        (6, "anchor", "Haley", "anchor", None),   # fixed outro
    ]
    return [ScriptSegment(order=o, role=r, speaker_key=s, text=f"line {o}", desk=d,
                          source_hn_id=hn, voice_id="flux-haley-en")
            for o, r, s, d, hn in specs]


def tone(seconds, amplitude=7000):
    """Loud, distinct-length synthetic PCM. Whole milliseconds so offsets are exact at 24 kHz."""
    n = int(hn_config.SAMPLE_RATE * seconds)
    return array.array("h", [amplitude if i % 2 else -amplitude for i in range(n)]).tobytes()


def _raw_pcm(segments):
    return [tone(0.3 + 0.05 * i, amplitude=6500 + 91 * i) for i in range(len(segments))]


def _silence_status(monkeypatch):
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    monkeypatch.setattr(status, "done", lambda *a, **k: None)


def _fresh_render(tmp_path, monkeypatch):
    """A real render through `pipeline._finalize`, the ground truth a rebuild must match."""
    fresh_dir = tmp_path / "fresh"
    monkeypatch.setattr(hn_config, "EPISODES_DIR", fresh_dir)
    _silence_status(monkeypatch)
    segs = _segments()
    pcm = _raw_pcm(segs)
    episode = pipeline._finalize(
        segs, pcm, episode_id="ep", title="t",
        source_items=[{"hn_id": i, "title": f"s{i}", "url": "u"} for i in STORY_IDS],
        edition="frontpage", summary="fresh summary", log=lambda *a, **k: None)
    wav_bytes = (fresh_dir / "ep" / "episode.wav").read_bytes()
    return episode, segs, pcm, wav_bytes


def _seed_rebuild_dir(tmp_path, segs, pcm, *, stale_duration=1.0):
    """An episode seeded from the deployed image: script + cache + episode.json, no episode.wav.

    Mirrors what `pipeline._finalize` itself writes to `segments/`, minus `episode.wav`, so the
    rebuild path is exercised exactly as it runs against a real Fly volume.
    """
    rebuild_dir = tmp_path / "rebuild"
    out = rebuild_dir / "ep"
    seg_dir = out / "segments"
    seg_dir.mkdir(parents=True)
    for s, p in zip(segs, pcm):
        (seg_dir / f"{s.order}.pcm").write_bytes(p)

    # Deliberately stale start_seconds/duration: exactly what an image-seeded episode looks like
    # before this rebuild runs - the numbers on disk describe nothing real yet.
    stale = [ScriptSegment(order=s.order, role=s.role, speaker_key=s.speaker_key, text=s.text,
                           desk=s.desk, source_hn_id=s.source_hn_id, voice_id=s.voice_id,
                           start_seconds=0.0) for s in segs]
    (out / "script.json").write_text(json.dumps([s.to_dict() for s in stale], indent=2))
    (out / "episode.json").write_text(json.dumps({
        "id": "ep", "title": "t", "generated_at": "2026-08-08T00:00:00Z",
        "audio_path": str(out / "episode.wav"),
        "source_items": [{"hn_id": i, "title": f"s{i}", "url": "u"} for i in STORY_IDS],
        "duration_seconds": stale_duration, "edition": "frontpage",
        "summary": "already has a summary, so no Claude call",
    }, indent=2))
    return rebuild_dir, out


@needs_ffmpeg
def test_rebuild_produces_the_same_duration_as_a_fresh_render(tmp_path, monkeypatch):
    episode, segs, pcm, fresh_wav = _fresh_render(tmp_path, monkeypatch)

    rebuild_dir, out = _seed_rebuild_dir(tmp_path, segs, pcm)
    monkeypatch.setattr(hn_config, "EPISODES_DIR", rebuild_dir)
    rc = add_chapters.main()
    assert rc == 0

    rebuilt = json.loads((out / "episode.json").read_text())
    # The invariant that was broken: a rebuild that forgot music (or stopped after pacing) would
    # write a shorter WAV while claiming a duration computed some other way. Pin the number, not
    # just "music is present".
    assert rebuilt["duration_seconds"] == pytest.approx(episode.duration_seconds, abs=0.01)
    assert (out / "episode.wav").read_bytes() == fresh_wav, (
        "a rebuilt episode must be byte-identical to a fresh render of the same script and cache"
    )


@needs_ffmpeg
def test_rebuild_persists_start_seconds_that_match_where_segments_begin_in_the_rebuilt_wav(
    tmp_path, monkeypatch
):
    _, segs, pcm, _ = _fresh_render(tmp_path, monkeypatch)

    rebuild_dir, out = _seed_rebuild_dir(tmp_path, segs, pcm)
    monkeypatch.setattr(hn_config, "EPISODES_DIR", rebuild_dir)
    add_chapters.main()

    rebuilt_script = json.loads((out / "script.json").read_text())
    starts = {d["order"]: d["start_seconds"] for d in rebuilt_script}
    with wave.open(str(out / "episode.wav"), "rb") as w:
        written = w.readframes(w.getnframes())
    bps = hn_config.SAMPLE_RATE * hn_config.SAMPLE_WIDTH

    # Recompute the paced (pre-music) takes so every segment outside the cold-open bed can be
    # found byte-for-byte; the bedded takes (0, 1) carry music mixed on top and are checked by
    # position falling inside a piece boundary instead, same as the music-module tests do.
    story_ids = pacing.story_ids_from([{"hn_id": i} for i in STORY_IDS])
    paced, _ = pacing.apply(segs, pcm, story_ids=story_ids)
    for s in segs[music.BED_SEGMENTS:]:
        i = s.order
        actual = written.find(paced[i])
        assert actual >= 0, f"segment {i} is not in the rebuilt WAV at all"
        assert starts[i] == pytest.approx(actual / bps, abs=0.002), (
            f"persisted start_seconds[{i}]={starts[i]} but the audio begins at {actual / bps}"
        )
    # And the persisted starts were not invented on the side: they are exactly what `music.apply`
    # reports for this rebuild, never `stitch.segment_start_times` on the un-musicked pieces.
    _, _, want_starts = music.apply(segs, paced,
                                    pacing.gap_plan(segs, pacing.SHOW_POLICY, story_ids),
                                    story_ids=story_ids, log=lambda *a, **k: None)
    for s in segs:
        assert starts[s.order] == pytest.approx(want_starts[s.order], abs=1e-6)
