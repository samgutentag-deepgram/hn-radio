"""Change one published episode's title in place and rebuild the site.

    python scripts/retitle.py 2026-09-04-am --prefix "Morning Edition"
    python scripts/retitle.py 2026-09-04-am --title "Exactly this title"

Written for the day the slot prefix landed: the first scheduled episode had already been published
with a plain title. Edits `episode.json` only, the one file the feed and the index are built from,
and then runs the same `publish.rebuild_site` every generator runs, so the feed, `index.json` and
`voices.json` all agree with it. The audio, script and transcript are untouched.

`--prefix` is idempotent: an episode that already carries it is left alone and reported as such.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config, publish
from hn_radio.jsonio import write_json


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="retitle.py", description=__doc__.splitlines()[0])
    p.add_argument("episode_id", help="directory name under the episodes dir, e.g. 2026-09-04-am")
    how = p.add_mutually_exclusive_group(required=True)
    how.add_argument("--prefix", help='prepend "<prefix>: " unless the title already starts with it')
    how.add_argument("--title", help="replace the title outright")
    args = p.parse_args(argv)

    path = config.EPISODES_DIR / args.episode_id / "episode.json"
    if not path.exists():
        print(f"no such episode: {path}", file=sys.stderr)
        return 1
    data = json.loads(path.read_text())
    old = data.get("title", "")
    if args.title is not None:
        new = args.title
    elif old.startswith(f"{args.prefix}:"):
        print(f"{args.episode_id}: already titled {old!r}, nothing to do")
        return 0
    else:
        new = f"{args.prefix}: {old}"
    data["title"] = new
    write_json(path, data)
    publish.rebuild_site(config.EPISODES_DIR)
    print(f"{args.episode_id}: {old!r} -> {new!r}; feed and index rebuilt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
