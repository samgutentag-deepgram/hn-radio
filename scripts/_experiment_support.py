"""Shared loader for the pace / breath / coldopen experiment scripts.

WHY THIS EXISTS, decided 2026-08-22 (judgment call 18). Four scripts in `scripts/` replay a
rendered episode from its cached PCM to try a different pacing or music policy. Three of them
already shared one `load` by importing it from `breath_experiment`; `pace_experiment` carried its
own copy of both `Seg` and `load`. One shared module beats a script importing a sibling script for
a helper, and it beats a fourth copy.

`load` reads the per-segment cache through `stitch.load_cached_segments` rather than hand-rolling
the walk. Both previous copies hand-rolled it, which meant both carried their own version of the
"do NOT glob the directory" guard: a re-render leaves orphaned .pcm files behind with higher
indices (one episode on disk has 23 files for a 19-segment script), and globbing splices those
strangers into the audio. That guard now lives in exactly one place, where it is also tested.

The counterpart decision: NO `load_cached_script` in `stitch.py`. That module deliberately does not
know what a script segment is, and giving it a script loader to spare these scripts an import
would trade a real boundary for a small convenience.
"""

from __future__ import annotations

import json

from hn_radio import config, pacing, stitch


class Seg:
    """The few script fields pacing and music classify on.

    Not a `ScriptSegment`: rehydrating the full model would pull in validation these replays do
    not need, and the classifiers only read these six fields plus `start_seconds`.

    `start_seconds` starts as None because `music.apply` assigns it. `pace_experiment`'s old copy
    of this class omitted the attribute entirely, which was survivable only because that script
    never reached the music stage.
    """

    def __init__(self, d: dict):
        self.order = d["order"]
        self.role = d.get("role") or ""
        self.speaker_key = d.get("speaker_key") or ""
        self.text = d.get("text") or ""
        self.desk = d.get("desk")
        self.source_hn_id = d.get("source_hn_id")
        self.start_seconds = None


def load(episode_id: str):
    """`(script, pcm, story_ids)` for a rendered episode, read off disk. No network, no key.

    `story_ids` is None when the episode has no `episode.json`, which is different from an empty
    set: None means "unknown, do not classify story changes", empty means "known to have none".
    """
    ep_dir = config.EPISODES_DIR / episode_id
    script = [Seg(d) for d in json.loads((ep_dir / "script.json").read_text())]

    story_ids = None
    ep_json = ep_dir / "episode.json"
    if ep_json.exists():
        items = json.loads(ep_json.read_text()).get("source_items", [])
        story_ids = pacing.story_ids_from(items)

    # Orders from the script, never a glob. See the module docstring.
    pcm = stitch.load_cached_segments(ep_dir / "segments", [s.order for s in script])
    return script, pcm, story_ids
