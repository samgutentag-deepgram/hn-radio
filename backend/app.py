"""HN Radio backend (v3): one small FastAPI app that serves the static site AND holds the
Deepgram key server-side for one-click recast / generate. Follows the fastapi-flux starter shape.

It serves:
  /                -> web/ (the vanilla app: index.html, episode.html, app.js, brand.css)
  /e/<id>          -> episode.html with this episode's OG tags in the head (the share-card link)
  /episodes/...    -> the generated data + audio + samples (episode.json, script.json, *.wav, feed.xml)
  POST /api/recast -> reload an episode's script, remap voices, re-render (calls Flux batch)
  GET  /api/health

Run:  uvicorn backend.app:app --reload --port 8000   (see DEPLOY.md)
The Deepgram key is read from the environment / .env by hn_radio.config; it never reaches the browser.
"""

from __future__ import annotations

import html
import json as _json
import re
from pathlib import Path
from typing import Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hn_radio import cards, config, publish
from .limits import play_beacon, render_slot

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

app = FastAPI(title="HN Radio")


def _rebuild_static() -> None:
    """Regenerate the JSON API + feed + landing so the freshly rendered episode shows up."""
    config.EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    publish.rebuild_site(config.EPISODES_DIR)


@app.on_event("startup")
def _startup() -> None:
    _rebuild_static()


# `POST /api/recast` was deleted 2026-09-16, with the picker that was its only caller. Sam:
# "remove all of the recast your own episode features." What it did: took `{episode_id, mapping}`,
# validated the mapping against `recast.validate_mapping` (server-side, because the endpoint held
# the Deepgram key and anyone could curl it), re-rendered the episode into `<id>-recast` and
# handed back the audio url.
#
# TWO THINGS SURVIVE IT AND ARE UNREACHABLE FROM THE SITE ON PURPOSE. `hn_radio/recast.py` and
# `pipeline.render_recast` are still here, and so is `scripts/recast_archive.py`; they are
# operator tooling now, run by hand, with no HTTP route and no page. That is a deliberate
# half-measure rather than an oversight: deleting a render path that has never misbehaved is a
# separate decision from taking a feature off the website, and the archive scripts are the only
# way to re-voice an old episode at all.
#
# `models.is_recast` and the `-recast` filtering in `feed.py` and `manifest.py` also stay. There
# may be `<id>-recast` directories on the volume from when the feature was live, and they must
# keep being excluded from the feed and the manifest whether or not anything can create new ones.

# `POST /api/generate` was deleted. Zero callers anywhere: no frontend fetch, no test,
# no script, no documented curl. It was also the only render route with no test of its own, and
# the same job is reachable two wired ways already, `make episode` and `scripts/daily.py`.
#
# Worth knowing if it ever comes back: it used `PanelWriter`, so an operator hitting it got the
# DETERMINISTIC script, not the Claude one the nightly cron produces. An ops hook that renders a
# different show from the one that airs is a trap, not a convenience.


@app.get("/api/health")
def health():
    """Liveness. KEPT, and deliberately NOT wired into fly.toml.

    Three lines, documented in the README, and conventional on a public API. Nothing in fly.toml,
    the Dockerfile, the entrypoint, crontab or the workflow probes it, but absence of a repo-side
    prober is not proof nothing does.

    A fly.toml http_check was considered and rejected, on two grounds:

      - It would be green through exactly the incident it looks like it covers. Every handler in
        this module is a sync `def`, so FastAPI dispatches them to the threadpool; during a
        deliberately wedged render this route still answered in 1-2 ms. Process death is already
        covered by uvicorn-as-PID-1 plus Fly's own restart.
      - It adds a restart trigger, and `auto_stop_machines = "off"` exists precisely to stop the
        machine being interrupted so the 3am cron always runs.

    The real monitoring gap is operational rather than a missing route:
    `fly secrets set HN_RADIO_ALERT_WEBHOOK=...`, which `hn_radio/alerts.py` and
    `scripts/daily.py:47,67` already implement and nothing has configured.
    """
    return {"ok": True}


@app.get("/api/status")
def api_status():
    """Generation status for the feed-page status board: current state + next scheduled run."""
    from datetime import datetime
    from hn_radio import status as status_mod, window

    st = status_mod.read()
    # Derived, not stored: a stall is an inference from silence, so it has to be computed when the
    # record is READ. Writing it into status.json would freeze a judgement about elapsed time at the
    # moment of the last write, which is exactly the moment a hung run stopped being able to write.
    st["stalled"] = status_mod.is_stalled(st)
    quiet = status_mod.silent_for(st)
    st["silent_seconds"] = None if quiet is None else int(quiet)
    st["stall_after_seconds"] = status_mod.stall_seconds()
    now = datetime.now(config.PACIFIC)
    # ONE TIMER PER RUN, because the page shows one per column. This used to be a single hardcoded
    # `hour=3`, which was right until the cron went to `0 3,15` on 2026-09-04 and then counted to
    # the wrong run for twelve days while the page said "daily run 3am Pacific". The hours now come
    # from `window.RUN_HOURS`, next to the function that decides which slot a clock time is in.
    runs = window.next_runs(now)
    st["next_runs"] = {slot: at.isoformat() for slot, at in runs.items()}
    # The sooner of the two, kept because the board's polling cadence latches onto "a run is about
    # to start" and does not care which one it is.
    st["next_run"] = min(runs.values()).isoformat()
    st["now"] = now.isoformat()
    return st


@app.get("/api/trending")
def api_trending():
    """Live HN front page for the feed page's idle state.

    Always 200, with an empty story list on failure, so the status board has exactly one code
    path to render instead of an error branch.
    """
    from hn_radio import trending

    return trending.snapshot()


class PlayReq(BaseModel):
    """One thing that happened on the site. `pct` is required for `progress` and ignored otherwise.

    `Optional[int]` rather than `int | None`, matching BuildReq below: the container runs 3.12 but
    a local .venv built from Xcode's python3 is 3.9, where the union syntax fails at import.
    """
    episode_id: str
    event: str
    pct: Optional[int] = None


@app.post("/api/plays", status_code=202, dependencies=[Depends(play_beacon)])
def api_plays(req: PlayReq):
    """Record a play-counter event. Returns 202 and nothing useful, on purpose.

    This is the first route here that a browser calls without anyone clicking, and the first whose
    job is to write caller-supplied data to the volume, so both halves of its behaviour are odd
    compared to the rest of the file and both are deliberate:

    **It validates hard, then it swallows.** The 400s below are not there to tell a browser it got
    something wrong -- `navigator.sendBeacon` cannot read a response, so nobody is listening. They
    are there because an unvalidated `episode_id` makes this an append-anything primitive on our
    volume, and a permanent one: a junk id is a permanent key in the rollup. Past that gate,
    `plays.record` cannot raise, so a read-only volume or a full disk is still a 202. A listener
    pressing play must never see an error from a counter.

    **It counts the site, not the world.** Podcast clients pulling the MP3 out of `feed.xml` are
    invisible to this and always will be. See the module docstring in `hn_radio/plays.py`.
    """
    from hn_radio import plays

    if req.event not in plays.EVENTS:
        raise HTTPException(400, f"unknown event {req.event!r}")
    if req.event == "progress" and req.pct not in plays.MILESTONES:
        raise HTTPException(400, f"progress needs one of {list(plays.MILESTONES)}")
    if not plays.known_episode(req.episode_id):
        raise HTTPException(400, f"episode {req.episode_id!r} not found")
    plays.record(req.episode_id, req.event, req.pct)
    return {"ok": True}


@app.get("/api/stats")
def api_stats():
    """The play rollup: a row per episode, grand totals, and a per-day series.

    Live rather than baked into `index.json` by `_rebuild_static()`. A published file would only be
    as fresh as the last rebuild, and rebuilds happen on deploy, on the nightly run and after a
    render -- so a count could sit hours behind the play that produced it, on the one page whose
    entire job is to show that number.

    Rows for episodes whose files have since been deleted are KEPT, titled by their id. Dropping
    them would make the totals stop matching the sum of the rows, and a stats page that cannot add
    up is not worth having.
    """
    import json as _json

    from hn_radio import plays

    rolled = plays.counts()
    rows = []
    for ep_id, counters in rolled["episodes"].items():
        title = ep_id
        meta = config.EPISODES_DIR / ep_id / "episode.json"
        try:
            title = (_json.loads(meta.read_text()).get("title") or ep_id)
        except Exception:
            pass  # deleted, unreadable, or written by an older schema: the id is a fine label
        rows.append({"id": ep_id, "title": title, **counters})
    rows.sort(key=lambda r: r["id"], reverse=True)
    return {"episodes": rows, "totals": rolled["totals"], "daily": rolled["daily"]}


class BuildReq(BaseModel):
    """A custom-episode config. `desks` maps a desk role to the voice the listener picked.

    Typing note: `Optional[str]` rather than `str | None`. The container runs Python 3.12 but a
    local .venv built from Xcode's python3 is 3.9, where the union syntax fails at import.
    """
    anchor: Optional[str] = None
    desks: Dict[str, str] = {}
    drama: Optional[str] = None
    days: int = 3


@app.get("/api/build/pool")
def api_build_pool(days: int = 3):
    """Candidate stories for the picker: rendered episodes plus today's live front page.

    Each row carries its routed desk and whether it is `episode` (cheap to reuse) or `live`
    (needs writing and rendering), so the page can show what a desk toggle would actually pull in.

    NO `desks` KEY. It used to publish `custom.ROUTABLE_DESKS`, but `loadPool` never
    read it: the picker's desk rows are written by hand at `build.html:317`. Driving the rows off
    the response was considered and rejected -- `Object.keys(rows)` includes the `always: true`
    anchor row whose `box` is null, which is an instant TypeError that kills every plan and build,
    and it would post `drama` as a desk, which `custom.py:86-87` rejects with a 400.
    """
    from hn_radio import custom

    days = max(1, min(int(days), 7))
    live = custom.live_stories_cached()
    pool = custom.build_pool(days, live_stories=live)
    return {"days": days, "stories": pool, "live_available": bool(live),
            "max_stories": custom.MAX_STORIES}


@app.post("/api/build/plan")
def api_build_plan(req: BuildReq):
    """Price a build without rendering: how many segments reuse, re-render, or are new.

    The confirm dialog shows this, so a listener knows whether they are about to wait seconds or
    spend real TTS on today's stories.
    """
    from hn_radio import custom

    cfg = req.model_dump()
    try:
        valid = custom.validate(cfg)
        live = custom.live_stories_cached()
        chosen = custom.select(valid, custom.build_pool(valid["days"], live_stories=live))
        plan = custom.plan(valid, chosen)
    except custom.ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"config_id": custom.config_id(valid), **plan}


@app.post("/api/build", dependencies=[Depends(render_slot)])
def api_build(req: BuildReq):
    """Render a custom episode. Blocks for as long as the render takes, like /api/recast does."""
    from hn_radio import custom

    cfg = req.model_dump()
    try:
        episode = custom.build(cfg, live_stories=custom.live_stories_cached())
    except custom.ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:  # missing API key, or Deepgram refused
        raise HTTPException(status_code=502, detail=str(e))
    _rebuild_static()
    return {"id": episode.id, "url": f"/episode.html?id={episode.id}"}


class RevalidatingStatic(StaticFiles):
    """StaticFiles that tells browsers to revalidate instead of guessing.

    Starlette sends `etag` and `last-modified` but no `Cache-Control`. With no directive a browser
    falls back to *heuristic* freshness and may serve a cached copy without asking us at all. That
    is not theoretical: it shipped a stale `brand.css` to a real page after a deploy, so new HTML
    rendered against an old stylesheet and every rule was missing at once.

    `no-cache` does not mean "do not store". It means "store, but revalidate before reuse", so the
    existing `etag` turns the common case into a 304 with no body. Correctness for the price of a
    conditional request.

    Applied to the audio too, deliberately. `scripts/add_chapters.py` rewrites `episode.mp3` in
    place at a stable URL, which is exactly what happened when every episode was re-encoded from
    24 kHz to 44.1 kHz. A long max-age there would have pinned listeners to the unplayable copy.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


# --- the shareable episode link ------------------------------------------------------------

# `episode.html?id=<id>` is the real page and it stays the real page. This is a second door onto
# it that exists because of what goes on the share cards: a URL printed on an image is a URL
# somebody retypes off a phone, and `.../episode.html?id=2026-09-16-am` is four extra tokens of
# punctuation to get wrong. `hn_radio/cards` prints this form.
#
# It also fixes the thing a redirect could not. `web/` has no build step and no server rendering,
# so `episode.html` is one static file for every episode and its <head> cannot name any of them.
# Link unfurlers -- Slack, LinkedIn, X, iMessage -- read the <head> and do not run the JavaScript
# that fills the page in, so an episode shared anywhere produced a blank card. This route injects
# that episode's tags, including `og:image`, which is exactly the 1200x630 card the same commit
# generates. The episode page keeps its own JSON fetches; nothing else is server-rendered.
_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _meta(tag: str, key: str, value: str) -> str:
    return f'<meta {tag}="{html.escape(key)}" content="{html.escape(value or "")}">'


@app.get("/e/{episode_id}", response_class=HTMLResponse)
def episode_page(episode_id: str) -> HTMLResponse:
    """Serve `web/episode.html` for one episode with its own <head>.

    The id is pattern-checked BEFORE it is joined to a path. It arrives from a URL, it is used to
    open files, and `..%2f..%2fetc%2fpasswd` is the reason this is not a bare `/ `join: the check
    rejects a separator outright rather than trying to normalise one away.
    """
    if not _ID_OK.match(episode_id):
        raise HTTPException(status_code=404, detail="no such episode")
    episode_json = config.EPISODES_DIR / episode_id / "episode.json"
    if not episode_json.is_file():
        raise HTTPException(status_code=404, detail="no such episode")
    try:
        episode = _json.loads(episode_json.read_text())
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="no such episode")

    app_url = config.site_app_url()
    title = episode.get("title") or config.SITE_TITLE
    summary = (episode.get("summary") or config.SITE_DESCRIPTION).strip()
    page_url = f"{app_url}/e/{episode_id}"

    # The card and its alt text, when `hn_radio.cards` has built them. Absent is a normal state --
    # a machine with no fonts builds no cards -- and an `og:image` pointing at a 404 is worse than
    # no `og:image`, so the tags are added together or not at all.
    card = config.EPISODES_DIR / episode_id / cards.CARD_JSON
    og_image = []
    if card.is_file():
        try:
            doc = _json.loads(card.read_text())
            social = doc["cards"]["social"]
            src = f"{app_url}/episodes/{episode_id}/{social['file']}"
            og_image = [
                _meta("property", "og:image", src),
                _meta("property", "og:image:width", str(social["width"])),
                _meta("property", "og:image:height", str(social["height"])),
                _meta("property", "og:image:alt", doc.get("alt", "")),
                _meta("name", "twitter:card", "summary_large_image"),
            ]
        except (OSError, ValueError, KeyError):
            og_image = []

    head = "\n".join([
        # Every other asset in episode.html is referenced relatively, and this page is served one
        # path segment deep. One <base> is the whole fix; rewriting five href attributes at
        # request time would be five chances to miss one.
        '<base href="/">',
        _meta("name", "description", summary),
        _meta("property", "og:type", "article"),
        _meta("property", "og:site_name", config.SITE_TITLE),
        _meta("property", "og:title", title),
        _meta("property", "og:description", summary),
        _meta("property", "og:url", page_url),
        *og_image,
    ])

    page = (WEB / "episode.html").read_text()
    page = page.replace("<title>HN Radio: Episode</title>",
                        f"<title>{html.escape(title)}</title>\n{head}", 1)
    # Same revalidate-don't-cache posture as the static files: an episode's title and summary can
    # be rewritten by `scripts/retitle.py`, and a cached head would keep unfurling the old one.
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


# Static mounts LAST so they don't shadow the /api routes above.
app.mount("/episodes", RevalidatingStatic(directory=str(config.EPISODES_DIR)), name="episodes")
app.mount("/", RevalidatingStatic(directory=str(WEB), html=True), name="web")
