"""Drawing primitives. Pillow lives here and nowhere else under `hn_radio/cards/`.

It knows how to make marks and nothing about what a card says. Every colour and every dimension
arrives as an argument from `layout.py`, which reads them from `tokens.py`. There is not a hex
value or a pixel offset in this file, which is the seal `tokens.py`'s header describes.

TWO THINGS ARE COPIED FROM `scripts/make_cover.py` AND THE COPY IS DELIBERATE. The orb geometry
and its additive compositing are the design language's, and the cover script is a standalone tool
Sam runs by hand a couple of times a year, outside the package, with its own oversampling and its
own honeycomb. Importing this module into it would couple a hand-run script to the nightly render
path for two functions. The values both read now come from `tokens.py`, which is the coupling
that actually mattered.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import tokens

RGB = Tuple[int, int, int]


def canvas(size: Tuple[int, int], palette: tokens.Palette) -> Image.Image:
    """The ground: the page colour with the ambient field washed over it."""
    w, h = size
    img = Image.new("RGB", size, palette.ground)
    field = Image.new("RGB", size, (0, 0, 0))
    d = ImageDraw.Draw(field)
    for box, lobe in ((tokens.AMBIENT_WARM_BOX, tokens.AMBIENT_WARM),
                      (tokens.AMBIENT_COOL_BOX, tokens.AMBIENT_COOL)):
        left, top, rightf, bottom = box
        d.ellipse([w * left, h * top, w * rightf, h * bottom], fill=palette.ambient[lobe])
    field = field.filter(ImageFilter.GaussianBlur(w * tokens.AMBIENT_BLUR))
    return Image.blend(img, field, tokens.AMBIENT_STRENGTH)


def orb(d: int, shades: Sequence[RGB], glow_rgb: RGB, ground: RGB) -> Image.Image:
    """One molten orb, `d` pixels across, as RGBA with a transparent surround.

    THE SHADES ACCUMULATE. The reference composites its lava layers with `lighter`, so an overlap
    is brighter than either layer. Alpha-blending them instead turns the object into a grey smudge;
    that is the first mistake anyone makes here and it is why this says so.

    Shades run deep-to-light onto the ellipses, the reverse of the reference's declared order. On
    screen the reference scales its lava layer down and clips most of the largest, lowest ellipse
    at the orb's edge, so it gets away with the lightest shade there. Unclipped, that fills the
    lower half with near-white and the orb reads as a blob.
    """
    ss = tokens.ORB_SUPERSAMPLE
    n = d * ss
    lava = Image.new("RGB", (n, n), (0, 0, 0))
    for (lx, ty, w, h, blur), shade in zip(tokens.ORB_ELLIPSES, tuple(reversed(tuple(shades)))):
        layer = Image.new("RGB", (n, n), (0, 0, 0))
        ImageDraw.Draw(layer).ellipse([lx * n, ty * n, (lx + w) * n, (ty + h) * n], fill=shade)
        lava = ImageChops.lighter(lava, layer.filter(ImageFilter.GaussianBlur(blur * n)))
    lava = ImageChops.lighter(lava, Image.new("RGB", (n, n), ground))

    # The glow rides the inside of the top edge, which is what stops the sphere reading as a flat
    # disc with a stain on it.
    inset = Image.new("RGB", (n, n), (0, 0, 0))
    ir = int(n * tokens.ORB_INSET_GLOW_R)
    ImageDraw.Draw(inset).ellipse(
        [n / 2 - ir, int(n * tokens.ORB_INSET_GLOW_TOP), n / 2 + ir, 2 * ir],
        outline=glow_rgb, width=max(1, int(n * tokens.ORB_INSET_GLOW_W)))
    lava = ImageChops.lighter(
        lava, inset.filter(ImageFilter.GaussianBlur(int(n * tokens.ORB_INSET_GLOW_BLUR))))

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, n - 1, n - 1], fill=255)
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    out.paste(lava, (0, 0), mask)
    return out.resize((d, d), Image.LANCZOS)


def bleed(img: Image.Image, cx: int, cy: int, d: int, glow_rgb: RGB) -> None:
    """The voice's glow spilled under and around the orb, in place. The reference's drop shadow.

    ADDITIVE, where `scripts/make_cover.py` blends. The difference matters and it is not taste.
    A blend against a mostly-black glow layer DARKENS everything it covers: the cover gets away
    with that because it applies thirty-six of them to the whole canvas before it draws anything,
    so the dimming is uniform and reads as the deep ground the art wants. Here it dimmed a
    #fbfbff wordmark to #7a7a7c, and cropping the blend to the glow's own box only traded that
    for two visible rectangles where the ambient wash stepped darker inside the crop.

    Adding light has neither failure. Outside the ellipse the layer is zero, so the operation is
    the identity there, and inside it the ground gets brighter, which is what a glow does.
    """
    r = int(d * tokens.ORB_GLOW_R)
    drop = int(d * tokens.ORB_GLOW_DROP)
    layer = Image.new("RGB", img.size, (0, 0, 0))
    ImageDraw.Draw(layer).ellipse([cx - r, cy - r + drop, cx + r, cy + r + drop], fill=glow_rgb)
    layer = layer.filter(ImageFilter.GaussianBlur(max(1, int(d * tokens.ORB_GLOW_BLUR))))
    layer = layer.point(lambda v: int(v * tokens.ORB_GLOW_STRENGTH))
    img.paste(ImageChops.add(img, layer), (0, 0))


def place_orb(img: Image.Image, cx: int, cy: int, d: int, shades, glow_rgb, ground) -> None:
    """Paste one orb. The caller lays down every `bleed` first; see `layout._paint_cast`."""
    o = orb(d, shades, glow_rgb, ground)
    img.paste(o, (cx - d // 2, cy - d // 2), o)


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
# easier to scan (brand.css block 1's standing rule, applied to type instead of orbs).
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
            out.append((buf, palette.accent_alt))
            buf = ""
        flush(palette.ink_secondary)
        buf = ch
        flush(palette.ink_secondary)
        i += 1
    if buf in _KEYWORDS:
        out.append((buf, palette.accent_alt))
    else:
        flush(palette.ink_secondary)
    return out


def code_line(draw: ImageDraw.ImageDraw, xy, spans: Sequence, font) -> None:
    """Draw pre-coloured spans on one line, advancing by the measured width of each."""
    x, y = xy
    for text, colour in spans:
        draw.text((x, y), text, font=font, fill=colour)
        x += draw.textlength(text, font=font)
