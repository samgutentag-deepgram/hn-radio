#!/usr/bin/env python3
"""Render the site's favicon: ONE orb, not the album art shrunk down.

WHY NOT THE COVER. `episodes/cover.png` is a 36-orb honeycomb with a wordmark knocked out across
the middle. That composition is tuned for 55px in a podcast subscription list and it barely
survives there. A browser tab is 16px. At 16px the honeycomb is four grey pixels of noise and the
wordmark is gone entirely, so downscaling the cover produces a favicon that is indistinguishable
from every other dark-square favicon in the tab strip.

So the favicon is a SINGLE orb, which is the one shape the whole project is built out of: it is
what `web/orb.js` paints beside every voice, it is the unit the cover is tiled from, and it is a
circle, which is the only silhouette that still reads as a deliberate object at 16px.

WHICH ORB. The green run (light #a1d7fd, mid #12b76a, deep #075433). Green is the show's one brand
signal, it is the color of the eyebrow on the cover, and it is uncommon in a tab strip full of blue
product marks. Read out of `web/brand.css` like everything else, so this cannot drift from the site.

GEOMETRY AND SHADES are imported from `make_cover`, not copied. The cover's docstring explains why
the shades run deep-to-light and why the layers composite additively; that reasoning applies here
unchanged and there is no reason for two files to hold it.

WHAT IT WRITES, all into `web/`, which `backend/app.py` mounts at `/`:

    favicon.ico          16 / 32 / 48, the one every browser asks for by name
    icon-192.png         Android home screen, and the generic large PNG link
    icon-512.png         source of truth, and what a PWA manifest would point at
    apple-touch-icon.png 180, on an opaque black ground because iOS composites a
                         transparent icon onto white and the orb would vanish into it

Run it with `uv run python scripts/make_favicon.py`. Pillow is in the `dev` group, same as the
cover, so this never ships in the container.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.make_cover import palettes, orb  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "web"

# Any voice in the green run. The palettes are shared across a run, so this picks a color, not a
# cast member, and the favicon does not imply that one host speaks for the show.
FAVICON_VOICE = "flux-brittany-en"

# Render big and downsample. The orb is three Gaussian blurs deep; rasterizing it straight at 16px
# gives a muddy disc, where LANCZOS down from 1024 keeps the highlight crisp.
MASTER = 1024

# The orb occupies this share of the icon box. The remainder is the halo, which is what keeps the
# mark from disappearing against a dark tab bar: the orb's own top half is near-black by design.
ORB_SHARE = 0.88


def master(voice: str) -> Image.Image:
    """One orb with its glow bled outward, RGBA, transparent beyond the halo."""
    light, mid, deep, glow_rgb = palettes()[voice]
    d = int(MASTER * ORB_SHARE)
    cx = cy = MASTER // 2

    # HALO. `glow()` in make_cover blends onto an opaque canvas, which is right for the cover and
    # wrong here, so this builds the same ellipse and derives alpha from its own brightness. The
    # result is a colored bloom that fades to fully transparent instead of to black, so the icon
    # sits on a light tab bar without a grey box around it.
    halo = Image.new("RGB", (MASTER, MASTER), (0, 0, 0))
    r = int(d * 0.60)
    ImageDraw.Draw(halo).ellipse(
        [cx - r, cy - r + int(d * 0.07), cx + r, cy + r + int(d * 0.07)], fill=glow_rgb)
    halo = halo.filter(ImageFilter.GaussianBlur(int(d * 0.17)))
    # Scale the halo's own luminance into alpha, capped well under opaque: a halo that reaches 255
    # reads as a second, larger disc and the silhouette stops being a sphere.
    alpha = halo.convert("L").point(lambda v: int(v * 0.85))
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    canvas.paste(halo.convert("RGBA"), (0, 0), alpha)

    o = orb(d, (light, mid, deep), glow_rgb)
    canvas.paste(o, (cx - d // 2, cy - d // 2), o)
    return canvas


def main() -> int:
    img = master(FAVICON_VOICE)
    WEB.mkdir(parents=True, exist_ok=True)
    written = []

    for size in (512, 192):
        p = WEB / f"icon-{size}.png"
        img.resize((size, size), Image.LANCZOS).save(p, optimize=True)
        written.append(p)

    # iOS clips its own rounded corners and does NOT honor transparency, so this one gets the
    # cover's ground painted in. Inset a little so the orb is not cropped by that corner radius.
    apple = Image.new("RGB", (180, 180), (0, 0, 0))
    inner = img.resize((156, 156), Image.LANCZOS)
    apple.paste(inner, (12, 12), inner)
    p = WEB / "apple-touch-icon.png"
    apple.save(p, optimize=True)
    written.append(p)

    # Pillow builds every listed size from the image it is handed, and its internal downscale is
    # not LANCZOS, so the 16px frame comes out soft. Resizing each frame here and passing the
    # sharpest one as the base is the difference between a legible mark and a smudge.
    frames = [img.resize((s, s), Image.LANCZOS) for s in (48, 32, 16)]
    p = WEB / "favicon.ico"
    frames[0].save(p, format="ICO", sizes=[(48, 48), (32, 32), (16, 16)],
                   append_images=frames[1:])
    written.append(p)

    for f in written:
        print(f"wrote {f.relative_to(WEB.parent)} ({f.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
