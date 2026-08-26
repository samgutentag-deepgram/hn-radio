#!/usr/bin/env python3
"""Render the podcast's album art: a honeycomb of every voice in the catalog, over black.

WHY THIS SHAPE. Apple Podcasts renders this at 55x55 in a subscription list, and that size decides
whether anyone recognises the show. An earlier cover carried five lines of text, two at 150pt; all
of it collapsed into grey mud. The lesson stuck and it constrains this one: whatever fills the frame
has to survive being 55px, and the description belongs in the feed's <itunes:summary> where a reader
can actually read it.

So the field is a HONEYCOMB of all 36 published voices, one orb each, in tight rows with offset
columns -- every voice in its own seat, which is what the show is. At full size you can pick out
individual spheres and their palettes. At 55px it stops being 36 objects and becomes one dense,
high-chroma texture, which is a recognisable thing rather than mud: it reads as a colour and a
pattern, and the wordmark knocked out of a scrim across the middle stays legible because it is two
words on a darkened band rather than text competing with art.

WHY ALL 36 AND NOT THE CAST. The show is two people a day drawn from the whole catalog, so the
catalog IS the premise. A cover showing one orb showed the host; this one shows the bench.

THE ORBS are the same object the site and talk.deepgram.com use, so the art and the app are
recognisably one thing. Geometry is the reference implementation's, proportionally scaled from its
144px box: three overlapping ellipses, each blurred, painted in one voice's three shades over a
near-black ground, with the voice's glow bled outward.

THE PALETTES are read at runtime out of web/brand.css block 1, not duplicated here. That block is
itself lifted from the official per-voice orb SVGs the team supplied, so there is exactly one place
in this repo where a voice's colours live and the cover cannot drift from the site. If the regex
below stops matching, the cover fails loudly rather than shipping wrong colours.

Run it with `uv run python scripts/make_cover.py`. Pillow is in the `dev` dependency group, so
`uv sync` installs it locally and `uv sync --no-dev` keeps it out of the container.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hn_radio import config  # noqa: E402

S = 3000  # Apple's maximum, and the size every other platform downscales from.

# ---- palette -------------------------------------------------------------------------------
# From the talk.deepgram.com design language. `flux-2026` pack for the ground and ink, the
# voice widget's per-voice map for the orb shades.
BG = (0, 0, 0)                 # --dg-surface-page: pure black, committed
ORB_GROUND = (16, 16, 20)      # #101014, the reference's own orb fill
INK = (251, 251, 255)          # --dg-ink-primary, deliberately not pure white
FAINT = (148, 148, 152)        # --dg-ink-muted
GREEN = (19, 239, 147)         # --dg-accent, the one brand signal on the cover
# The one hardcoded run is gone: `palettes()` reads all 36 out of brand.css block 1, which is
# itself lifted from the official orb SVGs. One source of truth for a voice's colours.
BRAND_CSS = Path(__file__).resolve().parent.parent / "web" / "brand.css"
VOICE_RULE = re.compile(
    r'\[data-voice="(flux-[^"]+)"\]\s*\{\s*--v-l:\s*(#\w{6});\s*--v-m:\s*(#\w{6});'
    r'\s*--v-d:\s*(#\w{6});\s*--v-g:\s*([\d, ]+);')


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def palettes() -> dict:
    """voice id -> (light, mid, deep, glow), read from web/brand.css.

    Fails loudly on an empty match rather than falling back to a default: a cover that silently
    ships the wrong colours looks fine and is wrong, which is the worst failure this script has.
    """
    css = BRAND_CSS.read_text()
    out = {}
    for vid, light, mid, deep, glow in VOICE_RULE.findall(css):
        out[vid] = (_rgb(light), _rgb(mid), _rgb(deep),
                    tuple(int(x) for x in glow.split(",")))
    if len(out) < 30:
        raise SystemExit(f"only {len(out)} voice palettes matched in {BRAND_CSS}; "
                         "block 1's shape changed and this regex needs updating")
    return out

# ---- orb geometry --------------------------------------------------------------------------
# The reference lays these out in a 144px box; every value here is that number over 144, so
# the shape is identical at any size. Ellipses are (left, top, width, height, blur).
ORB_ELLIPSES = (
    (4 / 144, 72 / 144, 135 / 144, 75 / 144, 9 / 144),
    (9 / 144, 60 / 144, 124 / 144, 69 / 144, 8 / 144),
    (40 / 144, 66 / 144, 66 / 144, 37 / 144, 7 / 144),
)
# HONEYCOMB GEOMETRY. Six columns by six rows, 36 seats for 36 voices, offset every other row so
# the packing is hexagonal rather than square. Sized to BLEED off all four edges: a grid that fits
# inside the frame reads as a diagram of orbs, and one that runs off it reads as a crowd.
COLS, ROWS = 6, 6
ORB_D = int(S * 0.207)         # ~620px: still legibly a sphere at full size
STEP_X = int(ORB_D * 0.97)     # tight, a hair of overlap so the rows lock together
STEP_Y = int(ORB_D * 0.866)    # true hex spacing: sin(60 degrees)

# Deliberately no font file in the repo: a font is a licensed binary and the cover is a
# generated artifact, not a design deliverable. Candidates cover macOS, Debian/CI and Windows,
# and `font()` TRIES each rather than stat'ing it so a present-but-unreadable file falls through.
BLACK_FONTS = (
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
_warned = False


def font(candidates, size: int):
    global _warned
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    if not _warned:
        _warned = True
        print("!!  no candidate TrueType font on this machine; falling back to Pillow's bitmap "
              "font. The cover will render, and it will not look like the shipped one.")
    return ImageFont.load_default(size=size)


def orb(d: int, shades, glow_rgb) -> Image.Image:
    """One molten orb, `d` px across, in `shades`, as RGBA with a transparent surround."""
    # Oversample so the blurs and the circular clip stay smooth once composited down.
    ss = 2
    n = d * ss
    # The shades ACCUMULATE. The reference composites the lava layer with `lighter`, so where
    # two ellipses overlap the result is brighter than either. An alpha blend instead of an
    # additive one is what turns this from a molten object into a grey smudge; that was the
    # first attempt here and it is why this is spelled out.
    lava = Image.new("RGB", (n, n), (0, 0, 0))
    # Shades run DEEP to LIGHT here, the reverse of the reference's order. On screen the
    # reference paints its lightest shade on the largest, lowest ellipse and gets away with it
    # because the lava layer is scaled to 0.917 and most of that mass is clipped by the orb's
    # edge. At 3000px with nothing clipped, the near-white filled the whole lower half and the
    # orb read as a white blob. Deep on the mass and light as the small highlight is the same
    # object as the eye reads it, which is what the cover has to match.
    for (lx, ty, w, h, blur), shade in zip(ORB_ELLIPSES, tuple(reversed(shades))):
        layer = Image.new("RGB", (n, n), (0, 0, 0))
        ImageDraw.Draw(layer).ellipse(
            [lx * n, ty * n, (lx + w) * n, (ty + h) * n], fill=shade)
        lava = ImageChops.lighter(lava, layer.filter(ImageFilter.GaussianBlur(blur * n)))
    lava = ImageChops.lighter(lava, Image.new("RGB", (n, n), ORB_GROUND))

    # `inset 0 11px 36px var(--orb-glow)`: the glow rides the inside of the top edge, which is
    # what stops the sphere reading as a flat disc with a stain on it.
    inset = Image.new("RGB", (n, n), (0, 0, 0))
    ir = int(n * 0.52)
    ImageDraw.Draw(inset).ellipse([n / 2 - ir, -int(n * 0.06), n / 2 + ir, 2 * ir], outline=glow_rgb,
                                  width=int(n * 0.062))
    lava = ImageChops.lighter(lava, inset.filter(ImageFilter.GaussianBlur(int(n * 0.055))))

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, n - 1, n - 1], fill=255)
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    out.paste(lava, (0, 0), mask)
    return out.resize((d, d), Image.LANCZOS)


def glow(canvas: Image.Image, cx: int, cy: int, d: int, glow_rgb, strength=0.38) -> None:
    """The reference's outer shadow: the voice's glow bled below and around the orb."""
    layer = Image.new("RGB", canvas.size, (0, 0, 0))
    r = int(d * 0.62)
    ImageDraw.Draw(layer).ellipse([cx - r, cy - r + int(d * 0.09), cx + r, cy + r + int(d * 0.09)],
                                  fill=glow_rgb)
    layer = layer.filter(ImageFilter.GaussianBlur(int(d * 0.20)))
    canvas.paste(Image.blend(canvas, layer, strength), (0, 0))


# Where the lockup sits. The wordmark is centred on the canvas; the eyebrow rides just above it.
WORDMARK_Y = int(S * 0.435)
EYEBROW_Y = int(S * 0.392)
BAND_PAD = int(S * 0.045)      # breathing room above the eyebrow and below the wordmark


def _band(draw, wordmark_font, eyebrow_font):
    """Top and bottom of the scrim, measured off the real glyph boxes.

    `textbbox` with anchor="ma" returns the box the text will actually occupy, ascenders and
    descenders included, which is the only way to be sure the band covers the letterforms. The
    version that hardcoded these two fractions let a 500pt descender hang onto bare honeycomb.
    """
    eb = draw.textbbox((S / 2, EYEBROW_Y), "D E E P G R A M   ·   F L U X   T T S",
                       font=eyebrow_font, anchor="ma")
    wm = draw.textbbox((S / 2, WORDMARK_Y), "HN RADIO", font=wordmark_font, anchor="ma")
    return max(0, eb[1] - BAND_PAD), min(S, wm[3] + BAND_PAD)


def seating(voices, cols=COLS, rows=ROWS):
    """Which voice sits in which seat, ordered so neighbours rarely share a palette run.

    Straight alphabetical order clusters the runs badly: Colin, Conor and Cole land beside each
    other and three neighbouring seats read as one blob. So voices are dealt round-robin from
    their RUN groups -- take one amber, one green, one blue, one violet, and so on -- which
    spreads a repeated run across the grid instead of stacking it.

    Deterministic, no shuffle: the cover has to be reproducible, and a random layout means two
    runs of this script disagree about what the show's art is.
    """
    groups = {}
    for vid in sorted(voices):
        light, mid, deep, _ = voices[vid]
        groups.setdefault((light, mid, deep), []).append(vid)
    # largest run first, so the six ambers are the ones spread widest
    queues = sorted(groups.values(), key=len, reverse=True)
    order, i = [], 0
    while len(order) < cols * rows and any(queues):
        q = queues[i % len(queues)]
        if q:
            order.append(q.pop(0))
        i += 1
        if i > 10000:
            break
    return order


def main() -> int:
    voices = palettes()
    img = Image.new("RGB", (S, S), BG)

    # Ambient field: the site's own body wash, two low-alpha radials so the ground is not flat.
    # Kept from the single-orb cover: the honeycomb does not reach the corners identically and a
    # flat black behind it reads as a cutout rather than a room.
    field = Image.new("RGB", (S, S), (0, 0, 0))
    fd = ImageDraw.Draw(field)
    fd.ellipse([-S * 0.25, -S * 0.30, S * 0.75, S * 0.55], fill=(28, 14, 4))
    fd.ellipse([S * 0.45, S * 0.55, S * 1.35, S * 1.30], fill=(6, 24, 18))
    img = Image.blend(img, field.filter(ImageFilter.GaussianBlur(S * 0.10)), 0.85)

    order = seating(voices)
    grid_w = (COLS - 1) * STEP_X + ORB_D + STEP_X // 2   # + the odd-row offset
    grid_h = (ROWS - 1) * STEP_Y + ORB_D
    x0 = (S - grid_w) // 2
    y0 = (S - grid_h) // 2

    # Two passes. Every glow first, then every orb, so a neighbour's glow never washes over an
    # orb that was already painted -- one pass produced a visible seam along each row.
    placed = []
    for i, vid in enumerate(order):
        row, col = divmod(i, COLS)
        cx = x0 + col * STEP_X + (STEP_X // 2 if row % 2 else 0) + ORB_D // 2
        cy = y0 + row * STEP_Y + ORB_D // 2
        placed.append((vid, cx, cy))

    for vid, cx, cy in placed:
        # Weaker than the single-orb cover's 0.38: 36 glows at that strength summed into a
        # uniform haze and the spheres stopped reading as separate objects.
        glow(img, cx, cy, ORB_D, voices[vid][3], strength=0.13)
    for vid, cx, cy in placed:
        light, mid, deep, glow_rgb = voices[vid]
        o = orb(ORB_D, (light, mid, deep), glow_rgb)
        img.paste(o, (cx - ORB_D // 2, cy - ORB_D // 2), o)

    eyebrow_font = font(BLACK_FONTS, 92)
    wordmark_font = font(BLACK_FONTS, 500)
    d0 = ImageDraw.Draw(img)

    # SCRIM. The wordmark cannot compete with 36 saturated spheres, so the band it sits on is
    # darkened rather than the text being outlined or shadowed. A horizontal band also survives
    # downscaling: at 55px it is a dark stripe with two words on it, which is a shape, where
    # unbacked text is noise.
    #
    # Measured off the actual glyph box rather than guessed. The first version hardcoded the band
    # at 0.395-0.625 and the 500pt wordmark overran it at the bottom, so the descender sat on bare
    # honeycomb and a third line of text ran straight through the letterforms.
    band_top, band_bot = _band(d0, wordmark_font, eyebrow_font)
    scrim = img.crop((0, band_top, S, band_bot))
    scrim = Image.blend(scrim, Image.new("RGB", scrim.size, (0, 0, 0)), 0.80)
    img.paste(scrim, (0, band_top))
    # Feather both edges so the band is a shadow across the crowd, not a pasted rectangle.
    for edge_y, direction in ((band_top, -1), (band_bot, 1)):
        for k in range(48):
            y = edge_y + direction * k
            if not (0 <= y < S):
                continue
            strip = img.crop((0, y, S, y + 1))
            img.paste(Image.blend(strip, Image.new("RGB", (S, 1), (0, 0, 0)),
                                  0.80 * (1 - k / 48)), (0, y))

    # TWO LINES, and that is the whole lockup. A third line is exactly what the previous cover was
    # criticised for: at 55px it is a grey smear across the wordmark, and it collided with the
    # 500pt glyphs even at full size. What it said now lives in the feed's <itunes:summary>.
    d = ImageDraw.Draw(img)
    d.text((S / 2, EYEBROW_Y), "D E E P G R A M   ·   F L U X   T T S",
           font=eyebrow_font, fill=GREEN, anchor="ma")
    d.text((S / 2, WORDMARK_Y), "HN RADIO", font=wordmark_font, fill=INK, anchor="ma")

    config.EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    out = config.EPISODES_DIR / "cover.png"
    img.save(out)
    print(f"wrote {out} ({img.size[0]}x{img.size[1]}), {len(order)} orbs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
