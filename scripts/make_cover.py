#!/usr/bin/env python3
"""Render the podcast's album art on the Deepgram 2026 brand kit.

The Deepgram logo alone at the top, the show name set large in Work Sans, and the bench in one
group bubble: both hosts in the biggest seats, then four carded voices, each the official
voice-card object on its tile. The ground is one of the kit's felt colorways, softened so a 3000px
JPEG stays near 500 KB. Everything is drawn by `hn_radio.art`, which also draws the per-episode art
(`scripts/make_art.py`), so the two cannot drift apart.

It used to be a honeycomb of 36 molten orbs, one per voice. The orbs went with the rebrand: the
kit's voice identity is the voice card, and a voice with no card is left out of a bubble rather
than given an invented one.

Writes `<episodes dir>/cover.jpg` (config.COVER_FILE), honoring HN_RADIO_EPISODES_DIR, or the path
given with --out. Run it with `uv run python scripts/make_cover.py`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hn_radio import art  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write the show cover.")
    ap.add_argument("--out", type=Path, help="where to write it (default: <episodes dir>/cover.jpg)")
    args = ap.parse_args(argv)
    out = art.make_cover(args.out)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
