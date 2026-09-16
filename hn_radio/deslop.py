"""De-slop gate: catch the machine fingerprints in a script before it costs a Flux render.

Catches the countable, high-precision constructions that read as AI-written: the "it's not X,
it's Y" (DiGiorno) construct, the "worth ~ing" insight hedge, the self-validating "and that
matters", the metaphorical "load-bearing", and the assigned-opposition opener ("I'll take the
other side"). Deliberately skips a pet-word blacklist (delve, toolkit, gap, ...): this show
discusses AI and dev tooling daily, and "toolkit" is exactly the kind of domain term a blacklist
would flag by mistake. The real tell -- a mixed metaphor that reads fine at a glance and falls
apart the moment you picture it -- is conceptual, not lexical, and no regex catches that; it still
needs a human read.

Runs over the WRITER's segments only, before `pipeline._intro_segments` /
`_outro_segments` wrap them. The fixed intro, the outro and the cost line are reviewed copy built
from templates, not generated text, so linting them would just be noise.

Thresholds are counts, not zero-tolerance singles, except where there's no legitimate use at
all. A script here is a few hundred words of two people talking; one "that's not X, it's Y" is a
normal rhetorical move in banter, and a cluster is what actually reads as machine-written.

A PERFORMED COMMENT IS NOT THE WRITER'S PROSE, and a rule has to say whether it reads them.
`role == "commenter"` segments carry a real person's words off Hacker News, which HARD RULE 7 in
`writers.py` requires the show to perform faithfully. A threshold-0 rule that reads them fails the
whole episode over somebody else's writing and knocks the show into the deterministic fallback --
which costs a second full render and, on the night of 2026-09-04, failed verification too. Not
hypothetical: two of the eight historical "load bearing" hits on the feed are inside a quoted HN
comment (2026-08-17), so the rule added for it would have rejected that episode for words the show
was obliged to read out exactly as written.

So each rule declares its corpus. `prose_only=True` reads the regulars' lines and skips performed
comments; `prose_only=False` reads everything. Anything aimed at how the WRITER writes wants the
former. The existing three were switched to it as well: they were always about the writer's prose,
and reading quoted comments was only ever a way for them to be wrong.

EVERY RULE HERE WAS MEASURED AGAINST ALL 58 SCRIPTS ON THE FEED BEFORE IT LANDED, which is the
house rule for this file (ledger 2026-09-04, "measure the gate against what it guards before you
trust it"). Counts at the time of writing, over the regulars' prose only:

    digiorno          0-2 per script, every match the real thing
    worth-ing         5 hits in 4 episodes
    self-validation   0 hits
    load-bearing      6 hits in 6 episodes
    assigned-side     6 hits in 6 episodes
"""

from __future__ import annotations

import re
from typing import List, Tuple

from .models import ScriptSegment

# (name, pattern, why, max allowed before it flags, prose_only).
# `prose_only=True` skips `role == "commenter"` segments. See the header: a performed comment is a
# real person's words and the show is required to read them unchanged, so a rule that lints them
# rejects episodes for writing the writer did not do.
RULES: List[Tuple[str, "re.Pattern[str]", str, int, bool]] = [
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
     "the 'it's not X, it's Y' construct -- the single most reliable AI tell", 2, True),
    ("worth-ing",
     re.compile(r"\bworth\s+[a-z]+ing\b|\bis worth\b", re.I),
     "the insight hedge (\"worth sitting with\") -- assert the thing instead of hedging it", 0,
     True),
    ("self-validation",
     re.compile(r"\band that matters\b|\bhere'?s the thing\b", re.I),
     "tells the listener the previous line was important instead of earning it", 1, True),
    # Sam's call, 2026-09-16: "nothing is load bearing". Six hits across six episodes on the feed,
    # every one of them the metaphor rather than a wall -- "doing a lot of load-bearing work in
    # that sentence", "the whole load-bearing sentence", "one word doing the load-bearing". It is a
    # way of saying a detail matters without saying what it does, which is the same failure as
    # `self-validation` above wearing an engineering costume.
    #
    # Zero allowed, and `prose_only` is doing real work: the literal sense ("a load-bearing wall")
    # is rare but constructible on a show that covers building things, and an HN commenter using
    # the metaphor is their business, not the gate's.
    ("load-bearing",
     re.compile(r"\bload[-\s]?bearing\b", re.I),
     "nothing is 'load-bearing' -- say what the thing actually does", 0, True),
    # THE ASSIGNED-OPPOSITION OPENER, and the reason this rule exists is that the PROMPT was
    # asking for it. `writers.py` used to list "a framing they can argue with, where they take the
    # other side" as one of three handoff shapes, and the writer handed that instruction straight
    # back as dialogue: "I'll take the other side" opens a desk turn in six episodes on the feed,
    # including 2026-08-23's "I'll take the other side, but set it up first, because the premise is
    # great", which is the debate format announcing itself out loud.
    #
    # The shape is gone from the prompt (2026-09-16, Sam: "we don't wanna do the segment where I
    # set it up and you defend it or I set it up and you knock it down"). This is the gate that
    # catches it coming back, because a phrasing that produced six episodes of the same move will
    # not disappear just because one list stopped naming it.
    #
    # LEXICAL, AND THEREFORE PARTIAL. It catches the announcement, not the structure. Two people
    # assigned opposite positions with no announcement reads exactly as badly and no regex finds
    # it; that is a human read, like the mixed metaphor in the header.
    ("assigned-side",
     re.compile(r"\b(?:I'?ll|I'?m going to|let me|I will)\s+(?:just\s+)?"
                r"(?:take|play|argue)\s+the\s+other\s+side\b"
                r"|\bdevil'?s advocate\b|\bsteel[-\s]?man(?:ning)?\b", re.I),
     "the debate format announcing itself -- disagree because you read it differently, "
     "not because the structure assigned you a side", 0, True),
]


def lint(segments: List[ScriptSegment]) -> List[Tuple[str, int, str]]:
    """Every rule that exceeds its threshold across the whole script, combined.

    Combined across segments rather than checked per-segment: a script is one continuous read to
    a listener's ear, and two instances of a construction split across two segments are still a
    cluster.

    Two corpora, because `prose_only` rules must not read performed comments. Both are built
    whether or not a rule wants them -- the strings are a few hundred words and building one
    conditionally would put a branch between a rule and the text it is measured against.
    """
    everything = " ".join(s.text for s in segments if s.text)
    prose = " ".join(s.text for s in segments if s.text and s.role != "commenter")
    hits = []
    for name, pattern, why, max_allowed, prose_only in RULES:
        count = len(pattern.findall(prose if prose_only else everything))
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
