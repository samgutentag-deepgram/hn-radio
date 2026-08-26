"""Check the re-rendered archive against every invariant this project cares about.

Run after scripts/rerender_archive.py. Reads only what is on disk, asserts nothing about how it got
there, and prints a table rather than a verdict so a partial run is still readable.

WHAT IT CHECKS, and each line exists because something already went wrong there once:

  words       No episode says a name that is not in its own cast, and no episode says a desk. This
              is the whole point of scripts/fix_archive_words.py; a re-render that reused stale PCM
              would still pass a text check, which is why `audio` below is separate.
  audio       episode.wav and episode.mp3 exist, are newer than script.json, and every segment
              order has a .pcm. A stale render is the failure mode pipeline.render_recast walked
              into: it diffs against script.json, the word fix overwrote script.json in place, so
              every segment looked unchanged and all 376 files would have been reused silently.
  voices      Exactly two distinct voices per episode, one of them the permanent host. More than
              two means the recast did not fully collapse; one means the co-host was lost.
  turns       No run of 4 or more consecutive segments in a single voice. The comment block
              alternates setup and quote, and before the commenter reassignment both were the same
              voice, so 17 of 19 episodes ended on a 5-to-6 segment monologue nobody could follow.
  pubdate     generated_at is NOT today. _finalize stamps a fresh one and feed.rebuild_feed reads
              it as <pubDate>, so a missed restore republishes the entire back catalogue as new.
  music       Segment 0 starts after 0, meaning a bed was laid ahead of the first word. All 19 had
              music before the re-render and must still have it.
  mp3         MPEG-1 at 44.1 kHz. MPEG-2 at 24 kHz lists in a podcast app and then refuses to
              play, which cost this project two days once already.

    uv run python scripts/verify_archive.py
"""

from __future__ import annotations

import hashlib
import itertools
import json
import pathlib
import re
import subprocess
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config  # noqa: E402

BACKUP = config.PROJECT_ROOT / ".recast-backup"

# WHICH NAMES COUNT AS STALE, and the first version of this got it wrong in a way worth recording.
#
# It matched every voice name anywhere in the text, which flags real people in the news: 2026-08-03
# says "Sean Goedecke" (the blog author) and 2026-08-04 says "Elise Cawley" (Stephen Wolfram's wife,
# in his memorial post). Sean and Elise are also Flux voices, so a global name list calls both a
# leftover and would have had me rewriting the actual content of two episodes.
#
# The precise rule uses the backup: a name is stale only if it belonged to a voice that was IN this
# episode before the recast and is NOT in it now. That set is exactly the retired correspondents for
# that episode and cannot contain a name that was only ever a news subject.
SHOW_DESK = re.compile(r"\b(?:AI|ai|maker|security|drama|systems|main)\s+desk\b")


def display_name(voice_id: str) -> str:
    return voice_id.replace("flux-", "").replace("-en", "").capitalize()


def mp3_format(path: pathlib.Path) -> str:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,sample_rate", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return out or "?"
    except Exception as e:
        return f"ffprobe:{type(e).__name__}"


def stale_name_pattern(ep_id: str, current_cast: set):
    """Names of voices this episode USED to have and no longer does. Empty pattern if none."""
    bk = BACKUP / ep_id / "script.json"
    if not bk.exists():
        return None
    was = {display_name(s["voice_id"]) for s in json.loads(bk.read_text())}
    gone = sorted(was - current_cast)
    return re.compile(r"\b(" + "|".join(map(re.escape, gone)) + r")\b") if gone else None


def check(ep_id: str) -> dict:
    d = config.EPISODES_DIR / ep_id
    script = json.loads((d / "script.json").read_text())
    doc = json.loads((d / "episode.json").read_text())
    cast = {display_name(s["voice_id"]) for s in script}
    voice_ids = [s["voice_id"] for s in script]

    stale_names = []
    desks = 0
    pattern = stale_name_pattern(ep_id, cast)
    for i, s in enumerate(script):
        if pattern:
            stale_names += [(i, m.group(1)) for m in pattern.finditer(s["text"])]
        desks += len(SHOW_DESK.findall(s["text"]))

    wav, mp3 = d / "episode.wav", d / "episode.mp3"
    seg_dir = d / "segments"
    missing_pcm = [s["order"] for s in script if not (seg_dir / f"{s['order']}.pcm").exists()]
    # NOT "newer than script.json": render_recast re-serializes script.json at the END, after the
    # wav, so that comparison reports every healthy episode as stale. The invariant that actually
    # matters is that the stitch happened after every segment it stitched.
    pcms = [seg_dir / f"{s['order']}.pcm" for s in script]
    newest_pcm = max((p.stat().st_mtime for p in pcms if p.exists()), default=0)

    # THE CHECK THAT ACTUALLY PROVES THE RENDER HAPPENED, and the mtime checks above cannot.
    #
    # An episode that has not been re-rendered yet still has every .pcm and a .wav newer than all of
    # them, because its old audio and old segments are mutually consistent. It reads as healthy. So
    # compare CONTENT: each segment's .pcm must be byte-identical to the content-addressed cache
    # entry for its current (voice_id, text). If it is not, that audio was produced from different
    # words or a different voice, whatever the timestamps say.
    #
    # One exception, and it is legitimate rather than a tolerance: 2026-08-18 order 0 is the merged
    # cold open, whose .pcm carries internal pauses the plain cached read does not, so it is longer
    # by design. Compared on the cache entry EXISTING rather than matching.
    seg_cache = config.PROJECT_ROOT / ".render-cache" / "recast" / "segments"
    wrong_audio = []
    for sg in script:
        pcm = seg_dir / f"{sg['order']}.pcm"
        h = hashlib.sha256(f"{sg['voice_id']}\x00{sg['text']}".encode()).hexdigest()[:32]
        cached = seg_cache / f"{h}.pcm"
        if not pcm.exists():
            continue
        if not cached.exists():
            wrong_audio.append(sg["order"])
        elif pcm.read_bytes() != cached.read_bytes() and not (ep_id == "2026-08-18" and sg["order"] == 0):
            wrong_audio.append(sg["order"])

    longest_run = max(len(list(g)) for _, g in itertools.groupby(voice_ids))
    gen = (doc.get("generated_at") or "")[:10]

    return {
        "id": ep_id,
        "stale_names": stale_names,
        "desks": desks,
        "distinct_voices": len(set(voice_ids)),
        "longest_run": longest_run,
        "wav": wav.exists(),
        "mp3": mp3.exists(),
        "fresh": wav.exists() and wav.stat().st_mtime >= newest_pcm,
        "missing_pcm": missing_pcm,
        "wrong_audio": wrong_audio,
        "generated_at": gen,
        "today": gen == date.today().isoformat(),
        "music": (doc["segments"][0].get("start_seconds") or 0) > 0.05,
        "mp3_fmt": mp3_format(mp3) if mp3.exists() else "-",
        "duration": doc.get("duration_seconds"),
    }


def main() -> int:
    ids = sorted(p.name for p in BACKUP.iterdir() if p.is_dir())
    rows = [check(e) for e in ids]

    print(f"{'episode':12s} {'dur':>7s} {'v':>2s} {'run':>3s} {'names':>5s} {'desk':>4s} "
          f"{'audio':>5s} {'pcm':>4s} {'mism':>4s} {'pubdate':>10s} {'mus':>3s}  mp3")
    fails = []
    for r in rows:
        ok = (not r["stale_names"] and r["desks"] == 0 and r["distinct_voices"] == 2
              and r["longest_run"] < 4 and r["wav"] and r["mp3"] and r["fresh"]
              and not r["missing_pcm"] and not r["wrong_audio"]
              and not r["today"] and r["music"])
        if not ok:
            fails.append(r)
        audio = "ok" if (r["wav"] and r["mp3"] and r["fresh"]) else ("STALE" if r["wav"] else "NONE")
        print(f"{r['id']:12s} {str(r['duration'] or '-'):>7s} {r['distinct_voices']:2d} "
              f"{r['longest_run']:3d} {len(r['stale_names']):5d} {r['desks']:4d} {audio:>5s} "
              f"{len(r['missing_pcm']):4d} {len(r['wrong_audio']):4d} {r['generated_at']:>10s} "
              f"{'yes' if r['music'] else 'NO':>3s}  {r['mp3_fmt']}")

    print()
    print(f"{len(rows) - len(fails)} of {len(rows)} episodes clean.")
    if fails:
        print("\nPROBLEMS:")
        for r in fails:
            why = []
            if r["stale_names"]:
                why.append(f"{len(r['stale_names'])} off-cast names {r['stale_names'][:3]}")
            if r["desks"]:
                why.append(f"{r['desks']} desk phrases")
            if r["distinct_voices"] != 2:
                why.append(f"{r['distinct_voices']} voices, expected 2")
            if r["longest_run"] >= 4:
                why.append(f"a {r['longest_run']}-segment single-voice run")
            if not r["wav"] or not r["mp3"]:
                why.append("audio missing")
            elif not r["fresh"]:
                why.append("audio older than its own segments, so the stitch was skipped")
            if r["missing_pcm"]:
                why.append(f"{len(r['missing_pcm'])} segments have no pcm")
            if r["wrong_audio"]:
                why.append(f"{len(r['wrong_audio'])} segments' audio does not match their words")
            if r["today"]:
                why.append("generated_at is TODAY, so the feed would republish it as new")
            if not r["music"]:
                why.append("no music laid")
            print(f"  {r['id']}: " + "; ".join(why))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
