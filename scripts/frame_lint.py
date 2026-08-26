#!/usr/bin/env python3
"""Flag lines where the show breaks its own frame. Zero TTS calls, zero LLM calls.

HN Radio is a produced show, not a research report. A line that narrates where the text came
from, or how much of it there was, tells the listener there is a pipeline behind the voices.
Two real examples from rendered episodes:

    "That's genuinely all the page gives us, and I'd rather stop there than guess."   (08-07)
    "We have a title and a link, and no fetched page, so what I can tell you is..."   (08-01)

This reads `episodes/<id>/script.json`, which is the WORDS, so it costs nothing and runs over
every past episode at once. That matters: it is the before/after measurement for a prompt
change, and a prompt change is the one lever on this show that does require a paid re-render.
Lint the script first, render second.

ATTRIBUTION IS NOT A VIOLATION. Naming who said a thing is normal broadcast practice and the
show should keep doing it. What is banned is assessing the SOURCE ITSELF: whether a page
existed, how much of it was fetched, whether the show has enough to go on.

    allowed:  "The piece from OpenRSS argues Google ran a classic embrace-extend-extinguish play"
    allowed:  "JFrog's Afek Berger writes that a newly created repo published a batch of CVEs"
    banned:   "From the write-up: Automating discovery to accelerate science"
    banned:   "That's genuinely all the page gives us."

Usage:
    python scripts/frame_lint.py                     # every episode
    python scripts/frame_lint.py 2026-08-07          # one episode
    python scripts/frame_lint.py --quiet             # counts only, for a before/after diff

Exit code is 1 when anything is flagged, so this can gate a render.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hn_radio import config  # noqa: E402

# Each rule is (name, compiled pattern, why it breaks the frame). The `why` is printed with the
# hit, because a bare regex name does not tell a reader what to write instead.
SOURCING_NARRATION = [
    ("fetch-prefix",
     r"\bfrom the (write-?up|readme|page|article|source)\b",
     "names the artifact the pipeline fetched instead of the person who wrote it"),
    ("no-page",
     r"\bno (fetched |source )?(page|write-?up|article)\b|\bpage (was )?(not |n't )?fetch",
     "tells the listener a fetch failed"),
    ("headline-only",
     r"\b(just|only) (a |the )?(title|headline)\b|\bheadline only\b|\btitle and a link\b",
     "describes what the ingest step returned"),
    ("page-as-supplier",
     r"\bthe page (gives|gave|has|had|doesn'?t|does not|only|offers)\b",
     "treats the source page as the show's supplier, on air"),
    ("source-text",
     r"\bsource (text|material)\b|\bfetched\b",
     "pipeline vocabulary spoken aloud"),
]

COVERAGE_HEDGE = [
    ("rather-not-guess",
     r"\bthan guess\b|\brather (not guess|stop there)\b|\b(won'?t|not going to) speculate\b",
     "announces a decision not to say more, which is a production note, not content"),
    ("thats-all-there-is",
     r"\b(that'?s|this is) (genuinely |basically |about )?all (the|we|i)\b"
     r"|\ball (we|i) (have|know|get|can (tell|say))\b",
     "assesses the adequacy of the show's own coverage"),
    ("not-much-here",
     r"\bnot much (beyond|to go on|more|there)\b|\bbeyond the headline\b"
     r"|\bcan'?t (tell|say) (you )?much\b|\bhard to say (more|much)\b"
     r"|\b(light|short) on detail\b",
     "tells the listener the story is thin instead of simply saying less about it"),
    ("self-discipline",
     r"\bdisciplined about (that|this|it)\b|\bleave it (there|at that)\b",
     "narrates the writer's own restraint"),
]

RULES = [("sourcing-narration", SOURCING_NARRATION), ("coverage-hedge", COVERAGE_HEDGE)]
COMPILED = [(cat, name, re.compile(pat, re.I), why)
            for cat, rules in RULES for name, pat, why in rules]


def check(text: str):
    """Every rule this line trips, as (category, rule_name, matched_text, why)."""
    hits = []
    for cat, name, pat, why in COMPILED:
        m = pat.search(text)
        if m:
            hits.append((cat, name, m.group(0), why))
    return hits


def _segments(path: Path):
    """script.json is a bare list of segment dicts; tolerate a wrapper object too."""
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    return data.get("segments") or data.get("script") or []


def lint_episode(path: Path):
    """(episode_id, [(segment, hits), ...]) for one script.json."""
    flagged = []
    for seg in _segments(path):
        if not isinstance(seg, dict):
            continue
        # Commenter lines are REAL Hacker News comments performed verbatim. A stranger quoted on
        # the show is not the show speaking, so their words cannot break the show's frame, and
        # linting them would flag the source material rather than the script.
        if seg.get("role") == "commenter":
            continue
        hits = check(seg.get("text") or "")
        if hits:
            flagged.append((seg, hits))
    return path.parent.name, flagged


def _wrap(text: str, width: int = 88, indent: str = " " * 6) -> str:
    out, line = [], indent
    for word in text.split():
        if len(line) + len(word) + 1 > width and line.strip():
            out.append(line)
            line = indent
        line += word + " "
    out.append(line.rstrip())
    return "\n".join(out)


def main(argv):
    quiet = "--quiet" in argv
    args = [a for a in argv if not a.startswith("-")]

    root = config.EPISODES_DIR
    if args:
        paths = [root / a / "script.json" for a in args]
        missing = [p for p in paths if not p.exists()]
        if missing:
            print(f"no script.json for: {', '.join(p.parent.name for p in missing)}")
            return 2
    else:
        # Sorted so a before/after diff of two runs lines up. `_pace`/`_music` experiment dirs
        # hold no script.json of their own, so the glob skips them for free.
        paths = sorted(root.glob("*/script.json"))

    total, episodes_hit = 0, 0
    for path in paths:
        ep, flagged = lint_episode(path)
        if not flagged:
            continue
        episodes_hit += 1
        for seg, hits in flagged:
            total += len(hits)
            if quiet:
                continue
            who = f"{seg.get('role')}/{seg.get('speaker_key')}"
            cats = ", ".join(sorted({f"{c}:{n}" for c, n, _, _ in hits}))
            print(f"{ep}  {who:<18} {cats}")
            for _, _, matched, why in hits:
                print(f"      -> {matched!r}: {why}")
            print(_wrap(f'"{seg.get("text")}"'))
            print()

    print(f"{total} flag(s) across {episodes_hit}/{len(paths)} episodes  (0 API calls)")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
