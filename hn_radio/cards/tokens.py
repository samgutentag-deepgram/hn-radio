"""Every colour, size and spacing value the share cards use. One module, on purpose.

Deepgram is mid-rebrand, so the cards are built to be
restyled by someone who does not read Python. Two surfaces and no third:

    COLOUR      web/brand.css, block 0 and block 1, read at render time by the functions below.
    GEOMETRY    the `Layout` instances at the bottom of this file. Sizes, spacing, weights.

Nothing under `hn_radio/cards/` is allowed to contain a colour literal, a font size or a pixel
offset. `paint.py` draws what it is handed and `layout.py` places what this file measures out.
That is the same seal `brand.css` puts on itself (see its header), extended to the raster.

WHY COLOUR IS NOT COPIED INTO THIS FILE. The site, the album art and these cards would then hold
three copies of the palette, and the rebrand would land on one of them. `brand.css` block 0 is
the pack and block 1 is the official per-voice orb runs; both are already the single source for
the web app and for `scripts/make_cover.py`. A card that drifted from the site it links to is
the specific failure worth designing against, because the reader sees them a click apart.

FONT FILES ARE NOT IN THIS REPO AND MUST NOT BE. A font is a licensed binary. The candidates in
`FONT_CANDIDATES` cover macOS, Debian (the container, once `fonts-dejavu-core` is installed) and
Windows. Unlike `scripts/make_cover.py` there is NO bitmap fallback here: that script is run by
hand a few times a year on a Mac, and these cards are generated unattended and sent to marketing.
A card with Pillow's default bitmap font looks broken rather than unbranded, so a machine with no
usable TrueType raises `FontsUnavailable` and the episode's card is skipped with a log line.

None of these are the real Deepgram faces. `brand.css` names Space Grotesk, Inter and Fira Code
and hosts none of them; these cards land on Arial or DejaVu for the same reason the site lands on
system-ui. The day a licensed woff2/ttf can live in the repo, it is one entry in the tuple below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Sequence, Tuple

RGB = Tuple[int, int, int]

# Bumped whenever a value in this file, or a composition in `layout.py`, changes what a card looks
# like. It is written into `card.json` and compared on every rebuild, so a restyle regenerates the
# archive on the next boot rather than leaving old cards on disk looking like the old brand.
CARD_VERSION = 1

BRAND_CSS = Path(__file__).resolve().parent.parent.parent / "web" / "brand.css"


class FontsUnavailable(RuntimeError):
    """No usable TrueType font on this machine. Raised rather than falling back to a bitmap."""


# --- reading the pack ----------------------------------------------------------------

# Block 0 is the `:root { ... }` at the top of brand.css and it is the ONLY place in that file
# allowed to hold a literal (its own header says so). Taking the first :root block is therefore
# exact rather than a guess: `html[data-theme="light"]` below it is a selector, not :root.
_ROOT_BLOCK = re.compile(r":root\s*\{(.*?)\n\}", re.DOTALL)
_DECL = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;]+);")

# Block 1, one rule per voice. Lifted verbatim from `scripts/make_cover.py`, which is now a caller
# rather than a second copy.
_VOICE_RULE = re.compile(
    r'\[data-voice="(flux-[^"]+)"\]\s*\{\s*--v-l:\s*(#\w{6});\s*--v-m:\s*(#\w{6});'
    r'\s*--v-d:\s*(#\w{6});\s*--v-g:\s*([\d, ]+);')


def _hex_rgb(text: str) -> RGB:
    h = text.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _parse_colour(value: str, ground: RGB) -> RGB:
    """A brand.css colour value as opaque RGB, compositing any alpha over `ground`.

    The strokes in the pack are white-alpha hairlines rather than grey lines, deliberately (see
    brand.css block 0). A raster has no backdrop to blend against at draw time, so the blend
    happens here, against the surface the card actually paints them on.
    """
    value = value.strip()
    if value.startswith("#"):
        return _hex_rgb(value)
    m = re.match(r"rgba?\(([^)]+)\)", value)
    if not m:
        raise ValueError(f"not a colour brand.css would write: {value!r}")
    parts = [p.strip() for p in m.group(1).split(",")]
    r, g, b = (int(float(p)) for p in parts[:3])
    a = float(parts[3]) if len(parts) > 3 else 1.0
    return tuple(int(round(c * a + gc * (1 - a))) for c, gc in zip((r, g, b), ground))


@lru_cache(maxsize=1)
def _declarations() -> Dict[str, str]:
    css = BRAND_CSS.read_text()
    block = _ROOT_BLOCK.search(css)
    if not block:
        raise RuntimeError(f"no :root block in {BRAND_CSS}; block 0's shape changed")
    return {name: value.strip() for name, value in _DECL.findall(block.group(1))}


def token(name: str, ground: RGB = (0, 0, 0)) -> RGB:
    """One `--dg-*` colour from block 0, as RGB. `name` is written without the `--`.

    Fails loudly on a missing token for the reason `make_cover.palettes` does: a card that
    silently shipped a default colour would look fine and be wrong, which is the worst outcome
    available to a generated brand asset.
    """
    decls = _declarations()
    if name not in decls:
        raise KeyError(f"{name} is not in {BRAND_CSS} block 0")
    return _parse_colour(decls[name], ground)


@lru_cache(maxsize=1)
def voice_palettes() -> Dict[str, Tuple[RGB, RGB, RGB, RGB]]:
    """voice id -> (light, mid, deep, glow), out of brand.css block 1.

    Block 1 is read straight off the official per-voice orb SVGs the team supplied, so this is
    the same run the site paints, the album art paints and the widget paints. `make_cover.py`
    imports this rather than keeping the regex it used to own.
    """
    out = {}
    for vid, light, mid, deep, glow in _VOICE_RULE.findall(BRAND_CSS.read_text()):
        out[vid] = (_hex_rgb(light), _hex_rgb(mid), _hex_rgb(deep),
                    tuple(int(x) for x in glow.split(",")))
    if len(out) < 30:
        raise RuntimeError(f"only {len(out)} voice palettes matched in {BRAND_CSS}; "
                           "block 1's shape changed and the regex needs updating")
    return out


# --- the colours a card uses ---------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    """The seven colours the compositions are allowed to name, resolved from the pack."""
    ground: RGB
    ink: RGB
    ink_secondary: RGB
    ink_muted: RGB
    accent: RGB
    # A second identity colour the pack declares "for per-voice differentiation". Used here for
    # one thing only: keywords in a code snippet, where the accent green is already spoken for
    # by strings and would make a snippet read as one undifferentiated colour.
    accent_alt: RGB
    danger: RGB
    # PREMULTIPLIED, to be ADDED rather than painted. The pack's strokes are white-alpha
    # hairlines and not grey lines, deliberately, so that they sit correctly over glass as well as
    # over the page (brand.css block 0 says so in those words). A raster has no backdrop to blend
    # against at draw time, and baking one in gets it wrong: a hairline resolved over pure black
    # is #141414, which is DARKER than the ambient wash it crosses lower down the card, so the
    # rule under the credit line came out as a dark scratch while the one under the lockup came
    # out as a light one. Adding 8% of white to whatever is already there is the same rule the
    # browser applies, and it reads identically in both places.
    hairline_add: RGB
    orb_ground: RGB
    ambient: Tuple[RGB, ...]


# The orb's own ground, and the ONE colour here that is not a `--dg-*` token. Block 1's header
# records it as the `rgb(16,16,20)` ground rect every official orb SVG opens with; it is a fact
# about the asset rather than a pack value, and `make_cover.py` carries the same literal for the
# same reason.
_ORB_GROUND: RGB = (16, 16, 20)


@lru_cache(maxsize=1)
def palette() -> Palette:
    ground = token("dg-surface-page")
    return Palette(
        ground=ground,
        ink=token("dg-ink-primary"),
        ink_secondary=token("dg-ink-secondary"),
        ink_muted=token("dg-ink-muted"),
        accent=token("dg-accent"),
        accent_alt=token("dg-accent-alt"),
        danger=token("dg-danger"),
        hairline_add=token("dg-line-hairline", ground=(0, 0, 0)),
        orb_ground=_ORB_GROUND,
        ambient=tuple(token(f"dg-ambient-{i}") for i in range(1, 7)),
    )


# --- type ----------------------------------------------------------------------------

# Three roles, the same three brand.css block 0 declares: display for the numerals and the
# wordmark, ui for everything set in sentences, mono for the URL. A URL in a proportional face
# is harder to copy by eye off an image, which is the only way some readers will reach it.
FONT_CANDIDATES: Dict[str, Sequence[str]] = {
    "display": (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ),
    "ui": (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ),
    "mono": (
        "/System/Library/Fonts/Monaco.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ),
}


@lru_cache(maxsize=64)
def font(role: str, size: int):
    """A PIL font for `role` at `size`. Raises `FontsUnavailable` rather than degrading.

    TRIES each candidate rather than stat'ing it, so a present-but-unreadable file falls through
    to the next one instead of being chosen and then failing mid-render.

    A MISSING PILLOW COMES BACK AS `FontsUnavailable` TOO, which is not a lie about the cause and
    is the right shape for the caller. `publish.rebuild_site` builds cards inside the app's
    startup hook and inside the nightly render, and neither the website nor tomorrow's episode
    should stop existing because an image library did. One failure mode, handled in one place; the
    message says which one it actually was.
    """
    try:
        from PIL import ImageFont
    except ImportError as e:
        raise FontsUnavailable(f"Pillow is not installed, so no card can be drawn: {e}") from e
    for path in FONT_CANDIDATES[role]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    raise FontsUnavailable(
        f"no usable TrueType font for the '{role}' role. Tried: "
        + ", ".join(FONT_CANDIDATES[role])
        + ". On Debian/Ubuntu: apt-get install fonts-dejavu-core")


def fonts_available() -> bool:
    """True when all three roles resolve. Cheap probe, so a caller can skip rather than raise."""
    try:
        for role in FONT_CANDIDATES:
            font(role, 16)
    except FontsUnavailable:
        return False
    return True


# --- geometry ------------------------------------------------------------------------

@dataclass(frozen=True)
class Layout:
    """One card size, laid out. Every number a composition needs, and nowhere else.

    Coordinates are absolute pixels from the top-left of the physical image, not a scale factor
    applied to a shared grid. The two cards carry the same elements in two different arrangements
    rather than one arrangement at two zooms: the social card sets the cast beside the number and
    the email card sets it up in the lockup, because the email card is a third of the area and the
    social grid shrunk to fit it puts the cost label at nine pixels. Two sets of numbers is the
    honest cost of that, which is why they are spelled out here instead of derived.
    """
    name: str
    width: int
    height: int
    css_width: int          # what the <img> should be sized at; the raster is 2x this for email
    pad_x: int

    # band A: the lockup
    eyebrow_y: int
    eyebrow_size: int
    eyebrow_track: int      # extra pixels between letterforms; PIL has no tracking of its own
    wordmark_y: int
    wordmark_size: int
    left_col_width: int     # how much room the hero and its label have before the cast column
    dateline_y: int
    dateline_size: int
    rule_top_y: int

    # band B: the number and the cast
    hero_y: int
    hero_size: int
    hero_label_y: int
    hero_label_size: int
    hero_label_leading: int
    orb_d: int
    orb_center_y: int
    orb_gap: int
    orb_name_y: int
    orb_name_size: int
    meta_y: int
    meta_size: int
    # The runtime and story count ride the dateline on the email card and sit under the cast on
    # the social one. A flag and not an `if` in `layout.py`, because where a thing sits is this
    # file's business.
    meta_inline: bool

    # band C: what the credit buys, and where to go
    rule_bottom_y: int
    credit_y: int
    credit_size: int
    credit_min_size: int    # the credit line is the one string that changes width with the cost
    url_y: int
    url_size: int

    @property
    def size(self) -> Tuple[int, int]:
        return (self.width, self.height)

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.pad_x


# 1.91:1, which is what Open Graph, Twitter/X, LinkedIn and Slack unfurls all crop toward. Also
# the episode page's og:image, so it is the card a link preview shows without anyone sending one.
SOCIAL = Layout(
    name="social",
    width=1200, height=630, css_width=1200, pad_x=64,
    eyebrow_y=52, eyebrow_size=20, eyebrow_track=5,
    wordmark_y=84, wordmark_size=42, left_col_width=734,
    dateline_y=96, dateline_size=22,
    rule_top_y=158,
    hero_y=196, hero_size=150,
    hero_label_y=390, hero_label_size=27, hero_label_leading=36,
    orb_d=132, orb_center_y=276, orb_gap=26,
    orb_name_y=352, orb_name_size=20,
    meta_y=390, meta_size=20, meta_inline=False,
    rule_bottom_y=482,
    credit_y=508, credit_size=29, credit_min_size=23,
    url_y=556, url_size=25,
)

# Rendered at 2x and displayed at 600 CSS pixels, because a 600px raster in an email is soft on
# every phone made since about 2014 and marketing cannot fix that at send time. 600x360 displayed
# keeps the card shorter than the social one so it does not push an email's CTA below the fold.
EMAIL = Layout(
    name="email",
    width=1200, height=800, css_width=600, pad_x=72,
    eyebrow_y=72, eyebrow_size=24, eyebrow_track=6,
    wordmark_y=114, wordmark_size=50, left_col_width=1056,
    dateline_y=190, dateline_size=26,
    rule_top_y=262,
    hero_y=300, hero_size=168,
    hero_label_y=528, hero_label_size=32, hero_label_leading=42,
    orb_d=116, orb_center_y=116, orb_gap=22,
    orb_name_y=182, orb_name_size=22,
    meta_y=190, meta_size=26, meta_inline=True,
    rule_bottom_y=646,
    credit_y=674, credit_size=34, credit_min_size=27,
    url_y=730, url_size=28,
)

LAYOUTS = (SOCIAL, EMAIL)


# --- the two motifs ------------------------------------------------------------------
#
# The ambient field and the molten orb are the design language's signatures, and both are
# reproduced here from the same source `scripts/make_cover.py` works from, so the card, the album
# art and the site are recognisably one object. The orb's proportions are UPSTREAM'S and are not
# a knob: they are the reference widget's 144px box expressed as fractions, so the shape is
# identical at any diameter. The strengths below are this card's, and they are knobs.

# (left, top, width, height, blur), each as a fraction of the orb's diameter.
ORB_ELLIPSES = (
    (4 / 144, 72 / 144, 135 / 144, 75 / 144, 9 / 144),
    (9 / 144, 60 / 144, 124 / 144, 69 / 144, 8 / 144),
    (40 / 144, 66 / 144, 66 / 144, 37 / 144, 7 / 144),
)
ORB_INSET_GLOW_R = 0.52       # the `inset 0 11px 36px` rim, as a fraction of the diameter
ORB_INSET_GLOW_W = 0.062
ORB_INSET_GLOW_BLUR = 0.055
ORB_INSET_GLOW_TOP = -0.06    # how far above the orb the rim's own circle starts
ORB_GLOW_R = 0.62             # the outer bleed under the sphere
ORB_GLOW_DROP = 0.09
ORB_GLOW_BLUR = 0.20
ORB_GLOW_STRENGTH = 0.30      # weaker than the cover's 0.38: two orbs, not thirty-six
ORB_SUPERSAMPLE = 2           # drawn at 2x and resampled, so the blurs and the clip stay smooth

# The body wash. Two radials in the pack's own ambient hues, heavily blurred and composited at
# low strength, so the ground is a room rather than a flat black rectangle.
AMBIENT_STRENGTH = 0.30
AMBIENT_BLUR = 0.17           # gaussian radius as a fraction of the card's width
AMBIENT_WARM = 3              # index into Palette.ambient: the warm lobe, upper left
AMBIENT_COOL = 4              # the cool lobe, lower right
# (left, top, right, bottom) as fractions of the card, per lobe. Both run off the frame on
# purpose: a radial that fits inside the card reads as a drawn circle, one that bleeds reads as
# light.
AMBIENT_WARM_BOX = (-0.25, -0.45, 0.70, 0.75)
AMBIENT_COOL_BOX = (0.45, 0.45, 1.30, 1.60)

RULE_HEIGHT = 1               # a hairline is one pixel; the colour carries the weight


# --- strips --------------------------------------------------------------------------
#
# A second family, for a newsletter body rather than a social slot. Six of them stack into a
# "what this is" section, so each one carries ONE component of the project and they share a frame
# so the set reads as a set. 1200x400, shown at 600x200: short enough that three in a row do not
# push an email's call to action below the fold.
#
# A TYPE RAMP RATHER THAN SIX GRIDS. The cards in `Layout` above are two fixed compositions and
# every coordinate is spelled out; six strips laid out that way would be two hundred numbers and
# nobody would keep them consistent. So the strips get a frame plus six named sizes, and each
# composition derives its own positions from those. The values still live here; only the
# arithmetic lives in `strips.py`.

@dataclass(frozen=True)
class StripFrame:
    width: int
    height: int
    css_width: int
    pad_x: int
    eyebrow_y: int
    eyebrow_size: int
    eyebrow_track: int
    body_y: int             # where a strip's own content starts, under the eyebrow
    footer_y: int           # the URL line, measured from the top like everything else
    footer_size: int

    @property
    def size(self) -> Tuple[int, int]:
        return (self.width, self.height)

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.pad_x

    @property
    def body_height(self) -> int:
        return self.footer_y - self.body_y


STRIP = StripFrame(
    width=1200, height=400, css_width=600, pad_x=56,
    eyebrow_y=44, eyebrow_size=19, eyebrow_track=5,
    body_y=96,
    footer_y=330, footer_size=21,
)

# The ramp. Six sizes for six strips, so a headline is the same weight in all of them.
STRIP_HEADLINE = 33      # the one sentence a strip is making
STRIP_HERO = 76          # a number that has to be read across a room
STRIP_LABEL = 23         # what a number means, under it
STRIP_SMALL = 19         # captions and asides
STRIP_MONO = 20          # script text, receipts, anything quoted from the machine
STRIP_LEADING = 30       # line-to-line inside a block of small type

STRIP_COL_GAP = 34       # between columns in a multi-column strip
STRIP_ORB_D = 84         # a cast orb at strip scale
STRIP_BENCH_D = 76       # one of the thirty-six on the bench strip
STRIP_BENCH_OVERLAP = 0.86   # horizontal packing, as a fraction of a diameter
STRIP_BENCH_ROW_STEP = 0.62  # vertical, ditto: overlapping rows, not stacked ones
STRIP_BENCH_TOP = 228        # first row centre: clear of the subhead above and the URL below
STRIP_WAVE_H = 96        # the waveform block
STRIP_WAVE_BARS = 150    # how many buckets the envelope is drawn as
# How long the waveform strip will wait on ffmpeg. Generous, because a cold boot
# decodes an episode while the site is coming up; bounded, because nothing that runs
# inside a startup hook may wait forever.
ENVELOPE_TIMEOUT_SECONDS = 120
STRIP_RULE_GAP = 18      # above and below a hairline inside a strip

STRIP_BAND_H = 46        # a timeline band: the turn map, the chapter run
STRIP_BAND_GAP = 2       # between two spans in a band, so the turns read as separate
STRIP_TICK_H = 12        # a mark under a band

# Code strips get their own frame. A snippet worth showing is eight to ten lines, and ten lines
# at a size anyone can read does not fit 400px. Still 600 wide displayed, so it drops into the
# same newsletter column as the rest of the set.
STRIP_CODE_SIZE = 19
STRIP_CODE_LEADING = 26

# Height is sized to the LONGEST snippet in the set, eight lines, plus its footnote: 96 for the
# lockup, 33 of headline, 8 x 26 of code, and a line of prose under it. Shorter snippets leave
# air at the bottom rather than each code strip being its own height, because six strips of six
# different heights do not stack into anything.
STRIP_TALL = StripFrame(
    width=1200, height=500, css_width=600, pad_x=56,
    eyebrow_y=44, eyebrow_size=19, eyebrow_track=5,
    body_y=96,
    footer_y=440, footer_size=21,
)
