"""Render one short line in every catalog voice and build a page to audition them.

Every voice reads the SAME line, and the line is written to sound like a Hacker News comment
rather than a customer-service greeting. A voice the docs call "clear, professional, calm" can
read as a support line instead of a person posting at 2am, and that is only audible with real
words.

Built originally for one decision -- which voices belong in the comment-theater guest pool -- and
now serving a bigger one. Since 2026-08-20 the co-host is drawn from the WHOLE catalog with a
no-repeat window, so every voice on this page is a voice that will host a morning show sooner or
later. That makes auditioning the full catalog the point rather than a side effect, and it is how
a voice gets pulled by ear into `config.RETIRED_VOICES`.

Doubles as a catalog probe, which matters more than it sounds. `resolve_role` skips a voice that
is missing from the catalog, but the guest pool is read directly from `GUEST_VOICES`, so a dead id
in it is a hard render failure rather than a graceful substitution. Anything that renders a
preview here is safe to put in the pool.

Resumable, for the same reason scripts/local_episode.py is: a cold model can 500 on a raw
calls and this makes one call per voice, so a non-resumable version would rarely finish.

    uv run python scripts/voice_preview.py
    uv run python scripts/voice_preview.py --line "Some other sentence."
    uv run python scripts/voice_preview.py --fresh
"""

from __future__ import annotations

import argparse
import html
import pathlib
import sys
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import config, render  # noqa: E402
from hn_radio.cast import DEFAULT_CAST, ROLE_VOICES  # noqa: E402

# Opinionated on purpose. It has a first person, a specific number, a mild brag and a real object,
# which is what a forum comment has and a greeting does not.
DEFAULT_LINE = ("I ran this on a Raspberry Pi in my basement for six months "
                "and it never once fell over.")

OUT = config.EPISODES_DIR / "_voices"


def write_wav(pcm: bytes, path: pathlib.Path) -> float:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(config.CHANNELS)
        w.setsampwidth(config.SAMPLE_WIDTH)
        w.setframerate(config.SAMPLE_RATE)
        w.writeframes(pcm)
    return len(pcm) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="voice_preview")
    ap.add_argument("--line", default=DEFAULT_LINE, help="what every voice reads")
    ap.add_argument("--fresh", action="store_true", help="re-render voices already on disk")
    args = ap.parse_args(argv)

    # One call per voice across the whole catalog: wait out a transient failure rather than
    # restarting the pass.
    config.http_retries = lambda: 12

    OUT.mkdir(parents=True, exist_ok=True)
    key = config.get_api_key()

    # Which voices the show can SEAT. Post-2026-08-20 this is the host's preference chain plus
    # the default second chair; the rotating co-host can be anything in the catalog, so the "desk"
    # tag on the page now means "named in the cast tables", not "the only voices that ever air".
    desk_ids = {DEFAULT_CAST.anchor.voice_id, *(d.voice_id for d in DEFAULT_CAST.desks)}
    for prefs in ROLE_VOICES.values():
        desk_ids.update(p for p in prefs if p in config.VOICE_CATALOG)
    guest_ids = set(config.GUEST_VOICES)

    results = []
    for i, (vid, (name, note)) in enumerate(sorted(config.VOICE_CATALOG.items(),
                                                   key=lambda kv: kv[1][0]), 1):
        wav = OUT / f"{vid}.wav"
        if wav.exists() and not args.fresh:
            with wave.open(str(wav)) as w:
                secs = w.getnframes() / w.getframerate()
            results.append((vid, name, note, secs, None))
            print(f"  [{i:2}/{len(config.VOICE_CATALOG)}] cached  {name}")
            continue
        try:
            secs = write_wav(render.render_segment(args.line, vid, key), wav)
            results.append((vid, name, note, secs, None))
            print(f"  [{i:2}/{len(config.VOICE_CATALOG)}] ok      {name} ({secs:.1f}s)")
        except Exception as e:
            results.append((vid, name, note, None, str(e)[-90:]))
            print(f"  [{i:2}/{len(config.VOICE_CATALOG)}] FAILED  {name}")

    def group(vid):
        if vid in desk_ids:
            return "desk"
        if vid in guest_ids:
            return "guest"
        return "unused"

    (OUT / "index.html").write_text(build_page(results, group, args.line))
    ok = sum(1 for r in results if r[3] is not None)
    print(f"\n{ok}/{len(results)} rendered.  open {OUT / 'index.html'}")
    return 0 if ok == len(results) else 1


def build_page(results, group, line) -> str:
    order = {"guest": 0, "desk": 1, "unused": 2}
    titles = {
        "guest": ("In the guest pool now", "What a performed HN commenter can currently sound like. "
                                           "Every voice that is not holding a desk."),
        "desk": ("Cast to a desk", "Off limits for the guest pool: a performed comment has to sound "
                                   "like someone other than the correspondents."),
        "unused": ("Available", "Not cast anywhere. Candidates for widening the guest pool."),
    }
    rows = sorted(results, key=lambda r: (order[group(r[0])], r[1]))
    out = []
    current = None
    for vid, name, note, secs, err in rows:
        g = group(vid)
        if g != current:
            if current is not None:
                out.append("</div>")
            head, blurb = titles[g]
            n = sum(1 for r in rows if group(r[0]) == g)
            out.append(f'<h2>{html.escape(head)} <span class="n">{n}</span></h2>')
            out.append(f'<p class="blurb">{html.escape(blurb)}</p><div class="grid">')
            current = g
        accent = note.split(",")[0]
        chars = ",".join(note.split(",")[1:]).strip()
        if err:
            body = f'<p class="err">did not render: {html.escape(err)}</p>'
        else:
            body = (f'<audio controls preload="none" src="{html.escape(vid)}.wav"></audio>'
                    f'<p class="dur">{secs:.1f}s</p>')
        out.append(
            f'<div class="v"><h3>{html.escape(name)}</h3>'
            f'<p class="id">{html.escape(vid)}</p>'
            f'<p class="meta">{html.escape(accent)}</p>'
            f'<p class="chars">{html.escape(chars)}</p>{body}</div>')
    out.append("</div>")
    return PAGE.replace("{{BODY}}", "\n".join(out)).replace("{{LINE}}", html.escape(line))


PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HN Radio - voice auditions</title>
<style>
  :root { --bg:#fff; --fg:#16161a; --mut:#5b5b63; --line:#dededf; --card:#f7f7f8; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#111114; --fg:#eaeaee; --mut:#a0a0aa; --line:#2c2c33; --card:#191920; }
  }
  * { box-sizing:border-box; }
  body { font:15px/1.5 system-ui,-apple-system,sans-serif; max-width:1100px; margin:0 auto;
         padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg); }
  h1 { font-size:1.5rem; margin:0 0 .3rem; }
  .lede { color:var(--mut); margin:0 0 .4rem; }
  .line { background:var(--card); border:1px solid var(--line); border-radius:6px;
          padding:.6rem .8rem; margin:1rem 0 2rem; font-style:italic; }
  h2 { font-size:1.05rem; margin:2.2rem 0 .2rem; padding-bottom:.35rem;
       border-bottom:1px solid var(--line); }
  h2 .n { color:var(--mut); font-weight:400; font-size:.85rem; }
  .blurb { color:var(--mut); margin:.35rem 0 1rem; font-size:.9rem; max-width:60ch; }
  .grid { display:grid; gap:.8rem; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); }
  .v { border:1px solid var(--line); border-radius:8px; padding:.8rem; background:var(--card); }
  .v h3 { margin:0; font-size:1rem; }
  .id { font-family:ui-monospace,Menlo,monospace; font-size:.72rem; color:var(--mut); margin:.15rem 0 .5rem; }
  .meta { margin:0; font-size:.8rem; }
  .chars { margin:.1rem 0 .6rem; font-size:.8rem; color:var(--mut); }
  audio { width:100%; height:34px; }
  .dur { margin:.3rem 0 0; font-size:.72rem; color:var(--mut); }
  .err { color:var(--fg); font-size:.8rem; border-left:3px solid var(--mut); padding-left:.5rem; margin:.4rem 0 0; }
</style></head><body>
<h1>Voice auditions</h1>
<p class="lede">Every voice in the Flux GA catalog reading the same line, grouped by what it does in the show today.</p>
<div class="line">{{LINE}}</div>
{{BODY}}
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
