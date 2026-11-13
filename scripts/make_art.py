#!/usr/bin/env python3
"""Write per-episode art: <episode dir>/art.jpg, 1400x1400.

The edition and the date over that day's felt colorway (the same per-day rule as web/felt.js),
the stories in Azeret Mono, and the episode's cast as a group bubble: the host in the biggest seat,
then the other voices in order of appearance, voices without an official card left out. New
episodes get this at publish time (`hn_radio.publish`); this script is for the archive.

Usage:
    uv run python scripts/make_art.py episodes/2026-11-12-pm ...   # given dirs
    uv run python scripts/make_art.py --all                        # every episode in the archive

`--all` reads HN_RADIO_EPISODES_DIR (config.EPISODES_DIR), so a pulled copy of the archive can be
drawn without touching episodes/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hn_radio import art, config  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write per-episode art.")
    ap.add_argument("episodes", nargs="*", type=Path, help="episode directories")
    ap.add_argument("--all", action="store_true", help="every episode dir with an episode.json")
    args = ap.parse_args(argv)

    dirs = list(args.episodes)
    if args.all:
        dirs += sorted(p.parent for p in config.EPISODES_DIR.glob("*/episode.json"))
    if not dirs:
        ap.error("name episode directories or pass --all")
    failed = 0
    for ep in dirs:
        if not (ep / "episode.json").is_file():
            print(f"skip {ep}: no episode.json")
            failed += 1
            continue
        out = art.try_make_episode_art(ep)
        if out is None:
            failed += 1
            continue
        print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    print(f"{len(dirs) - failed} of {len(dirs)} episodes drawn")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
