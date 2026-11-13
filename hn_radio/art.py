"""The show cover and per-episode art, on the Deepgram 2026 brand kit.

Ported from the changelog show's `scripts/make_art.py` (the same rebrand, finished 2026-11-12),
which is why the two read alike. The ground is one of the kit's five felt colorways
(web/brand/art/). The voices sit in a group bubble, each one its official voice-card object on its
tile (web/brand/avatars/), the same cluster web/cluster.js puts on the play buttons. Type is Work
Sans for the date and the show name and Azeret Mono for the small labels and the story list, from
web/fonts/. The Deepgram logo stands alone, never locked up with the show name, per the kit.

HOW THE COLORWAY IS PICKED. Per DAY, not per week: HN Radio publishes a Morning and an Afternoon
Edition every day, and the site lays the archive out one row per date, so both editions of a day
share a colorway and the next day moves on. The rule matches web/felt.js exactly:
`days since 1970-01-01 % 5 + 1`.

Every asset is read from web/, which the Dockerfile copies into the image, so the render in the
container draws the same art as a local run. Pillow is the only non-stdlib import, and it is
imported lazily so a machine without it can still import this module (and fail per call).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from . import config

WEB = config.PROJECT_ROOT / "web"
FELT = WEB / "brand" / "art"
AVATARS = WEB / "brand" / "avatars"
FONTS = WEB / "fonts"
LOGO_SVG = WEB / "brand" / "deepgram-logo-2026-dark.svg"
ART_FILE = "art.jpg"

# The kit's colors, mirrored from web/brand.css block 0.
OFF_WHITE = (252, 250, 248)
OFF_BLACK = (29, 29, 29)
LIGHT_GRAY = (196, 196, 196)
FUCHSIA = (224, 66, 255)
TILES = {
    "bakelite": (250, 129, 0), "led": (127, 57, 255), "brass": (247, 243, 0), "glass": (0, 178, 255),
    "fuchsia": (224, 66, 255), "clay": (235, 42, 46), "phosphor": (124, 217, 65),
}
NEUTRAL_TILE = LIGHT_GRAY
# Which tile each voice card sits on, from the official card set (web/brand/avatars/voices.json).
VOICE_TILE = {
    "colin": "bakelite", "gemma": "bakelite", "alexis": "led", "brittany": "led",
    "haley": "brass", "kit": "brass", "sean": "brass", "sienna": "brass",
    "kai": "glass", "miles": "glass", "cliff": "glass", "brooke": "glass",
    "meena": "fuchsia", "bree": "fuchsia", "cole": "clay", "hannah": "clay", "heather": "phosphor",
}
COLORWAYS = 5
EDITION_LABEL = {"am": "Morning Edition", "pm": "Afternoon Edition"}

# Bubble seats as fractions of the cluster: x, y, diameter. One to four mirror web/cluster.js;
# five and six only appear on the show cover.
LAYOUTS = {
    1: [(0.50, 0.50, 0.72)],
    2: [(0.38, 0.40, 0.50), (0.65, 0.64, 0.42)],
    3: [(0.40, 0.38, 0.50), (0.72, 0.56, 0.36), (0.42, 0.74, 0.32)],
    4: [(0.37, 0.37, 0.46), (0.71, 0.37, 0.33), (0.65, 0.69, 0.40), (0.32, 0.69, 0.30)],
    5: [(0.38, 0.36, 0.42), (0.72, 0.33, 0.30), (0.74, 0.64, 0.32), (0.45, 0.72, 0.32), (0.18, 0.58, 0.22)],
    6: [(0.36, 0.34, 0.40), (0.70, 0.29, 0.28), (0.79, 0.58, 0.26), (0.57, 0.76, 0.30), (0.27, 0.69, 0.30),
        (0.57, 0.52, 0.20)],
}

# The cover's bench: both hosts first, then four carded voices from across the catalog, one per
# tile color the hosts do not already wear, so the bubble reads as the whole kit at 55px.
COVER_CAST = ["alexis", "cole", "heather", "kai", "meena", "sienna"]


# ---- fonts -------------------------------------------------------------------------------------

FALLBACK_SANS = ["/System/Library/Fonts/HelveticaNeue.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
FALLBACK_MONO = ["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
_font_cache: dict = {}


def font(family: str, size: int, weight: int = 400):
    """Work Sans ('sans') or Azeret Mono ('mono') from web/fonts at a weight, with system fallbacks."""
    from PIL import ImageFont

    key = (family, size, weight)
    if key in _font_cache:
        return _font_cache[key]
    own = FONTS / ("WorkSans-Variable.ttf" if family == "sans" else "AzeretMono-Variable.ttf")
    f = None
    if own.exists():
        try:
            f = ImageFont.truetype(str(own), size)
            try:
                f.set_variation_by_axes([weight])
            except (OSError, ValueError, AttributeError):
                pass   # a Pillow built without variation support draws the default weight
        except OSError:
            f = None
    if f is None:
        for path in (FALLBACK_SANS if family == "sans" else FALLBACK_MONO):
            if Path(path).exists():
                try:
                    f = ImageFont.truetype(path, size)
                    break
                except OSError:
                    continue
    if f is None:
        f = ImageFont.load_default(size)
    _font_cache[key] = f
    return f


def fit(text: str, family: str, size: int, weight: int, max_w: float):
    """The largest font no bigger than `size` that fits `text` in `max_w` pixels."""
    while size > 8:
        f = font(family, size, weight)
        if f.getlength(text) <= max_w:
            return f
        size -= max(1, size // 40)
    return font(family, size, weight)


def wrap(text: str, f, max_w: float, max_lines: int) -> List[str]:
    """Greedy word wrap to `max_lines`, the last line cut with an ellipsis if the text runs over."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if f.getlength(trial) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    used = sum(len(ln.split()) for ln in lines)
    if used < len(words) and lines:
        last = lines[-1]
        while last and f.getlength(last + "…") > max_w:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last.rstrip(" ,;:.") + "…"
    return lines


# ---- voices ------------------------------------------------------------------------------------

def voice_key(name_or_voice: str) -> str:
    """'Alexis' or 'flux-alexis-en' -> 'alexis'."""
    k = str(name_or_voice or "").strip().lower()
    if k.startswith("flux-"):
        k = k[5:]
    if len(k) > 3 and k[-3] == "-":
        k = k[:-3]
    return k


def has_card(name_or_voice: str) -> bool:
    return (AVATARS / f"{voice_key(name_or_voice)}.webp").exists()


def episode_cast(segments: Sequence[dict]) -> List[str]:
    """Voice ids in order of appearance, the host first: the first anchor/host line's voice."""
    host = next((s.get("voice_id") for s in segments
                 if s.get("voice_id") and (s.get("desk") == "anchor" or s.get("role") in ("anchor", "host"))),
                None)
    out = [host] if host else []
    for s in segments:
        v = s.get("voice_id")
        if v and v not in out:
            out.append(v)
    return out


# ---- felt --------------------------------------------------------------------------------------

def colorway(day: date) -> int:
    """1 to 5: the kit colorway for `day`. Same rule as web/felt.js: one colorway per date."""
    return (day - date(1970, 1, 1)).days % COLORWAYS + 1


def felt(size: int, style: int, image: str = "d"):
    from PIL import Image

    path = FELT / f"{image}{style}.jpg"
    try:
        im = Image.open(path).convert("RGB")
    except OSError:
        return Image.new("RGBA", (size, size), OFF_BLACK + (255,))
    return im.resize((size, size), Image.LANCZOS).convert("RGBA")


def scrim(img, top: float, bottom: float, left: float) -> None:
    """Darken the felt where the type sits: washes down from the top, up from the bottom, and in from
    the left, so off-white type clears 4.5:1 on every colorway. Built small and scaled up."""
    from PIL import Image

    w, h = img.size
    n = 200
    mask = Image.new("L", (n, n), 0)
    px = mask.load()
    for y in range(n):
        fy = y / n
        v = max(0.0, 1 - fy / 0.45) * top + max(0.0, (fy - 0.5) / 0.5) * bottom
        for x in range(n):
            a = v + max(0.0, 1 - (x / n) / 0.6) * left
            px[x, y] = min(255, int(255 * a))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    shade.putalpha(mask.resize((w, h), Image.BILINEAR))
    img.alpha_composite(shade)


# ---- the logo ----------------------------------------------------------------------------------

def logo(height: int, color=OFF_WHITE):
    """The 2026 wordmark at `height` pixels, in one ink, as RGBA. None if it cannot be drawn.

    The kit ships it as SVG and Pillow has no SVG reader, so the paths are rasterized here: the
    file is a handful of `<path d=...>` made of M/L/H/V/C/Z commands, which is small enough to
    flatten by hand (cubic curves are sampled). Drawn 4x and scaled down for clean edges.
    """
    from PIL import Image, ImageChops, ImageDraw

    try:
        svg = LOGO_SVG.read_text(encoding="utf-8")
    except OSError:
        return None
    vb = re.search(r'viewBox="([\d.\s-]+)"', svg)
    if not vb:
        return None
    _, _, vw, vh = (float(v) for v in vb.group(1).split())
    scale = height * 4 / vh
    w, h = int(vw * scale) + 2, int(height * 4) + 2
    mask = Image.new("L", (w, h), 0)
    # Even-odd per path, so the counters in the D, the e and the a stay open: each subpath is
    # XORed into its path's mask, then the paths are unioned.
    for d in re.findall(r'\sd="([^"]+)"', svg):
        part = Image.new("L", (w, h), 0)
        for poly in _svg_polys(d):
            if len(poly) >= 3:
                one = Image.new("L", (w, h), 0)
                ImageDraw.Draw(one).polygon([(x * scale, y * scale) for x, y in poly], fill=255)
                part = ImageChops.difference(part, one)
        mask = ImageChops.lighter(mask, part)
    mask = mask.resize((max(1, w // 4), max(1, h // 4)), Image.LANCZOS)
    out = Image.new("RGBA", mask.size, color + (255,))
    out.putalpha(mask)
    return out


def _svg_polys(d: str) -> List[List[tuple]]:
    """Flatten one SVG path into polygons. Absolute and relative M/L/H/V/C/Z are supported, which is
    everything the kit's logo file uses; anything else raises, so a changed logo fails loudly."""
    toks = re.findall(r"[MmLlHhVvCcZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    polys, cur, x, y, sx, sy, i, cmd = [], [], 0.0, 0.0, 0.0, 0.0, 0, None

    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    while i < len(toks):
        if re.fullmatch(r"[A-Za-z]", toks[i]):
            cmd = toks[i]; i += 1
            if cmd in "Zz":
                if cur:
                    polys.append(cur)
                cur, x, y = [], sx, sy
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            if cur:
                polys.append(cur)
            cur, sx, sy = [(x, y)], x, y
            cmd = "l" if rel else "L"
        elif c == "L":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            cur.append((x, y))
        elif c == "H":
            nx = num(); x = x + nx if rel else nx; cur.append((x, y))
        elif c == "V":
            ny = num(); y = y + ny if rel else ny; cur.append((x, y))
        elif c == "C":
            pts = [num() for _ in range(6)]
            if rel:
                pts = [pts[k] + (x if k % 2 == 0 else y) for k in range(6)]
            x0, y0 = x, y
            for t in [k / 12 for k in range(1, 13)]:
                mt = 1 - t
                px = mt**3 * x0 + 3 * mt * mt * t * pts[0] + 3 * mt * t * t * pts[2] + t**3 * pts[4]
                py = mt**3 * y0 + 3 * mt * mt * t * pts[1] + 3 * mt * t * t * pts[3] + t**3 * pts[5]
                cur.append((px, py))
            x, y = pts[4], pts[5]
        else:
            raise ValueError(f"logo path uses an unsupported command {cmd!r}")
    if cur:
        polys.append(cur)
    return polys


# ---- the group bubble --------------------------------------------------------------------------

def _tile(d: int, color):
    """A round voice-card tile: the color down to 42%, then fading to off-white at the bottom."""
    from PIL import Image, ImageDraw

    grad = Image.new("RGBA", (d, d))
    gp = ImageDraw.Draw(grad)
    for y in range(d):
        t = max(0.0, (y / d - 0.42) / 0.58)
        c = tuple(round(a + (b - a) * t) for a, b in zip(color, OFF_WHITE))
        gp.line([(0, y), (d, y)], fill=c + (255,))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    grad.putalpha(mask)
    return grad


def _bubble(name: str, d: int, ringed: bool):
    from PIL import Image, ImageDraw

    k = voice_key(name)
    tile = _tile(d, TILES.get(VOICE_TILE.get(k, ""), NEUTRAL_TILE))
    src = AVATARS / f"{k}.webp"
    drawn = False
    if src.exists():
        try:
            obj = Image.open(src).convert("RGBA")
            obj.thumbnail((int(d * 0.8), int(d * 0.8)), Image.LANCZOS)
            tile.alpha_composite(obj, ((d - obj.width) // 2, (d - obj.height) // 2))
            drawn = True
        except OSError:
            pass
    if not drawn:
        ImageDraw.Draw(tile).text((d / 2, d / 2), (k[:1] or "?").upper(),
                                  font=font("sans", int(d * 0.42), 600), fill=OFF_BLACK, anchor="mm")
    if ringed:
        w = max(3, d // 36)
        ring = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((w // 2, w // 2, d - 1 - w // 2, d - 1 - w // 2),
                                     outline=FUCHSIA + (255,), width=w)
        tile.alpha_composite(ring)
    return tile


def _soft_shadow(img, box, alpha: int, blur: float) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    pad = int(blur * 3)
    x0, y0, x1, y1 = (int(v) for v in box)
    layer = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((pad, pad, pad + x1 - x0, pad + y1 - y0), fill=(0, 0, 0, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)), (x0 - pad, y0 - pad))


def cluster(img, cx: float, cy: float, diameter: int, names: Sequence[str], max_seats: int = 4) -> List[str]:
    """The cast as one group bubble: a frosted glass disc, the host in the largest seat, ringed.

    Only voices with an official card get a seat. The host keeps theirs regardless (as a letter
    tile if they have no card), so the bubble is never empty. Same rule as cluster.js's
    cardsOnly. Returns the keys that were seated, host first.
    """
    from PIL import Image, ImageDraw, ImageFilter

    seen, cast = set(), []
    for n in names:
        k = voice_key(n)
        if k and k not in seen:
            seen.add(k)
            cast.append(k)
    cast = [k for i, k in enumerate(cast) if i == 0 or has_card(k)]
    cast = cast[:max_seats] or ["alexis"]
    x0, y0 = int(cx - diameter / 2), int(cy - diameter / 2)

    _soft_shadow(img, (x0, y0 + diameter * 0.04, x0 + diameter, y0 + diameter * 1.04), 120, diameter * 0.05)
    # Frosted glass: the felt under the disc, blurred and darkened, clipped round.
    region = img.crop((x0, y0, x0 + diameter, y0 + diameter)).filter(ImageFilter.GaussianBlur(diameter * 0.04))
    region.alpha_composite(Image.new("RGBA", region.size, OFF_BLACK + (120,)))
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    region.putalpha(mask)
    img.alpha_composite(region, (x0, y0))
    edge = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    ImageDraw.Draw(edge).ellipse((0, 0, diameter - 1, diameter - 1), outline=OFF_WHITE + (80,),
                                 width=max(2, diameter // 220))
    img.alpha_composite(edge, (x0, y0))

    seats = LAYOUTS[len(cast)]
    # The host goes on last, so their ring sits over any neighbor it touches.
    for i in list(range(1, len(cast))) + [0]:
        fx, fy, fd = seats[i]
        d = int(diameter * fd)
        bx, by = int(x0 + fx * diameter - d / 2), int(y0 + fy * diameter - d / 2)
        _soft_shadow(img, (bx, by + d * 0.05, bx + d, by + d * 1.05), 90, d * 0.05)
        img.alpha_composite(_bubble(cast[i], d, ringed=(i == 0)), (bx, by))
    return cast


# ---- the show cover ----------------------------------------------------------------------------

def make_cover(out: Optional[Path] = None, max_kb: int = 560) -> Path:
    """3000x3000: the show name, its "read by Deepgram Flux" subtitle with the wordmark, and the
    bench in one bubble, over felt.

    It has to survive 55px in a subscription list, so it carries two words of type at size and one
    object; the description belongs in the feed. Saved as JPEG (config.COVER_FILE) and stepped
    down in quality until it is under `max_kb`: the felt's grain is expensive, so it is softened
    first, and some directories time out on a multi-megabyte cover.
    """
    from PIL import ImageDraw, ImageFilter

    out = Path(out) if out else config.EPISODES_DIR / config.COVER_FILE
    size = 3000
    img = felt(size, 3, "a").filter(ImageFilter.GaussianBlur(6))
    scrim(img, top=0.62, bottom=0.45, left=0.25)
    d = ImageDraw.Draw(img)
    margin = 230

    d.text((margin, 260), "TWICE DAILY  ·  MORNING AND AFTERNOON", font=font("mono", 72, 500),
           fill=LIGHT_GRAY, anchor="lt")

    # The name on two lines, so each word stays big enough to read at 55px in a subscription list.
    big = fit("Hacker News", "sans", 470, 600, size - 2 * margin)
    for i, word in enumerate(("Hacker News", "Radio")):
        d.text((margin - big.size * 0.04, 400 + i * big.size), word, font=big, fill=OFF_WHITE, anchor="lt")

    # The subtitle, "read by Deepgram Flux", with Deepgram as the wordmark rather than typed out.
    sub = font("sans", 130, 500)
    base = 400 + 2 * big.size + 230
    x = margin
    d.text((x, base), "read by", font=sub, fill=LIGHT_GRAY, anchor="ls")
    x += sub.getlength("read by ")
    mark = logo(126)
    if mark is not None:
        # The wordmark's box runs from cap height to the bottom of the p and g; sit its baseline,
        # about 78% of the way down, on the text's.
        img.alpha_composite(mark, (int(x), int(base - mark.height * 0.78)))
        x += mark.width + sub.getlength(" ")
    else:
        d.text((x, base), "Deepgram", font=sub, fill=OFF_WHITE, anchor="ls")
        x += sub.getlength("Deepgram ")
    d.text((x, base), "Flux", font=sub, fill=OFF_WHITE, anchor="ls")

    cluster(img, size - margin - 640, size - margin - 600, 1280, COVER_CAST, max_seats=6)

    d.text((margin, size - margin - 100), "THE HACKER NEWS", font=font("mono", 70, 500),
           fill=LIGHT_GRAY, anchor="ls")
    d.text((margin, size - margin), "FRONT PAGE, ALOUD", font=font("mono", 70, 500),
           fill=OFF_WHITE, anchor="ls")

    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = img.convert("RGB")
    for q in (86, 80, 74, 68, 62):
        rgb.save(out, "JPEG", quality=q, optimize=True, progressive=True)
        if out.stat().st_size <= max_kb * 1024:
            break
    return out


# ---- per-episode art ---------------------------------------------------------------------------

def _day_and_slot(ep_id: str):
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:-(am|pm))?", str(ep_id))
    if not m:
        return datetime.now(timezone.utc).date(), None
    try:
        return date.fromisoformat(m.group(1)), m.group(2)
    except ValueError:
        return datetime.now(timezone.utc).date(), m.group(2)


# Chapters that recur in every episode and name no story, so they say nothing on the art.
GENERIC_CHAPTERS = {"intro", "outro", "wrap-up", "wrap up", "from the threads", "cold open", "sign-off"}


def _stories(ep_dir: Path, episode: dict) -> List[str]:
    """The stories the episode covered, as titles. Chapters first (what aired, in order), then
    `source_items` (what was picked) for an episode with no chapter file."""
    titles: List[str] = []
    try:
        chapters = json.loads((ep_dir / "chapters.json").read_text(encoding="utf-8"))["chapters"]
        for ch in chapters:
            t = str(ch.get("title", "")).strip() if isinstance(ch, dict) else ""
            if t and t.lower() not in GENERIC_CHAPTERS and t not in titles:
                titles.append(t)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if titles:
        return titles
    items = episode.get("source_items") or []
    if isinstance(items, str):
        return []   # an old episode stored the list as its repr; there is nothing safe to read
    for it in items:
        t = str(it.get("title", "")).strip() if isinstance(it, dict) else ""
        if t and t not in titles:
            titles.append(t)
    return titles


def make_episode_art(ep_dir: Path) -> Path:
    """Write <ep_dir>/art.jpg, 1400x1400: the edition and date over the day's felt, the stories
    in Azeret Mono, and the episode's cast as a group bubble."""
    from PIL import ImageDraw

    ep_dir = Path(ep_dir)
    episode = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    segments = episode.get("segments")
    if not segments:
        try:
            segments = json.loads((ep_dir / "script.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            segments = []
    day, slot = _day_and_slot(episode.get("id") or ep_dir.name)

    size = 1400
    img = felt(size, colorway(day), "d")
    scrim(img, top=0.62, bottom=0.58, left=0.32)
    d = ImageDraw.Draw(img)
    margin = 104

    d.text((margin, 96), "HACKER NEWS RADIO", font=font("mono", 34, 500), fill=OFF_WHITE, anchor="lt")
    mark = logo(30)
    if mark is not None:
        img.alpha_composite(mark, (size - margin - mark.width, 96))

    label = EDITION_LABEL.get(slot or "", "")
    y = 176
    if label:
        d.text((margin, y), label.upper(), font=font("mono", 40, 500), fill=LIGHT_GRAY, anchor="lt")
        y += 72
    when = f"{day:%B} {day.day}"
    fdate = fit(when, "sans", 180, 600, size - 2 * margin)
    d.text((margin - fdate.size * 0.05, y), when, font=fdate, fill=OFF_WHITE, anchor="lt")
    y = d.textbbox((margin, y), when, font=fdate, anchor="lt")[3] + 26
    d.text((margin, y), str(day.year), font=font("mono", 52, 400), fill=OFF_WHITE, anchor="lt")

    bubble_d = 600
    cluster(img, size - margin - bubble_d / 2 + 30, size - margin - bubble_d / 2 + 10, bubble_d,
            episode_cast(segments))

    # The stories, bottom left, in the column the bubble leaves free.
    stories = _stories(ep_dir, episode)[:3]
    sf = font("mono", 32, 500)
    col_w = size - 2 * margin - bubble_d + 10
    lines: List[tuple] = []
    for t in stories:
        for j, ln in enumerate(wrap(t, sf, col_w - sf.getlength("+ "), 2)):
            lines.append((j == 0, ln))
    pitch = 50
    y = size - margin - pitch * (len(lines) - 1)
    for first, ln in lines:
        x = margin
        if first:
            d.text((x, y), "+", font=sf, fill=FUCHSIA, anchor="ls")
        d.text((x + sf.getlength("+ "), y), ln, font=sf, fill=OFF_WHITE, anchor="ls")
        y += pitch

    out = ep_dir / ART_FILE
    tmp = out.with_suffix(".jpg.partial")
    img.convert("RGB").save(tmp, "JPEG", quality=80, optimize=True, progressive=True)
    tmp.replace(out)
    return out


def art_url(episodes_dir: Path, ep_id: str) -> Optional[str]:
    """The per-episode artwork URL with a content hash, or None when there is no art.jpg."""
    import hashlib

    f = Path(episodes_dir) / ep_id / ART_FILE
    if not f.is_file():
        return None
    return f"{config.site_base_url()}/{ep_id}/{ART_FILE}?v={hashlib.sha256(f.read_bytes()).hexdigest()[:8]}"


def try_make_episode_art(ep_dir: Path, log=print) -> Optional[Path]:
    """make_episode_art, but a failure is one log line and None. Art never takes a publish down."""
    try:
        return make_episode_art(ep_dir)
    except Exception as e:   # noqa: BLE001 -- decoration must not fail the show
        log(f"[art] no art for {Path(ep_dir).name}: {type(e).__name__}: {e}")
        return None


# Files whose change can change the picture: the cast comes from the segments, the stories from
# the chapters or the picked items.
_ART_INPUTS = ("episode.json", "script.json", "chapters.json")


def _art_is_current(ep_dir: Path) -> bool:
    out = ep_dir / ART_FILE
    if not out.is_file():
        return False
    drawn = out.stat().st_mtime
    return all(not (ep_dir / n).is_file() or (ep_dir / n).stat().st_mtime <= drawn for n in _ART_INPUTS)


def backfill(episodes_dir: Path, force: bool = False, log=None) -> dict:
    """Draw art for every episode on disk that has none, or whose art is older than its inputs.

    Called from `publish.rebuild_site`, next to `cards.backfill` and for the same reason: the
    archive that aired before the rebrand gets art the first time the new code boots, with no
    migration to remember. About 0.15 seconds an episode, so a cold boot over the whole archive
    adds roughly half a minute once, and milliseconds after that.

    Images only: no network, no Deepgram key, no audio touched. Nothing here raises; a failure is
    one log line per episode, because this runs inside the app's startup hook.
    """
    log = log or (lambda *a: None)
    written, skipped, failed = [], [], []
    for episode_json in sorted(Path(episodes_dir).glob("*/episode.json")):
        ep_dir = episode_json.parent
        if not force and _art_is_current(ep_dir):
            skipped.append(ep_dir.name)
            continue
        if try_make_episode_art(ep_dir, log=log) is None:
            failed.append(ep_dir.name)
        else:
            written.append(ep_dir.name)
    if written or failed:
        log(f"[art] drew {len(written)}, {len(failed)} failed, {len(skipped)} already current")
    return {"written": written, "skipped": skipped, "failed": failed}
