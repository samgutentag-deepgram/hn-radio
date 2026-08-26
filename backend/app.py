"""HN Radio backend (v3): one small FastAPI app that serves the static site AND holds the
Deepgram key server-side for one-click recast / generate. Follows the fastapi-flux starter shape.

It serves:
  /                -> web/ (the vanilla app: index.html, episode.html, app.js, brand.css)
  /episodes/...    -> the generated data + audio + samples (episode.json, script.json, *.wav, feed.xml)
  POST /api/recast -> reload an episode's script, remap voices, re-render (calls Flux batch)
  GET  /api/health

Run:  uvicorn backend.app:app --reload --port 8000   (see DEPLOY.md)
The Deepgram key is read from the environment / .env by hn_radio.config; it never reaches the browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hn_radio import config, publish
from .limits import render_slot
from hn_radio import recast as recast_mod

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


class RecastReq(BaseModel):
    episode_id: str
    mapping: dict  # {role: voice_id}, roles being `anchor` (Showrunner) and `cohost` (Guest host)


@app.post("/api/recast", dependencies=[Depends(render_slot)])
def api_recast(req: RecastReq):
    # Validated here and NOT trusted from the browser. The page disables a voice already taken by
    # the other role and offers Flux only, but the endpoint holds the Deepgram key and anyone can
    # post to it with curl, so every rule the picker shows has to be true on this side as well.
    # `recast_mod.validate_mapping` is the single definition of those rules; this call is stricter
    # than the CLI's on purpose, accepting only the two roles the show actually has.
    try:
        mapping = recast_mod.validate_mapping(req.mapping)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        episode = recast_mod.recast(req.episode_id, mapping, log=lambda *a: None)
    except FileNotFoundError:
        raise HTTPException(404, f"episode {req.episode_id!r} not found")
    except ValueError as e:  # a mapping the episode's own script cannot satisfy
        raise HTTPException(400, str(e))
    except Exception as e:  # Flux/render error, etc.
        raise HTTPException(500, str(e))
    _rebuild_static()
    return {"id": episode.id, "audio_url": f"/episodes/{episode.id}/episode.wav"}


# `POST /api/generate` was deleted 2026-08-22. Zero callers anywhere: no frontend fetch, no test,
# no script, no documented curl. It was also the only render route with no test of its own, and
# the same job is reachable two wired ways already, `make episode` and `scripts/daily.py`.
#
# Worth knowing if it ever comes back: it used `PanelWriter`, so an operator hitting it got the
# DETERMINISTIC script, not the Claude one the nightly cron produces. An ops hook that renders a
# different show from the one that airs is a trap, not a convenience.


@app.get("/api/health")
def health():
    """Liveness. KEPT on 2026-08-22, and deliberately NOT wired into fly.toml.

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
    from datetime import datetime, timedelta
    from hn_radio import status as status_mod

    st = status_mod.read()
    # Derived, not stored: a stall is an inference from silence, so it has to be computed when the
    # record is READ. Writing it into status.json would freeze a judgement about elapsed time at the
    # moment of the last write, which is exactly the moment a hung run stopped being able to write.
    st["stalled"] = status_mod.is_stalled(st)
    quiet = status_mod.silent_for(st)
    st["silent_seconds"] = None if quiet is None else int(quiet)
    st["stall_after_seconds"] = status_mod.stall_seconds()
    now = datetime.now(config.PACIFIC)
    nxt = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    st["next_run"] = nxt.isoformat()
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

    NO `desks` KEY as of 2026-08-22. It published `custom.ROUTABLE_DESKS` and `loadPool` never
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


# Static mounts LAST so they don't shadow the /api routes above.
app.mount("/episodes", RevalidatingStatic(directory=str(config.EPISODES_DIR)), name="episodes")
app.mount("/", RevalidatingStatic(directory=str(WEB), html=True), name="web")
