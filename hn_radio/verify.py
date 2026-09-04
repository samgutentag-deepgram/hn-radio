"""Verification pass: is this a real episode, or a broken one that happens to have rendered?

Written after an episode shipped at 173 seconds. The Claude writer had failed, the
pipeline fell back to `PanelWriter` as designed, and the fallback read a story's raw source text
aloud: a markdown image tag and an S3 URL, spoken by Flux, on the public feed. Every stage had
succeeded. Nothing was checking whether what they produced was a show.

Two checks, at two points in the run, and both raise `VerificationError` so the caller decides
what a failed take costs:

  `gate_script`    BEFORE the render. Text that cannot be spoken: URLs, markdown, HTML. Catching it
                   here saves the Flux spend on a take that is going to be thrown away.
  `gate_duration`  AFTER the stitch, BEFORE publish. A show under `config.MIN_EPISODE_SECONDS` is
                   not the show; every fallback night on record was under three minutes and every
                   Claude night over five. The gate sits before `publish` so a short take never
                   reaches the feed, not even for the minutes it takes to re-run.

`scripts/daily.py` is the caller that re-runs. `run_panel` applies the script gate to every run
because unspeakable text is never right; the duration gate is opt-in (`min_seconds`) because a
one-story `python -m hn_radio --stories 1` is legitimately short.
"""

from __future__ import annotations

import re
from typing import Iterable, List

from .models import ScriptSegment


class VerificationError(RuntimeError):
    """The take rendered, but it is not an episode. `problems` says why, one line each."""

    def __init__(self, problems: Iterable[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# Each is a fingerprint of text that was never meant to be read aloud. High precision on purpose:
# a false positive here throws away a good take and costs a re-run, so these match syntax, never
# vocabulary. "dot com" spoken as words is fine; "https://" is not.
_UNSPEAKABLE = [
    ("url", re.compile(r"https?://\S+|\bwww\.\S+\.\S+", re.I)),
    ("markdown", re.compile(r"!\[[^\]]*\]\(|\]\([^)]*\)|(^|\s)#{1,6}\s|```|\*\*[^*]+\*\*")),
    ("html", re.compile(r"</?[a-z][a-z0-9]*(\s[^>]*)?>|&[a-z]+;|&#\d+;", re.I)),
]


def script_problems(segments: List[ScriptSegment]) -> List[str]:
    """Every segment whose text a listener would hear as garbage, one line per hit."""
    problems = []
    for seg in segments:
        text = seg.text or ""
        for name, pattern in _UNSPEAKABLE:
            m = pattern.search(text)
            if m:
                snippet = text[max(0, m.start() - 20): m.end() + 20].replace("\n", " ")
                problems.append(f"segment {seg.order} ({seg.speaker_key}) has {name} in spoken "
                                f"text: ...{snippet}...")
                break  # one reason per segment is enough to fail the take
    return problems


def gate_script(segments: List[ScriptSegment]) -> None:
    """Raise `VerificationError` if any line is unspeakable. Runs before the paid render."""
    problems = script_problems(segments)
    if problems:
        raise VerificationError(problems)


def gate_duration(duration_seconds: float, min_seconds: float) -> None:
    """Raise `VerificationError` if the stitched episode is shorter than `min_seconds`."""
    if duration_seconds < min_seconds:
        raise VerificationError([
            f"episode is {duration_seconds:.0f}s, under the {min_seconds:.0f}s minimum; a take this "
            f"short means the writer fell back or the render dropped lines"
        ])
