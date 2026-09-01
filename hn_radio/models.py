"""Data models for the pipeline. Mirrors the PRD's ScriptSegment and Episode shapes.

Kept as plain dataclasses so every stage passes simple, inspectable objects and
each stage can be understood and tested on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Story:
    """One Hacker News front-page story."""

    id: int
    title: str
    url: Optional[str]
    points: int
    author: str
    num_comments: int
    rank: int  # 1-based position on the front page
    kids: List[int] = field(default_factory=list)  # top-level comment ids
    source_text: Optional[str] = None   # fetched README/article text (v2-2), truncated
    source_kind: str = "none"           # "repo" | "article" | "text" | "none"
    summary: Optional[str] = None       # extractive gloss / talking points for the desk

    @property
    def hn_url(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"


@dataclass
class Comment:
    """One Hacker News comment (raw text is HTML as returned by the HN API)."""

    id: int
    author: str
    text: str  # raw HTML from HN; cleaned in script_assembly
    # No `story_id`. It was written once (`ingest.py`) and read nowhere: the provenance that
    # survives into the artifact is `ScriptSegment.source_hn_id`, and since `fetch_top_comments`
    # runs on exactly one story per episode the caller already holds it as `top_story`. Removed;
    # a comment recording a redundancy is not provenance.


@dataclass
class ScriptSegment:
    """One line of the episode script, tagged with who says it. Plain text only, no markup."""

    order: int
    role: str  # "host" | "commenter" (v1), or "anchor" | "desk" | "commenter" (v2 panel)
    # "host"/"anchor", a regular's display name, or -- for a performed comment -- the HN
    # username, ALWAYS, even though the two-person show reads those comments in a regular's
    # voice. This field is the record of who wrote the words; `voice_id` is who spoke them.
    speaker_key: str
    text: str
    source_hn_id: Optional[int] = None
    voice_id: Optional[str] = None  # filled in by the voices stage
    # v2: which seat spoke -- "anchor" (the host) or "cohost" today, or one of the retired themed
    # desks ("ai" / "maker" / "security" / "drama") in an older episode that predates the
    # two-person show. None for v1, and None for a performed comment even in v2: `recast` reads an untagged
    # commenter line as its "guest" slot and `custom.py` reads `desk` to find a story's coverage.
    desk: Optional[str] = None
    start_seconds: Optional[float] = None  # start offset in the stitched episode; set at render

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Episode:
    """A produced episode: its script, its audio, and where the audio came from."""

    id: str  # e.g. "2026-08-03"
    title: str
    generated_at: str  # ISO 8601
    segments: List[ScriptSegment]
    audio_path: str
    source_items: List[dict]  # [{hn_id, title, url}]
    duration_seconds: float
    edition: str = ""  # v3: which edition produced this episode ("" for v1/legacy)
    summary: str = ""  # 1-2 sentence episode summary for show notes (Claude writer sets it)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

_RECAST_SUFFIX = re.compile(r"(-recast)+$")


def is_recast(episode_id: str) -> bool:
    """True when an episode id names a recast rather than a canonical episode.

    A recast is the same day's script re-rendered with different voices: a live voice-comparison
    demo, not a new episode. It gets its own directory (`<id>-recast`) so the audio is browsable,
    but it must stay out of the feed and out of the episode list, or subscribers see the same news
    twice in different voices.

    Lives here, on the model, because it is a fact about what an episode id MEANS. It was briefly
    a private regex in both `feed.py` and `manifest.py` -- two definitions of one rule, in exactly
    the two modules a test asserts must agree about it.
    """
    return bool(_RECAST_SUFFIX.search(episode_id))
