#!/usr/bin/env python3
"""Rebuild a past episode's audio under different pacing policies. Zero TTS calls.

Every rendered episode caches its per-segment PCM under `episodes/<id>/segments/`, so the audio
can be re-concatenated with any gap policy without touching Deepgram. That makes pacing the one
lever on this show that can be evaluated by ear for free, which is the whole point of this script.

Output goes to `episodes/_pace/<id>-<policy>/`, one level deeper than a real episode. That is
deliberate: `rebuild_feed`, `build_manifest`, and `build_index` all enumerate episodes with
`glob("*/episode.json")`, so nesting keeps these experiments structurally invisible to the feed,
the manifest, AND the landing page. The `-recast` regex only covers the first two.

Usage:
    python scripts/pace_experiment.py                       # both baselines, all three policies
    python scripts/pace_experiment.py 2026-08-03            # one episode, all three policies
    python scripts/pace_experiment.py 2026-08-03 tight      # one episode, one policy
"""

from __future__ import annotations

import html
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hn_radio import config, pacing, stitch  # noqa: E402
from _experiment_support import load  # noqa: E402

OUT_ROOT = config.EPISODES_DIR / "_pace"
DEFAULT_EPISODES = ["2026-08-03", "2026-08-04"]




def build(episode_id: str, policy: pacing.GapPolicy) -> dict:
    script, raw_pcm, story_ids = load(episode_id)
    pcm = [pacing.normalize_edges(p) for p in raw_pcm] if policy.normalize_edges else raw_pcm
    gaps = pacing.gap_plan(script, policy, story_ids)

    # The invariant that matters: ONE gap list feeds both the offsets and the audio.
    starts = stitch.segment_start_times(pcm, gaps)
    out_dir = OUT_ROOT / f"{episode_id}-{policy.name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = stitch.stitch(pcm, out_dir / "episode.wav", gaps)

    kinds = ["show_boundary"] + [
        pacing.boundary_kind(script[i], script[i + 1], story_ids) for i in range(1, len(script) - 2)
    ] + (["show_boundary"] if len(script) > 2 else [])
    trimmed = sum(len(a) - len(b) for a, b in zip(raw_pcm, pcm))
    meta = {
        "episode_id": episode_id,
        "policy": policy.name,
        "note": policy.note,
        "normalize_edges": policy.normalize_edges,
        "duration_seconds": round(duration, 2),
        "segments": len(script),
        "silence_removed_seconds": round(
            trimmed / (config.SAMPLE_RATE * config.SAMPLE_WIDTH), 2),
        "boundary_counts": dict(Counter(kinds)),
        "boundaries": [
            {"after_order": script[i].order, "kind": kinds[i], "gap_seconds": gaps[i],
             "from": script[i].speaker_key, "to": script[i + 1].speaker_key}
            for i in range(len(gaps))
        ],
        "start_seconds": {str(s.order): st for s, st in zip(script, starts)},
    }
    (out_dir / "pace.json").write_text(json.dumps(meta, indent=2))
    return meta


def comparison_page(results: list[dict]) -> str:
    by_episode: dict[str, list[dict]] = {}
    for r in results:
        by_episode.setdefault(r["episode_id"], []).append(r)

    blocks = []
    for ep_id, runs in by_episode.items():
        rows = []
        for r in runs:
            src = f"{ep_id}-{r['policy']}/episode.wav"
            rows.append(
                f"<tr><td><b>{html.escape(r['policy'])}</b><br>"
                f"<span class=note>{html.escape(r['note'])}</span></td>"
                f"<td class=num>{r['duration_seconds']:.1f}s</td>"
                f"<td class=num>{r['silence_removed_seconds']:.1f}s</td>"
                f"<td><audio controls preload=none src='{html.escape(src)}'></audio></td></tr>"
            )
        blocks.append(
            f"<h2>{html.escape(ep_id)}</h2><table>"
            "<tr><th>policy</th><th>length</th><th>silence removed</th><th>listen</th></tr>"
            + "".join(rows) + "</table>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HN Radio pacing comparison</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; max-width: 52rem; margin: 0 auto;
         padding: 1.5rem 1.2rem 4rem; line-height: 1.5; background: Canvas; color: CanvasText; }}
  h1 {{ font-size: 1.3rem; }}
  h2 {{ font-size: 1rem; margin-top: 2rem; border-bottom: 1px solid #8884; padding-bottom: .3rem; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td, th {{ text-align: left; padding: .6rem .5rem; border-bottom: 1px solid #8883;
            vertical-align: middle; font-size: .9rem; }}
  th {{ font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; color: #888; }}
  td.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .note {{ color: #888; font-size: .78rem; }}
  audio {{ width: 100%; min-width: 16rem; }}
  .lede {{ border-left: 3px solid #c9a227; padding: .5rem .9rem;
           background: rgba(201,162,39,.08); font-size: .88rem; }}
</style></head><body>
<h1>Pacing comparison</h1>
<p class=lede>Same words, same voices, same rendered audio. The only difference is how much
silence sits between segments. Rebuilt from the cached per-segment PCM, so no TTS calls were
made. <b>uniform</b> is what the show sounds like today.</p>
{''.join(blocks)}
<p class=note>Per-boundary gaps and start offsets for each run are in that run's
<code>pace.json</code>.</p>
</body></html>
"""


def main() -> int:
    args = sys.argv[1:]
    episodes = [a for a in args if a not in pacing.POLICIES] or DEFAULT_EPISODES
    names = [a for a in args if a in pacing.POLICIES] or list(pacing.POLICIES)

    results = []
    for ep in episodes:
        for name in names:
            meta = build(ep, pacing.POLICIES[name])
            results.append(meta)
            print(f"{ep:12} {name:16} {meta['duration_seconds']:7.1f}s  "
                  f"(-{meta['silence_removed_seconds']:.1f}s renderer silence)  "
                  f"-> {OUT_ROOT / (ep + '-' + name) / 'episode.wav'}")

    page = OUT_ROOT / "index.html"
    page.write_text(comparison_page(results))
    print(f"\ncompare: {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
