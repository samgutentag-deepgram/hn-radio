"""Generate the daily front-page episodes for a date range, one per day (id = YYYY-MM-DD).

Past days use the Algolia HN Search lookback (an approximation of that day's front page); today
uses the live front page.

--clean removes VARIANT RENDERS of a daily episode: `2026-08-04-recast`, `2026-08-04-new`, and
anything else shaped like a date with a suffix. That is all it was ever for, and it is now all it
can do.

IT USED TO BE AN ALLOW-LIST, AND THAT WAS A LOADED GUN. The rule was "delete any directory whose
name is not YYYY-MM-DD, except `samples`", which is safe only as long as nobody adds a directory.
Two months later `episodes/` held `_ads`, `_coldopen`, `_music`, `_pace` and `_voices`, so
`--clean` had quietly become "delete 349 MB of paid renders". `_ads` (hand-reviewed ad reads) and
`_music` (228 MB of variant renders) have NO GENERATOR anywhere in this repo and are
unrecoverable at any price; `_voices` is the Flux preview cache that `voice_preview.py` skips a
call for when the file is already there. The allow-list predated all five: `_clean` landed
first, the directories over the following two weeks.

The fix is the SHAPE, not the list. Adding `_` to the exceptions would have left the same
structure that had already failed once for `samples`, and would fail again for the next directory
someone adds. A delete-list can only ever delete what it names, so a new directory is safe by
default instead of dangerous by default. `scripts/recast_archive.py` documents the same rule for
the same reason.

It also prints what it is about to remove and asks, because a sweep that runs silently is a sweep
whose blast radius nobody checks. `--dry-run` prints and exits.

Run:
    uv run python scripts/backfill.py 2026-08-01 2026-08-04                      # generate
    uv run python scripts/backfill.py 2026-08-01 2026-08-04 --clean --dry-run    # what would go
    uv run python scripts/backfill.py 2026-08-01 2026-08-04 --clean              # sweep, generate
    uv run python scripts/backfill.py 2026-08-01 2026-08-04 --clean --yes        # no prompt
"""

import argparse
import pathlib
import re
import shutil
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config, pipeline, publish

DAILY = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # a clean daily episode id

# What --clean is allowed to remove: a daily id with a suffix, e.g. `2026-08-04-recast` or
# `2026-08-04_new`. Anchored at both ends, and the separator is required, so a bare daily episode
# can never match it. Nothing else in episodes/ is deletable by this script, by construction.
VARIANT = re.compile(r"^\d{4}-\d{2}-\d{2}[-_].+$")


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _variants(episodes_dir: pathlib.Path) -> list:
    """Variant renders, and nothing else. See the module docstring for why this is a delete-list."""
    return [c for c in sorted(episodes_dir.iterdir()) if c.is_dir() and VARIANT.match(c.name)]


def _clean(episodes_dir: pathlib.Path, dry_run: bool, assume_yes: bool) -> bool:
    """Show, confirm, then remove. Returns False if the caller should stop."""
    targets = _variants(episodes_dir)
    if not targets:
        print("Sweep: no variant renders to remove.")
        return not dry_run

    total = sum(f.stat().st_size for c in targets for f in c.rglob("*") if f.is_file())
    print(f"Sweep would remove {len(targets)} variant render(s), {total / 1e6:.1f} MB:")
    for c in targets:
        print(f"  {c.name}")
    kept = [c.name for c in sorted(episodes_dir.iterdir())
            if c.is_dir() and not VARIANT.match(c.name) and not DAILY.match(c.name)]
    if kept:
        print(f"Leaving alone: {', '.join(kept)}")

    if dry_run:
        print("--dry-run: nothing removed.")
        return False
    if not assume_yes:
        # input(), not a flag default, so the destructive path cannot be reached by a typo in a
        # longer command line. Anything but an exact "yes" stops.
        if input("Remove them? [yes/N] ").strip().lower() != "yes":
            print("Aborted; nothing removed.")
            return False
    for c in targets:
        shutil.rmtree(c)
        print(f"  removed {c.name}")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Backfill daily HN Radio front-page episodes.")
    p.add_argument("start", help="first date YYYY-MM-DD")
    p.add_argument("end", help="last date YYYY-MM-DD (inclusive)")
    p.add_argument("--clean", action="store_true",
                   help="first delete VARIANT renders of a daily episode (YYYY-MM-DD-<suffix>)")
    p.add_argument("--dry-run", action="store_true",
                   help="with --clean: print what would be removed and exit without generating")
    p.add_argument("--yes", action="store_true", help="with --clean: skip the confirmation prompt")
    args = p.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    if args.clean:
        if not _clean(config.EPISODES_DIR, args.dry_run, args.yes):
            return 0
    elif args.dry_run:
        p.error("--dry-run only applies to --clean")

    for day in _daterange(start, end):
        print(f"\n=== {day.isoformat()} (front page) ===")
        pipeline.run_panel(edition="frontpage", episode_date=day)

    print("\nRebuilding feed + JSON API...")
    publish.rebuild_site(config.EPISODES_DIR)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
