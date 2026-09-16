"""The JSON the browser app reads: the episode list and the voice catalog.

`web/` is vanilla JavaScript with no build step, so these two files ARE its API. Nothing here
renders HTML; the browser does that from this data.

  - index.json  : one entry per canonical episode, newest first
  - voices.json : the voice catalog plus who is currently seated at each desk
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config, pricing
from .jsonio import write_json
from .models import is_recast

# A default voice per build-your-own topic desk, for the picker's one-click Flux preset. These
# used to fall out of `cast.ROLE_VOICES`, which listed a preferred voice per themed desk; the
# desks were retired and took that table's per-beat rows with them. Kept here rather than
# revived there because these are a UI suggestion for one page, not a casting rule the show obeys.
_TOPIC_DESK_SUGGESTIONS = {
    "ai": "flux-meena-en",        # was Priya, who Sam retired by ear
    "maker": "flux-wade-en",
    "security": "flux-jack-en",
}

def _fill_topic_desks(preset: dict, catalog, routable) -> None:
    """Seat the topic desks and the drama slot in `preset`, gated on `catalog`, in place.

    THE NARROW EXTRACTION ONLY, and deliberately so. `flux_preset` and `build_preset` are the same
    five lines against different gates -- `recast.selectable_voices()` for the recast picker, which
    follows the configured host, and `config.VOICE_CATALOG` for the build page, which does not.
    That difference is the whole reason both exist, so it stays the caller's to pass in.

    The wider `_preset_for(catalog, host_voice, ...)` the review floated was rejected: the two
    presets genuinely differ above this point (two resolved seats plus a guest, versus one anchor
    plus a spare-fill loop), and `flux_preset`'s anchor must stay the RESOLVED seat rather than
    `config.host_voice()`. The two can differ: the constant is a preference, and the resolved
    anchor is whoever that preference could actually be seated against the live catalog.

    `setdefault` on drama, not assignment: a topic keeps its own suggestion and only a topic with
    none falls through, so no two rows start on the same voice.
    """
    for role, vid in _TOPIC_DESK_SUGGESTIONS.items():
        if role in routable and vid in catalog:
            preset[role] = vid
    if "flux-cole-en" in catalog:
        preset.setdefault("drama", "flux-cole-en")



def build_manifest(episodes_dir: Path) -> Path:
    """Write episodes/index.json: a flat list of canonical episodes, newest first.

    Recasts (`<id>-recast`) are live voice-comparison demos, not catalogued episodes, so they are
    left out of the feed. One tile per real episode.
    """
    episodes = []
    # Per-episode cost, read but NOT published. `totals` below is a fold over these, and the
    # landing page has no other use for them: it stopped showing a price per row on 2026-09-16
    # (Sam: "that's actually not as useful as I thought it would be"), and the receipt on
    # episode.html reads that episode's own `episode.json` rather than this file.
    #
    # Kept out of the rows on purpose rather than left in as harmless. This file is the frontend's
    # whole API, and a key nothing reads is how it drifts: `cohost_pool` was published here for
    # weeks after `cast.html` stopped reading it. If a row needs a price again, add it back with a
    # reader.
    costs = []
    for ej in sorted(episodes_dir.glob("*/episode.json"), reverse=True):
        d = json.loads(ej.read_text())
        if is_recast(d["id"]):
            continue
        costs.append({"id": d["id"], "cost": d.get("cost") or {}})
        episodes.append({
            "id": d["id"],
            "title": d["title"],
            "edition": d.get("edition", ""),
            "generated_at": d.get("generated_at", ""),
            "duration_seconds": d.get("duration_seconds", 0),
            # The feed page shows this under each title, NPR-style. Blank on episodes whose
            # writer did not produce show notes, which is visible rather than hidden.
            "summary": (d.get("summary") or "").strip(),
        })
    path = episodes_dir / "index.json"
    write_json(path, {"episodes": episodes, "totals": _totals(costs)})
    return path


def _totals(episodes: list) -> dict:
    """Catalog-wide cost, for the one thing on the landing page no single row can say.

    THE REASON THIS IS NOT DONE IN THE BROWSER. Every per-episode figure the page shows was
    computed by `pricing.py` and published, and the page only formats it -- that is deliberate,
    because the show now SAYS its cost out loud and a second implementation of the arithmetic in
    JavaScript is a second answer to a question the audio already answered. Summing published
    dollars is admittedly a thinner computation than deriving them, but the aggregate is a fact
    about the catalog and it belongs next to the definition of what a cost is, not in a page.

    `episodes_per_day` is MEASURED, not read off the cron. The schedule went from daily to twice
    daily on 2026-09-04, and for eleven days the committed crontab and the deployed one disagreed
    about that without anything noticing. A page that hardcodes a cadence is wrong the next time it
    changes, and wrong silently; counting what actually shipped cannot drift.

    TWO WINDOWING DECISIONS, both of which change the number:

      - THE NEWEST DATE IS EXCLUDED. It is almost always a partial day -- an episode airs at 03:00
        Pacific and the next at 15:00, so for twelve hours of every day the catalog holds one
        episode for today. Counting it drags the average toward 1 and makes the credit look like
        it lasts longer than it will.
      - SEVEN COMPLETE DATES, not fourteen. A fortnight straddles the daily/twice-daily switch, so
        it averages two different shows and describes neither. A week is long enough to survive a
        single missed run and short enough to be about the show as it runs now.

    So this is "the pace it has been publishing at", which is what the page calls it. It is not a
    promise about the schedule, and the copy does not make one.

    Takes `{id, cost}` rows, not the rows that get published: per-episode cost is no longer one of
    the published keys (see `build_manifest`), so this reads the costs its caller collected on the
    way past. Same two fields either way.

    Recasts never reach here: `build_manifest` filtered them out before calling this.
    """
    priced = [e for e in episodes if (e.get("cost") or {}).get("usd") is not None]
    if not priced:
        # No priced episode means no total. A zero would read as "this catalog was free".
        return {}

    usd = sum(e["cost"]["usd"] for e in priced)
    characters = sum(e["cost"].get("characters") or 0 for e in priced)
    mean = usd / len(priced)

    # `episodes` is newest-first (build_manifest sorts reversed), so the recent window is a head
    # slice of the dates, not a tail. Ids are `YYYY-MM-DD` with an optional `-am`/`-pm`/edition
    # suffix, so the date is the first ten characters.
    seen, ordered = set(), []
    for e in episodes:
        day = str(e.get("id", ""))[:10]
        if day and day not in seen:
            seen.add(day)
            ordered.append(day)
    window = set(ordered[1:8])  # skip today (partial), then seven complete dates
    in_window = [e for e in episodes if str(e.get("id", ""))[:10] in window]
    per_day = (len(in_window) / len(window)) if window else 0.0

    return {
        "episodes": len(priced),
        "characters": characters,
        "usd": round(usd, 6),
        "mean_usd": round(mean, 6),
        "episodes_per_day": round(per_day, 2),
        # How long the signup credit runs the show at the measured cadence. The number that makes
        # the per-episode figure mean something: cents per episode is abstract, "over a year of a
        # twice-daily podcast" is not.
        "days_per_credit": int(pricing.FREE_CREDIT_USD // (mean * per_day)) if mean and per_day else 0,
        "episodes_per_credit": pricing.episodes_per_credit(mean),
        "rate_usd_per_1k": pricing.rate_usd_per_1k(),
        "plan": pricing.plan(),
        "credit_usd": pricing.FREE_CREDIT_USD,
        "pricing_url": pricing.PRICING_URL,
    }


def build_voices_json(episodes_dir: Path) -> Path:
    """Write episodes/voices.json: the voice catalog for the recast picker (v3 JSON API).

    FLUX ONLY, and filtered HERE rather than in the browser. Two reasons, in order:

      - this file is a PUBLISHED artifact that outlives the code that wrote it. A UI-side filter
        leaves the Aura ids sitting in the JSON, so the next page to read `doc.voices` without
        knowing the rule offers them again. Dropping them at generation means a stale published
        file cannot reintroduce them.
      - the rule is not cosmetic. `POST /api/recast` refuses a non-Flux voice, so publishing one
        would be advertising a choice the endpoint rejects.

    The six Aura-2 entries and the `presets.aura` map went with the old-vs-new comparison.
    `config.AURA_CATALOG` and `render.py`'s /v1/speak path are untouched: an Aura render still
    works, it is just not something the site offers.
    """
    from . import custom
    from .cast import (COHOST_ROLE, RoleUnavailable, cohost_candidates, recent_cohost_voices,
                       resolve_role)

    # Resolved by ROLE against the live catalog, which is exactly what the render path does
    # (`episode_cast` -> `resolve_role`). It used to read `active_cast()` instead, and the two
    # DID disagree: on production `active_cast()` seats Haley at the anchor desk while
    # `resolve_role("anchor")` casts Alexis, so voices.json advertised one anchor and the
    # committed script rendered another.
    #
    # This is the catalog view, not an episode view. It lists TWO seats rather than five: the
    # host, who is the same every day, and the second chair, which is filled by whoever the
    # rotation would pick right now. `before=None` is
    # correct for a page: it means "as of the latest episode", which is what a visitor is asking.
    #
    # `taken` accumulates across the loop for the same reason `episode_cast` does it: each role
    # resolves independently, so without it two roles whose fallback chains bottom out on the
    # same voice would seat one character twice. Not hypothetical in shape, only in timing.
    # Reading `active_cast()` made this structurally impossible here (fixed, distinct literal
    # ids), so resolving by role reopened it for the page.
    seats = {}
    taken = set()
    for role in ("anchor", COHOST_ROLE):
        candidates = None
        if role == COHOST_ROLE:
            host_voice = seats["anchor"].voice_id if "anchor" in seats else ""
            # This computation is load-bearing and must stay: `candidates` is the only thing that
            # seats the second chair, and `resolve_role("cohost", ...)` raises `RoleUnavailable`
            # immediately without it. Only the `cohost_pool`/`cohost_recency_window` keys that
            # used to be published off it were removed after `cast.html` was rewritten and
            # stopped being their reader.
            candidates = cohost_candidates(recent_cohost_voices(), host_voice)
        try:
            desk = resolve_role(role, exclude=taken, candidates=candidates)[0]
        except RoleUnavailable:
            # DEGRADE, do not raise. This differs from `episode_cast` on purpose: an episode
            # that cannot be cast should fail loudly, but the cast page is part of the site
            # build, and one unfillable seat must not take the whole site down. A missing seat
            # is honest; a duplicated one advertises a cast that can never air.
            continue
        seats[role] = desk
        taken.add(desk.voice_id)

    def entries(catalog, family):
        return [{"id": vid, "name": name, "note": note, "family": family}
                for vid, (name, note) in catalog.items()]

    # `recast.selectable_voices()`, not the raw catalog constant: it is the filtered pool that
    # voices and nothing else, so the picker and the cast page cannot offer a voice the configured
    # host would reject. It is also the exact set `POST /api/recast` validates against, so the page
    # and the endpoint cannot disagree about what is castable.
    from . import recast as recast_roles
    from .recast import selectable_voices
    voices = entries(selectable_voices(), "flux")
    # Seating for the cast page: who sits where, and what they cover.
    seating = [
        {"role": d.role, "name": d.name, "voice": d.voice_id, "beat": d.beat, "persona": d.persona}
        for d in seats.values()
    ]
    # THE WHOLE PUBLISHED CATALOG, 36 entries, host-independent and separate from `voices` above.
    #
    # `voices` is what the recast picker may OFFER, so it is `selectable_voices()` and it follows
    # the configured host, so a page built against one endpoint never advertises another's
    # at GA and now 400. A page that wants to show the reader the Flux range cannot use it, and the
    # cast page proved that by rendering eight cards, most of them dead.
    #
    # So this key answers a different question: "what does the published catalog contain", which
    # has one answer regardless of which host this process is pointed at. Every entry carries
    # `retired` so a page can label Marcus and Priya in WORDS rather than hiding them or, worse,
    # distinguishing them by colour. It matches the official orb set exactly, 36 for 36.
    catalog = [{"id": vid, "name": name, "note": note, "family": "flux",
                "retired": config.is_retired(vid)}
               for vid, (name, note) in sorted(config.PUBLISHED_VOICES.items(),
                                               key=lambda kv: kv[1][0].lower())]
    # What the BUILD page may offer: the GA catalog the show can actually seat. Not host-dependent
    # for the same reason `catalog` is not, and a strict subset of what `POST /api/build` accepts
    # (`config.ALL_VOICES`), so the picker cannot offer a voice the endpoint refuses.
    buildable = [vid for vid in config.VOICE_CATALOG]
    # The show's permanent host, host-independent like the two keys above. `seating` answers "who
    # would be cast right now on THIS endpoint", which is not necessarily the catalog id the
    # grid was built from; the cast page grids the published catalog, so a badge keyed off
    # seating matched no card there and the host went unlabelled.
    host_voice = config.HOST_VOICE
    # Both hosts, by edition, for the cast page's badges. `host_voice` above is kept as the morning
    # host for any older reader; this key is the one that knows there are two shows a day. The
    # names come off the catalog rather than a literal so a renamed voice cannot leave the page
    # calling Cole something else.
    hosts = {
        slot: {"voice": vid, "name": config.voice_name(vid) or vid, "edition": edition}
        for slot, vid, edition in (("am", config.HOST_VOICE, "Morning Edition"),
                                   ("pm", config.AFTERNOON_HOST_VOICE, "Afternoon Edition"))
    }
    # The BUILD page's starting cast, gated on `buildable` rather than on `selectable_voices()`.
    #
    # `presets.flux` above belongs to the recast picker and follows the configured host, so on
    # a mismatched endpoint its anchor, cohost and guest are unknown ids and the suggestions
    # out entirely. web/build.html seeded itself from that, `setVoice` silently failed on every id
    # its <select> did not contain, and four of five rows fell back to option 0 -- which put one
    # voice in four seats, the exact thing `episode_cast` raises over.
    #
    # `setdefault` order matters: a topic keeps its own suggestion, and only a topic with none
    # falls through to a distinct spare so no two rows start on the same voice.
    build_preset = {}
    if host_voice in config.VOICE_CATALOG:
        build_preset["anchor"] = host_voice
    _fill_topic_desks(build_preset, config.VOICE_CATALOG, custom.ROUTABLE_DESKS)
    spare = [vid for vid in config.VOICE_CATALOG if vid not in set(build_preset.values())]
    for role in ("anchor", *custom.ROUTABLE_DESKS, "drama"):
        if role not in build_preset and spare:
            build_preset[role] = spare.pop(0)

    data = {
        "voices": voices,
        "catalog": catalog,
        "buildable": buildable,
        "host_voice": host_voice,
        "hosts": hosts,
        "build_preset": build_preset,
        "seating": seating,
        "host": config.api_host(),
        # One family, kept as a list rather than collapsed away: `web/build.html` groups its
        # <option> elements by it, and a page that reads `doc.families` and finds nothing renders
        # an empty select. One entry is also the honest shape for the day a second family comes
        # back.
        "families": [{"id": "flux", "label": "Flux"}],
        # TWO KEYS WERE DROPPED HERE on 2026-09-16, with the recast picker that was their only
        # consumer:
        #
        #   "roles"    [{id, label}] for the two seats the picker offered, Showrunner and Guest
        #              host, published rather than hardcoded in app.js so the labels could not
        #              drift from `recast.ROLES`.
        #   "presets"  {"flux": {anchor, cohost, guest, + topic desks}}, the one-click starting
        #              cast, resolved against the same seating published below so the picker could
        #              not offer a voice the page had just said sits elsewhere.
        #
        # `presets` was already dead before this: `web/build.html` stopped reading it on
        # 2026-08-21 (it seeds from `build_preset`, gated on `buildable`) and `web/app.js` never
        # read it at all. Only tests referenced it, which is the shape a published key takes on
        # its way to being a second source of truth nobody maintains -- the same way `cohost_pool`
        # went. `_fill_topic_desks` still has one caller, `build_preset` below.
    }
    path = episodes_dir / "voices.json"
    write_json(path, data)
    return path
