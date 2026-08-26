"""Stage 3a - Voice assignment. Give each speaker a distinct, consistent voice.

`assign_voices` is what the panel pipeline calls. Three branches, in order: a voice already
pinned on the segment wins, then the episode Cast's voice for that segment's seat, then the v1
`assign_voice` rule below.

BEWARE the third branch. It is UNREACHABLE in the show that ships, because every regular's line
carries a seat tag and every performed comment already has a voice pinned by the writer. Its
remaining callers are the v1 `--legacy` path (`pipeline.run`, which uses `TemplateAssembler` and
has no cast at all) and the assertions in tests/test_voices.py.

WHAT CHANGED ON 2026-08-20, because this docstring has been wrong in both directions before and
the code is always the answer.

The show went two-person: a consistent host and a rotating co-host, who between them also perform
the HN comments. So the SHOW no longer calls `guest_voice_for` at all. A performed comment is
pinned with the host's or the co-host's voice by the writer (see `writers.PanelWriter`,
`writers.ClaudeWriter._to_segments`), and keeps the commenter's real username in `speaker_key`.
Sam's reasoning, verbatim: "we're kind of removing some functionality from the hashing of
usernames, but we're just trying to simplify things."

`guest_voice_for` WAS KEPT ON THREE STATED GROUNDS, AND IT IS NOW DELETED (2026-08-22). Two of the
three grounds were false when they were written, which is worth recording because they read as
solid:
  - "recast still has a guest slot, so a listener can put a distinct voice back on quoted
    comments." FALSE. `recast.ROLES` is `("anchor", "cohost")` and `validate_mapping` raises on
    anything else, so no page, endpoint or CLI could ever map a guest voice. The only code that
    could was `recast.apply_mapping`, which had no production caller and is deleted in the same
    change.
  - "the listener-chosen quote voice needs it." FALSE. That voice lives in custom editions' `drama`
    slot, defaulted from the guest pool, and that path never called this function either.
  - "the rule itself is the only written record of how that used to work." TRUE, and that is what
    this docstring is for. The rule was: hash the username, then walk past voices already used
    this episode. Stability wherever it is free, distinctness inside one episode when the two
    conflict.

`config.GUEST_VOICES` is UNAFFECTED and still live: it is the recast picker's `presets.flux.guest`
and `scripts/voice_preview.py` reads it. Only the hash-and-walk function is gone. Bullet 1 of the
old rationale was really justifying the POOL, which is independently used.

Earlier history, kept because it is the same lesson twice. This docstring once claimed the
recurring-character effect was live when it was not: the writers assigned guest voices by
POSITION, cycling `guest_i % len(pool)` in order of appearance, so the first commenter in every
episode got the same voice whoever they were. That was corrected on 2026-08-12, and the effect
was made real the same day, in `guest_voice_for`.
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

from . import config
from .models import ScriptSegment


def assign_voice(role: str, speaker_key: str) -> str:
    """v1 assignment: fixed host voice, commenters hashed into the pool."""
    if role == "host":
        return config.host_voice()
    digest = hashlib.sha256(speaker_key.encode("utf-8")).hexdigest()
    pool = config.commenter_voices()
    index = int(digest, 16) % len(pool)
    return pool[index]


def assign_voices(segments: List[ScriptSegment], cast: Optional["object"] = None) -> List[ScriptSegment]:
    """Fill in `voice_id` on every segment in place, then return the list.

    v2: if a segment carries a `desk` and a `cast` is given, that seat's voice wins.
    Otherwise fall back to the v1 role/hash assignment, so v1 episodes still render.

    The pinned-voice branch is what carries a performed comment's voice: those segments have no
    `desk` (deliberately -- see `writers`), so without the pin they would drop through to the v1
    hash and pick a guest voice the show did not cast.
    """
    for seg in segments:
        if seg.voice_id:
            continue  # respect a pinned voice (e.g. a guest voice for comment theater)
        if cast is not None and seg.desk:
            seg.voice_id = cast.voice_for(seg.desk)
        else:
            seg.voice_id = assign_voice(seg.role, seg.speaker_key)
    return segments
