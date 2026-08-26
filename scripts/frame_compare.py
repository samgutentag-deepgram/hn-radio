#!/usr/bin/env python3
"""Build a side-by-side A/B page for a prompt change: what aired vs what the current prompt writes.

Same stories, same day, two scripts. The page mirrors the per-episode page in the web app on
purpose (same seek-button pattern, same active-line highlight, same accent), because that is the
surface Sam already knows how to read, and a comparison should not also be a new interface to
learn.

Judged by ear, so every line is playable: each row seeks its own episode's audio to that line's
`start_seconds`, and each column has a full-episode player at the top. The two players are
mutually exclusive, since the point is to compare one against the other rather than hear both.

Lint flags are rendered inline on the line that trips them, with the reason, so the verdict is
attached to the evidence instead of being a number at the top of the page.

Usage:
    python scripts/frame_compare.py 2026-08-07
"""

from __future__ import annotations

import html
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hn_radio import config  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "frame_lint", Path(__file__).resolve().parent / "frame_lint.py")
frame_lint = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frame_lint)

OUT_ROOT = config.EPISODES_DIR / "_frame"


def _fmt_time(sec: float) -> str:
    s = int(sec or 0)
    return f"{s // 60}:{s % 60:02d}"


def _load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"missing {path}")
    return json.loads(path.read_text())


def _render_side(ep: dict, audio_href: str, side: str) -> str:
    """One column: player, meta, then every line with its own seek button."""
    segs = ep.get("segments", [])
    flagged = 0
    rows = []
    for seg in segs:
        role = seg.get("role") or "desk"
        text = seg.get("text") or ""
        start = seg.get("start_seconds")
        hits = [] if role == "commenter" else frame_lint.check(text)
        if hits:
            flagged += 1

        who = html.escape(seg.get("speaker_key") or "")
        if role == "desk" and seg.get("desk"):
            who += f' <span class="desk">&middot; {html.escape(seg["desk"])} desk</span>'
        voice = html.escape(seg.get("voice_id") or "")

        ctrl = '<div class="ctrl"></div>'
        if start is not None:
            ts = _fmt_time(start)
            ctrl = (f'<div class="ctrl"><button class="play" data-seek="{start:.3f}" '
                    f'title="Play from {ts}" aria-label="Play from {ts}">&#9654;</button>'
                    f'<span class="ts">{ts}</span></div>')

        flag_html = ""
        if hits:
            items = "".join(
                f'<li><code>{html.escape(c)}:{html.escape(n)}</code> '
                f'&mdash; matched {html.escape(repr(m))}: {html.escape(w)}</li>'
                for c, n, m, w in hits)
            flag_html = f'<ul class="flags">{items}</ul>'

        attr = f' data-start="{start:.3f}"' if start is not None else ""
        rows.append(
            f'<div class="seg {role}{" flagged" if hits else ""}"{attr}>{ctrl}'
            f'<div class="body"><div class="who">{who} <span class="voice">{voice}</span></div>'
            f'<div class="text">{html.escape(text)}</div>{flag_html}</div></div>')

    verdict = (f'<span class="bad">{flagged} flagged line{"" if flagged == 1 else "s"}</span>'
               if flagged else '<span class="good">clean</span>')
    dur = _fmt_time(ep.get("duration_seconds") or 0)
    return f"""
<section class="col" id="col-{side}">
  <div class="colhead">
    <h2>{html.escape(ep.get("title") or side)}</h2>
    <p class="meta">{len(segs)} lines &middot; {dur} &middot; {verdict}</p>
    <audio id="player-{side}" controls preload="metadata" src="{html.escape(audio_href)}"></audio>
  </div>
  <div class="lines">{"".join(rows)}</div>
</section>"""


def build(episode_id: str) -> Path:
    aired = _load(config.EPISODES_DIR / episode_id / "episode.json")
    new = _load(OUT_ROOT / f"{episode_id}-new" / "episode.json")

    # Relative to episodes/_frame/index.html, so the page works over file:// with no server.
    left = _render_side(aired, f"../{episode_id}/episode.mp3", "aired")
    right = _render_side(new, f"{episode_id}-new/episode.mp3", "replay")

    aired_stories = ", ".join(html.escape(s["title"]) for s in aired.get("source_items", []))

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frame A/B &middot; {html.escape(episode_id)}</title>
<style>
  :root {{ color-scheme: light dark; --accent: #c9a227; --bad: #d1495b; --good: #2a9d5c; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0 auto; max-width: 88rem;
         padding: 0 1.2rem 4rem; line-height: 1.5; background: Canvas; color: CanvasText; }}
  header.top {{ padding: 1.4rem 0 1rem; border-bottom: 1px solid #8884; }}
  header.top h1 {{ font-size: 1.3rem; margin: 0 0 0.3rem; }}
  header.top p {{ margin: 0.3rem 0; color: #888; font-size: 0.85rem; max-width: 60rem; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.6rem; margin-top: 1.2rem; }}
  @media (max-width: 62rem) {{ .grid {{ grid-template-columns: 1fr; }} }}
  .col {{ min-width: 0; }}
  .colhead {{ position: sticky; top: 0; z-index: 5; background: Canvas; padding: 0.9rem 0 0.6rem;
         border-bottom: 1px solid #8884; }}
  .colhead h2 {{ font-size: 1rem; margin: 0 0 0.2rem; }}
  .meta {{ color: #888; font-size: 0.8rem; margin: 0; }}
  audio {{ width: 100%; margin: 0.6rem 0 0.2rem; }}
  .good {{ color: var(--good); font-weight: 600; }}
  .bad {{ color: var(--bad); font-weight: 600; }}
  .seg {{ display: flex; gap: 0.7rem; padding: 0.55rem 0.4rem; border-bottom: 1px solid #8882;
         border-radius: 6px; scroll-margin-top: 9rem; transition: background 0.15s ease; }}
  .ctrl {{ flex: 0 0 auto; display: flex; flex-direction: column; align-items: center;
          gap: 0.15rem; width: 2.1rem; }}
  button.play {{ width: 1.9rem; height: 1.9rem; border-radius: 999px; border: 1px solid #8886;
          background: transparent; color: inherit; cursor: pointer; font-size: 0.7rem;
          line-height: 1; display: flex; align-items: center; justify-content: center; }}
  button.play:hover {{ border-color: var(--accent); color: var(--accent); }}
  .ts {{ font-size: 0.66rem; color: #999; font-variant-numeric: tabular-nums; }}
  .body {{ flex: 1; min-width: 0; }}
  .who {{ font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.03em; color: #888; }}
  .who .desk {{ text-transform: none; }}
  .who .voice {{ text-transform: none; color: #aaa; font-size: 0.68rem; }}
  .text {{ font-size: 0.92rem; }}
  .seg.commenter .text {{ font-style: italic; }}
  .seg.active {{ background: rgba(201,162,39,0.14); }}
  .seg.active button.play {{ border-color: var(--accent); color: var(--accent); }}
  .seg.flagged {{ background: rgba(209,73,91,0.10); border-left: 3px solid var(--bad); }}
  .flags {{ margin: 0.4rem 0 0; padding-left: 1.1rem; font-size: 0.74rem; color: var(--bad); }}
  .flags code {{ font-size: 0.72rem; }}
</style>
</head>
<body>
<header class="top">
  <h1>Frame A/B &middot; {html.escape(episode_id)}</h1>
  <p><strong>Same three stories, same day, two prompts.</strong> Left is what aired. Right is the
  same stories re-written by the current prompt and re-rendered. Red rows are lines the frame lint
  flags: the show narrating its own sourcing, or assessing how much it had to go on.</p>
  <p>Stories: {aired_stories}</p>
  <p>The two players are mutually exclusive, so starting one stops the other. Every row seeks its
  own column's audio. Cast differs between the columns: the pre-GA voices the aired episode used
  are no longer in the published catalog, so the replay is cast from the GA catalog.</p>
</header>
<div class="grid">{left}{right}</div>
<script>
(function () {{
  function qsa(s, r) {{ return Array.prototype.slice.call((r || document).querySelectorAll(s)); }}
  var players = qsa('audio');
  players.forEach(function (p) {{
    // Comparing means hearing one at a time. Two episodes playing over each other is noise.
    p.addEventListener('play', function () {{
      players.forEach(function (o) {{ if (o !== p) o.pause(); }});
    }});
  }});
  qsa('.col').forEach(function (col) {{
    var player = col.querySelector('audio');
    var segs = qsa('.seg[data-start]', col).map(function (el) {{
      return {{ el: el, start: parseFloat(el.getAttribute('data-start')) }};
    }});
    qsa('button.play', col).forEach(function (b) {{
      b.addEventListener('click', function () {{
        player.currentTime = parseFloat(b.getAttribute('data-seek'));
        player.play();
      }});
    }});
    player.addEventListener('timeupdate', function () {{
      var t = player.currentTime, active = null;
      for (var i = 0; i < segs.length; i++) {{
        if (segs[i].start <= t + 0.02) active = segs[i]; else break;
      }}
      segs.forEach(function (s) {{ s.el.classList.toggle('active', s === active); }});
    }});
  }});
}})();
</script>
</body>
</html>
"""
    out = OUT_ROOT / "index.html"
    out.write_text(page)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path = build(sys.argv[1])
    print(f"wrote {path}")
