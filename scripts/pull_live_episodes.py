"""Pull episodes down from the deployed archive into a local staging directory. READ ONLY.

Why this exists: the local `episodes/` directory is a working scratch dir, not a mirror of what
aired. Compared against the live `index.json` on 2026-08-21 it held ten episodes, three of which
were the same show as live, six of which were a DIFFERENT show under the same date id, and one
(2026-08-20) which existed only locally. Ten live episodes were not on the machine at all. So
"recast the archive" needs the archive first.

OVER HTTPS, NOT OVER SSH, and that is the safety argument rather than a convenience. `backend/
app.py` mounts the volume's episodes directory as plain static files at `/episodes`, so every
file this needs is a GET against the public app: no shell on the machine, no `fly ssh` session
competing with the 03:00 Pacific cron, nothing that can write, restart, deploy or read a secret.
The one thing a caller still owes is timing -- do not run this while the nightly render is
writing that directory.

WHAT IT FETCHES, and what it deliberately does not. `episode.json`, `script.json`,
`chapters.json`, `transcript.vtt`, and every `segments/<order>.pcm`. NOT `episode.wav` and NOT
`episode.mp3`: those are the bulk of an episode on disk (about 30MB of 35MB) and they are pure
functions of the per-segment PCM, so anything that re-stitches locally regenerates them. The PCM
is the part that cannot be recreated without paying Flux again, which is exactly why it comes
down: a recast reuses any segment whose voice and words are both unchanged.

Segment names are derived from `script.json`'s `order` values rather than guessed as a range,
because several archived episodes have MORE `.pcm` files than segments (leftovers from an earlier,
longer script for that date) and a couple have gaps.

Writes into a staging directory and never into `episodes/` itself. Installing a pulled episode
means overwriting or displacing a local one, which is a decision with a blast radius; it belongs
to the caller that knows what it is trying to do, not to the thing that does the download.

Usage:
    uv run python scripts/pull_live_episodes.py --list
    uv run python scripts/pull_live_episodes.py --out /tmp/live-pull 2026-08-09 2026-08-10
    uv run python scripts/pull_live_episodes.py --out /tmp/live-pull --all
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_ORIGIN = "https://dg-devrel-hn-radio.fly.dev"
META_FILES = ("episode.json", "script.json", "chapters.json", "transcript.vtt")

# Six, and modestly so. This is a single Fly machine with one process serving a static mount, and
# the reason for any concurrency at all is round-trip latency rather than throughput. The failure
# to avoid is looking like load to a host that also has a cron to run.
WORKERS = 6


def _get(url: str, timeout: float = 60.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _get_to(url: str, dest: pathlib.Path) -> int:
    """Download one file. Via a temp name and a rename, so an interrupted pull cannot leave a
    truncated .pcm that a later run treats as complete audio."""
    if dest.exists():
        return -1  # already have it; -1 distinguishes "skipped" from "zero bytes"
    data = _get(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    tmp.write_bytes(data)
    tmp.rename(dest)
    return len(data)


def live_index(origin: str) -> list:
    return json.loads(_get(f"{origin}/episodes/index.json").decode())["episodes"]


def pull_episode(origin: str, ep_id: str, out: pathlib.Path, log=print) -> dict:
    """Pull one episode's metadata and per-segment PCM. Returns a small report dict."""
    dest = out / ep_id
    dest.mkdir(parents=True, exist_ok=True)
    got, missing = 0, []
    for name in META_FILES:
        try:
            n = _get_to(f"{origin}/episodes/{ep_id}/{name}", dest / name)
            got += max(n, 0)
        except urllib.error.HTTPError as e:
            # chapters.json / transcript.vtt are absent on older episodes, and that is a fact
            # about the archive rather than a failed pull. episode.json and script.json are not
            # optional, and the caller checks for them below.
            missing.append(f"{name} (HTTP {e.code})")
    if not (dest / "script.json").exists():
        raise RuntimeError(f"{ep_id}: no script.json on the live host; cannot recast it")

    orders = [s["order"] for s in json.loads((dest / "script.json").read_text())]
    absent = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_get_to, f"{origin}/episodes/{ep_id}/segments/{o}.pcm",
                               dest / "segments" / f"{o}.pcm"): o for o in orders}
        for fut in as_completed(futures):
            o = futures[fut]
            try:
                got += max(fut.result(), 0)
            except urllib.error.HTTPError as e:
                # A missing cache entry is survivable: the recast re-renders that line and pays
                # for it. Recorded so the spend has a stated cause instead of appearing from
                # nowhere.
                absent.append(f"{o} (HTTP {e.code})")
    log(f"  {ep_id}: {len(orders)} segments, {got / 1e6:.1f}MB new"
        + (f", metadata missing: {', '.join(missing)}" if missing else "")
        + (f", {len(absent)} PCM absent: {', '.join(absent[:6])}" if absent else ""))
    return {"id": ep_id, "segments": len(orders), "bytes": got,
            "missing_meta": missing, "absent_pcm": absent}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pull_live_episodes", description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", help="episode ids to pull")
    ap.add_argument("--all", action="store_true", help="pull every id in the live index.json")
    ap.add_argument("--list", action="store_true", help="print the live index and exit")
    ap.add_argument("--origin", default=DEFAULT_ORIGIN)
    ap.add_argument("--out", default="/tmp/live-pull", help="staging directory (never episodes/)")
    args = ap.parse_args(argv)

    log = print
    index = live_index(args.origin)
    if args.list:
        for e in index:
            log(f"{e['id']}  {e['duration_seconds']:7.1f}s  {e['title']}")
        log(f"{len(index)} episodes on {args.origin}")
        return 0

    ids = [e["id"] for e in index] if args.all else args.ids
    if not ids:
        log("Nothing to pull. Give ids, or --all, or --list.")
        return 2
    known = {e["id"] for e in index}
    unknown = [i for i in ids if i not in known]
    if unknown:
        log(f"Not in the live index: {unknown}")
        return 2

    out = pathlib.Path(args.out)
    log(f"[pull] {len(ids)} episodes from {args.origin} -> {out} (read only, HTTPS)")
    reports, failed = [], []
    for ep_id in sorted(ids):
        try:
            reports.append(pull_episode(args.origin, ep_id, out, log=log))
        except Exception as e:
            log(f"  {ep_id}: FAILED {type(e).__name__}: {e}")
            failed.append(ep_id)
    total = sum(r["bytes"] for r in reports)
    log(f"[pull] {len(reports)} episodes, {total / 1e6:.1f}MB")
    if failed:
        log(f"[pull] failed: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
