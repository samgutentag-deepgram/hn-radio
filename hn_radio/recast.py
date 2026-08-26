"""Recast: re-render an existing episode with different voices per speaker slot.

Because every episode saves its script (script.json) and rendering is per-segment, recasting is
cheap: reload the script, remap voices by slot, re-render. This shows off the Flux voice range.

One piece: recast() reloads an episode, applies a mapping, and renders a new variant. Voice
preview samples are `scripts/make_cast_samples.py`'s job alone, not this module's.

TWO LAYERS, and the difference matters. A **slot** is the low-level thing a script segment carries:
its `desk`, or "guest" for a performed comment (which carries no desk). A **role** is what the
picker offers, and as of the two-person show (2026-08-20) there are exactly two of them:

    Showrunner -> the `anchor` slot        Guest host -> every other seat

WHY A ROLE IS NOT JUST A SLOT, twice over.

First, the quotes. In the current show a performed comment is pinned to the host's or the co-host's
voice by the writer (see `writers.PanelWriter`) and carries no `desk`, so `_slot_of` calls it
"guest". A slot-level {anchor, cohost} mapping therefore skipped every quoted line, and a "recast"
episode came out with FOUR voices in it: two new regulars and two stale ones reading the comments.

Second, the archive. Every episode on disk as of 2026-08-20 predates the two-person format: they
carry `ai` / `maker` / `security` / `drama` desks and a separate guest voice on the quotes, and not
one of them has a `cohost` slot. Matching roles to slots by name would have left the Guest host
picker dead on every episode that exists. So the Guest host absorbs every non-host seat, a recast
of an old episode reduces it to the two-person format, and nothing is left on a pre-recast voice.
`role_of` holds that rule and states what it costs.

`SLOT_LABELS` therefore survives as the vocabulary of what a script.json can carry, not as a set
of things the picker offers. The themed-desk CLI flags are gone with the desks: the two roles now
cover an old script completely, so a flag per dead desk was a third way to say the same thing.

There used to be a bare `SLOTS` list beside it saying the same thing with no labels. Deleted
2026-08-22 with zero readers in any language: the code that actually reads a slot is `_slot_of`,
`role_of` and `role_coverage`, and a literal list reads nothing.

CLI:
    python -m hn_radio.recast <episode_id> --anchor flux-cole-en --cohost flux-heather-en
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import config, render, stitch
from .cast import active_cast
from .models import Episode, ScriptSegment

# No SAMPLE_DIR / SAMPLE_TEXT here any more. `build_samples` owned them and is gone (2026-08-22):
# it and `scripts/make_cast_samples.py` both wrote `episodes/samples/<voice_id>.wav` with DIFFERENT
# scripts and both skipped if the file existed, so alternating them left one line under some cards
# and another under the rest while `web/cast.html:197` printed "Every card reads the same line, so
# the grid is a fair comparison". One namespace, one producer, which is `make_cast_samples.py`.

# The two roles the picker offers, and what the page calls them. The KEYS stay the internal slot
# names on purpose: `desk="anchor"` is written into every script.json on disk and read by
# `custom._owning_desk`, `cast.py` and the segment renderer, so renaming the slot to match the
# label would invalidate every episode in the archive to change two strings in a table header.
ROLES = ("anchor", "cohost")
ROLE_LABELS = {"anchor": "Showrunner", "cohost": "Guest host"}

# How to name a script slot in prose, for the notice the picker shows on a legacy episode ("the
# Guest host takes over the AI desk"). Keyed by slot; callers fall back to the raw slot name, so a
# slot from some future script shape is named awkwardly rather than not at all.
SLOT_LABELS = {
    "anchor": "Showrunner", "cohost": "Guest host", "ai": "AI desk", "maker": "Maker desk",
    "security": "Security desk", "drama": "Comment theater", "guest": "Quoted comments",
}


def selectable_voices() -> dict:
    """The voices the recast picker may offer: the active Flux catalog, minus the retired ones.

    Flux only, and filtered HERE rather than in the browser, because this is what the endpoint
    validates against too. A UI-side filter would leave `POST /api/recast` accepting an Aura id
    from anyone with curl, and recast exists to show the Flux range specifically.

    `active_voice_catalog()` is already free of `RETIRED_VOICES` on production (the filter is
    applied to the VOICE_CATALOG literal), but NOT against a substituted catalog, which is a
    hand-written dict. Re-applying it costs nothing and means the rule is stated once, here,
    instead of depending on which host happens to be configured.
    """
    return {vid: entry for vid, entry in config.active_voice_catalog().items()
            if vid not in config.RETIRED_VOICES and config.voice_family(vid) == "flux"}


def validate_mapping(mapping) -> Dict[str, str]:
    """Check a recast mapping and return it normalized, or raise ValueError with a stated reason.

    ONE validator, shared by the endpoint, the CLI and `recast()` itself, so "Flux only", "not
    retired" and "not the same voice twice" cannot mean one thing on the page and another in a
    shell. Callers turn the ValueError into a 400: every refusal here is a caller mistake with a
    fixable description, and a 500 would tell a visitor the app is broken when what actually
    happened is that they asked for Aura.
    """
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("No voices chosen. Pick a voice for the Showrunner or the Guest host.")
    normalized: Dict[str, str] = {}
    for key, voice in mapping.items():
        if key not in ROLES:
            names = ", ".join(ROLE_LABELS[r] for r in ROLES)
            raise ValueError(f"{key!r} is not a role on this show. Choose from: {names}.")
        if not isinstance(voice, str) or not voice:
            raise ValueError(f"no voice given for the {SLOT_LABELS.get(key, key)}")
        # Retired BEFORE unknown, because a retired id is not unknown: Priya and Marcus are both
        # still in the published Flux docs. Telling someone their voice does not exist when the
        # truth is that we pulled it by ear sends them looking for a typo that is not there.
        if voice in config.RETIRED_VOICES:
            raise ValueError(f"{voice} is retired and is no longer cast on this show")
        if config.voice_family(voice) != "flux":
            raise ValueError(f"{voice} is not a Flux voice; this show casts Flux voices only")
        if voice not in selectable_voices():
            raise ValueError(f"unknown voice: {voice}")
        normalized[key] = voice
    # One voice, two characters. Enforced server-side because the browser rule is bypassable, and
    # it is the same invariant `cast.episode_cast` raises over when it seats an episode: two seats
    # on one voice is not a two-person show, it is a one-person show that claims otherwise.
    taken: Dict[str, str] = {}
    for key, voice in normalized.items():
        if voice in taken:
            here, there = SLOT_LABELS.get(key, key), SLOT_LABELS.get(taken[voice], taken[voice])
            raise ValueError(f"the {there} and the {here} cannot use the same voice ({voice}). "
                             f"Two people, two voices.")
        taken[voice] = key
    return normalized




def _load_segments(episode_id: str) -> List[ScriptSegment]:
    path = config.EPISODES_DIR / episode_id / "script.json"
    data = json.loads(path.read_text())
    return [ScriptSegment(**d) for d in data]


def _load_episode_meta(episode_id: str) -> dict:
    return json.loads((config.EPISODES_DIR / episode_id / "episode.json").read_text())


def _slot_of(seg: ScriptSegment) -> Optional[str]:
    return seg.desk if seg.desk else ("guest" if seg.role == "commenter" else None)


# `apply_mapping` was deleted 2026-08-22: no production caller since the role model landed.
# `recast()` uses `apply_roles`, and `custom.py` imports only `rewrite_names`. It was the only
# code that could perform a slot-level GUEST remap, and `voices.py` used to cite that as a live
# reason for keeping `guest_voice_for`; both are gone now, together, rather than leaving a
# capability documented in one file and unimplemented in the other.


def _original_voices(segments: List[ScriptSegment]) -> Dict[str, str]:
    """Each slot's pre-recast voice, from the first segment that carries one."""
    orig: Dict[str, str] = {}
    for seg in segments:
        slot = _slot_of(seg)
        if slot and slot not in orig and seg.voice_id:
            orig[slot] = seg.voice_id
    return orig


ANCHOR_ROLE, COHOST_ROLE = ROLES


def role_of(seg: ScriptSegment, anchor_voice: str) -> str:
    """Which of the two roles owns this segment, in the show as it exists now.

    Three rules, in order, and the order is the whole design:

      1. the `anchor` slot is the Showrunner's. That slot name is in every script.json on disk.
      2. a performed comment pinned to the Showrunner's ORIGINAL voice is the Showrunner's. In the
         current show the regulars read the quotes themselves, so a quote belongs to whoever read
         it, and its slot ("guest", because it carries no desk) cannot tell us which.
      3. everything else is the Guest host's.

    Rule 3 is what makes this work on the archive. EVERY episode on disk as of 2026-08-20 predates
    the two-person format: they have `ai` / `maker` / `security` / `drama` desks and a separate
    guest voice on the quotes, and not one of them has a `cohost` slot. A role model that only
    matched slot names by equality would leave the Guest host picker dead on every episode that
    exists, which is not a two-role page, it is a one-role page with a decoration.

    So the Guest host absorbs every non-host seat. Recasting a five-desk episode reduces it to the
    two-person format, which is the show recast is meant to demonstrate. It is also complete: no
    line is left on a pre-recast voice, which is the failure mode the old slot-level mapping had.
    The cost is real and stated on the page: an old episode's several correspondents become one
    person, so a hand-off that used to be between two people is now a person naming themselves.
    """
    slot = _slot_of(seg)
    if slot == ANCHOR_ROLE:
        return ANCHOR_ROLE
    if seg.role == "commenter" and anchor_voice and seg.voice_id == anchor_voice:
        return ANCHOR_ROLE
    return COHOST_ROLE


def role_coverage(segments: List[ScriptSegment]) -> Dict[str, List[tuple]]:
    """Per role, the (slot, voice) pairs it covers in this script, in order of first appearance.

    This is what the picker shows. On a current-format episode each role covers one seat and there
    is nothing to explain. On a legacy episode the Guest host covers several, and the page has to
    say which and at what cost, because the alternative is a listener discovering by ear that
    three correspondents became one.
    """
    orig = _original_voices(segments)
    anchor_voice = orig.get(ANCHOR_ROLE, "")
    out: Dict[str, List[tuple]] = {role: [] for role in ROLES}
    for seg in segments:
        slot = _slot_of(seg)
        if not slot or not seg.voice_id:
            continue
        role = role_of(seg, anchor_voice)
        if (slot, seg.voice_id) not in out[role]:
            out[role].append((slot, seg.voice_id))
    return {role: pairs for role, pairs in out.items() if pairs}


def role_takeovers(segments: List[ScriptSegment]) -> Dict[str, List[tuple]]:
    """Per role, the (slot, voice) pairs that are NOT simply that role's own seat.

    A role covering two slots is not by itself a takeover: in the current show the host reads some
    of the quotes, so her coverage is `anchor` plus `guest` on her OWN voice, which is the format
    working as designed. What counts is covering a seat that the two-person show does not have --
    a themed desk, or a quote that had its own separate voice -- because that is where recasting
    changes the episode's shape rather than just its casting.

    This is the predicate the page and the CLI both report from, so they say the same thing.
    """
    out = {}
    for role, pairs in role_coverage(segments).items():
        lead = pairs[0][1]
        extra = [(slot, voice) for slot, voice in pairs if slot != role and voice != lead]
        if extra:
            out[role] = pairs
    return out




def apply_roles(segments: List[ScriptSegment], mapping: Dict[str, str]) -> Dict[str, List[str]]:
    """Apply a {role: voice} mapping. Returns, per role, the old voices that role took over.

    The return value is what `rewrite_role_names` needs: a role that absorbed three old desks has
    three old names to replace in the copy, not one.
    """
    orig = _original_voices(segments)
    anchor_voice = orig.get(ANCHOR_ROLE, "")
    absorbed: Dict[str, List[str]] = {role: [] for role in mapping}
    for seg in segments:
        if not _slot_of(seg):
            continue
        role = role_of(seg, anchor_voice)
        if role not in mapping:
            continue
        if seg.voice_id and seg.voice_id not in absorbed[role]:
            absorbed[role].append(seg.voice_id)
        seg.voice_id = mapping[role]
    return absorbed


def _rename_all(segments: List[ScriptSegment], renames: List[tuple]) -> None:
    """Replace spoken names, in ONE pass over each line.

    `renames` is a list of (old_voice, new_voice, owns), where `owns(seg)` says whether that
    segment is the renamed party's own line, which is the only place `speaker_key` may change.

    One pass, not one per rename, because the pickers make a straight SWAP of the two voices an
    obvious thing to try ("make the co-host the host"). Substituting sequentially turned
    Alexis->Cole followed by Cole->Alexis into every name reading "Alexis", so both people had the
    same name in a script whose entire job is telling them apart.

    Never touches a `commenter` segment: that text is a real quote from a real person.
    """
    pairs = []
    for old_voice, new_voice, owns in renames:
        # voice_name, not a bare VOICE_CATALOG lookup: it spans the Aura-2 catalog too, so an
        # archive script rendered on the previous generation still reports a name rather than
        # skipped all name rewriting, leaving the old correspondent's name in the new voice.
        old_name = config.voice_name(old_voice)
        new_name = config.voice_name(new_voice)
        if not old_name or not new_name or old_name == new_name:
            continue
        pairs.append((old_name, new_name, owns))
    if not pairs:
        return
    # Longest first so a name that is a prefix of another cannot shadow it.
    by_name = {}
    for old_name, new_name, _owns in pairs:
        by_name.setdefault(old_name, new_name)
    pattern = re.compile("|".join(rf"\b{re.escape(n)}\b"
                                 for n in sorted(by_name, key=len, reverse=True)))
    for seg in segments:
        if seg.role == "commenter":
            continue
        seg.text = pattern.sub(lambda m: by_name[m.group(0)], seg.text)
        for old_name, new_name, owns in pairs:
            if seg.speaker_key == old_name and owns(seg):
                seg.speaker_key = new_name
                break


def rewrite_names(segments: List[ScriptSegment], mapping: Dict[str, str], orig_voice: Dict[str, str]) -> None:
    """Slot-level renaming: keep named hand-offs coherent after a voice swap on one slot."""
    renames = []
    for slot, new_voice in mapping.items():
        old_voice = orig_voice.get(slot)
        if not old_voice or old_voice == new_voice:
            continue
        renames.append((old_voice, new_voice, lambda seg, s=slot: _slot_of(seg) == s))
    _rename_all(segments, renames)


def rewrite_role_names(segments: List[ScriptSegment], mapping: Dict[str, str],
                       absorbed: Dict[str, List[str]]) -> None:
    """Role-level renaming, for a role that may have taken over several old seats.

    Ownership is read off the voice `apply_roles` has already written, rather than off the slot:
    after a legacy recast the Guest host's lines are exactly the ones carrying its new voice, and
    their slots are still `ai` / `maker` / `drama`. Keying on the slot would have left three old
    correspondents' names in a script one person now reads.
    """
    renames = []
    for role, new_voice in mapping.items():
        for old_voice in absorbed.get(role, []):
            if old_voice == new_voice:
                continue
            renames.append((old_voice, new_voice, lambda seg, nv=new_voice: seg.voice_id == nv))
    _rename_all(segments, renames)


def recast(episode_id: str, mapping: Dict[str, str], log=print) -> Episode:
    """Re-render `episode_id` with `mapping` applied. Writes a `<id>-recast` episode.

    `mapping` is keyed by ROLE (`anchor` / `cohost`). Validated here as well as at the endpoint, so
    the CLI, a test and a future caller all get the same refusals: this function spends real money
    on Flux calls, and it is the last place that can say no for free.
    """
    from . import pipeline  # avoid an import cycle at module load
    mapping = validate_mapping(mapping)
    segments = _load_segments(episode_id)
    # Say out loud when a role is taking over more than its own seat, because that is a change to
    # the SHAPE of the episode and not just its voices. The page says the same thing before the
    # click; this is for the CLI and the server log, which are the other two ways in.
    for role, pairs in role_takeovers(segments).items():
        if role in mapping:
            log(f"{ROLE_LABELS[role]} takes over " + ", ".join(
                f"{SLOT_LABELS.get(slot, slot)} ({config.voice_name(v) or v})" for slot, v in pairs))
    absorbed = apply_roles(segments, mapping)
    rewrite_role_names(segments, mapping, absorbed)
    meta = _load_episode_meta(episode_id)
    new_id = f"{episode_id}-recast"
    log(f"Recasting {episode_id} -> {new_id} with {mapping}")
    return pipeline.render_recast(
        segments,
        original_id=episode_id,
        episode_id=new_id,
        title=meta.get("title", episode_id) + " (recast)",
        source_items=meta.get("source_items", []),
        cast=active_cast(),  # call-time, never captured at import; see cast.active_cast
        edition=meta.get("edition", ""),
        summary=meta.get("summary", ""),
        log=log,
    )


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hn_radio.recast")
    parser.add_argument("episode", help="episode id to recast")
    for role in ROLES:
        parser.add_argument(f"--{role}", help=f"voice id for the {ROLE_LABELS[role]}")
    args = parser.parse_args(argv)

    mapping = {role: getattr(args, role) for role in ROLES if getattr(args, role)}
    if not mapping:
        print("No roles given. Example: --anchor flux-cole-en --cohost flux-sharon-en",
              file=sys.stderr)
        return 2
    try:
        mapping = validate_mapping(mapping)
    except ValueError as e:
        print(f"{e}\nChoose from: {', '.join(selectable_voices())}", file=sys.stderr)
        return 2
    ep = recast(args.episode, mapping)
    print(f"\nPlay it:  open {ep.audio_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
