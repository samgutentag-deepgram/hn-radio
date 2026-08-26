"""Render the cast page's per-voice sample, one call per voice, into episodes/samples/.

The cast page puts every voice in the published Flux catalog on one grid and plays a sample when
you press a card. Those samples used to be whatever line each voice happened to have been rendered
with, from several different scripts and several different eras, so the grid could not be used to
compare voices: a voice reading a forum comment and a voice reading a show open do not sound
comparable even when they are.

So every card reads the SAME line, and the line is the show's own cold open dated 2026-08-12:

    From Deepgram, this is Hacker News Radio. It's Wednesday, August 12, and here's what's
    happening today. Let's get you caught up.

That is Flux TTS GA day, and it is a Wednesday (verified, not assumed). Nothing in the app reads
the date, so it is an easter egg for anyone who presses enough cards to notice that all 36 voices
are introducing a show on a day that already happened. If you regenerate these, keep the date.

SUPERSEDES hn_radio.recast.build_samples, which was deleted on 2026-08-22. That function wrote
the SAME episodes/samples/<voice_id>.wav paths this script does, with a different sample line, and
both skipped if the file already existed. So a `build-samples` run followed by this one left
recast's line under some cards and this script's under the rest, while web/cast.html prints "Every
card reads the same line, so the grid is a fair comparison" unconditionally. Reproduced, with the
wrong words on disk. web/build.html:272 already told the reader to run THIS script when a preview
was missing, so the consumer had been reassigned before the producer was removed.

The `samples/.line` marker file that was floated as the alternative was rejected: it keeps two
rival lines fighting over one namespace, and makes each command re-render every overlapping voice
against the API whenever someone alternates them. One namespace, one producer.

The one thing lost with it: build_samples could also produce previews for ids outside the
published catalog. That is not a regression today, because the published catalog is the only
set this show can seat, which is the
same reason the hardcode exists.

WHY NOT scripts/voice_preview.py. That script auditions the catalog for casting decisions: it
renders a deliberately comment-shaped line, groups the page by whether a voice holds a seat, and
writes its own review page into episodes/_voices/. This one produces a shipped asset for a
specific page, in the location that page fetches from, for a superset of the catalog. Same API
call, different job, and welding the two together would make both harder to read.

RETIRED VOICES ARE INCLUDED, which is the one surprising choice here. Marcus and Priya are in
config.RETIRED_VOICES: Sam pulled them by ear, and today's archive work exists to scrub them out
of episodes that still name them. They are still in the published docs and they still have an
official orb, so the cast page shows all 36 with those two labelled `Retired` in words. Rendering
their sample too is what keeps the grid honest: 34 cards on the show's line and 2 on some older
line would read as an oversight rather than a retirement. Pass --catalog-only to skip them.

Resumable, because a cold model can 500 on a raw call and this makes one call per voice.

    uv run python scripts/make_cast_samples.py
    uv run python scripts/make_cast_samples.py --fresh
    uv run python scripts/make_cast_samples.py --only flux-maeve-en,flux-jack-en
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config, render, stitch  # noqa: E402

# Keep the date. See the module docstring.
CAST_LINE = ("From Deepgram, this is Hacker News Radio. It's Wednesday, August 12, "
             "and here's what's happening today. Let's get you caught up.")

OUT = config.EPISODES_DIR / "samples"


def voice_ids(catalog_only: bool) -> list:
    """The 36 in `config.PUBLISHED_VOICES`, or the 34 the show can actually seat.

    Sorted by display name rather than id so the run log reads in the same order as the grid.
    """
    ids = config.VOICE_CATALOG if catalog_only else config.PUBLISHED_VOICES
    return sorted(ids, key=lambda v: ids[v][0].lower())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_cast_samples")
    ap.add_argument("--line", default=CAST_LINE, help="what every voice reads")
    ap.add_argument("--fresh", action="store_true", help="re-render voices already on disk")
    ap.add_argument("--catalog-only", action="store_true",
                    help="skip the retired ids the cast page labels rather than hides")
    ap.add_argument("--only", default="", help="comma-separated voice ids, for a repair run")
    args = ap.parse_args(argv)

    # One call per voice across the whole catalog: wait out a transient failure rather than
    # restarting the pass.
    config.http_retries = lambda: 12

    wanted = voice_ids(args.catalog_only)
    if args.only:
        asked = [v.strip() for v in args.only.split(",") if v.strip()]
        unknown = [v for v in asked if v not in wanted]
        if unknown:
            print(f"not in the catalog: {', '.join(unknown)}", file=sys.stderr)
            return 2
        wanted = asked

    OUT.mkdir(parents=True, exist_ok=True)
    key = config.get_api_key()
    print(f"host {config.api_host()}, {len(wanted)} voices, line: {args.line[:48]}...\n")

    ok = cached = 0
    failed = []
    for i, vid in enumerate(wanted, 1):
        wav = OUT / f"{vid}.wav"
        if wav.exists() and not args.fresh:
            print(f"  [{i:2}/{len(wanted)}] cached  {vid}")
            cached += 1
            continue
        try:
            # stitch.stitch for a single segment: byte-identical to a hand-rolled wave.open
            # (verified by sha256, odd trailing sample included) and it is the one place in this
            # repo that knows how to write a WAV. A local copy was a second place to keep correct.
            secs = stitch.stitch([render.render_segment(args.line, vid, key)], wav)
            print(f"  [{i:2}/{len(wanted)}] ok      {vid} ({secs:.1f}s)")
            ok += 1
        except Exception as e:  # one dead id must not cost the other 35
            print(f"  [{i:2}/{len(wanted)}] FAILED  {vid}  {str(e)[-90:]}")
            failed.append(vid)

    print(f"\n{ok} rendered, {cached} cached, {len(failed)} failed.")
    if failed:
        print("retry with: --only " + ",".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
