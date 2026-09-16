"""Price the archive: what every episode on disk cost in Flux TTS, as a table.

The backfill itself is NOT this script's job and never was. `publish.rebuild_site` runs
`pricing.backfill` on every boot and every publish, so the deployed archive prices itself the
first time this code comes up on Fly and there is no migration to remember. This is the thing you
run when you want to LOOK at the result: one row per episode, a total, and the per-episode average
that the "how many episodes does the credit buy" figure rests on.

    uv run python scripts/episode_costs.py                 # report on episodes/
    uv run python scripts/episode_costs.py --write         # also write any missing cost blocks
    uv run python scripts/episode_costs.py --write --force # recompute every block
    uv run python scripts/episode_costs.py --plan growth   # price the same archive on Growth

READ-ONLY WITHOUT `--write`, because the interesting use is checking the arithmetic against the
Deepgram console before trusting a figure the show says out loud, and that should not be a
mutating operation.

Reads the SCRIPT, not the stored cost block, for every row. The two agree once a backfill has run,
and when they do not, the script is right: it is the text that was billed for. A row whose stored
block disagrees is flagged, which is how a rate change or a hand-edited episode.json shows up.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config, pricing  # noqa: E402
from hn_radio.models import is_recast  # noqa: E402


def rows(episodes_dir: pathlib.Path, plan_name: str) -> list:
    out = []
    for script_json in sorted(episodes_dir.glob("*/script.json")):
        ep_id = script_json.parent.name
        try:
            segments = json.loads(script_json.read_text())
        except (OSError, json.JSONDecodeError) as e:
            out.append({"id": ep_id, "error": f"{type(e).__name__}: {e}"})
            continue
        chars = pricing.script_characters(segments)
        episode_json = script_json.parent / "episode.json"
        stored = {}
        if episode_json.exists():
            try:
                stored = json.loads(episode_json.read_text()).get("cost") or {}
            except (OSError, json.JSONDecodeError):
                stored = {}
        out.append({
            "id": ep_id,
            "recast": is_recast(ep_id),
            "segments": len(segments),
            "characters": chars,
            "usd": pricing.cost_usd(chars, plan_name),
            # None when nothing is stored yet, which reads differently from "stored and wrong".
            "stored_usd": stored.get("usd"),
            "stale": bool(stored) and stored.get("characters") != chars,
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="episode_costs", description=__doc__.split("\n")[0])
    ap.add_argument("--episodes-dir", default=str(config.EPISODES_DIR))
    ap.add_argument("--plan", default=pricing.plan(), choices=sorted(pricing.RATES_USD_PER_1K),
                    help="which published rate to price against (default: the configured plan)")
    ap.add_argument("--write", action="store_true", help="write missing cost blocks to episode.json")
    ap.add_argument("--force", action="store_true", help="with --write, recompute every block")
    args = ap.parse_args(argv)

    episodes_dir = pathlib.Path(args.episodes_dir)
    if not episodes_dir.is_dir():
        print(f"no such episodes directory: {episodes_dir}")
        return 2

    rate = pricing.rate_usd_per_1k(args.plan)
    print(f"Flux TTS, {args.plan}: ${rate:.4f} / 1k characters "
          f"(published {pricing.PRICING_AS_OF}, {pricing.PRICING_URL})")
    print(f"{episodes_dir}\n")

    table = rows(episodes_dir, args.plan)
    if not table:
        print("no episodes on disk")
        return 0

    print(f"{'episode':16} {'segs':>5} {'chars':>7} {'cost':>9}  note")
    priced = []
    for r in table:
        if "error" in r:
            print(f"{r['id']:16} {'':>5} {'':>7} {'':>9}  unreadable: {r['error']}")
            continue
        notes = []
        if r["recast"]:
            notes.append("recast, not in the feed")
        if r["stored_usd"] is None:
            notes.append("no stored cost")
        elif r["stale"]:
            notes.append(f"stored ${r['stored_usd']:.4f} disagrees")
        print(f"{r['id']:16} {r['segments']:5} {r['characters']:7} ${r['usd']:8.4f}  "
              + "; ".join(notes))
        priced.append(r)

    canonical = [r for r in priced if not r["recast"]]
    total_chars = sum(r["characters"] for r in canonical)
    total_usd = sum(r["usd"] for r in canonical)
    mean_usd = total_usd / len(canonical) if canonical else 0.0
    print(f"\n{len(canonical)} canonical episodes: {total_chars:,} characters, ${total_usd:.2f}")
    if len(priced) != len(canonical):
        print(f"  plus {len(priced) - len(canonical)} recast(s), excluded from the totals: "
              "same script, and priced separately only because the page serves them")
    print(f"average episode: {total_chars // max(len(canonical), 1):,} characters, ${mean_usd:.4f}")
    print(f"${pricing.FREE_CREDIT_USD:.0f} signup credit at that average: "
          f"{pricing.episodes_per_credit(mean_usd):,} episodes")

    if args.write:
        print()
        result = pricing.backfill(episodes_dir, force=args.force, log=print)
        if result["failed"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
