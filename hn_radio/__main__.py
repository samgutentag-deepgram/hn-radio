"""CLI entry point.

    python -m hn_radio                       # today's panel episode, Makers edition
    python -m hn_radio --edition ai          # AI edition
    python -m hn_radio --edition frontpage   # no topic skew
    python -m hn_radio --no-music            # speech only, no cues or bed
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from . import config
from .editions import DEFAULT_EDITION, EDITIONS
from .pipeline import run_panel
from .writers import ClaudeWriter, PanelWriter


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hn_radio", description="Generate an HN Radio episode.")
    parser.add_argument("--edition", choices=EDITIONS, default=DEFAULT_EDITION,
                        help=f"which edition to produce (default: {DEFAULT_EDITION})")
    parser.add_argument("--date", help="episode date YYYY-MM-DD (default: today)")
    parser.add_argument("--stories", type=int, default=config.N_STORIES, help="stories to cover")
    parser.add_argument("--comments", type=int, default=config.N_COMMENTS,
                        help="comments to perform from the top thread")
    parser.add_argument("--writer", choices=["panel", "claude"], default="panel",
                        help="script writer: 'panel' (deterministic, default) or 'claude' (Opus 5, longer)")
    parser.add_argument("--minutes", type=int, default=5,
                        help="target length in minutes for the claude writer (default: 5, lands ~6)")
    parser.add_argument("--music", action=argparse.BooleanOptionalAction, default=None,
                        help="lay the intro cue, stings and cold-open bed "
                             "(default: HN_RADIO_MUSIC, which defaults to on)")
    args = parser.parse_args(argv)

    episode_date = date.fromisoformat(args.date) if args.date else date.today()
    writer = ClaudeWriter(target_minutes=args.minutes) if args.writer == "claude" else PanelWriter()

    try:
        episode = run_panel(edition=args.edition, n_stories=args.stories,
                            n_comments=args.comments, episode_date=episode_date, writer=writer,
                            with_music=args.music)
    except Exception as e:
        print(f"\nPipeline failed: {e}", file=sys.stderr)
        return 1

    print(f"\nPlay it:  open {episode.audio_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
