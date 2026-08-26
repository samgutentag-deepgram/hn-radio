"""Re-render the 19 archive episodes from the script.json currently on disk.

WHY NOT scripts/recast_archive.py. That one recasts FROM `.recast-backup/` and its `_verify`
enforces "the same words in the same order", which `scripts/fix_archive_words.py` has now
deliberately violated: 194 lines were rewritten and 36 quoted comments reassigned so the archive
matches the two-person cast. Running it would both re-derive from the pristine text and fail its own
check. This script takes the CURRENT script.json as truth and renders it.

THE TRAP THIS EXISTS TO AVOID, and it is the reason this is a script rather than a one-liner.
`pipeline.render_recast` decides what to re-render by comparing each segment against
`episodes/<id>/script.json` and reusing `episodes/<id>/segments/<order>.pcm` when the text and the
voice both match. The word fix edited script.json IN PLACE, so that file now matches itself for
every segment. Left alone, render_recast would reuse all 376 stale PCM files and render NOTHING:
every episode would keep its old audio while its script claimed to be fixed, and the run would look
like a clean success.

So the stale per-order PCM is deleted first. Which segments are stale is derived from present state
rather than from a memory of what changed: a segment whose (voice_id, text) pair has no entry in the
content-addressed cache under `.render-cache/recast/segments/` is one whose audio does not exist
yet. That was 230 of 376 at the time of writing, which cross-checks exactly against 194 text edits
plus 36 voice reassignments.

ONE SEGMENT IS DELIBERATELY EXEMPT. 2026-08-18 order 0 is the merged cold open: its order-file is
407040 bytes against 357120 in the cache, because the merge inserts internal pauses the plain read
does not have. Its text is unchanged, so it must be reused as-is. The rule already handles this
correctly (its pair IS cached, so it is never deleted), and it is written down because the next
person to see a byte mismatch will otherwise assume corruption.

MUSIC STAYS ON. All 19 episodes on disk have it: segment 0 starts at 7.16s in every one. Rendering
dry now would leave the archive matching the new format on voices and the old one on everything
else, which is the inconsistency this whole task exists to remove.

`generated_at` is restored from the backup afterwards, because `_finalize` stamps a fresh one and
`feed.rebuild_feed` reads it for `<pubDate>`. Without the restore, re-rendering the archive
republishes 19 back episodes as if they all aired today.

    uv run python scripts/rerender_archive.py --dry-run     # what would render, no calls
    uv run python scripts/rerender_archive.py               # render everything that needs it
    uv run python scripts/rerender_archive.py --only 2026-08-13
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hn_radio import config, publish  # noqa: E402
from hn_radio.models import ScriptSegment  # noqa: E402

import recast_archive as RA  # noqa: E402

BACKUP = config.PROJECT_ROOT / ".recast-backup"


def stale_orders(ep_id: str) -> list:
    """Segment orders whose audio does not exist yet, read off the content cache."""
    segs = json.loads((config.EPISODES_DIR / ep_id / "script.json").read_text())
    out = []
    for s in segs:
        if not RA._cache_path(s["voice_id"], s["text"]).exists():
            out.append(s["order"])
    return out


def render_one(ep_id: str, log=print, dry_run: bool = False) -> dict:
    ep_dir = config.EPISODES_DIR / ep_id
    segs_raw = json.loads((ep_dir / "script.json").read_text())
    stale = stale_orders(ep_id)
    log(f"[{ep_id}] {len(segs_raw)} segments, {len(stale)} need a render")
    if dry_run:
        return {"episode": ep_id, "calls": len(stale), "rendered": False}

    # Drop the stale audio so render_recast's own reuse check misses on exactly those segments.
    seg_dir = ep_dir / "segments"
    for order in stale:
        p = seg_dir / f"{order}.pcm"
        if p.exists():
            p.unlink()

    meta = json.loads((ep_dir / "episode.json").read_text())
    pristine_meta = json.loads((BACKUP / ep_id / "episode.json").read_text()) \
        if (BACKUP / ep_id / "episode.json").exists() else meta

    segments = [ScriptSegment(**{k: v for k, v in s.items()
                                if k in ScriptSegment.__dataclass_fields__}) for s in segs_raw]

    from hn_radio import cast as cast_mod
    RA._prefetch(ep_id, segments, log)

    from hn_radio import pipeline
    pipeline.render_recast(
        segments,
        original_id=ep_id,
        episode_id=ep_id,
        title=meta.get("title", ep_id),
        source_items=meta.get("source_items", []),
        cast=cast_mod.DEFAULT_CAST,
        edition=meta.get("edition", ""),
        summary=meta.get("summary", ""),
        log=log,
    )

    # `_finalize` stamps a fresh generated_at; the feed reads it as <pubDate>. Put the real one back
    # or the whole back catalogue republishes as if it aired today.
    doc = json.loads((ep_dir / "episode.json").read_text())
    doc["generated_at"] = pristine_meta.get("generated_at", doc["generated_at"])
    (ep_dir / "episode.json").write_text(json.dumps(doc, indent=2) + "\n")
    return {"episode": ep_id, "calls": len(stale), "rendered": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rerender_archive")
    ap.add_argument("--only", default="", help="one episode id")
    ap.add_argument("--dry-run", action="store_true", help="report what would render, no calls")
    args = ap.parse_args(argv)

    ids = [args.only] if args.only else sorted(p.name for p in BACKUP.iterdir() if p.is_dir())

    # The host is NOT patched here, so DEEPGRAM_API_HOST decides where the calls go and nothing
    # in this script can silently redirect them. Retries are raised because a full archive pass
    # makes hundreds of calls in a row and would rather wait than restart.
    config.http_retries = lambda: 12
    RA._install_render_cache(print)
    print(f"host {config.api_host()}, {len(ids)} episodes\n")

    total = sum(len(stale_orders(e)) for e in ids)
    print(f"{total} segments need a render across {len(ids)} episodes.\n")
    if args.dry_run:
        for e in ids:
            render_one(e, dry_run=True)
        return 0

    t0 = time.monotonic()
    done, failed = [], []
    for e in ids:
        try:
            done.append(render_one(e))
        except Exception as exc:  # one bad episode must not cost the other 18
            print(f"[{e}] FAILED: {type(exc).__name__}: {str(exc)[:200]}")
            failed.append(e)

    print("\nRebuilding feed + JSON API...")
    publish.rebuild_site(config.EPISODES_DIR)
    mins = (time.monotonic() - t0) / 60
    print(f"\n{len(done)} rendered, {len(failed)} failed, {mins:.1f} min.")
    print(f"calls {RA.STATS['calls']}, cache hits {RA.STATS['cache_hits']}")
    if failed:
        print("retry: " + " ".join(f"--only {e}" for e in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
