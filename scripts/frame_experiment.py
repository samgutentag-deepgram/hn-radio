#!/usr/bin/env python3
"""Re-run a past episode's stories through the CURRENT writer prompt, then A/B it against what
actually aired.

A prompt change is the one lever on this show that cannot be evaluated from cache, because the
words themselves are different. So this is the expensive experiment, and it is built to spend as
little as possible: the script is written and LINTED first, and TTS is only reached if that
passes. A bad prompt costs one Claude call, not 29 Flux renders.

Reproducing the day is the whole trick. `episode.json` stores only hn_id/title/url/points per
story, NOT the fetched article text, so the source material has to be re-fetched. Everything
else is deterministic given the date: `fetch_front_page_for_date` replays that day's front page
via Algolia, `select_stories` picks the same three, and `episode_cast_for(before=<date>)` seats
the same cast, because the recency nudge is relative to the episode being generated rather than
to today.

Output goes to `episodes/_frame/<id>-new/`, one level deeper than a real episode, exactly as
`scripts/pace_experiment.py` does. `feed.rebuild_feed`, `manifest.build_manifest` and
`custom.py` all enumerate with `glob("*/episode.json")`, so nesting keeps this structurally
invisible to the feed, the manifest AND the custom-episode story pool. The aired episode is
never touched.

Usage:
    python scripts/frame_experiment.py 2026-08-07         # write, lint, render
    python scripts/frame_experiment.py 2026-08-07 --dry   # write and lint only, no TTS
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hn_radio import config, editions, ingest, pipeline, sources  # noqa: E402
from hn_radio.cast import active_cast, episode_cast as episode_cast_for  # noqa: E402
from hn_radio.writers import ClaudeWriter  # noqa: E402

import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "frame_lint", Path(__file__).resolve().parent / "frame_lint.py")
frame_lint = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frame_lint)

OUT_ROOT = config.EPISODES_DIR / "_frame"


def _aired(episode_id: str) -> dict:
    path = config.EPISODES_DIR / episode_id / "episode.json"
    if not path.exists():
        raise SystemExit(f"no aired episode at {path}")
    return json.loads(path.read_text())


def _lint_segments(segments) -> list:
    """(segment, hits) for every non-commenter line that trips a rule."""
    out = []
    for seg in segments:
        role = seg.get("role") if isinstance(seg, dict) else seg.role
        text = (seg.get("text") if isinstance(seg, dict) else seg.text) or ""
        if role == "commenter":
            continue
        hits = frame_lint.check(text)
        if hits:
            out.append((seg, hits))
    return out


def _report(label, flagged, total_lines):
    print(f"\n  {label}: {len(flagged)} flagged line(s) of {total_lines}")
    for seg, hits in flagged:
        text = seg.get("text") if isinstance(seg, dict) else seg.text
        who = seg.get("speaker_key") if isinstance(seg, dict) else seg.speaker_key
        rules = ", ".join(sorted({f"{c}:{n}" for c, n, _, _ in hits}))
        print(f"    [{who}] {rules}")
        print(f"      {text[:160]}")


def main(argv):
    dry = "--dry" in argv
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        raise SystemExit(__doc__)
    episode_id = args[0]
    episode_date = date.fromisoformat(episode_id)

    aired = _aired(episode_id)
    aired_ids = [s["hn_id"] for s in aired["source_items"]]
    n_stories = len(aired_ids)

    print(f"[1/5] Replaying {episode_id}: {n_stories} stories, edition "
          f"{aired.get('edition', 'frontpage')}")
    pool = ingest.fetch_front_page_for_date(episode_date, pipeline.SELECTION_POOL)
    selected = editions.select_stories(pool, aired.get("edition") or "frontpage", n_stories,
                                       active_cast())
    got = [s.id for s in selected]
    if got != aired_ids:
        # Not fatal: a changed selection still produces a usable script, but it stops being an
        # A/B of the PROMPT, because the two sides would be covering different stories.
        print(f"      [warn] selection drifted: aired {aired_ids}, replay {got}")
        print("      the comparison is no longer story-for-story. Ctrl-C now if that matters.")
    else:
        print(f"      same three stories: {got}")

    cast, substitutions = episode_cast_for(selected, before=episode_date.isoformat())
    print(f"      cast: {', '.join(d.name for d in [cast.anchor] + list(cast.desks))}"
          + (f" (covering for {', '.join(substitutions.values())})" if substitutions else ""))
    ingest.populate_kids(selected)

    print("[2/5] Re-fetching each source page (not stored in episode.json)...")
    for s in selected:
        sources.enrich_story(s)
        have = len((s.source_text or "").strip())
        print(f"      [{s.source_kind:7}] {have:>6} chars  {s.title[:46]}")

    top = ingest.pick_top_thread(selected)
    comments = ingest.fetch_top_comments(top, config.N_COMMENTS) if top else []

    print("[3/5] Writing with the CURRENT prompt (one Claude call)...")
    writer = ClaudeWriter(target_minutes=5, substitutions=substitutions)
    segments = writer.write(selected, top, comments, cast, aired.get("edition") or "frontpage",
                            episode_date)
    segments = (pipeline._intro_segments(cast, episode_date) + segments
                + pipeline._outro_segments(cast))
    for i, seg in enumerate(segments):
        seg.order = i
    print(f"      {len(segments)} lines")

    print("[4/5] Frame lint, both sides:")
    old_flagged = _lint_segments(aired["segments"])
    new_flagged = _lint_segments(segments)
    _report(f"AIRED  {episode_id}", old_flagged, len(aired["segments"]))
    _report("REPLAY (current prompt)", new_flagged, len(segments))

    out_id = f"_frame/{episode_id}-new"
    out_dir = config.EPISODES_DIR / out_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script.json").write_text(
        json.dumps([s.__dict__ for s in segments], indent=2, default=str))
    print(f"\n      script written to {out_dir}/script.json")

    if new_flagged:
        print("\n      [!] the new script still trips the lint. Not rendering; fix the prompt "
              "and re-run. That just saved a full episode of TTS.")
        return 1
    if dry:
        print("\n      --dry: stopping before TTS.")
        return 0

    print(f"[5/5] Render (this is the paid step) -> {out_id}")
    source_items = [{"hn_id": s.id, "title": s.title, "url": s.url, "points": s.points}
                    for s in selected]
    pipeline.render_panel(
        segments, episode_id=out_id,
        title=writer.episode_title() or f"HN Radio replay {episode_id}",
        source_items=source_items, cast=cast,
        edition=aired.get("edition") or "frontpage",
        summary=writer.episode_summary() or "")
    print(f"\ndone. compare with: python scripts/frame_compare.py {episode_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
