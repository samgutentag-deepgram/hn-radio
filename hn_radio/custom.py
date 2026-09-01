"""Custom episodes: a listener picks which desks appear and what voice each one uses.

Phase 1 produces one episode on demand, like recast does. The config dict is deliberately the
entire input, because a later phase persists it and points an RSS feed at it. Nothing in here
should need to change for that; only the caller does.

Selecting desks does double duty. A desk that is present in the config is cast AND included, so
the same choice that assigns Priya to the AI desk is also the topic filter that keeps AI stories
in and everything else out.

Three cost tiers when building, cheapest first:
  reuse     a past episode's segment whose words AND voice are both unchanged. Byte copy, free.
  rerender  a past episode's line in a newly chosen voice. Same words, new audio.
  new       today's stories, which have no script yet, plus the intro and sign-off.

The intro and sign-off are ALWAYS in the `new` tier, never reused. They name the day and the
stories the episode contains, so a cached pair lifted from a different episode would make the
audio lie about its own contents. Two short lines is a cheap price for that.

Reuse is keyed on (source episode, segment order) rather than order alone, because a custom
episode draws from several past episodes at once and orders collide across them. This is the one
place it differs from recast, which only ever reuses from a single original.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config, editions
from .cast import COHOST_ROLE, Cast, Desk
from .models import ScriptSegment

# Topics a listener can pick, which double as the desks a custom episode seats. "anchor" hosts
# every episode and "drama" performs comments rather than stories, so neither is selectable.
#
# Read from `editions.TOPICS` rather than written out here, because that module is now the one
# place the keyword tables live (they moved out of `cast.py` when the daily show went two-person;
# see `cast.py`). A second literal would be a second thing to keep in step.
#
# NOTE ON WHAT THESE MEAN NOW. In this module a desk is still a real seat with a voice, because
# picking one is how a listener casts it. In the DAILY show there are no desks at all, so a
# rendered episode's story lines are tagged `cohost` instead of `ai`/`maker`/`security`. That
# gap is handled in `_owning_desk` and `_desk_of_segment`, and it is the whole reason those two
# exist.
ROUTABLE_DESKS = tuple(editions.TOPICS)

# A custom episode is capped so that a wide date range filtered to one busy desk cannot produce a
# twenty-minute show. Ordered by points, so the cap keeps the biggest stories.
MAX_STORIES = 6

DEFAULT_DAYS = 3

# Episode ids we never treat as source material: recasts are derivatives, and customs would
# compound (a custom built from a custom, whose provenance no longer points at real segments).
_DERIVED = re.compile(r"-recast$|-custom-")

_ID_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class ConfigError(ValueError):
    """The submitted build config is not usable. Message is safe to show a user."""


def validate(cfg: dict) -> dict:
    """Normalize and check a build config. Raises ConfigError with a user-facing message.

    Returns a new dict with keys: anchor, desks, drama (may be None), days.
    """
    if not isinstance(cfg, dict):
        raise ConfigError("Config must be an object.")

    anchor = cfg.get("anchor") or config.host_voice()
    if anchor not in config.ALL_VOICES:
        raise ConfigError(f"Unknown anchor voice {anchor!r}.")

    raw_desks = cfg.get("desks") or {}
    if not isinstance(raw_desks, dict):
        raise ConfigError("`desks` must be an object mapping desk role to voice id.")
    desks: Dict[str, str] = {}
    for role, voice in raw_desks.items():
        if role not in ROUTABLE_DESKS:
            raise ConfigError(f"Unknown desk {role!r}. Choose from {list(ROUTABLE_DESKS)}.")
        if voice not in config.ALL_VOICES:
            raise ConfigError(f"Unknown voice {voice!r} for the {role} desk.")
        desks[role] = voice
    if not desks:
        raise ConfigError("Pick at least one desk, or the episode would have no stories.")

    drama = cfg.get("drama")
    if drama is not None and drama not in config.ALL_VOICES:
        raise ConfigError(f"Unknown comment-theater voice {drama!r}.")

    try:
        days = int(cfg.get("days", DEFAULT_DAYS))
    except (TypeError, ValueError):
        raise ConfigError("`days` must be a whole number.")
    if not 1 <= days <= 7:
        raise ConfigError("`days` must be between 1 and 7.")

    # Sorted desks so an equivalent config always hashes to the same id regardless of key order.
    return {"anchor": anchor, "desks": dict(sorted(desks.items())), "drama": drama, "days": days}


def config_id(cfg: dict) -> str:
    """Short stable id for a validated config. Same choices always give the same id.

    This is the seam for phase 2: a persisted config keyed by this id is what a custom RSS feed
    would point at.
    """
    canonical = json.dumps(validate(cfg), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _episode_date(episode_id: str) -> Optional[date]:
    m = _ID_DATE.match(episode_id)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _owning_desk(segments: List[dict], hn_id: int, title: str = "",
                 url: Optional[str] = None) -> Optional[str]:
    """Which topic desk this story belongs to, for the purposes of a custom episode.

    Two eras of script to read, and both have to keep working or the cache tier dies.

    OLD (before the two-person show): the story was covered by a themed desk and the script says which,
    so read it off the script rather than re-deriving it. That way a rebuilt episode reflects
    what was really said. The anchor also has lines tagged with a story's id (it throws to the
    desk), so anchor lines are skipped.

    NEW: the daily show is two people and tags story lines `cohost`, which names no subject at
    all. So a cohost-covered story is classified from its title and url instead, exactly the way
    `pool_from_live` classifies a story that has not been covered yet. Without this fallback,
    every episode rendered after the two-person change would contribute NOTHING to the pool:
    `select` reserves half the cap for already-rendered stories, so the picker would have quietly
    degraded to a full render every time while still looking like it was using the cache.
    """
    covered_by_cohost = False
    for seg in segments:
        if seg.get("source_hn_id") != hn_id:
            continue
        desk = seg.get("desk")
        if desk in ROUTABLE_DESKS:
            return desk
        if desk == COHOST_ROLE:
            covered_by_cohost = True
    if covered_by_cohost:
        return editions.topic_of_text(title, url)
    return None


def _desk_of_segment(seg: dict, story_desk: str) -> Optional[str]:
    """The topic desk a rendered segment belongs to, from the listener's point of view.

    A `cohost` line is that story's coverage whatever subject the story is, so it maps onto the
    desk the story was placed in. Without this, an episode rendered after the two-person change
    had its story lines match no picked desk, got dropped by `collect_segments`, and the story
    ended up listed in
    `source_items` and in the chapters while being absent from the audio -- the same
    everything-except-the-audio failure the story cap once caused.
    """
    desk = seg.get("desk")
    return story_desk if desk == COHOST_ROLE else desk


def pool_from_episodes(days: int = DEFAULT_DAYS, today: Optional[date] = None,
                       episodes_dir: Optional[Path] = None) -> List[dict]:
    """Candidate stories drawn from already-rendered episodes within the last `days` days.

    These are the cheap ones: their script and their per-segment PCM are both already on disk.
    """
    episodes_dir = episodes_dir or config.EPISODES_DIR
    today = today or date.today()
    earliest = today - timedelta(days=days - 1)

    out: List[dict] = []
    seen = set()
    for ep_json in sorted(episodes_dir.glob("*/episode.json"), reverse=True):
        ep_id = ep_json.parent.name
        if _DERIVED.search(ep_id):
            continue
        ep_date = _episode_date(ep_id)
        if ep_date is None or not (earliest <= ep_date <= today):
            continue
        script_path = ep_json.parent / "script.json"
        if not script_path.exists():
            continue
        try:
            meta = json.loads(ep_json.read_text())
            segments = json.loads(script_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for item in meta.get("source_items", []):
            hn_id = item.get("hn_id")
            if hn_id is None or hn_id in seen:
                continue
            desk = _owning_desk(segments, hn_id, item.get("title", ""), item.get("url"))
            if desk is None:
                continue  # nothing covered it as a story, so no desk selection can include it
            seen.add(hn_id)
            out.append({
                "hn_id": hn_id,
                "title": item.get("title", ""),
                "url": item.get("url"),
                "points": item.get("points", 0),
                "desk": desk,
                "day": ep_date.isoformat(),
                "source": "episode",
                "episode_id": ep_id,
            })
    return out


def pool_from_live(stories, today: Optional[date] = None) -> List[dict]:
    """Candidate stories from today's live front page, routed to a desk by the cast heuristic.

    These are the expensive ones: nothing is written or rendered for them yet.
    """
    today = today or date.today()
    out = []
    for s in stories:
        # `editions.topic_of`, not a cast: the daily show routes nothing to a speaker any more,
        # and this was never asking about speakers. It asks which subject a story is about, which
        # is the question the listener's desk picks are answering.
        desk = editions.topic_of(s)
        if desk not in ROUTABLE_DESKS:
            continue
        out.append({
            "hn_id": s.id,
            "title": s.title,
            "url": s.url,
            "points": s.points,
            "desk": desk,
            "day": today.isoformat(),
            "source": "live",
            "episode_id": None,
        })
    return out


def build_pool(days: int = DEFAULT_DAYS, live_stories=None, today: Optional[date] = None,
               episodes_dir: Optional[Path] = None) -> List[dict]:
    """The full candidate pool: rendered episodes plus today's live front page.

    A story already covered by a rendered episode wins over its live duplicate, because the
    rendered one is free to reuse.
    """
    pool = pool_from_episodes(days, today=today, episodes_dir=episodes_dir)
    known = {p["hn_id"] for p in pool}
    for p in pool_from_live(live_stories or [], today=today):
        if p["hn_id"] not in known:
            pool.append(p)
    return pool


def select(cfg: dict, pool: List[dict], cap: int = MAX_STORIES) -> List[dict]:
    """Keep only stories whose desk the listener picked, best-first, capped.

    The cap is filled from BOTH sources rather than by a single global ranking, because a plain
    points sort hands the whole episode to today's live stories: episodes only started recording
    `points` in source_items recently, so older cached stories score 0 and always lose. That made
    every build a full render and wasted the segment cache entirely.

    So at least half the cap is reserved for already-rendered stories, which are free to reuse,
    and the rest goes to today. Whichever side has room absorbs the other's leftovers, so a day
    with no live stories still fills up from cache and vice versa.
    """
    cfg = validate(cfg)
    wanted = set(cfg["desks"])
    matched = [p for p in pool if p["desk"] in wanted]

    def rank(items):
        return sorted(items, key=lambda p: (p.get("day") or "", p.get("points") or 0), reverse=True)

    cached = rank([p for p in matched if p["source"] == "episode"])
    live = rank([p for p in matched if p["source"] == "live"])

    keep_cached = min(len(cached), max(1, cap - cap // 2))
    keep_live = min(len(live), cap - keep_cached)
    keep_cached = min(len(cached), cap - keep_live)   # absorb whatever live could not fill

    chosen = cached[:keep_cached] + live[:keep_live]
    return rank(chosen)


def _load_script(episode_id: str, episodes_dir: Path) -> List[dict]:
    path = episodes_dir / episode_id / "script.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def plan(cfg: dict, chosen: List[dict], episodes_dir: Optional[Path] = None) -> dict:
    """Work out what building this episode would cost, without rendering anything.

    Returns {"reuse": n, "rerender": n, "new": n, "stories": [...]}, where `new` already counts
    the intro and sign-off, which are never reused.
    """
    cfg = validate(cfg)
    episodes_dir = episodes_dir or config.EPISODES_DIR
    voice_for = dict(cfg["desks"])
    voice_for["anchor"] = cfg["anchor"]
    if cfg["drama"]:
        voice_for["drama"] = cfg["drama"]

    reuse = rerender = new = 0
    scripts: Dict[str, List[dict]] = {}
    for story in chosen:
        if story["source"] == "live":
            new += 1  # needs a script written before it can even be rendered
            continue
        ep_id = story["episode_id"]
        if ep_id not in scripts:
            scripts[ep_id] = _load_script(ep_id, episodes_dir)
        for seg in scripts[ep_id]:
            if seg.get("source_hn_id") != story["hn_id"]:
                continue
            desk = _desk_of_segment(seg, story["desk"])
            if desk == "anchor":
                continue  # the anchor's hand-off lines are rewritten for the new running order
            want = voice_for.get(desk)
            if want is None:
                continue  # this desk is not in the config, so the line is dropped
            if (episodes_dir / ep_id / "segments" / f"{seg['order']}.pcm").exists() \
                    and seg.get("voice_id") == want:
                reuse += 1
            else:
                rerender += 1

    new += 2  # intro and sign-off, always fresh so they describe this episode honestly
    return {"reuse": reuse, "rerender": rerender, "new": new, "stories": chosen}


def collect_segments(cfg: dict, chosen: List[dict], episodes_dir: Optional[Path] = None
                     ) -> Tuple[List[ScriptSegment], Dict[int, Tuple[str, int]], Dict[str, str]]:
    """Gather the reusable story segments for a custom episode, in running order.

    Returns three things:
      segments    renumbered from 1, leaving 0 for the intro
      provenance  new order -> (source episode id, original order), which is what lets the
                  renderer reuse cached PCM across several source episodes instead of just one
      orig_voice  desk role -> the voice it had in the source, needed to rewrite correspondent
                  names when the listener picks a different voice for that desk
    """
    cfg = validate(cfg)
    episodes_dir = episodes_dir or config.EPISODES_DIR
    voice_for = dict(cfg["desks"])
    if cfg["drama"]:
        voice_for["drama"] = cfg["drama"]

    segments: List[ScriptSegment] = []
    provenance: Dict[int, Tuple[str, int]] = {}
    orig_voice: Dict[str, str] = {}
    scripts: Dict[str, List[dict]] = {}
    order = 1
    for story in chosen:
        if story["source"] == "live":
            continue  # written separately; it has no existing script to collect
        ep_id = story["episode_id"]
        if ep_id not in scripts:
            scripts[ep_id] = _load_script(ep_id, episodes_dir)
        for seg in scripts[ep_id]:
            if seg.get("source_hn_id") != story["hn_id"]:
                continue
            desk = _desk_of_segment(seg, story["desk"])
            if desk == "anchor" or desk not in voice_for:
                continue
            src_order = seg["order"]
            # Remember the source voice before it is overridden, so a name rewrite can find the
            # old correspondent's name if the listener picked someone else for this desk.
            if desk not in orig_voice and seg.get("voice_id"):
                orig_voice[desk] = seg["voice_id"]
            fresh = ScriptSegment(
                order=order,
                role=seg.get("role", "desk"),
                speaker_key=seg.get("speaker_key", ""),
                text=seg.get("text", ""),
                source_hn_id=seg.get("source_hn_id"),
                voice_id=voice_for[desk],
                desk=desk,
            )
            segments.append(fresh)
            provenance[order] = (ep_id, src_order)
            order += 1
    return segments, provenance, orig_voice


# The intro and sign-off describe this specific episode: which desks are in it and how far back
# the stories go. That is why they are never reused from another episode's cache.
_INTRO = ("Good morning. This is HN Radio, a custom edition: {desks}, "
          "from the last {days} days on Hacker News.")
_OUTRO = "That's your custom front page. Go touch grass."


def _desk_phrase(cast: Cast, roles: List[str]) -> str:
    """'the AI desk with Priya' style listing, for the intro. Reads naturally for 1, 2 or 3."""
    names = []
    for role in roles:
        desk = cast.by_role(role)
        label = "AI" if role == "ai" else role.capitalize()
        names.append(f"the {label} desk with {desk.name}" if desk else f"the {label} desk")
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


# The anchor's and comment desk's descriptions for a custom cast. They used to be read off
# `cast.DEFAULT_CAST`, which now describes a two-person show and has no comment desk to read.
_ANCHOR_BEAT = "Hosts, connective tissue, signs off."
_ANCHOR_PERSONA = "Warm, quick, lightly wry. Keeps the show moving."
_DRAMA_BEAT = "Performs the spiciest comments from the top thread."
_DRAMA_PERSONA = "Deadpan delivery that makes the comment funnier."


def cast_from_config(cfg: dict) -> Cast:
    """A Cast carrying the listener's chosen voices, so names and voices stay coherent downstream.

    Building the cast up front is why this does not need recast's name-rewriting pass for the
    lines it writes fresh: the writer already addresses the desks by their chosen names.

    Beats and personas come from `editions.TOPICS` rather than from `cast.DEFAULT_CAST`, which now
    describes a two-person show and no longer has an ai/maker/security seat to copy. Keywords are
    NOT carried any more: `Desk.keywords`/`Desk.domains` were deleted because nothing read them.
    `editions.TOPICS` still holds the keyword lists, and they are still
    live -- the topic FILTER reads them there. What is gone is the dead copy onto each Desk.

    ONE REDUCTION TO KNOW ABOUT, and it is not fixed here. `PanelWriter` no longer routes a story
    to the desk whose keywords match it, so on a multi-desk custom episode every FRESHLY WRITTEN
    line goes to the first picked desk instead of to the matching one. The topic FILTER is intact,
    so the episode still contains exactly the subjects the listener asked for, and reused lines
    keep the voice they were rendered in. Restoring per-story routing for fresh lines means giving
    the writer a per-story desk, which is a change to the writer interface and out of scope for
    the two-person work.
    """
    cfg = validate(cfg)
    anchor_name = config.ALL_VOICES[cfg["anchor"]][0]
    anchor = Desk(role="anchor", name=anchor_name, voice_id=cfg["anchor"],
                  beat=_ANCHOR_BEAT, persona=_ANCHOR_PERSONA)

    desks = []
    for role in list(ROUTABLE_DESKS) + ["drama"]:
        voice = cfg["desks"].get(role) or (cfg["drama"] if role == "drama" else None)
        if voice is None:
            continue  # not picked, so it cannot hold a story or speak
        spec = editions.TOPICS.get(role, {})
        desks.append(Desk(
            role=role, name=config.ALL_VOICES[voice][0], voice_id=voice,
            beat=spec.get("beat", _DRAMA_BEAT), persona=spec.get("persona", _DRAMA_PERSONA)))
    # Anything matching nothing falls to a desk that is actually present.
    default_role = "maker" if "maker" in cfg["desks"] else sorted(cfg["desks"])[0]
    return Cast(anchor=anchor, desks=desks, default_role=default_role)


def assemble(cfg: dict, chosen: List[dict], live_stories=None, today: Optional[date] = None,
             episodes_dir: Optional[Path] = None) -> dict:
    """Build the full ordered script for a custom episode, without rendering anything.

    Separated from build() so the ordering and provenance logic can be tested without an API key
    or a renderer. Returns everything render_custom needs.
    """
    from . import ingest, sources
    from .recast import rewrite_names
    from .writers import PanelWriter

    cfg = validate(cfg)
    today = today or date.today()
    episodes_dir = episodes_dir or config.EPISODES_DIR
    cast = cast_from_config(cfg)
    cid = config_id(cfg)

    # --- reused half: lines already written and rendered in past episodes ---
    reused_segments, provenance, orig_voice = collect_segments(cfg, chosen,
                                                               episodes_dir=episodes_dir)
    # Rename correspondents only where the voice actually changed. That matches the reuse rule: an
    # unchanged voice keeps its exact words, which is precisely what makes its cached audio
    # reusable. A changed voice is re-rendered anyway, so rewriting its text costs nothing.
    changed = {role: voice for role, voice in cfg["desks"].items()
               if role in orig_voice and orig_voice[role] != voice}
    if changed:
        rewrite_names(reused_segments, changed, orig_voice)
        for seg in reused_segments:
            desk = cast.by_role(seg.desk)
            if desk:
                seg.speaker_key = desk.name

    # --- fresh half: today's picks have no script yet ---
    fresh_segments: List[ScriptSegment] = []
    live_picks = [s for s in chosen if s["source"] == "live"]
    if live_picks and live_stories:
        by_id = {s.id: s for s in live_stories}
        picked = [by_id[p["hn_id"]] for p in live_picks if p["hn_id"] in by_id]
        for s in picked:
            sources.enrich_story(s)
        top = ingest.pick_top_thread(picked) if cfg["drama"] else None
        comments = ingest.fetch_top_comments(top, config.N_COMMENTS) if top else []
        fresh_segments = PanelWriter().write(picked, top, comments, cast, "custom", today)

    # --- assemble: intro, reused, fresh, sign-off; renumber and re-key provenance ---
    intro = ScriptSegment(order=0, role="anchor", speaker_key=cast.anchor.name, desk="anchor",
                          text=_INTRO.format(desks=_desk_phrase(cast, sorted(cfg["desks"])),
                                             days=cfg["days"]))
    outro = ScriptSegment(order=0, role="anchor", speaker_key=cast.anchor.name, desk="anchor",
                          text=_OUTRO)
    ordered = [intro] + reused_segments + fresh_segments + [outro]
    # Re-key provenance by position, not by identity. The reused block always occupies indices
    # 1..len(reused_segments), and this must be read BEFORE renumbering overwrites seg.order.
    # (Comparing segments by value would be wrong: ScriptSegment is a dataclass, so two lines with
    # identical fields compare equal and could match the wrong entry.)
    new_provenance = {}
    for offset, seg in enumerate(reused_segments):
        src = provenance.get(seg.order)
        if src:
            new_provenance[1 + offset] = src
    for i, seg in enumerate(ordered):
        seg.order = i

    titles = [s["title"] for s in chosen[:3] if s.get("title")]
    title = "Custom: " + ", ".join(titles) if titles else f"Custom edition {cid}"
    source_items = [{"hn_id": s["hn_id"], "title": s["title"], "url": s.get("url")}
                    for s in chosen]

    return {
        "segments": ordered,
        "provenance": new_provenance,
        "cast": cast,
        "title": title,
        "source_items": source_items,
        "episode_id": f"{today.isoformat()}-custom-{cid}",
        "config_id": cid,
        "config": cfg,
    }


def build(cfg: dict, live_stories=None, today: Optional[date] = None, log=print):
    """Produce one custom episode from a config. Returns the Episode.

    Reused lines come from past episodes; today's stories are written fresh and rendered. The
    anchor's intro and sign-off are always written and rendered for this episode.
    """
    from . import pipeline

    cfg = validate(cfg)
    today = today or date.today()
    pool = build_pool(cfg["days"], live_stories=live_stories, today=today)
    chosen = select(cfg, pool)
    if not chosen:
        raise ConfigError(f"No stories in the last {cfg['days']} days matched those desks. "
                          "Try adding a desk or widening the day range.")

    a = assemble(cfg, chosen, live_stories=live_stories, today=today)
    log(f"[custom] {a['episode_id']}: {len(chosen)} stories, {len(a['segments'])} segments")
    ep = pipeline.render_custom(a["segments"], episode_id=a["episode_id"], title=a["title"],
                               source_items=a["source_items"], cast=a["cast"],
                               provenance=a["provenance"], summary="", log=log)

    # Persist the config beside the episode. This is the phase-2 seam: a feed points at this file.
    try:
        (config.EPISODES_DIR / a["episode_id"] / "config.json").write_text(
            json.dumps({"config": cfg, "config_id": a["config_id"]}, indent=2))
    except OSError:
        pass  # the episode is already rendered; a missing sidecar must not fail the build
    return ep


# --- live front page, cached ------------------------------------------------------------------
# The picker asks for the pool on every page load, and fetch_front_page walks the ranked id list
# with retries, so an uncached call is both slow and unbounded. Same shape as trending's cache,
# including stamping FAILURES: without that, an HN outage means every request re-runs the full
# walk. The failure stamp is deliberately separate from the success stamp so a failure cannot make
# a stale good list look fresh.
LIVE_TTL_SECONDS = 15 * 60
LIVE_FAILURE_TTL_SECONDS = 60
LIVE_POOL_SIZE = 15

_live: Optional[list] = None
_live_at: float = 0.0
_live_failed_at: float = 0.0


def reset_live_cache() -> None:
    """Drop the live-front-page cache. Used by tests so each controls its own fetch behavior."""
    global _live, _live_at, _live_failed_at
    _live, _live_at, _live_failed_at = None, 0.0, 0.0


def live_stories_cached() -> list:
    """Today's front page as Story objects, cached. Returns [] rather than raising.

    Story objects, not trending's dicts, because desk routing reads the outbound URL's domain
    (github.com and friends signal the Maker desk) and trending only carries the HN permalink.
    """
    global _live, _live_at, _live_failed_at
    import time

    from . import ingest

    now = time.monotonic()
    if _live is not None and (now - _live_at) < LIVE_TTL_SECONDS:
        return list(_live)
    if _live is None and (now - _live_failed_at) < LIVE_FAILURE_TTL_SECONDS and _live_failed_at:
        return []
    try:
        stories = ingest.fetch_front_page(n=LIVE_POOL_SIZE)
    except Exception:
        _live_failed_at = time.monotonic()
        return list(_live) if _live is not None else []
    _live = stories
    _live_at = time.monotonic()
    return list(_live)
