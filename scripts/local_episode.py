"""Render an episode locally and resumably, paying only for what is genuinely missing.

`make episode` is the plain path and it is the one to use when it works. This script exists for
when it does not, because a failed render otherwise throws away every segment that already
succeeded.

`_finalize` writes the per-segment PCM cache first thing so a later crash cannot lose the spend,
but nothing protects the window between the first Flux call and that write. An episode is around
22 segments, and any one of them can fail on a cold model or a transient 500. One run
reached 347 seconds of rendered audio and then died before `_finalize` entered, binning 25
successful calls.

  So both expensive stages are cached to disk here, keyed by content: the writer's script, because
  ClaudeWriter produces different words every run and a changed script invalidates every segment
  below it, and each rendered segment by (voice, sha256(text)). Re-running pays only for what is
  genuinely missing.

  The right long-term home for the segment half is `render.render_all` itself. This script is where
  it lives until then.

Usage:
    uv run python scripts/local_episode.py                      # today, frontpage, Claude writer
    uv run python scripts/local_episode.py --date 2026-08-17
    uv run python scripts/local_episode.py --writer panel       # no Anthropic spend
    uv run python scripts/local_episode.py --no-music           # speech only
    uv run python scripts/local_episode.py --fresh              # ignore the cache, pay again
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config, pipeline, render  # noqa: E402
from hn_radio.editions import DEFAULT_EDITION, EDITIONS  # noqa: E402
from hn_radio.models import ScriptSegment  # noqa: E402
from hn_radio.writers import ClaudeWriter, PanelWriter  # noqa: E402

CACHE_DIR = config.PROJECT_ROOT / ".render-cache"


def _install_script_cache(cache: pathlib.Path, log) -> None:
    """Cache the writer's output so a resumed run renders the SAME words.

    Without this the segment cache is useless against ClaudeWriter: every attempt would produce
    different text, so every key would miss and every retry would pay in full.
    """
    real_write = ClaudeWriter.write

    def cached_write(self, *a, **kw):
        if cache.exists():
            segs = [ScriptSegment(**d) for d in json.loads(cache.read_text())]
            log(f"      [cache] reusing {len(segs)} written segments from {cache.name}")
            return segs
        segs = real_write(self, *a, **kw)
        cache.write_text(json.dumps([s.__dict__ for s in segs], default=str))
        return segs

    ClaudeWriter.write = cached_write


def _install_resumable_render(seg_cache: pathlib.Path, log) -> None:
    """Persist each segment as it lands, so a 500 costs one segment and not the episode."""
    seg_cache.mkdir(parents=True, exist_ok=True)

    def resumable_render_all(segments, api_key, on_progress=None):
        out, hits, n = [], 0, len(segments)
        for i, seg in enumerate(segments, 1):
            key = hashlib.sha256(f"{seg.voice_id}\x00{seg.text}".encode()).hexdigest()[:32]
            f = seg_cache / f"{key}.pcm"
            if f.exists():
                out.append(f.read_bytes())
                hits += 1
            else:
                pcm = render.render_segment(seg.text, seg.voice_id, api_key)
                f.write_bytes(pcm)  # write BEFORE appending, so a crash below still keeps it
                out.append(pcm)
                log(f"      rendered {i}/{n} ({seg.voice_id})")
            if on_progress:
                on_progress(i, n)
        log(f"      [cache] {hits} of {n} reused, {n - hits} newly rendered")
        return out

    pipeline.render.render_all = resumable_render_all


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="local_episode", description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="episode date YYYY-MM-DD (default: today)")
    ap.add_argument("--edition", choices=EDITIONS, default="frontpage",
                    help="which edition to produce (default: frontpage, matching the nightly show)")
    ap.add_argument("--writer", choices=["claude", "panel"], default="claude",
                    help="'claude' matches scripts/daily.py; 'panel' is deterministic and free")
    ap.add_argument("--music", action=argparse.BooleanOptionalAction, default=None,
                    help="override HN_RADIO_MUSIC for this run")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore the caches and pay for the script and every segment again")
    args = ap.parse_args(argv)

    log = print
    episode_date = date.fromisoformat(args.date) if args.date else date.today()
    cache = CACHE_DIR / episode_date.isoformat()
    if args.fresh and cache.exists():
        for f in cache.rglob("*"):
            if f.is_file():
                f.unlink()
        log(f"[cache] cleared {cache}")

    # A long resumable run would rather wait out a transient failure than restart from zero.
    config.http_retries = lambda: 12
    _install_script_cache(cache / "script.json", log)
    _install_resumable_render(cache / "segments", log)
    (cache).mkdir(parents=True, exist_ok=True)

    writer = ClaudeWriter() if args.writer == "claude" else PanelWriter()
    log(f"[run] {episode_date.isoformat()} {args.edition} via {type(writer).__name__} "
        f"| host={config.api_host()} | music={config.music_enabled() if args.music is None else args.music}")

    try:
        ep = pipeline.run_panel(edition=args.edition, episode_date=episode_date,
                                writer=writer, with_music=args.music, log=log)
    except Exception as e:
        log(f"\nRender failed: {e}")
        done = len(list((cache / "segments").glob("*.pcm")))
        log(f"{done} segments are cached under {cache}. Re-run to resume; they will not be paid for again.")
        return 1

    # No `rebuild_site` call here any more. `publish.publish` now does the whole site-wide
    # refresh, and `_finalize` publishes into `config.EPISODES_DIR / episode_id`, so the
    # `out_dir.parent` it rebuilds is the same directory this line used to name. Neither cache
    # installed above touches the publish stage -- one wraps `ClaudeWriter.write`, the other
    # wraps `render.render_all`, both upstream of it -- so nothing here was keeping it honest.

    log(f"\nDONE {ep.id}  {ep.duration_seconds:.0f}s  {len({s.voice_id for s in ep.segments})} voices")
    log(f"Play it:  open {ep.audio_path.replace('.wav', '.mp3')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
