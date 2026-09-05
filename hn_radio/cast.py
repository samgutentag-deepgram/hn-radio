"""The cast: one consistent host, plus a co-host who changes every episode.

Two people. No desks.

The show used to seat themed correspondent desks -- ai, maker, security -- and pick one per
episode by scoring the day's stories against beat keywords, alongside a fixed "drama" desk that
performed the comments. Sam deleted all of it after being shown what it cost.
Gone with it, on purpose and not by accident: beat routing (a story no longer reaches a speaker
chosen by subject), and desk substitution (no more "Priya is out today, so someone else is
covering the AI desk", because there is no AI desk to cover).

What that buys, and why it is not merely subtraction. Three fixed personas meant the show could
only ever exhibit three voices plus the host, out of a GA catalog of thirty-four. The co-host is
drawn from the WHOLE catalog with a no-repeat window, so the same daily show becomes a rolling
showcase of the thing it is a demo for. That was the trade.

Topic keyword matching did NOT come along. It moved to `editions.py`, which is where it was
always actually read: reweighting story SELECTION for the ai/security/makers editions, and
filtering the build-your-own pool in `custom.py`. Deleting it with the desks would have removed
two shipped features nobody asked to lose. See that module's docstring.

The role key for the host is still "anchor", not "host". That is deliberate and it is not
nostalgia: `desk="anchor"` is read by `custom._owning_desk`, by `recast.role_of` and
`recast._slot_of`, by the segment colours in `web/brand.css` and `web/index.html`, and by every
script.json already on disk. (It said `recast.SLOTS` for a while, which was wrong twice over: a
literal list is not a reader, and that list is now deleted.)
Renaming it would churn all of that for no listener-audible difference. "cohost" is a new key,
so nothing old can be mistaken for it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import config
from .window import AFTERNOON

COHOST_ROLE = "cohost"


@dataclass
class Desk:
    """One seat: one Flux voice, one spoken name, one line on tone.

    Still called `Desk` because it is still the record every downstream consumer reads through
    `Cast.anchor` / `Cast.desks`, and because `custom.py` genuinely does still seat topic desks
    for its build-your-own editions. In the daily show there are exactly two of these and neither
    is themed.

    NO `keywords` / `domains` any more. They were kept as "inert data for `custom.py`",
    and inert was the accurate word: nothing read them anywhere. `cast.py` copied them off a
    `DEFAULT_CAST` desk where both were always `[]`, no JSON on disk carries a `keywords` key, and
    `Cast.route`, the routing they were the input to, no longer exists. If per-story routing comes
    back it needs a per-story desk handed to the writer, which is a writer-interface change; these
    two fields would not have been enough on their own.
    """

    role: str          # stable key: "anchor" (the host) | "cohost"
    name: str          # display name, e.g. "Alexis"
    voice_id: str      # Flux model string
    beat: str          # one line on what this seat covers
    persona: str       # one line on tone, used later by the LLM writer


@dataclass
class Cast:
    anchor: Desk
    desks: List[Desk]               # the daily show puts exactly one co-host here
    default_role: str = COHOST_ROLE  # the seat a line falls to when its own role is unavailable

    def by_role(self, role: str) -> Optional[Desk]:
        if role == self.anchor.role:
            return self.anchor
        for d in self.desks:
            if d.role == role:
                return d
        return None

    def voice_for(self, role: str) -> str:
        desk = self.by_role(role)
        return desk.voice_id if desk else self.anchor.voice_id

    @property
    def cohost(self) -> Optional[Desk]:
        """The second chair. `desks[0]` in the daily show.

        Still Optional, because a `Cast` with an empty `desks` list is constructible in Python.
        But the old docstring here claimed `custom.py` produces one "when a listener picks
        nothing", and that is false: `custom.validate` raises `ConfigError` on an empty desks map
        (`custom.py:91-92`), so no listener config reaches a seatless cast. Corrected when
        `pipeline`'s solo-episode branches were deleted for resting on the same false premise.
        """
        return self.by_role(COHOST_ROLE) or (self.desks[0] if self.desks else None)


# The show's two seats as data. `voice_id` here is the DEFAULT seating, not the daily seating:
# the co-host is re-cast every episode by `episode_cast`, and this constant only decides who sits
# there for callers that do not cast an episode at all (`active_cast`, used by `recast`).
#
# Wade is that default because he is already the voice this repo reached for when it needed a warm
# second chair, and the docs describe him for casual chat rather than IVR. Nothing about the daily
# show depends on it.
DEFAULT_CAST = Cast(
    anchor=Desk(
        role="anchor", name="Alexis", voice_id=config.HOST_VOICE,
        beat="Hosts. Opens the show, names the day's headlines, signs off.",
        persona="Warm, quick, lightly wry. Keeps the show moving.",
    ),
    desks=[Desk(
        role=COHOST_ROLE, name="Wade", voice_id="flux-wade-en",
        beat="Second chair. Takes every story with the host and reads the thread with them.",
        persona="Curious and specific. Says the interesting part, not the whole page.",
    )],
)


class RoleUnavailable(Exception):
    """No candidate voice for a role exists in the active catalog."""


# Ordered candidates per role, most-wanted first. The ACTIVE catalog decides who actually gets
# it: a preference is a wish, not a guarantee, and `resolve_role` reports who it could not seat
# so the script can say so out loud.
#
# Haley is the fallback rather than a second anchor. If Alexis is ever pulled from the catalog
# the show still opens, in a named voice, and the listener is told a substitution happened.
#
# ONE ENTRY, and that is the design rather than an omission. The per-beat rows (ai / maker /
# security / drama) went with the desks. The co-host deliberately has no fixed
# preference list: a list is exactly what would collapse it back into a small recurring pool,
# which is the thing the change exists to escape. Its candidates are computed per episode from
# the live catalog by `cohost_candidates`, and passed to `resolve_role` explicitly.
ROLE_VOICES = {
    "anchor":   ["flux-alexis-en", "flux-haley-en"],
}

# The Afternoon Edition's host, same shape: most-wanted first, a named fallback second. Cole is
# the afternoon host by Sam's pick; Jack stands in if Cole ever leaves the catalog, and the show
# says so the same way it says "Alexis is out today". `ROLE_VOICES["anchor"]` above remains the
# morning and calendar-day list, unrenamed, because a row of tests and the archive re-render reach
# it by that name.
AFTERNOON_HOST_VOICES = [config.AFTERNOON_HOST_VOICE, "flux-jack-en"]

# `-am`, `-pm`, or nothing, read off an episode id. `episode_cast` uses it when a caller hands over
# an id but no slot, which is every caller that predates the two-host show.
_SLOT_SUFFIX = re.compile(r"-(am|pm)$")


def slot_of_episode_id(episode_id: Optional[str]) -> Optional[str]:
    m = _SLOT_SUFFIX.search(episode_id or "")
    return m.group(1) if m else None


def host_candidates(slot: Optional[str]) -> List[str]:
    """The ordered host preferences for a slot. Afternoon gets its own host; everything else Alexis."""
    return AFTERNOON_HOST_VOICES if slot == AFTERNOON else ROLE_VOICES["anchor"]


def host_voice_ids() -> set:
    """Every voice that hosts SOME edition. Kept out of the co-host rotation in BOTH shows, so the
    afternoon host is never the morning's second chair and vice versa: each of them is one
    character with one job, and a listener hearing Cole at 3pm should not have heard him as
    somebody's guest at 3am. Only the first preference per slot; a fallback host (Haley, Jack) is
    an ordinary co-host candidate until the day it actually has to host."""
    return {ROLE_VOICES["anchor"][0], AFTERNOON_HOST_VOICES[0]}


def host_name_for(slot: Optional[str]) -> str:
    """The display name of whoever would host `slot` today. For the outro's hand-off line."""
    return resolve_role("anchor", candidates=host_candidates(slot))[0].name


def resolve_role(role: str, exclude: Optional[set] = None,
                 candidates: Optional[Sequence[str]] = None):
    """Fill `role` from the active catalog. Returns (Desk, name_of_preferred_who_is_missing).

    The second value is None when the first preference got the job, and otherwise the display
    name of the character the show WANTED, so the script can say "Alexis is out today" rather
    than silently sounding different. Kept for BOTH seats even though the desk personas are gone:
    it is the difference between a listener hearing a different voice and a listener being told
    why, and it costs one string.

    `exclude` is the set of voice ids already cast in THIS episode. One voice may not hold two
    seats: it would put the host in conversation with herself, in the same voice.

    `candidates` overrides `ROLE_VOICES[role]`. That is how the co-host works -- its preference
    order is computed per episode against the live catalog rather than written down -- and it is
    also why this function does not care which of the two it is filling.
    """
    if candidates is None:
        candidates = ROLE_VOICES.get(role)
    if not candidates:
        raise RoleUnavailable(f"no voice preferences defined for role {role!r}")
    catalog = config.active_voice_catalog()
    taken = exclude or set()
    canonical = DEFAULT_CAST.by_role(role)
    if canonical is None:
        raise RoleUnavailable(f"role {role!r} is not part of the cast")

    for i, voice_id in enumerate(candidates):
        if voice_id not in catalog or voice_id in taken:
            continue
        name = config.voice_name(voice_id) or canonical.name
        desk = Desk(role=role, name=name, voice_id=voice_id,
                    beat=canonical.beat, persona=canonical.persona)
        wanted = config.voice_name(candidates[0]) if i > 0 else None
        # A substitution only counts if the audience would notice a DIFFERENT character. Haley
        # replacing Haley across catalogs is the same person, not a stand-in. Nor is someone the
        # show already cast somewhere else this episode: announcing "Haley is out today" while
        # Haley anchors the show is a lie the listener can hear.
        if wanted == name or candidates[0] in taken:
            wanted = None
        return desk, wanted

    raise RoleUnavailable(
        f"no candidate voice for role {role!r} is in the active catalog"
        + (f" and not already cast ({sorted(taken)})" if taken else "")
        + f"; tried {list(candidates)[:8]}"
    )


def active_cast() -> Cast:
    """The default seating, read by both the render path and the cast page.

    One rule, deliberately. The two sides disagreed before this existed: `publish` resolved the
    seating one way while `pipeline.run_panel` took `cast=DEFAULT_CAST` as a default argument
    bound at import, so the site could list one cast and the audio render another. Nothing
    failed; the only symptom was the wrong voices in the audio, which no test could catch.

    MUST be called, never captured as a default argument value. Capturing it at import is
    exactly how the two sides drifted apart.

    This is the DEFAULT seating, not an episode's. The daily show goes through `episode_cast`,
    which re-casts the second chair. The only remaining caller is `recast`, which re-voices an
    existing script and therefore needs a cast for names rather than for casting.
    """
    return DEFAULT_CAST


# How many recent episodes block a voice from taking the second chair again.
#
# Fourteen, where the desk rule it replaces used three. Three was right for three desks: with
# only three routable roles a wider window had nothing left to choose from. The co-host pool is
# the whole catalog minus the host and the retired, which is 33 on production today, so three
# would let a voice come back twice a week and the show would still sound like a tiny rotation --
# the exact failure the desks were deleted to escape.
#
# It was fourteen while the show ran once a day: two weeks before anyone returned, with 19 of 33
# voices free on any given day. The count is in EPISODES, not days, so when the cron went to two
# shows a day fourteen silently became one week. Twenty is ten days at two a day and still leaves
# 13 of 33 free, so the walk in `cohost_candidates` almost never runs out of fresh options and the
# rotation never has to visibly strain. Not the full two weeks (28), on purpose: that would leave
# five free and turn the second chair into a near strict round-robin, which is both predictable
# and, at that length, indistinguishable from random anyway.
COHOST_RECENCY_WINDOW = 20

# A bare date (the original once-a-day show) or a date plus `-am`/`-pm` (the twice-daily show).
# Both are canonical episodes and both count toward recency. Edition suffixes and `-recast` are
# excluded by not matching, same as before.
_EPISODE_ID = re.compile(r"^\d{4}-\d{2}-\d{2}(-(am|pm))?$")


def _recent_episode_ids(limit: int, before: Optional[str]) -> List[str]:
    """Ids of the `limit` episodes before `before`, newest first.

    Deliberately derived from what aired rather than from a state file. The episodes directory
    is already the record, so there is nothing extra to keep in sync, nothing to migrate, and
    nothing that can disagree with reality.

    `before` is the episode id being generated (YYYY-MM-DD), and recency is relative to IT, not
    to the wall clock. Without it, backfilling 2026-07-01 read episodes from August as "recent",
    and re-rendering today read its own previous script and suppressed the voice it had just
    cast, so the same command produced a different cast the second time.

    `-recast` variants are excluded by the id pattern: a recast is the same episode wearing
    different voices, so counting it would make one day's news block a voice twice.
    """
    return sorted((p.name for p in config.EPISODES_DIR.glob("*")
                   if p.is_dir() and _EPISODE_ID.match(p.name)
                   and (before is None or p.name < before)), reverse=True)[:limit]


def recent_cohost_voices(limit: int = COHOST_RECENCY_WINDOW,
                         before: Optional[str] = None) -> List[str]:
    """Voices that held the second chair in the `limit` episodes before `before`, newest first.

    Reads VOICES, not roles. Its predecessor `recent_desk_roles` read `seg["desk"]` because a
    desk role was the thing that repeated; now the role is always "cohost" and the voice is the
    thing that must not repeat, so this reads `seg["voice_id"]`.

    Episodes from before the two-person change contribute nothing, which is correct rather than
    unfortunate: their second voice was a themed desk chosen by subject, so it says nothing about
    whether a co-host sounds stale today.
    """
    out: List[str] = []
    for ep_id in _recent_episode_ids(limit, before):
        try:
            segs = json.loads((config.EPISODES_DIR / ep_id / "script.json").read_text())
            for seg in segs:
                if seg.get("desk") != COHOST_ROLE:
                    continue
                vid = seg.get("voice_id")
                if vid and vid not in out:
                    out.append(vid)
                break  # one co-host per episode, so the first tagged line settles it
        except (OSError, ValueError, AttributeError, TypeError):
            continue  # a half-written or malformed episode must not break casting
    return out


def cohost_candidates(recent_voices: Sequence[str], host_voice: str,
                      before: Optional[str] = None) -> List[str]:
    """Every voice eligible for the second chair, best-first, for `resolve_role` to walk.

    Three rules, in order of who wins when they conflict.

    1. ELIGIBLE: everything in the live catalog except the host's own voice and anything in
       `config.RETIRED_VOICES`. Retired voices are already absent from `VOICE_CATALOG`; the
       subtraction here is what makes the by-ear veto hold even against a substituted catalog.
    2. ROTATED by a hash of the episode id. Deterministic, so a re-render of a failed episode
       casts the same co-host and is the same show; and unrelated across adjacent dates, so
       consecutive episodes do not walk the catalog alphabetically. Same hash-then-walk shape as
       `voices.guest_voice_for`, for the same reason: stability where it is free.
    3. FRESH FIRST, stale after. A voice inside the recency window goes to the back rather than
       being dropped. With 33 eligible against a window of 14 that tail is never reached today,
       but a strict filter would empty the list the moment the catalog shrank below the window
       and raise `RoleUnavailable` on a show that could perfectly well go out. A repeated
       co-host is a worse episode; no episode is worse than that.

    `sorted()` rather than catalog order: `active_voice_catalog` returns a dict whose order comes
    from a literal, and the rotation must not silently change meaning when someone adds a row.
    """
    hosts = host_voice_ids() | {host_voice}
    pool = [v for v in sorted(config.active_voice_catalog())
            if v not in hosts and v not in config.RETIRED_VOICES]
    if not pool:
        return []
    start = int(hashlib.sha256((before or "").encode("utf-8")).hexdigest(), 16) % len(pool)
    rotated = pool[start:] + pool[:start]
    recent = set(recent_voices or ())
    return [v for v in rotated if v not in recent] + [v for v in rotated if v in recent]


def episode_cast(recent_voices: Optional[List[str]] = None, before: Optional[str] = None,
                 slot: Optional[str] = None):
    """The two regulars for ONE episode. Returns (Cast, {role: missing_preferred_name}).

    `slot` picks the host: `AFTERNOON` seats Cole, anything else seats Alexis. Left None, it is
    read off `before` (`-am` / `-pm`), so every caller that passes an episode id and nothing else
    already casts the right host.

    Returns an ordinary Cast on purpose. Every downstream consumer already reads `cast.anchor`
    and `cast.desks`, so changing who sits in them needs no change in the writers, in
    `voices.assign_voices`, or on the cast page.

    Took a `stories` argument once, so `desk_of_the_day` could score the day's
    stories against each desk's beat. Nothing about casting reads the news any more, so keeping
    it would have been a parameter that only looked meaningful.

    `before` is the id of the episode being generated. It does two jobs: it makes "held the chair
    recently" relative to THIS episode rather than to the clock (see `recent_cohost_voices`), and
    it seeds the rotation, so the same date always casts the same co-host.

    Does NOT catch RoleUnavailable, and must not start. An episode nobody can cast is a loud
    problem; an episode where two characters share one voice is a silent one, and the silent one
    is what actually shipped before `exclude` existed.
    """
    substitutions = {}
    if slot is None:
        slot = slot_of_episode_id(before)
    # Each seat resolves independently, so without this nothing stops one voice taking both.
    taken: set = set()

    anchor, anchor_sub = resolve_role("anchor", exclude=taken, candidates=host_candidates(slot))
    if anchor_sub:
        substitutions["anchor"] = anchor_sub
    taken.add(anchor.voice_id)

    if recent_voices is None:
        recent_voices = recent_cohost_voices(before=before)
    # The host is excluded twice over -- by name here and by `taken` in resolve_role -- because
    # the two guard different things. This keeps her out of the ROTATION, so removing her does
    # not shift every other voice's position and change who the co-host is. `taken` is the
    # backstop that makes a duplicate impossible whatever the rotation produced.
    candidates = cohost_candidates(recent_voices, anchor.voice_id, before=before)
    cohost, cohost_sub = resolve_role(COHOST_ROLE, exclude=taken, candidates=candidates)
    if cohost_sub:
        # In practice this never fires, and that is the correct outcome rather than dead weight.
        # A co-host substitution means "the voice we wanted is not in the catalog", but the
        # candidate list is BUILT from the live catalog, so its first entry is always available.
        # The branch stays because it is one line and it is what makes the two seats resolve
        # through the same code path; announcing "Wade is out today" for a rotating chair nobody
        # was promised would be the wrong thing to say anyway.
        substitutions[COHOST_ROLE] = cohost_sub

    return Cast(anchor=anchor, desks=[cohost], default_role=COHOST_ROLE), substitutions
