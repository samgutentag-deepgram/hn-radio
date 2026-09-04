"""De-slop gate: catch the machine fingerprints in a script before it costs a Flux render.

Catches the countable, high-precision constructions that read as AI-written: the "it's not X,
it's Y" (DiGiorno) construct and the "worth ~ing" insight hedge. Deliberately skips a pet-word
blacklist (delve, toolkit, gap, ...): this show discusses AI and dev tooling daily, and "toolkit"
is exactly the kind of domain term a blacklist would flag by mistake. The real tell -- a mixed
metaphor that reads fine at a glance and falls apart the moment you picture it -- is conceptual,
not lexical, and no regex catches that; it still needs a human read.

Runs over the WRITER's segments only, before `pipeline._intro_segments` /
`_outro_segments` wrap them. The fixed intro and outro are reviewed copy, not generated text, so
linting them would just be noise.

Thresholds are counts, not zero-tolerance singles, except where there's no legitimate use at
all. A script here is a few hundred words of two people talking; one "that's not X, it's Y" is a
normal rhetorical move in banter, and a cluster is what actually reads as machine-written.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from .models import ScriptSegment

# (name, pattern, why, max allowed before it flags).
RULES: List[Tuple[str, "re.Pattern[str]", str, int]] = [
    # The construct, not the negation. This used to match every "is not <word>" and every ", not
    # <word>", which is ordinary speech ("he is not entirely sure", "specified down to the disks,
    # not rented capacity"), and on the 32 Claude scripts then on the feed it flagged two that a
    # listener would never have noticed while knocking two of four live episodes into the fallback.
    # What reads as machine-written is the SHAPE: a negated clause, a break, then a clause that
    # supplies the replacement ("is not a bug, it's a feature"; "not the accuracy, it is the price
    # tag"; "not X, but Y"). Measured on the same 32 scripts this matches 0 to 2 per script, and
    # every match is the real thing.
    ("digiorno",
     re.compile(r"\b(?:is|are|was|were|it's|that's|this is|there's)\s+not\s+[^.,;?!]{1,60}[.,;]\s*"
                r"(?:it|that|this|they|these|those|what)(?:'s|'re| is| are| was)\b"
                r"|\bnot\s+[^.,;?!]{1,40},\s*(?:but|it's|it is|that's)\b", re.I),
     "the 'it's not X, it's Y' construct -- the single most reliable AI tell", 2),
    ("worth-ing",
     re.compile(r"\bworth\s+[a-z]+ing\b|\bis worth\b", re.I),
     "the insight hedge (\"worth sitting with\") -- assert the thing instead of hedging it", 0),
    ("self-validation",
     re.compile(r"\band that matters\b|\bhere'?s the thing\b", re.I),
     "tells the listener the previous line was important instead of earning it", 1),
]


def lint(segments: List[ScriptSegment]) -> List[Tuple[str, int, str]]:
    """Every rule that exceeds its threshold across the whole script, combined.

    Combined across segments rather than checked per-segment: a script is one continuous read to
    a listener's ear, and two instances of a construction split across two segments are still a
    cluster.
    """
    text = " ".join(s.text for s in segments if s.text)
    hits = []
    for name, pattern, why, max_allowed in RULES:
        count = len(pattern.findall(text))
        if count > max_allowed:
            hits.append((name, count, why))
    return hits


def gate(segments: List[ScriptSegment]) -> None:
    """Raise if the script fails the de-slop pass. The caller decides what happens next.

    `pipeline.run_panel` catches this exactly where it catches a writer exception, and falls back
    to `PanelWriter` rather than take the show off air over a regex hit -- the alternative is a
    nightly show that can silently stop publishing over a false positive nobody is watching for.
    """
    hits = lint(segments)
    if hits:
        detail = "; ".join(f"{name} x{count} ({why})" for name, count, why in hits)
        raise RuntimeError(f"de-slop gate failed: {detail}")
