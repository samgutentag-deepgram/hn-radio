"""Per-episode share cards: the picture marketing drops into a Flux TTS email or a social post.

WHAT THIS IS FOR, in the words of the ask. Lifecycle marketing wants HN Radio inside the Flux TTS
promo emails as proof of what a developer's credits actually buy: "as opposed to having a button
or a link, something to show them." A bare fly.dev URL was explicitly not the deliverable. Each
episode already has a page; this is the visual that points at it.

A STATIC RASTER AND NOTHING ELSE. No iframe, no embedded player, no web font, no JavaScript at
view time, because the destination is an email client and half of them would strip all four. The
URL is therefore VISIBLE TEXT on the card rather than a link on it, and `alt` carries the cost
and the pitch in words, because a great many clients block images by default and those readers
are the ones a lifecycle email most needs to reach.

    tokens.py   every colour, size and spacing value, and the only file a restyle touches
    facts.py    episode.json -> the four facts, as strings
    paint.py    Pillow. Marks, no meaning
    layout.py   where each fact lands on each size
    this file   when a card is (re)built, and what lands on disk beside the episode

TWO SIZES. `card-social.png` at 1200x630 is what Open Graph, Slack, LinkedIn and X all crop
toward. `card-email.png` is 1200x800 displayed at 600, rendered at 2x because a 600px raster is
soft on every phone made since about 2014 and nobody can fix that at send time.

THE COST IS AN INPUT. It is read from the `cost` block `hn_radio/pricing.py` writes, an episode at
a time, and an episode without one gets no card rather than a card with a plausible number on it.

IDEMPOTENT AND CHEAP TO CALL, because `publish.rebuild_site` calls it on every publish and every
app boot, the same way it calls `pricing.backfill`. A card is rebuilt only when the strings it
paints change, or when `tokens.CARD_VERSION` moves because the design did.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..jsonio import write_json
from . import facts as _facts
from . import tokens

# `layout` is NOT imported here, and that is the point: it imports `paint`, which imports Pillow at
# module scope. `publish.py` imports this package, `backend/app.py` imports `publish`, and the
# nightly cron imports both -- so an eager Pillow import would put the website and tomorrow's
# episode behind an image library. It is a declared runtime dependency and it should be there;
# this is about what happens on the day it is not.

CARD_JSON = "card.json"


def card_filename(layout: tokens.Layout) -> str:
    return f"card-{layout.name}.png"


def build_cards(episode_dir: Path, app_url: str, force: bool = False) -> Optional[dict]:
    """Render both cards for one episode. Returns the card.json contents, or None if unchanged.

    Raises `facts.NoCost` for an unpriced episode and `tokens.FontsUnavailable` on a machine with
    no usable TrueType. Both are caught by `backfill`, which is what the pipeline calls; a direct
    caller that wants to know gets told.
    """
    from . import layout as _layout

    episode_dir = Path(episode_dir)
    episode = json.loads((episode_dir / "episode.json").read_text())
    f = _facts.facts_for(episode, app_url)

    doc = {
        "version": tokens.CARD_VERSION,
        "fingerprint": f.fingerprint,
        # The alt text lives HERE and not only inside the image's metadata, because the whole
        # point of it is to be pasted into an email template or a social composer by someone who
        # is not going to run exiftool.
        "alt": f.alt,
        "cards": {
            L.name: {
                "file": card_filename(L),
                "width": L.width, "height": L.height,
                # What the <img> should be sized at. The social card is 1:1; the email card is
                # rendered at 2x and MUST be set to 600 or it arrives twice as wide as the column.
                "css_width": L.css_width,
            } for L in tokens.LAYOUTS
        },
        # The figures the card asserts, so a reader of this file can check the picture without
        # OCR'ing it. Copies of `cost`, not a second computation of it.
        "facts": {
            "usd": f.usd,
            "hero": f.hero,
            "credit_line": f.credit_line,
            "url": f.url_full,
            "dateline": f.dateline,
        },
    }

    if not force and _current(episode_dir, doc):
        return None

    for L in tokens.LAYOUTS:
        # `optimize` costs a few milliseconds and takes roughly a fifth off a card that is mostly
        # flat gradient. These get attached to emails.
        _layout.compose(L, f).save(episode_dir / card_filename(L), optimize=True)
    write_json(episode_dir / CARD_JSON, doc)
    return doc


def _current(episode_dir: Path, doc: dict) -> bool:
    """True when the cards on disk already say exactly this.

    Checks the FILES as well as the fingerprint. A card.json with no PNG beside it happens: the
    volume filled once and 35 episodes lost audio, and a half-written episode directory must heal
    on the next boot rather than stay broken because a sidecar says it is fine.
    """
    try:
        existing = json.loads((episode_dir / CARD_JSON).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if existing.get("fingerprint") != doc["fingerprint"]:
        return False
    return all((episode_dir / card_filename(L)).exists() for L in tokens.LAYOUTS)


def backfill(episodes_dir: Path, app_url: Optional[str] = None, force: bool = False,
             log=None) -> dict:
    """Build a card for every episode on disk that needs one. Safe on every boot.

    Shaped like `pricing.backfill` and called next to it, for the same reasons: there is no
    migration to remember, a deploy prices and pictures the whole archive, and an episode restored
    from the image gets both the first time the app comes up.

    NOTHING HERE RAISES. `rebuild_site` runs inside the app's startup hook, and a machine with no
    fonts, one unreadable episode.json, or one unpriced episode must not take the site down over
    a picture. Every failure is collected, logged and reported.

    Recasts get cards like anything else. They are out of the feed and out of the manifest, and
    `<id>-recast/episode.json` is still served, so a reader can still reach one and share it.
    """
    from .. import config
    log = log or (lambda *a: None)
    # `public_app_url`, not `site_app_url`: see that function for why a card must never print
    # the origin it happened to be generated on.
    app_url = app_url or config.public_app_url()
    written, skipped, failed = [], [], []

    if not tokens.fonts_available():
        # One line, not sixty-three. This is a machine-level fact, not a per-episode one.
        log("[cards] no usable TrueType font on this machine, so no cards were built. "
            "On Debian/Ubuntu: apt-get install fonts-dejavu-core")
        return {"written": written, "skipped": skipped,
                "failed": [("*", "no usable TrueType font")]}

    for episode_json in sorted(Path(episodes_dir).glob("*/episode.json")):
        name = episode_json.parent.name
        try:
            if build_cards(episode_json.parent, app_url, force=force) is None:
                skipped.append(name)
            else:
                written.append(name)
        except _facts.NoCost:
            # Not a failure. `pricing.backfill` runs immediately before this one, so an episode
            # still unpriced here is one whose script could not be read, and that has already
            # been reported once by the thing whose job it is.
            skipped.append(name)
        except Exception as e:                      # noqa: BLE001 - see the docstring
            failed.append((name, f"{type(e).__name__}: {e}"))

    if written or failed:
        log(f"[cards] built {len(written)} card set(s), {len(skipped)} already current"
            + (f", {len(failed)} failed" if failed else ""))
    for name, why in failed:
        log(f"[cards] {name}: {why}")
    return {"written": written, "skipped": skipped, "failed": failed}
