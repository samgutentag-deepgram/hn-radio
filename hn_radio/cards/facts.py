"""What a card says, pulled out of one episode.json. No drawing, no measuring, no Pillow.

FOUR FACTS, and they are the ask rather than a design preference. Marketing wants the card inside
the Flux TTS lifecycle emails as proof of what a developer's credits actually buy, so every card
carries: what this is, what THIS episode cost in dollars, what that rate means at a glance, and
where to go next in text a reader can retype.

THE COST IS AN INPUT AND NEVER A CONSTANT. Every figure below is read out of the `cost` block
`hn_radio/pricing.py` wrote, which is arithmetic on the exact text that was billed. Nothing here
computes a price, rounds a rate, or carries a fallback number: an episode with no cost block gets
no card, because a card quoting a made-up price is worse than no card at all. The archive today
spans $0.1004 to $0.2724 an episode and the real rate is not confirmed, so the layout is checked
at $0.06 and $0.20 as well (see `scripts/make_share_cards.py`).

WHAT IS DELIBERATELY NOT HERE: a per-recipient credit balance. "You have $143 left" is an email-tool
merge field in the email body, not a pixel in a shared image that gets forwarded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from . import tokens

# Titles have read "Morning Edition: ..." since the show went two-a-day; `pipeline.SLOT_TITLES`
# writes that prefix. Read back off the title rather than re-deriving it from the id, so there is
# one authored label and the card cannot disagree with the page. Episodes from before the split
# have no prefix and no slot, and they get no edition line rather than an invented one.
_EDITION_PREFIX = re.compile(r"^([A-Z][A-Za-z]+ Edition):\s*")
_ID_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


class NoCost(ValueError):
    """The episode has no usable `cost` block, so there is nothing honest to put on a card."""


@dataclass(frozen=True)
class Speaker:
    name: str
    voice_id: str


@dataclass(frozen=True)
class CardFacts:
    """Every string and number the two compositions place. Strings, already formatted."""
    episode_id: str
    dateline: str            # "MORNING EDITION · SEPTEMBER 16, 2026"
    hero: str                # "$0.24"
    hero_label: Tuple[str, ...]
    credit_line: str         # "$200 of signup credit covers 819 episodes like this one"
    url_text: str            # "dg-devrel-hn-radio.fly.dev/e/2026-09-16-am"
    url_full: str
    meta: str                # "6:15 · 3 stories"
    speakers: Tuple[Speaker, ...]
    alt: str
    usd: float
    episodes_per_credit: int
    fingerprint: str = field(compare=False)


def _duration(seconds: Optional[float]) -> str:
    """m:ss. The site's `format.mmss` in Python; four characters, and it never wraps a line."""
    total = int(round(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


def _edition_and_date(episode_id: str, title: str) -> Tuple[str, str]:
    """("Morning Edition", "September 16, 2026"). Either half can be empty.

    Returned as a pair rather than one joined string because the card and the alt text want
    different joins: the card sets them upper-case around a middot, and the alt text has to read
    as a sentence, where "Morning Edition · September 16" is a typographic artifact.
    """
    edition = _EDITION_PREFIX.match(title or "")
    m = _ID_DATE.match(episode_id)
    when = ""
    if m:
        y, mo, d = (int(x) for x in m.groups())
        # Written out rather than %B'd so the string does not change with the container's locale,
        # which is a real difference between a Mac and python:3.12-slim.
        when = f"{_MONTHS[mo - 1]} {d}, {y}"
    return (edition.group(1) if edition else ""), when


_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


def _speakers(segments: List[dict]) -> Tuple[Speaker, ...]:
    """The two people on this episode: the anchor, and whoever holds the second chair.

    DEGRADES TO THE OLD SHOW RATHER THAN FAILING ON IT. Episodes before the two-hander have three
    topic desks sharing one voice, so "the co-host" is not a thing they have; the first desk
    segment is the honest answer and its orb is the right colour, which is all the card claims.
    Returns one speaker, or none, rather than padding with a stand-in.
    """
    anchor = next((s for s in segments if s.get("role") == "anchor" and s.get("voice_id")), None)
    second = next((s for s in segments if s.get("desk") == "cohost" and s.get("voice_id")), None)
    if second is None:
        second = next((s for s in segments
                       if s.get("role") == "desk" and s.get("voice_id")
                       and s.get("voice_id") != (anchor or {}).get("voice_id")), None)
    out = [Speaker(str(s.get("speaker_key") or "").strip(), s["voice_id"])
           for s in (anchor, second) if s]
    return tuple(out)


def _credit_line(credit_usd: float, per_credit: int) -> str:
    """The rate translated into something a reader can hold: how many shows the credit buys.

    "signup credit" and not "free credits" or "starter credits", because that is the phrase
    episode.html's receipt uses and the two sit a click apart.
    """
    return (f"${credit_usd:,.0f} of Deepgram signup credit covers "
            f"{per_credit:,} episodes like this one")


def _alt(edition: str, when: str, hero: str, credit_line: str, url: str) -> str:
    """Alt text carrying the cost and the pitch, because many clients block images by default.

    It is not a description of the picture. A reader whose client dropped the image should end up
    with the same four facts a reader who saw it got, in a sentence they can act on; "a dark card
    with two glowing orbs" would be a faithful description and a wasted impression.
    """
    said = " for ".join(filter(None, (edition, when))) or "an episode"
    return (f"Hacker News Radio, {said}. This episode cost {hero} to render end to end with "
            f"Deepgram Flux text to speech. {credit_line}. Listen at {url}")


def facts_for(episode: dict, app_url: str) -> CardFacts:
    """Build the facts for one parsed episode.json. Raises `NoCost` when it cannot be priced."""
    cost = episode.get("cost") or {}
    usd = cost.get("usd")
    per_credit = cost.get("episodes_per_credit")
    if usd is None or not cost.get("characters") or not per_credit:
        raise NoCost(f"{episode.get('id')} has no usable cost block")

    episode_id = episode["id"]
    edition, when = _edition_and_date(episode_id, episode.get("title", ""))
    dateline = " · ".join(filter(None, (edition, when))).upper()
    # Two decimals, not the receipt's four. The page is where you check arithmetic; a card is a
    # glance, and every value between $0.06 and $0.99 is five glyphs wide, so the hero never
    # reflows as the rate moves.
    hero = f"${usd:,.2f}"
    credit_line = _credit_line(float(cost.get("credit_usd") or 0), int(per_credit))
    url_text = f"{app_url.split('://', 1)[-1]}/e/{episode_id}"
    url_full = f"{app_url}/e/{episode_id}"
    stories = len(episode.get("source_items") or [])

    return CardFacts(
        episode_id=episode_id,
        dateline=dateline,
        hero=hero,
        # Reads on from the hero as one sentence: "$0.24 to render this whole episode with
        # Deepgram Flux text to speech." Two lines because one at this size is 40 characters of
        # 27px type across a column that is 700 wide.
        hero_label=("to render this whole episode with",
                    "Deepgram Flux text to speech"),
        credit_line=credit_line,
        url_text=url_text,
        url_full=url_full,
        meta=" · ".join(filter(None, [
            _duration(episode.get("duration_seconds")),
            f"{stories} stories" if stories != 1 else "1 story",
        ])),
        speakers=_speakers(episode.get("segments") or []),
        alt=_alt(edition, when, hero, credit_line, url_text),
        usd=float(usd),
        episodes_per_credit=int(per_credit),
        fingerprint=_fingerprint(episode_id, dateline, hero, credit_line, url_text),
    )


def _fingerprint(*parts: str) -> str:
    """What decides whether a card on disk is still current.

    Every string the card PAINTS, plus `CARD_VERSION`. Not the whole episode.json: a chapter
    backfill or a retitle that does not change the dateline must not rerender 63 cards on the next
    boot, and a rate change or a restyle must. Same idea as `pricing.backfill`'s staleness check,
    for the same reason: `rebuild_site` runs on every deploy.
    """
    # Read off the module rather than bound at import, so bumping it in a running
    # process (a test, a REPL) invalidates the archive the way an edit to the file does.
    h = hashlib.sha1(f"v{tokens.CARD_VERSION}".encode())
    for p in parts:
        h.update(b"\x00")
        h.update(p.encode())
    return h.hexdigest()[:16]


def load(episode_json: Path, app_url: str) -> CardFacts:
    return facts_for(json.loads(Path(episode_json).read_text()), app_url)
