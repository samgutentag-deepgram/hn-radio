#!/usr/bin/env python3
"""Is the cold open three recordings, or one read of a list of three? A/B it. One TTS call.

Sam, by ear on 2026-08-07: the cold open "still feels like three recordings stitched together vs
one coherent read of three items in a list." It is. That episode's cold open is THREE
ScriptSegments, so it is three separate Flux requests, each rendered with no knowledge of the
others. Every one of them lands on a sentence-final falling contour, because to the renderer each
IS a complete utterance. A human reading a list holds pitch up on items one and two and only
falls on the last, and no amount of gap tuning between three finished recordings can produce
that, because the intonation is baked into the audio.

The 2026-08-08 episode did it the other way: one segment carrying all three headlines. So the
show has shipped both shapes and this is a regression, not an open question.

This renders the three headlines as ONE utterance and splices it into the cached episode in place
of the three, changing nothing else. Cost: a single Flux call. Everything else is rebuilt from
the per-line PCM cache.

Worth knowing: a one-segment cold open also closes the bed bug for free. `music.BED_SEGMENTS = 2`
counts the fixed intro as one of its two, so against a three-segment cold open the bed covers the
intro plus ONE headline and then drops 1.2s of BED_TAIL silence into the middle of the list. With
one cold-open segment the same constant covers exactly intro + cold open, and the tail lands
where a tail belongs, at the end.

Usage:
    python scripts/coldopen_experiment.py _frame/2026-08-07-new
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hn_radio import config, music, pacing, render, stitch  # noqa: E402
from _experiment_support import load  # noqa: E402

import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "breath_experiment", Path(__file__).resolve().parent / "breath_experiment.py")
breath = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(breath)

OUT_ROOT = config.EPISODES_DIR / "_coldopen"

# Sam chose 0.25 by ear on 2026-08-09 from scripts/breath_experiment.py. Applied to BOTH sides
# here so the cold-open shape is the only thing that differs between them.
BREATH_CAP = 0.25


def cold_open_range(script):
    """Indices of the cold-open headline segments: anchor lines after the fixed intro, before
    the first line that claims a story id.

    Keyed on `source_hn_id` being absent rather than on a count, because the number of headlines
    follows `--stories` and a hardcoded 3 was already the cause of one bug on this project.
    """
    idx = []
    for i, s in enumerate(script):
        if i == 0:
            continue  # the fixed intro
        if s.source_hn_id:
            break
        if s.role != "anchor":
            break
        idx.append(i)
    return idx


def build(episode_id: str, name: str, merge: bool, note: str, voice_id: str | None) -> dict:
    script, raw, story_ids = load(episode_id)
    co = cold_open_range(script)
    if merge:
        if len(co) < 2:
            raise SystemExit(f"cold open is already {len(co)} segment(s); nothing to merge")
        joined = " ".join(script[i].text.strip() for i in co)
        print(f"      rendering ONE utterance ({len(joined.split())} words) on {voice_id}")
        merged_pcm = render.render_segment(joined, voice_id, config.get_api_key())
        keep = [0] + co[:1] + list(range(co[-1] + 1, len(script)))
        script = [script[i] for i in keep]
        raw = [raw[i] for i in keep]
        script[1].text = joined
        raw[1] = merged_pcm
        for n, s in enumerate(script):
            s.order = n

    capped = [breath.cap_internal_silence(p, BREATH_CAP) for p in raw]
    paced, gaps = pacing.apply(script, capped, story_ids=story_ids)
    pieces, gaps, starts = music.apply(script, paced, gaps, story_ids=story_ids,
                                       log=lambda *_: None)

    slug = episode_id.replace("/", "-")
    out_dir = OUT_ROOT / f"{slug}-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = stitch.stitch(pieces, out_dir / "episode.wav", gaps)

    # The cold open ends where the first desk line starts, so that start time is exactly how long
    # the top of the show takes. That number is the thing under test.
    first_desk = next((st for s, st in zip(script, starts) if s.source_hn_id), starts[-1])
    return {"name": name, "note": note, "duration_seconds": round(duration, 2),
            "cold_open_ends": round(first_desk, 2), "segments": len(script),
            "cold_open_segments": len(cold_open_range(script)),
            "audio": f"{out_dir.name}/episode.wav",
            "text": " / ".join(script[i].text for i in cold_open_range(script))}


def page(episode_id, results):
    rows = "".join(
        f"<tr><td><b>{html.escape(r['name'])}</b><br><span class=note>{html.escape(r['note'])}"
        f"</span></td><td class=num>{r['cold_open_segments']}</td>"
        f"<td class=num>{r['cold_open_ends']:.1f}s</td>"
        f"<td class=num>{r['duration_seconds']:.0f}s</td>"
        f"<td><audio controls preload=none src='{html.escape(r['audio'])}'></audio></td></tr>"
        f"<tr><td colspan=5 class=say>{html.escape(r['text'])}</td></tr>"
        for r in results)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cold open A/B &middot; {html.escape(episode_id)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 58rem; margin: 0 auto;
         padding: 0 1.2rem 4rem; line-height: 1.5; background: Canvas; color: CanvasText; }}
  h1 {{ font-size: 1.3rem; margin: 1.4rem 0 0.3rem; }}
  p {{ font-size: 0.88rem; color: #888; max-width: 52rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: 0.6rem 0.5rem; border-bottom: 1px solid #8883;
           vertical-align: top; font-size: 0.88rem; }}
  th {{ font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.04em; color: #888; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td.say {{ font-size: 0.82rem; color: #999; border-bottom: 2px solid #8886; padding-top: 0; }}
  .note {{ color: #888; font-size: 0.78rem; }}
  audio {{ width: 17rem; }}
  b {{ color: CanvasText; }}
</style></head><body>
<h1>Cold open A/B &middot; {html.escape(episode_id)}</h1>
<p>Identical words, identical voice, identical music, and the {BREATH_CAP} breath cap on both.
The only difference is whether the three headlines are three renders or one.</p>
<p>Listen to the first fifteen seconds. Three separate renders each end on a falling, sentence-final
contour, because each one IS a complete utterance as far as the renderer is concerned. One render
of the same three sentences can hold the line up across the first two and fall only on the last,
which is what makes a list sound like a list.</p>
<table>
<tr><th>variant</th><th>segments</th><th>top of show</th><th>length</th><th>listen</th></tr>
{rows}
</table>
</body></html>
"""


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    episode_id = argv[0]
    script, _, _ = load(episode_id)
    co = cold_open_range(script)
    voice = None
    ep_json = config.EPISODES_DIR / episode_id / "episode.json"
    if ep_json.exists():
        segs = json.loads(ep_json.read_text())["segments"]
        voice = next((s.get("voice_id") for s in segs if s["order"] == co[0]), None)
    if not voice:
        raise SystemExit("could not determine the anchor's voice from episode.json")

    print(f"  cold open is {len(co)} segment(s): {[script[i].text[:34] for i in co]}")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = [
        build(episode_id, "three-renders", False,
              "What 2026-08-07 shipped. One Flux call per headline.", voice),
        build(episode_id, "one-read", True,
              "All three headlines in a single Flux call, as 2026-08-08 did it.", voice),
    ]
    for r in results:
        print(f"  {r['name']:<14} cold open ends {r['cold_open_ends']:5.1f}s   "
              f"episode {r['duration_seconds']:.0f}s")
    out = OUT_ROOT / "index.html"
    out.write_text(page(episode_id, results))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
