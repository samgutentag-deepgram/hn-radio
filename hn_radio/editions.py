"""Stage v2-3 - Editions. Let a listener steer the show toward what they like.

An edition reweights story SELECTION. Selection is a cheap heuristic over popularity, topical
match, and a product-launch detector, so it stays predictable and needs no LLM.

  makers    (default) promote personal repos/blogs/Show HN; demote product launches
  ai                  promote models/research/ML
  security            promote breaches/CVEs/crypto/systems
  frontpage           no skew; straight popularity order

This module also owns the TOPIC keyword tables, which is new as of 2026-08-20 and worth
explaining because they used to live in `cast.py`.

They were the themed correspondent desks' routing keywords: each desk scored a story's title and
domain, the best score won, and the desk that won covered the story out loud. The show went
two-person that day (a consistent host plus a rotating co-host, see `cast.py`), so nothing routes
a story to a SPEAKER any more. But the keywords were never only about speakers: `_weight` below
has always used them to skew the ai/security/makers editions, and `custom.py` uses them to filter
the build-your-own pool. Both are selection concerns, so the tables came here with the code that
actually reads them rather than dying with the desks.

Two consequences to keep straight. A "topic" here names a subject, not a character: nothing in
the audio corresponds to it. And `beat`/`persona` ride along only because `custom.py` still
builds Casts out of these roles for its build-your-own editions; the daily show ignores them.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import Story

EDITIONS = ("makers", "ai", "security", "frontpage")
DEFAULT_EDITION = "makers"

# Subject -> the signals that say a story is about it, plus the persona `custom.py` still needs.
# Verbatim from the desks these replaced, so an edition's skew is bit-for-bit what it was before
# the two-person change: this move was meant to preserve selection exactly, and a "tidied" keyword
# list would have quietly reweighted every ai and security episode.
TOPICS: Dict[str, dict] = {
    "ai": {
        "beat": "Models, research, ML tooling and drama.",
        "persona": "Precise and a little skeptical of hype.",
        "keywords": ["llm", "gpt", "claude", "model", "models", "ml", "machine learning",
                     "neural", "transformer", "openai", "anthropic", "deepmind", "inference",
                     "fine-tune", "fine tuning", "embedding", "diffusion", "agent", " ai ",
                     "a.i.", "chatbot", "prompt"],
        "domains": [],
    },
    "maker": {
        "beat": "Personal blogs, GitHub repos, Show HN, 'I built' posts.",
        "persona": "Genuinely delighted by clever handmade things.",
        "keywords": ["show hn", "i built", "i made", "i wrote", "my ", "built a", "writing a",
                     "side project", "weekend project", "from scratch", "in rust", "in go",
                     "in zig", "a tool for", "homemade", "hobby"],
        "domains": ["github.com", "gitlab.com", "codeberg.org", "sourcehut", "bearblog",
                    "substack.com", ".dev/", "blog."],
    },
    "security": {
        "beat": "Breaches, CVEs, cryptography, systems.",
        "persona": "Dry, unflappable, has seen it all before.",
        "keywords": ["cve", "vulnerability", "vuln", "exploit", "breach", "malware",
                     "ransomware", "security", "cryptography", "encryption", "tls", "ssl",
                     "zero-day", "0-day", "backdoor", "phishing", "rce", "auth", "leak"],
        "domains": [],
    },
}

# The subject a story falls into when it matches nothing. Hacker News is mostly people showing
# each other things they made, so "maker" is the honest default rather than an arbitrary one.
DEFAULT_TOPIC = "maker"


def _haystack(title: str, url: Optional[str]) -> str:
    return f"{title} {url or ''}".lower()


def topic_score_text(title: str, url: Optional[str], topic: str) -> int:
    """How strongly a title+url matches a subject (0 = no match).

    Takes the two fields rather than a Story because `custom.py` scores stories it read back out
    of a rendered episode's `source_items`, which are plain dicts and never became Story objects.
    Requiring a Story there meant building a fake one, which is how the two call sites would
    eventually disagree about what a Story needs.
    """
    spec = TOPICS.get(topic)
    if not spec:
        return 0
    hay = _haystack(title, url)
    score = sum(1 for kw in spec["keywords"] if kw in hay)
    score += sum(2 for dom in spec["domains"] if dom in hay)  # domain is a strong signal
    return score


def topic_score(story: Story, topic: str) -> int:
    """`topic_score_text` for a Story. No fallback, on purpose: `_weight` needs to tell
    "matched nothing" apart from "matched the default", and a scorer that falls back cannot."""
    return topic_score_text(story.title, story.url, topic)


def topic_of_text(title: str, url: Optional[str] = None,
                  default: Optional[str] = DEFAULT_TOPIC) -> Optional[str]:
    """The subject that best fits a title+url, or `default` when nothing matches.

    Deterministic on a tie: `TOPICS` is ordered and insertion order decides, so the same front
    page always produces the same build-your-own pool. Dict iteration order would otherwise be
    the only thing standing between a listener and a pool that reshuffles between page loads.
    """
    best, best_score = None, 0
    for topic in TOPICS:
        score = topic_score_text(title, url, topic)
        if score > best_score:
            best, best_score = topic, score
    return best if best is not None else default


def topic_of(story: Story, default: Optional[str] = DEFAULT_TOPIC) -> Optional[str]:
    """`topic_of_text` for a Story."""
    return topic_of_text(story.title, story.url, default)

EDITION_TITLES = {
    "makers": "Makers",
    "ai": "AI",
    "security": "Security",
    "frontpage": "Front Page",
}

# Signals that a story is a company/product launch rather than a personal build.
_LAUNCH_SIGNALS = [
    "launches", "launch", "announces", "announcing", "introducing", "unveils",
    "raises", "series a", "series b", "series c", "funding", "acquires",
    "acquisition", "now available", "generally available", " ga ", "partnership",
    "valuation", "ipo",
]


def is_launch(story: Story) -> bool:
    hay = f"{story.title} {story.url or ''}".lower()
    return any(sig in hay for sig in _LAUNCH_SIGNALS)


def _weight(story: Story, edition: str) -> float:
    if edition == "frontpage":
        return 1.0
    if edition == "makers":
        maker = topic_score(story, "maker") > 0
        if is_launch(story) and not maker:
            return 0.3
        return 1.8 if maker else 1.0
    if edition == "ai":
        return 2.0 if topic_score(story, "ai") > 0 else 0.7
    if edition == "security":
        return 2.5 if topic_score(story, "security") > 0 else 0.6
    return 1.0


def select_stories(stories: List[Story], edition: str = DEFAULT_EDITION,
                   n: int = 5) -> List[Story]:
    """Pick the top `n` stories for an edition. Popularity times an edition weight.

    A stable sort keeps original front-page rank as the tiebreaker, so equal-scoring stories
    stay in the order HN ranked them.

    Took a `cast` argument until 2026-08-20, purely to reach `cast.score_for` for the keyword
    match. The keywords live here now, so the parameter had nothing left to do; `run_panel` was
    building a whole Cast just to pass it in, which is also how the import-time cast trap
    documented in `cast.active_cast` kept finding new places to happen.
    """
    if edition not in EDITIONS:
        raise ValueError(f"unknown edition {edition!r}; choose from {EDITIONS}")
    scored = sorted(
        stories,
        key=lambda s: max(s.points, 1) * _weight(s, edition),
        reverse=True,
    )
    selected = scored[:n]
    # renumber rank to reflect the edition's running order (1..n)
    for i, s in enumerate(selected, start=1):
        s.rank = i
    return selected
