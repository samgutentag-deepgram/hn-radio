"""Drawing primitives. Pillow lives here and nowhere else under `hn_radio/cards/`.

It knows how to make marks and nothing about what a card says. Every colour and every dimension
arrives as an argument from `layout.py`, which reads them from `tokens.py`. There is not a hex
value or a pixel offset in this file, which is the seal `tokens.py`'s header describes.

The felt and the voice-card bubble follow `scripts/make_art.py`, which draws the same two things
on the album art, so the card and the cover are one object. Not imported from there, because that
is a standalone script and this is the nightly render path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import tokens

RGB = Tuple[int, int, int]


@lru_cache(maxsize=None)
def _felt(path: Path, size: Tuple[int, int]) -> Image.Image:
    """The kit's felt at `size`, cover-cropped and softened. Off-black if the file is missing."""
    w, h = size
    try:
        src = Image.open(path).convert("RGB")
    except OSError:
        return Image.new("RGB", size, tokens.palette().ground)
    scale = max(w / src.width, h / src.height)
    sw, sh = round(src.width * scale), round(src.height * scale)
    src = src.resize((sw, sh), Image.LANCZOS)
    left, top = (sw - w) // 2, (sh - h) // 2
    src = src.crop((left, top, left + w, top + h))
    return src.filter(ImageFilter.GaussianBlur(w * tokens.FELT_BLUR))


def _scrim_mask(size: Tuple[int, int]) -> Image.Image:
    """How much of the scrim lands where: a floor, more at the top, the left and the bottom."""
    n = tokens.SCRIM_STEPS
    mask = Image.new("L", (n, n), 0)
    px = mask.load()
    for y in range(n):
        fy = y / n
        low = max(0.0, (fy - tokens.SCRIM_BOTTOM_FROM) / (1 - tokens.SCRIM_BOTTOM_FROM))
        high = max(0.0, 1 - fy / tokens.SCRIM_TOP_REACH)
        for x in range(n):
            side = max(0.0, 1 - (x / n) / tokens.SCRIM_LEFT_REACH)
            a = (tokens.SCRIM_BASE + side * tokens.SCRIM_LEFT + low * tokens.SCRIM_BOTTOM
                 + high * tokens.SCRIM_TOP)
            px[x, y] = min(255, int(255 * a))
    return mask.resize(size, Image.BILINEAR)


def canvas(size: Tuple[int, int], palette: tokens.Palette, episode_id: str = "") -> Image.Image:
    """The ground: the episode's felt colorway, darkened under a scrim where the type sits."""
    img = _felt(tokens.felt_path(episode_id), size).copy()
    img.paste(Image.new("RGB", size, palette.scrim), (0, 0), _scrim_mask(size))
    return img


def bubble(d: int, voice_id: str, palette: tokens.Palette, letter: str = "") -> Image.Image:
    """One voice card as a round bubble, `d` pixels across, RGBA.

    The voice's official object on its tile when the kit has a card for it; otherwise `letter`
    (the speaker's initial) on the neutral tile, which is the site's rule for those voices.
    """
    ss = tokens.CARD_SUPERSAMPLE
    n = d * ss
    tile = tokens.voice_tile(voice_id)
    grad = Image.new("RGB", (n, n), tile)
    g = ImageDraw.Draw(grad)
    split = tokens.TILE_SPLIT
    for y in range(n):
        t = max(0.0, (y / n - split) / (1 - split))
        g.line([(0, y), (n, y)], fill=tuple(round(a + (b - a) * t)
                                            for a, b in zip(tile, palette.tile_fade)))
    out = grad.convert("RGBA")
    card = tokens.voice_card(voice_id)
    drawn = False
    if card is not None:
        try:
            obj = Image.open(card).convert("RGBA")
            box = int(n * tokens.AVATAR_FILL)
            obj.thumbnail((box, box), Image.LANCZOS)
            out.alpha_composite(obj, ((n - obj.width) // 2, (n - obj.height) // 2))
            drawn = True
        except OSError:
            drawn = False
    if not drawn:
        initial = (letter or tokens.voice_key(voice_id))[:1].upper() or "?"
        ImageDraw.Draw(out).text((n / 2, n / 2), initial,
                                 font=tokens.font("display", int(n * tokens.LETTER_SIZE)),
                                 fill=palette.ink_on_tile, anchor="mm")
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, n - 1, n - 1], fill=255)
    out.putalpha(mask)
    return out.resize((d, d), Image.LANCZOS)


def place_bubble(img: Image.Image, cx: int, cy: int, d: int, voice_id: str,
                 palette: tokens.Palette, letter: str = "") -> None:
    """Paste one voice card centred on (cx, cy), over a soft shadow so it sits on the felt."""
    blur = max(1, int(d * tokens.BUBBLE_SHADOW))
    pad = blur * 3
    drop = int(d * tokens.BUBBLE_SHADOW_DROP)
    shadow = Image.new("L", (d + pad * 2, d + pad * 2), 0)
    ImageDraw.Draw(shadow).ellipse([pad, pad, pad + d, pad + d], fill=tokens.BUBBLE_SHADOW_ALPHA)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    x0, y0 = cx - d // 2 - pad, cy - d // 2 - pad + drop
    img.paste(Image.new("RGB", shadow.size, palette.scrim), (x0, y0), shadow)
    b = bubble(d, voice_id, palette, letter)
    img.paste(b, (cx - d // 2, cy - d // 2), b)


@lru_cache(maxsize=1)
def _logo_mask() -> Image.Image:
    return Image.open(tokens.LOGO_MASK).convert("L")


def logo(img: Image.Image, right: int, baseline: int, height: int, colour: RGB) -> int:
    """The 2026 wordmark, `height` pixels tall, its right edge at `right` and its foot on
    `baseline`. Painted through its alpha so it takes the card's ink. Returns its width."""
    src = _logo_mask()
    width = round(src.width * height / src.height)
    mask = src.resize((width, height), Image.LANCZOS)
    img.paste(Image.new("RGB", mask.size, colour), (right - width, baseline - height), mask)
    return width


def rule(img: Image.Image, x0: int, x1: int, y: int, add: RGB, height: int) -> None:
    """A hairline: `add` worth of light laid over whatever is already at that row.

    Takes the image rather than a draw handle because it composites instead of painting. See
    `Palette.hairline_add` for why a stroke on this card is added and not filled.
    """
    box = (x0, y, x1, y + height)
    region = img.crop(box)
    img.paste(ImageChops.add(region, Image.new("RGB", region.size, add)), box[:2])


def text(draw: ImageDraw.ImageDraw, xy, s: str, font, fill: RGB, anchor: str = "la") -> None:
    draw.text(xy, s, font=font, fill=fill, anchor=anchor)


def tracked(draw: ImageDraw.ImageDraw, xy, s: str, font, fill: RGB, track: int,
            anchor: str = "la") -> int:
    """Letterspaced text, drawn a glyph at a time. Returns the width painted.

    Pillow has no tracking, and the eyebrow lockup is tracked on the site, on the album art and in
    the design language. Faking it with spaces between letters gives the word-space width rather
    than a chosen one, and breaks the moment the face changes.

    Only `la` and `ra` anchors, because those are the only two a lockup uses and supporting the
    rest would mean reimplementing Pillow's anchor table for a case nothing calls.
    """
    width = tracked_width(draw, s, font, track)
    x, y = xy
    if anchor == "ra":
        x -= width
    elif anchor != "la":
        raise ValueError(f"tracked() supports the 'la' and 'ra' anchors, got {anchor!r}")
    for ch in s:
        draw.text((x, y), ch, font=font, fill=fill, anchor="la")
        x += draw.textlength(ch, font=font) + track
    return width


def tracked_width(draw: ImageDraw.ImageDraw, s: str, font, track: int) -> float:
    if not s:
        return 0.0
    return sum(draw.textlength(ch, font=font) for ch in s) + track * (len(s) - 1)


def fitted(draw: ImageDraw.ImageDraw, s: str, role: str, size: int, max_width: int,
           min_size: int) -> Optional[object]:
    """The largest font from `size` down to `min_size` at which `s` fits `max_width`.

    ONE STRING ON THESE CARDS CHANGES WIDTH WITH THE DATA, and it is the credit line: the archive
    runs from 734 episodes per credit to 1,992, and a rate of $0.06 would put 3,333 on it. A fixed
    size that fits the widest case wastes the line at every real value, and one that fits the
    common case overruns into the URL beneath it at the cheap end. Stepping down by a point at a
    time is the smallest thing that is correct at both.

    Returns None when even `min_size` overruns, which is the caller's signal that the copy is
    wrong rather than the type size. Nothing silently clips.
    """
    for pt in range(size, min_size - 1, -1):
        font = tokens.font(role, pt)
        if draw.textlength(s, font=font) <= max_width:
            return font
    return None


def wrapped(draw: ImageDraw.ImageDraw, s: str, font, max_width: int, max_lines: int) -> list:
    """`s` broken into at most `max_lines` lines that each fit `max_width`, last one ellipsized.

    Greedy and word-based, which is all a headline needs. Measures every candidate line with the
    real font rather than counting characters, because the difference between "MMMM" and "iiii" at
    33px is most of a column.
    """
    words, lines, line = s.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > max_width:
            lines.append(line)
            line = word
            if len(lines) == max_lines:
                break
        else:
            line = candidate
    if len(lines) < max_lines and line:
        lines.append(line)
    if len(lines) == max_lines and (len(" ".join(lines).split()) < len(words)):
        tail = lines[-1]
        while tail and draw.textlength(tail + "...", font=font) > max_width:
            tail = tail.rsplit(" ", 1)[0] if " " in tail else tail[:-1]
        lines[-1] = tail + "..."
    return lines


def envelope(mp3: "Path", buckets: int) -> list:
    """An amplitude envelope for `mp3`, as `buckets` values between 0 and 1.

    Decoded with ffmpeg, which this project already requires for the chaptered MP3, rather than by
    adding an audio library for one picture. Mono at 4 kHz is far below anything you would listen
    to and far above what a 150-bar waveform can show, so it is the cheapest decode that cannot
    change the shape.

    Returns an empty list on any failure. A strip without its waveform is a strip with a gap in
    it; a build that died because ffmpeg moved is a missing episode.
    """
    import array
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        return []
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-i", str(mp3), "-ac", "1", "-ar", "4000",
             "-f", "s16le", "-"],
            capture_output=True, check=True,
            timeout=tokens.ENVELOPE_TIMEOUT_SECONDS).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % samples.itemsize)])
    if not samples:
        return []
    per = max(1, len(samples) // buckets)
    peaks = [max(abs(v) for v in samples[i * per:(i + 1) * per] or [0])
             for i in range(buckets)]
    ceiling = max(peaks) or 1
    return [p / ceiling for p in peaks]


def bars(img: Image.Image, x0: int, y_mid: int, width: int, height: int, values: list,
         colour: RGB) -> None:
    """A waveform as centred bars. Draws nothing when `values` is empty."""
    if not values:
        return
    step = width / len(values)
    d = ImageDraw.Draw(img)
    for i, v in enumerate(values):
        h = max(1, int(v * height / 2))
        x = int(x0 + i * step)
        d.rectangle([x, y_mid - h, x + max(1, int(step) - 1), y_mid + h], fill=colour)


# Enough of a lexer to make a snippet read as code, and no more. Colour here is reinforcement:
# every one of these lines is legible in one ink, and the highlighting only makes the shape
# easier to scan (brand.css block 1's standing rule, applied to type).
_KEYWORDS = frozenset((
    "def", "return", "if", "else", "elif", "for", "while", "in", "not", "and", "or", "import",
    "from", "with", "as", "try", "except", "raise", "lambda", "None", "True", "False", "class",
    "curl", "export",
))


def code_spans(line: str, palette) -> list:
    """One source line as [(text, colour)]. Comments, strings, keywords, everything else.

    Hand-rolled rather than pulling in Pygments: this needs to colour four categories in two
    languages on a picture, and a syntax-highlighting dependency on the nightly render path to do
    it would be the tail wagging the dog.
    """
    out, buf, i = [], "", 0
    def flush(colour):
        nonlocal buf
        if buf:
            out.append((buf, colour))
            buf = ""

    while i < len(line):
        ch = line[i]
        if ch == "#":
            flush(palette.ink_secondary)
            out.append((line[i:], palette.ink_muted))
            return out
        if ch in ("'", '"'):
            flush(palette.ink_secondary)
            j = i + 1
            while j < len(line) and line[j] != ch:
                j += 1
            out.append((line[i:j + 1], palette.accent))
            i = j + 1
            continue
        if ch.isalnum() or ch == "_":
            buf += ch
            i += 1
            continue
        if buf in _KEYWORDS:
            out.append((buf, palette.keyword))
            buf = ""
        flush(palette.ink_secondary)
        buf = ch
        flush(palette.ink_secondary)
        i += 1
    if buf in _KEYWORDS:
        out.append((buf, palette.keyword))
    else:
        flush(palette.ink_secondary)
    return out


def code_line(draw: ImageDraw.ImageDraw, xy, spans: Sequence, font) -> None:
    """Draw pre-coloured spans on one line, advancing by the measured width of each."""
    x, y = xy
    for text, colour in spans:
        draw.text((x, y), text, font=font, fill=colour)
        x += draw.textlength(text, font=font)
