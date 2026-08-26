#!/usr/bin/env python3
"""Cap the silence Flux leaves INSIDE a line, and A/B the result. Zero API calls.

Measured on `_frame/2026-08-07-new`: 53.0s of the 333s episode is dead air, and only 9.2s of it
sits between lines where the gap policy lives. The other 43.8s is inside the rendered lines
themselves, across 110 pauses. One 69-word take runs 25.9s and is 19% internal silence, with a
0.86s hole mid-sentence. That is why the show reads as slow: not the words per minute (152, which
is ordinary broadcast pace), and not the gaps (an exchange is 0.16s, tighter than the 0.20s
median measured on NPR's Up First), but the breathing inside the lines.

`pacing.normalize_edges` already does this at each line's EDGES. This does the same thing inward,
which is the only lever that reaches the 83% of dead air the gap policy cannot touch.

Rebuilt from the per-line PCM cache through the real pacing AND music path, so each variant is
the whole show rather than dry audio. Output lands in `episodes/_breath/<id>-<name>/`, one level
deeper than a real episode, so `feed.rebuild_feed`, `manifest.build_manifest` and `custom.py`
(which all enumerate `glob("*/episode.json")`) never see it.

Usage:
    python scripts/breath_experiment.py _frame/2026-08-07-new
"""

from __future__ import annotations

import array
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hn_radio import config, music, pacing, stitch  # noqa: E402
from _experiment_support import load  # noqa: E402

OUT_ROOT = config.EPISODES_DIR / "_breath"

# (name, cap seconds or None, note). None is the control: exactly what ships today.
VARIANTS = [
    ("as-is", None, "What ships today. Every pause Flux rendered, kept in full."),
    ("0.35", 0.35, "Only the outliers trimmed. The mid-sentence 0.86s holes close; normal "
                   "phrasing is untouched."),
    ("0.25", 0.25, "Moderate. Roughly NPR's median pause, applied inside lines as well as "
                   "between them."),
    ("0.15", 0.15, "Aggressive. Near the floor before a breath starts sounding edited out."),
]



def cap_internal_silence(pcm: bytes, cap_seconds: float) -> bytes:
    """Shorten every run of silence inside `pcm` to at most `cap_seconds`.

    Runs touching either edge are left alone: `normalize_edges` owns those, and trimming them
    here as well would stack two trims on the same silence and clip the onset of a plosive.
    """
    samples = array.array("h")
    samples.frombytes(pcm)
    n = len(samples)
    if not n:
        return pcm
    cap = int(cap_seconds * config.SAMPLE_RATE)
    thr = pacing.SILENCE_THRESHOLD

    keep = array.array("h")
    i = 0
    while i < n:
        if abs(samples[i]) > thr:
            keep.append(samples[i])
            i += 1
            continue
        j = i
        while j < n and abs(samples[j]) <= thr:
            j += 1
        run = j - i
        # An edge run belongs to normalize_edges, so pass it through untouched.
        at_edge = (i == 0) or (j >= n)
        keep.extend(samples[i:j] if (at_edge or run <= cap) else samples[i:i + cap])
        i = j
    return keep.tobytes()




def _secs(b: bytes) -> float:
    return len(b) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)


def build(episode_id: str, name: str, cap, note: str) -> dict:
    script, raw, story_ids = load(episode_id)
    capped = raw if cap is None else [cap_internal_silence(p, cap) for p in raw]
    removed = sum(_secs(a) - _secs(b) for a, b in zip(raw, capped))

    paced, gaps = pacing.apply(script, capped, story_ids=story_ids)
    pieces, gaps, starts = music.apply(script, paced, gaps, story_ids=story_ids,
                                       log=lambda *_: None)
    for s, st in zip(script, starts):
        s.start_seconds = st

    slug = episode_id.replace("/", "-")
    out_dir = OUT_ROOT / f"{slug}-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = stitch.stitch(pieces, out_dir / "episode.wav", gaps)

    meta = {"name": name, "note": note, "cap": cap, "duration_seconds": round(duration, 2),
            "removed_seconds": round(removed, 2), "audio": f"{out_dir.name}/episode.wav",
            "starts": [round(s.start_seconds, 3) for s in script],
            "lines": [{"order": s.order, "who": s.speaker_key, "role": s.role, "text": s.text}
                      for s in script]}
    (out_dir / "breath.json").write_text(json.dumps(meta, indent=2))
    return meta


def page(episode_id: str, results: list, npr: str | None) -> str:
    rows = []
    base = results[0]["duration_seconds"]
    for r in results:
        delta = r["duration_seconds"] - base
        rows.append(
            f"<tr><td><b>{html.escape(r['name'])}</b><br>"
            f"<span class=note>{html.escape(r['note'])}</span></td>"
            f"<td class=num>{r['duration_seconds']:.0f}s</td>"
            f"<td class=num>{'' if not delta else f'{delta:+.0f}s'}</td>"
            f"<td class=num>{r['removed_seconds']:.0f}s</td>"
            f"<td><audio controls preload=none src='{html.escape(r['audio'])}'></audio></td></tr>")

    ref = ""
    if npr:
        ref = (f"<h2>Reference</h2><p class=note>NPR <i>Up First</i>, 2026-08-08. Same shape as "
               f"this show: three stories, two hosts, a cold open. Measured over its first two "
               f"minutes it has <b>13 pauses totalling 5.0s</b>; ours over the same window has "
               f"<b>47 totalling 20.5s</b>. Local reference copy, not redistributed.</p>"
               f"<audio controls preload=none src='{html.escape(npr)}'></audio>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breath A/B &middot; {html.escape(episode_id)}</title>
<style>
  :root {{ color-scheme: light dark; --accent: #c9a227; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 60rem; margin: 0 auto;
         padding: 0 1.2rem 4rem; line-height: 1.5; background: Canvas; color: CanvasText; }}
  h1 {{ font-size: 1.3rem; margin: 1.4rem 0 0.3rem; }}
  h2 {{ font-size: 1rem; margin: 2rem 0 0.5rem; }}
  p {{ font-size: 0.88rem; color: #888; max-width: 52rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 0.8rem; }}
  th, td {{ text-align: left; padding: 0.6rem 0.5rem; border-bottom: 1px solid #8883;
           vertical-align: top; font-size: 0.88rem; }}
  th {{ font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.04em; color: #888; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .note {{ color: #888; font-size: 0.78rem; }}
  audio {{ width: 17rem; }}
  b {{ color: CanvasText; }}
</style></head><body>
<h1>Breath A/B &middot; {html.escape(episode_id)}</h1>
<p>Same words, same voices, same music, same gap policy. The only thing changing is the cap on
silence <b>inside</b> a line. Rebuilt from the per-line PCM cache, so every row below cost zero
API calls.</p>
<p>Why this and not the gap policy: of 53.0s of dead air in this episode, only 9.2s sits between
lines. The other <b>43.8s is inside the lines</b>, across 110 pauses. The gap policy cannot reach
any of it.</p>
<table>
<tr><th>variant</th><th>length</th><th>vs as-is</th><th>silence cut</th><th>listen</th></tr>
{"".join(rows)}
</table>
{ref}
</body></html>
"""


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    episode_id = argv[0]
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for name, cap, note in VARIANTS:
        meta = build(episode_id, name, cap, note)
        results.append(meta)
        print(f"  {name:<6} {meta['duration_seconds']:6.1f}s  "
              f"(cut {meta['removed_seconds']:5.1f}s of internal silence)")

    npr_src = None
    npr = config.EPISODES_DIR / "_ref" / "upfirst-2026-08-08.mp3"
    if npr.exists():
        npr_src = f"../_ref/{npr.name}"

    out = OUT_ROOT / "index.html"
    out.write_text(page(episode_id, results, npr_src))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
