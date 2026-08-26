"""Stage: text normalization for speech. Applied to script text before rendering.

Deepgram TTS has no server-side pronunciation control (find-and-replace is STT-only; no SSML), so
any "say it this way" fix is done here on the INPUT text. This module does ONLY abbreviation
expansion (e.g. HN -> Hacker News), which is legitimate normalization and keeps display == audio.

It deliberately does NOT do phonetic respelling (e.g. qwen -> "kwen"). Per the PRD, that would be a
pronunciation override and, if ever added, must live in a transparent, published dictionary. This
dict is small and open on purpose.
"""

from __future__ import annotations

import re
from typing import List

from .models import ScriptSegment

# Whole-word abbreviation expansions. Keep this list short and visible.
EXPANSIONS = {
    "HN": "Hacker News",
}

_PATTERNS = [(re.compile(r"\b" + re.escape(k) + r"\b"), v) for k, v in EXPANSIONS.items()]


def normalize_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def normalize_segments(segments: List[ScriptSegment]) -> List[ScriptSegment]:
    """Expand abbreviations in every segment's text, in place. Idempotent."""
    for seg in segments:
        seg.text = normalize_text(seg.text)
    return segments
