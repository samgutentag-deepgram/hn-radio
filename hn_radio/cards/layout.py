"""The composition: where each fact lands on each card size.

Reads coordinates out of `tokens.Layout`, strings out of `facts.CardFacts`, and marks out of
`paint`. It contains no numbers of its own by design, so a restyle is one file away from a
designer who does not read Python (see `tokens.py`'s header).

THE READING ORDER THE LAYOUT IS BUILT AROUND, top to bottom, because it is the whole argument the
card makes and the geometry only exists to serve it:

    DEEPGRAM · FLUX TTS            what rendered this
    HACKER NEWS RADIO              what it is
    $0.24                          what this episode cost
    to render this whole episode
    with Deepgram Flux TTS         ...which completes the sentence the number starts
    $200 of signup credit
    covers 819 episodes            what the rate means to someone holding credits
    dg-devrel-hn-radio.fly.dev/... where to go, in text, because an image is not a link

The cast orbs are not decoration and not a fourth fact. They are painted in the two voices' real
per-voice runs out of `brand.css` block 1, so the card shows the product rather than describing
it, and they change every episode because the co-host rotates. Marketing asked for "something to
show them, as opposed to having a button or a link"; a stat card with a wordmark on it is the
thing that ask was rejecting.
"""

from __future__ import annotations

from typing import Tuple

from PIL import Image, ImageDraw

from . import paint, tokens
from .facts import CardFacts

# Fixed copy, as opposed to the per-episode strings in `facts.py`. Two lines, and the cover art
# carries the same two: the eyebrow is the attribution and the wordmark is the show.
EYEBROW = "DEEPGRAM · FLUX TTS"
WORDMARK = "HACKER NEWS RADIO"


class CopyTooWide(ValueError):
    """A line does not fit even at its minimum size. The copy is wrong, not the type size."""


def compose(layout: tokens.Layout, facts: CardFacts) -> Image.Image:
    p = tokens.palette()
    img = paint.canvas(layout.size, p)
    draw = ImageDraw.Draw(img)
    right = layout.width - layout.pad_x

    # The cast goes down on the bare ground, before a single glyph. Their glows are blends, and a
    # blend over type is type that got darker.
    _paint_cast(img, draw, layout, facts, p, right)

    # --- band A: the lockup ---------------------------------------------------------
    paint.tracked(draw, (layout.pad_x, layout.eyebrow_y), EYEBROW,
                  tokens.font("display", layout.eyebrow_size), p.accent, layout.eyebrow_track)
    paint.text(draw, (layout.pad_x, layout.wordmark_y), WORDMARK,
               tokens.font("display", layout.wordmark_size), p.ink)

    dateline = facts.dateline
    if layout.meta_inline:
        dateline = " · ".join(filter(None, (dateline, facts.meta.upper())))
    # Left-aligned when it carries the runtime too (it is a line of its own then), right-aligned
    # against the wordmark when it does not.
    if layout.meta_inline:
        paint.text(draw, (layout.pad_x, layout.dateline_y), dateline,
                   tokens.font("ui", layout.dateline_size), p.ink_muted)
    else:
        paint.text(draw, (right, layout.dateline_y), dateline,
                   tokens.font("ui", layout.dateline_size), p.ink_muted, anchor="ra")

    paint.rule(img, layout.pad_x, right, layout.rule_top_y, p.hairline_add, tokens.RULE_HEIGHT)

    # --- band B: the number ---------------------------------------------------------
    paint.text(draw, (layout.pad_x, layout.hero_y), facts.hero,
               tokens.font("display", layout.hero_size), p.ink)
    label_font = tokens.font("ui", layout.hero_label_size)
    for i, line in enumerate(facts.hero_label):
        paint.text(draw, (layout.pad_x, layout.hero_label_y + i * layout.hero_label_leading),
                   line, label_font, p.ink_secondary)

    if not layout.meta_inline:
        paint.text(draw, (right, layout.meta_y), facts.meta,
                   tokens.font("ui", layout.meta_size), p.ink_muted, anchor="ra")

    # --- band C: what the credit buys, and where to go ------------------------------
    paint.rule(img, layout.pad_x, right, layout.rule_bottom_y, p.hairline_add,
               tokens.RULE_HEIGHT)

    credit_font = paint.fitted(draw, facts.credit_line, "ui", layout.credit_size,
                               layout.content_width, layout.credit_min_size)
    if credit_font is None:
        raise CopyTooWide(f"the credit line does not fit the {layout.name} card even at "
                          f"{layout.credit_min_size}px: {facts.credit_line!r}")
    paint.text(draw, (layout.pad_x, layout.credit_y), facts.credit_line, credit_font, p.ink)

    # Mono, and in the accent. A URL nobody can click is a URL somebody has to retype, and a
    # proportional face is where `1`, `l` and `I` stop being distinguishable by eye.
    url_font = tokens.font("mono", layout.url_size)
    if draw.textlength(facts.url_text, font=url_font) > layout.content_width:
        raise CopyTooWide(f"the URL overruns the {layout.name} card: {facts.url_text!r}")
    paint.text(draw, (layout.pad_x, layout.url_y), facts.url_text, url_font, p.accent)

    return img


def _paint_cast(img: Image.Image, draw: ImageDraw.ImageDraw, layout: tokens.Layout,
                facts: CardFacts, p: tokens.Palette, right: int) -> None:
    """The episode's two voices as their own orbs, right-aligned, named underneath.

    NAMED, not just coloured. That is `brand.css` block 1's standing rule, quoted here because it
    is easy to lose in a raster: colour is reinforcement and never the only signal, so a reader
    with no colour perception loses decoration and no information.

    A voice missing from block 1 is SKIPPED rather than drawn in a default palette. Every GA voice
    is in there; one that is not means the catalog moved, and a grey orb labelled with a name
    would be a quiet lie about what the show sounds like.
    """
    palettes = tokens.voice_palettes()
    seats = [s for s in facts.speakers if s.voice_id in palettes]
    if not seats:
        return

    d = layout.orb_d
    name_font = tokens.font("ui", layout.orb_name_size)
    # Laid out from the right edge inward, so one seat sits where the second of two would.
    placed = [(right - d // 2 - i * (d + layout.orb_gap), s)
              for i, s in enumerate(reversed(seats))]

    # THREE PASSES, not one per orb, and each boundary is load-bearing. A `bleed` darkens what it
    # covers (see `paint.bleed`), so one orb's glow must not wash over the neighbour already
    # painted beside it or the name already set beneath it. Glows, then spheres, then names.
    for cx, speaker in placed:
        paint.bleed(img, cx, layout.orb_center_y, d, palettes[speaker.voice_id][3])
    for cx, speaker in placed:
        light, mid, deep, glow = palettes[speaker.voice_id]
        paint.place_orb(img, cx, layout.orb_center_y, d, (light, mid, deep), glow, p.orb_ground)
    for cx, speaker in placed:
        if speaker.name:
            paint.text(draw, (cx, layout.orb_name_y), speaker.name, name_font,
                       p.ink_secondary, anchor="ma")
