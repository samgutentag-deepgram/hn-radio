"""Every colour, size and spacing value the share cards use. One module, on purpose.

The cards wear the Deepgram 2026 brand kit, the same one the site wears, and they are built to be
restyled by someone who does not read Python. Three surfaces and no fourth:

    COLOUR      web/brand.css block 0 (the pack, tiles included) and block 1 (per-voice runs),
                read at render time by the functions below.
    MATERIALS   the kit's own files in web/: the felt colorways (brand/art), the voice-card
                objects and their tile map (brand/avatars), Work Sans and Azeret Mono (fonts),
                and the 2026 logo, rasterized once into cards/assets as an alpha mask.
    GEOMETRY    the `Layout` instances at the bottom of this file. Sizes, spacing, weights.

Nothing under `hn_radio/cards/` is allowed to contain a colour literal, a font size or a pixel
offset. `paint.py` draws what it is handed and `layout.py` places what this file measures out.
That is the same seal `brand.css` puts on itself (see its header), extended to the raster.

WHY COLOUR IS NOT COPIED INTO THIS FILE. The site, the album art and these cards would then hold
three copies of the palette, and the next rebrand would land on one of them. A card that drifted
from the site it links to is the specific failure worth designing against, because the reader
sees them a click apart.

THE FONTS ARE THE KIT'S, shipped in web/fonts under the OFL. Work Sans for display and UI,
Azeret Mono for anything technical, both variable, so the weight is set per role below. The
system faces after them in `FONT_CANDIDATES` only matter on a checkout with web/ missing. There
is NO bitmap fallback: a card with Pillow's default bitmap font looks broken rather than
unbranded, so a machine with no usable TrueType raises `FontsUnavailable` and the episode's card
is skipped with a log line.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

# Bumped whenever a value in this file, or a composition in `layout.py`, changes what a card looks
# like. It is written into `card.json` and compared on every rebuild, so a restyle regenerates the
# archive on the next boot rather than leaving old cards on disk looking like the old brand.
# 2: the Deepgram 2026 kit (felt, voice cards, Work Sans and Azeret Mono, the logo).
CARD_VERSION = 2

WEB = Path(__file__).resolve().parent.parent.parent / "web"
BRAND_CSS = WEB / "brand.css"
FELT_DIR = WEB / "brand" / "art"
AVATAR_DIR = WEB / "brand" / "avatars"
AVATAR_TILES = AVATAR_DIR / "voices.json"
FONT_DIR = WEB / "fonts"
# The kit's 2026 wordmark, rasterized once from web/brand/deepgram-logo-2026-dark.svg at 2400 dpi
# and kept as alpha only, so it is painted in whatever ink the card needs. Pillow cannot read SVG.
LOGO_MASK = Path(__file__).resolve().parent / "assets" / "deepgram-logo-2026-mask.png"


class FontsUnavailable(RuntimeError):
    """No usable TrueType font on this machine. Raised rather than falling back to a bitmap."""


# --- reading the pack ----------------------------------------------------------------

# Block 0 is the first `:root { ... }` in brand.css and it is the ONLY place in that file allowed
# to hold a literal (its own header says so). `html[data-theme="light"]` below it is a selector,
# not :root, and the reduced-motion :root comes later, so the first match is exact.
_ROOT_BLOCK = re.compile(r":root\s*\{(.*?)\n\}", re.DOTALL)
_DECL = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;]+);")

# Block 1, one rule per voice. The light shade `--v-l` left brand.css with the molten orb, so it is
# optional here and `voice_palettes` hands back the mid shade in its place.
_VOICE_RULE = re.compile(
    r'\[data-voice="(flux-[^"]+)"\]\s*\{\s*(?:--v-l:\s*(#\w{6});\s*)?--v-m:\s*(#\w{6});'
    r'\s*--v-d:\s*(#\w{6});\s*--v-g:\s*([\d, ]+);')


def _hex_rgb(text: str) -> RGB:
    h = text.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _parse_colour(value: str, ground: RGB) -> RGB:
    """A brand.css colour value as opaque RGB, compositing any alpha over `ground`.

    The strokes in the pack are alpha hairlines rather than grey lines, deliberately (see
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

    Fails loudly on a missing token: a card that silently shipped a default colour would look fine
    and be wrong, which is the worst outcome available to a generated brand asset.
    """
    decls = _declarations()
    if name not in decls:
        raise KeyError(f"{name} is not in {BRAND_CSS} block 0")
    return _parse_colour(decls[name], ground)


@lru_cache(maxsize=1)
def voice_palettes() -> Dict[str, Tuple[RGB, RGB, RGB, RGB]]:
    """voice id -> (light, mid, deep, glow), out of brand.css block 1.

    Block 1 is the set of voices the site knows, so the cards use it as the roster check: a voice
    with no entry is not a voice the show can name. `light` is the mid shade now that the stylesheet
    no longer carries a light one.
    """
    out = {}
    for vid, light, mid, deep, glow in _VOICE_RULE.findall(BRAND_CSS.read_text()):
        out[vid] = (_hex_rgb(light or mid), _hex_rgb(mid), _hex_rgb(deep),
                    tuple(int(x) for x in glow.split(",")))
    if len(out) < 30:
        raise RuntimeError(f"only {len(out)} voice palettes matched in {BRAND_CSS}; "
                           "block 1's shape changed and the regex needs updating")
    return out


def voice_key(voice_id: str) -> str:
    """'flux-alexis-en' -> 'alexis', the name the voice-card files go by."""
    k = str(voice_id or "").strip().lower()
    k = re.sub(r"^flux-", "", k)
    return re.sub(r"-[a-z]{2}$", "", k)


@lru_cache(maxsize=1)
def _avatar_tiles() -> Dict[str, str]:
    """voice key -> its tile token name ('tile-led'), from the official card set's own map.

    voices.json carries the tile as a hex. It is matched back to the `--dg-tile-*` token holding
    that value, so the colour still comes from block 0 and a tile the pack does not declare fails
    loudly instead of being painted in a stray hex.
    """
    raw = json.loads(AVATAR_TILES.read_text())
    by_value = {v.lower(): k for k, v in _declarations().items() if k.startswith("dg-tile-")}
    out = {}
    for key, hexval in raw.items():
        name = by_value.get(str(hexval).lower())
        if name is None:
            raise KeyError(f"{key}'s tile {hexval} in {AVATAR_TILES} is not a --dg-tile-* token")
        out[key] = name
    return out


def voice_card(voice_id: str) -> Optional[Path]:
    """The voice's official card object, or None when the kit has no card for it."""
    k = voice_key(voice_id)
    path = AVATAR_DIR / f"{k}.webp"
    return path if k in _avatar_tiles() and path.is_file() else None


def voice_tile(voice_id: str) -> RGB:
    """The saturated tile a voice's card sits on, or the neutral tile for a voice with no card."""
    name = _avatar_tiles().get(voice_key(voice_id))
    return token(name) if name else token("dg-tile-neutral")


# --- the felt ------------------------------------------------------------------------

COLORWAYS = 5
# The episode-page set of the kit's felt (web/brand/art/d1..d5.jpg). The `a` set is the site's
# chrome; a card always stands for one episode.
FELT_SET = "d"


def felt_colorway(episode_id: str) -> int:
    """1 to 5: the colorway for the DAY an episode aired. Both editions of a day share one.

    Per day rather than per week, because HN Radio publishes twice a day and its archive is a grid
    of days: a week of one colorway would put fourteen cards in a row that look identical.
    """
    try:
        day = date.fromisoformat(str(episode_id)[:10])
    except ValueError:
        return 1
    return (day - date(1970, 1, 1)).days % COLORWAYS + 1


def felt_path(episode_id: str) -> Path:
    return FELT_DIR / f"{FELT_SET}{felt_colorway(episode_id)}.jpg"


# --- the colours a card uses ---------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    """The colours the compositions are allowed to name, resolved from the pack."""
    ground: RGB
    ink: RGB
    ink_secondary: RGB
    ink_muted: RGB
    accent: RGB
    # Keywords in a code snippet. Glass blue, because the accent is already spoken for by strings
    # and LED violet is under 4.5:1 on the card's dark ground.
    keyword: RGB
    danger: RGB
    # PREMULTIPLIED, to be ADDED rather than painted. The pack's strokes are alpha hairlines, so a
    # raster adds that much light to whatever is under it, the same rule the browser applies.
    hairline_add: RGB
    # What the felt is darkened toward where the type sits.
    scrim: RGB
    # The voice-card tile fades to this at the bottom, the same in both themes.
    tile_fade: RGB
    # Ink on a tile: a letter on a voice with no card.
    ink_on_tile: RGB


@lru_cache(maxsize=1)
def palette() -> Palette:
    ground = token("dg-surface-raised", ground=token("dg-surface-page"))
    return Palette(
        ground=ground,
        ink=token("dg-ink-primary"),
        ink_secondary=token("dg-ink-secondary"),
        ink_muted=token("dg-ink-secondary"),
        accent=token("dg-accent"),
        keyword=token("dg-tile-glass"),
        danger=token("dg-danger"),
        hairline_add=token("dg-line-hover", ground=(0, 0, 0)),
        scrim=token("dg-surface-page"),
        tile_fade=token("dg-tile-fade"),
        ink_on_tile=token("dg-ink-on-accent"),
    )


# --- type ----------------------------------------------------------------------------

# Three roles, the same three brand.css block 0 declares: display for the numerals and the
# wordmark, ui for everything set in sentences, mono for the URL and anything technical. A URL in
# a proportional face is harder to copy by eye off an image, which is the only way some readers
# will reach it. The kit's files first; system faces only for a checkout without web/.
FONT_CANDIDATES: Dict[str, Sequence[str]] = {
    "display": (
        str(FONT_DIR / "WorkSans-Variable.ttf"),
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    "ui": (
        str(FONT_DIR / "WorkSans-Variable.ttf"),
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "mono": (
        str(FONT_DIR / "AzeretMono-Variable.ttf"),
        "/System/Library/Fonts/Monaco.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ),
}

# The weight each role is set at on the variable faces. Azeret Mono's file defaults to 100
# (Thin), so leaving this unset paints hairline type.
FONT_WEIGHTS: Dict[str, int] = {"display": 600, "ui": 400, "mono": 500}


@lru_cache(maxsize=64)
def font(role: str, size: int):
    """A PIL font for `role` at `size`. Raises `FontsUnavailable` rather than degrading.

    TRIES each candidate rather than stat'ing it, so a present-but-unreadable file falls through
    to the next one instead of being chosen and then failing mid-render.

    A MISSING PILLOW COMES BACK AS `FontsUnavailable` TOO. `publish.rebuild_site` builds cards
    inside the app's startup hook and inside the nightly render, and neither the website nor
    tomorrow's episode should stop existing because an image library did.
    """
    try:
        from PIL import ImageFont
    except ImportError as e:
        raise FontsUnavailable(f"Pillow is not installed, so no card can be drawn: {e}") from e
    for path in FONT_CANDIDATES[role]:
        try:
            f = ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
        try:
            f.set_variation_by_axes([FONT_WEIGHTS[role]])
        except (OSError, ValueError, AttributeError):
            pass   # a static system face, or a Pillow without variation support
        return f
    raise FontsUnavailable(
        f"no usable TrueType font for the '{role}' role. Tried: "
        + ", ".join(FONT_CANDIDATES[role]))


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
    cast_d: int             # one voice-card bubble's diameter
    cast_center_y: int
    cast_gap: int
    cast_name_y: int
    cast_name_size: int
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
    # The logo stands alone at the right end of the URL line: the kit never locks it up with the
    # show name, so it is as far from the wordmark as the card allows.
    logo_h: int

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
    cast_d=132, cast_center_y=276, cast_gap=26,
    cast_name_y=356, cast_name_size=20,
    meta_y=390, meta_size=20, meta_inline=False,
    rule_bottom_y=482,
    credit_y=508, credit_size=29, credit_min_size=23,
    url_y=556, url_size=25,
    logo_h=26,
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
    cast_d=116, cast_center_y=116, cast_gap=22,
    cast_name_y=186, cast_name_size=22,
    meta_y=190, meta_size=26, meta_inline=True,
    rule_bottom_y=646,
    credit_y=674, credit_size=34, credit_min_size=27,
    url_y=730, url_size=28,
    logo_h=30,
)

LAYOUTS = (SOCIAL, EMAIL)


# --- the two materials ---------------------------------------------------------------
#
# The felt and the voice cards are the kit's signatures, and both are read from the same files the
# site and the album art use, so the card, the cover and the page are recognisably one object.

# The felt. Softened a little so its grain does not fight the type, then darkened toward the scrim
# where the type sits: a uniform floor, plus washes down from the top (the lockup and dateline), in
# from the left (the column of text) and up from the bottom (the credit line and the URL). Values
# are the scrim's opacity, 0 to 1. Tuned so secondary ink clears 4.5:1 and the accent URL clears
# 4.5:1 on all five colorways (tests/test_cards.py measures it); the felt shows through between.
FELT_BLUR = 0.004             # gaussian radius as a fraction of the card's width
SCRIM_BASE = 0.54
SCRIM_LEFT = 0.32             # extra, fading out by SCRIM_LEFT_REACH of the width
SCRIM_LEFT_REACH = 0.72
SCRIM_BOTTOM = 0.30           # extra, rising from SCRIM_BOTTOM_FROM of the height
SCRIM_BOTTOM_FROM = 0.55
SCRIM_TOP = 0.30              # extra, fading out by SCRIM_TOP_REACH of the height
SCRIM_TOP_REACH = 0.35
SCRIM_STEPS = 200             # the scrim is built small and scaled up

# A voice card, as the site's .cluster-bubble draws it: a round tile, solid to TILE_SPLIT of its
# height and fading to the pale below, with the card's object filling AVATAR_FILL of it.
TILE_SPLIT = 0.42
AVATAR_FILL = 0.80
LETTER_SIZE = 0.42            # a voice with no card: its initial, as a fraction of the diameter
BUBBLE_SHADOW = 0.06          # blur of the soft shadow under a bubble, as a fraction of it
BUBBLE_SHADOW_DROP = 0.05
BUBBLE_SHADOW_ALPHA = 110
CARD_SUPERSAMPLE = 2          # bubbles are drawn at 2x and resampled, so the edge stays smooth

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
    logo_h: int             # the logo, alone at the right end of the URL line

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
    footer_y=330, footer_size=21, logo_h=22,
)

# The ramp. Six sizes for six strips, so a headline is the same weight in all of them.
STRIP_HEADLINE = 33      # the one sentence a strip is making
STRIP_HERO = 76          # a number that has to be read across a room
STRIP_LABEL = 23         # what a number means, under it
STRIP_SMALL = 19         # captions and asides
STRIP_MONO = 20          # script text, receipts, anything quoted from the machine
STRIP_LEADING = 30       # line-to-line inside a block of small type
STRIP_HERO_MIN = 52      # the floor a strip figure steps down to so it stays in its column
STRIP_SMALL_MIN = 15     # the floor a caption steps down to before it is cut

STRIP_COL_GAP = 34       # between columns in a multi-column strip
STRIP_CAST_D = 84        # a voice card at strip scale
STRIP_BENCH_D = 76       # one voice card on the bench strip
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
STRIP_CODE_MIN = 12      # the floor a snippet steps down to so its longest line fits

# Height is sized to the LONGEST snippet in the set, eight lines, plus its footnote: 96 for the
# lockup, 33 of headline, 8 x 26 of code, and a line of prose under it. Shorter snippets leave
# air at the bottom rather than each code strip being its own height, because six strips of six
# different heights do not stack into anything.
STRIP_TALL = StripFrame(
    width=1200, height=500, css_width=600, pad_x=56,
    eyebrow_y=44, eyebrow_size=19, eyebrow_track=5,
    body_y=96,
    footer_y=440, footer_size=21, logo_h=22,
)
