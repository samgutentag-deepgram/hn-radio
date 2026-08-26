#!/usr/bin/env python3
"""Space the headlines inside a ONE-READ cold open, without breaking the single read. Zero TTS.

Sam, 2026-08-09, after hearing three-renders vs one-read: "The one read feels most coherent,
maybe a touch too fast... the issue with the three renders is that the inflection and the voice
changes so much between them that it feels very stark jumps." And: the cold open should be "as
matter-of-fact as possible. We are just reporting information."

Both halves of that point the same way. The fix is NOT to go back to separate renders with bigger
gaps, because separate renders are the cause of the stark jumps: each one carries its own
sentence-final fall and its own onset. The fix is to keep the single utterance, whose intonation
is already continuous, and stretch the silence Flux left between its sentences.

So this is the exact inverse of `breath_experiment.cap_internal_silence`. That one is a ceiling
on internal silence, chosen at 0.25 for the body of the show. This SETS the cold open's internal
pauses to one fixed length, which is deliberate: an even pause makes a list scan as a list, and
evenness is most of what reads as matter-of-fact. A floor alone would leave them ragged.

The merged render is cached to `episodes/_coldopen/merged-<voice>.pcm`, so the single Flux call
this needs is paid once and every later variant is free.

Usage:
    python scripts/coldopen_spacing.py _frame/2026-08-07-new
"""

from __future__ import annotations

import hashlib
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hn_radio import config, music, pacing, render, stitch  # noqa: E402
from _experiment_support import load  # noqa: E402

import importlib.util  # noqa: E402

_B = importlib.util.spec_from_file_location(
    "breath_experiment", Path(__file__).resolve().parent / "breath_experiment.py")
breath = importlib.util.module_from_spec(_B)
_B.loader.exec_module(breath)

_C = importlib.util.spec_from_file_location(
    "coldopen_experiment", Path(__file__).resolve().parent / "coldopen_experiment.py")
coldopen = importlib.util.module_from_spec(_C)
_C.loader.exec_module(coldopen)

OUT_ROOT = config.EPISODES_DIR / "_coldopen"
BREATH_CAP = 0.25          # Sam's pick for the body of the show, 2026-08-09
SPACINGS = [None, 0.40, 0.55, 0.70]

# A run shorter than this is a zero crossing inside a word, not a pause. Without this guard the
# sample-level scan finds thousands of "runs" per line and padding every one of them destroys the
# audio. Well under the shortest real inter-sentence pause, which measures around 0.13s. Read from
# `pacing` now that the scan ships there, so the reported pauses are measured with the same floor
# the spacing pass uses.
MIN_RUN_SECONDS = pacing.MIN_INTERNAL_RUN_SECONDS


def space_boundaries(pcm: bytes, n_gaps: int, seconds: float) -> bytes:
    """Set the `n_gaps` longest internal silences to exactly `seconds`, leaving the rest alone.

    Targeting the LONGEST runs rather than every run is what keeps this honest. A three-headline
    cold open has two sentence boundaries and a handful of shorter intra-sentence breaths, and
    padding all of them would open holes in the middle of a headline. Verified against the
    2026-08-07 merged render: the two longest runs (0.22s at 3.21s, 0.13s at 8.25s) sit within a
    half second of the boundaries predicted from word counts (3.67s, 8.27s), while every other
    run is intra-sentence.

    Chosen over separator punctuation, which was tested and does not work: across five separators
    (space, newline, blank line, ellipsis, dash) the boundary pauses ranged 0.08-0.40s with no
    separator producing two even ones. Flux does not take pause direction from punctuation here.

    SHIPPED on 2026-08-20 as `pacing.set_internal_pauses`, at 0.55s
    (`pacing.COLD_OPEN_PAUSE_SECONDS`, which carries the arithmetic that picked it). This now
    delegates to that function rather than keeping a second copy of the mechanism: the point of
    this harness is to audition what the show will actually do, and two implementations of the
    same scan would drift the moment either one was tuned. The reasoning above stays here because
    this is where it was established.
    """
    return pacing.set_internal_pauses(pcm, n_gaps, seconds)


def internal_runs(pcm: bytes):
    """Lengths in seconds of the silence runs inside `pcm`, edges excluded.

    Delegates to `pacing.internal_silence_runs`, which is the same algorithm this used to carry a
    second copy of. The floor is passed EXPLICITLY rather than defaulted, so `MIN_RUN_SECONDS` and
    the note above it stay load-bearing instead of quietly becoming decoration.

    Verified equivalent before the swap rather than assumed: 400 fuzzed buffers identical, plus the
    exact one-sample floor edge. The delegation is also strictly better on an odd-length buffer,
    where the deleted copy raised ValueError while unpacking samples and pacing returns [].
    """
    return [(end - start) / config.SAMPLE_RATE
            for start, end in pacing.internal_silence_runs(pcm, MIN_RUN_SECONDS)]


def merged_cold_open(episode_id: str, script, raw, voice: str):
    """The three headlines as ONE utterance. Rendered once, then cached."""
    co = coldopen.cold_open_range(script)
    joined = " ".join(script[i].text.strip() for i in co)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    # hashlib, NOT hash(): str hashing is salted per process (PYTHONHASHSEED), so the built-in
    # produced a different filename every run and this "cache" paid for a fresh render each time.
    # Two Flux calls were burned that way before it was caught.
    digest = hashlib.sha256(joined.encode()).hexdigest()[:8]
    cache = OUT_ROOT / f"merged-{voice}-{digest}.pcm"
    if cache.exists():
        print(f"      reusing cached merged render ({cache.name})")
        return co, joined, cache.read_bytes()
    print(f"      rendering ONE utterance ({len(joined.split())} words) on {voice}")
    pcm = render.render_segment(joined, voice, config.get_api_key())
    cache.write_bytes(pcm)
    return co, joined, pcm


def build(episode_id, name, spacing, note, voice):
    script, raw, story_ids = load(episode_id)
    co, joined, merged = merged_cold_open(episode_id, script, raw, voice)

    if spacing is not None:
        # len(co) headlines have len(co) - 1 boundaries between them.
        merged = space_boundaries(merged, len(co) - 1, spacing)

    keep = [0] + co[:1] + list(range(co[-1] + 1, len(script)))
    script = [script[i] for i in keep]
    raw = [raw[i] for i in keep]
    script[1].text = joined
    raw[1] = merged
    for n, s in enumerate(script):
        s.order = n

    # The 0.25 cap is the body of the show's setting and must NOT touch the cold open, whose
    # spacing is the thing under test here.
    capped = [p if i == 1 else breath.cap_internal_silence(p, BREATH_CAP)
              for i, p in enumerate(raw)]
    paced, gaps = pacing.apply(script, capped, story_ids=story_ids)
    pieces, gaps, starts = music.apply(script, paced, gaps, story_ids=story_ids,
                                       log=lambda *_: None)

    slug = episode_id.replace("/", "-")
    out_dir = OUT_ROOT / f"{slug}-space-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = stitch.stitch(pieces, out_dir / "episode.wav", gaps)
    co_len = len(capped[1]) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)
    return {"name": name, "note": note, "duration_seconds": round(duration, 2),
            "cold_open_seconds": round(co_len, 2),
            "pauses": [round(r, 2) for r in internal_runs(merged)],
            "audio": f"{out_dir.name}/episode.wav"}


def page(episode_id, results, joined):
    rows = "".join(
        f"<tr><td><b>{html.escape(r['name'])}</b><br><span class=note>{html.escape(r['note'])}"
        f"</span></td><td class=num>{r['cold_open_seconds']:.1f}s</td>"
        f"<td class=num>{', '.join(f'{p:.2f}' for p in r['pauses']) or '&ndash;'}</td>"
        f"<td class=num>{r['duration_seconds']:.0f}s</td>"
        f"<td><audio controls preload=none src='{html.escape(r['audio'])}'></audio></td></tr>"
        for r in results)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cold open spacing &middot; {html.escape(episode_id)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 60rem; margin: 0 auto;
         padding: 0 1.2rem 4rem; line-height: 1.5; background: Canvas; color: CanvasText; }}
  h1 {{ font-size: 1.3rem; margin: 1.4rem 0 0.3rem; }}
  p {{ font-size: 0.88rem; color: #888; max-width: 52rem; }}
  .said {{ font-size: 0.9rem; color: CanvasText; border-left: 3px solid #c9a227;
          padding: 0.5rem 0.8rem; background: rgba(201,162,39,0.07); }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: 0.6rem 0.5rem; border-bottom: 1px solid #8883;
           vertical-align: top; font-size: 0.88rem; }}
  th {{ font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.04em; color: #888; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .note {{ color: #888; font-size: 0.78rem; }}
  audio {{ width: 17rem; }}
  b {{ color: CanvasText; }}
</style></head><body>
<h1>Cold open spacing &middot; {html.escape(episode_id)}</h1>
<p>One render, four spacings. The utterance is identical in every row, so the intonation across
the three headlines is the same continuous read every time. Only the silence between the
sentences changes, which is why none of these reintroduce the stark jumps of separate renders.</p>
<p class=said>{html.escape(joined)}</p>
<p>The rest of the show carries the 0.25 breath cap in every row. The cold open is deliberately
exempt from it, since its spacing is the thing under test.</p>
<table>
<tr><th>variant</th><th>cold open</th><th>pauses inside it</th><th>episode</th><th>listen</th></tr>
{rows}
</table>
</body></html>
"""


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    episode_id = argv[0]
    script, _, _ = load(episode_id)
    co = coldopen.cold_open_range(script)
    segs = json.loads((config.EPISODES_DIR / episode_id / "episode.json").read_text())["segments"]
    voice = next((s.get("voice_id") for s in segs if s["order"] == co[0]), None)
    if not voice:
        raise SystemExit("could not determine the anchor's voice")

    results, joined = [], ""
    for sp in SPACINGS:
        name = "as-rendered" if sp is None else f"{sp:.2f}"
        note = ("Flux's own spacing, untouched. This is the 'one-read' you heard."
                if sp is None else f"The two headline boundaries set to exactly {sp:.2f}s. Nothing inside a headline is touched.")
        r = build(episode_id, name, sp, note, voice)
        results.append(r)
        print(f"  {name:<12} cold open {r['cold_open_seconds']:5.2f}s   "
              f"pauses {r['pauses']}")
    script2, raw2, _ = load(episode_id)
    _, joined, _ = merged_cold_open(episode_id, script2, raw2, voice)

    out = OUT_ROOT / "spacing.html"
    out.write_text(page(episode_id, results, joined))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
