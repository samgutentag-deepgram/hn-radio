"""WebVTT transcripts, built from the script's own start times.

The smallest module in the package on purpose. A VTT cue is a time range plus a line of text,
and the script already knows both: `pacing` and `music` compute every segment's `start_seconds`,
so this only has to format them.

That dependency is worth stating: if the audio is re-paced or re-musicked without recomputing
`start_seconds`, the transcript drifts out of sync and nothing here can tell.
"""

from __future__ import annotations



def _vtt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def build_vtt(episode) -> str:
    """A WebVTT transcript from the script (each segment's start time, speaker, and words)."""
    segs = episode.segments
    lines = ["WEBVTT", ""]
    for i, seg in enumerate(segs):
        start = float(seg.start_seconds or 0.0)
        nxt = segs[i + 1].start_seconds if i + 1 < len(segs) else None
        end = float(nxt) if nxt else float(episode.duration_seconds)
        if end <= start:
            end = start + 2.0
        who = ("@" + seg.speaker_key) if seg.role == "commenter" else seg.speaker_key
        lines += [f"{_vtt_ts(start)} --> {_vtt_ts(end)}", f"{who}: {seg.text}", ""]
    return "\n".join(lines)
