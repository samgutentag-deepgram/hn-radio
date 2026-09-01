"""Chapter markers + the podcast-friendly MP3.

Derive chapters from the finished script (Intro, one per story, the comment theater, Wrap-up), then
render them two ways from the one list: a Podcasting 2.0 `chapters.json` (for the RSS + web page)
and ID3 CHAP frames baked into an MP3 via ffmpeg (for players that read embedded chapters).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List

HN_ITEM = "https://news.ycombinator.com/item?id={id}"
MIN_GAP = 0.5  # chapters must be strictly increasing; drop any that land on top of the previous one


def build_chapters(episode) -> List[dict]:
    """One chapter per beat: Intro, each story (at the anchor's throw), the threads, Wrap-up."""
    segs = episode.segments
    if not segs:
        return []
    stories = {it["hn_id"]: it for it in episode.source_items}

    chapters = [{"startTime": 0.0, "title": "Intro", "url": None}]
    # A SET, not "the previous id". The script permits a callback, a later story referring back
    # to an earlier one, and any such line carries the earlier story's source_hn_id. Comparing
    # against only the previous id minted a second chapter with the same title every time the
    # script looked back, so one early episode shipped 8 chapters for 3 stories with two titles
    # appearing twice. First occurrence opens the chapter; later references never do.
    seen_stories = set()
    commenter_start = None
    for seg in segs:
        start = float(seg.start_seconds or 0.0)
        if seg.role == "commenter" and commenter_start is None:
            commenter_start = start
        hn = seg.source_hn_id
        if hn in stories and hn not in seen_stories:
            seen_stories.add(hn)
            chapters.append({"startTime": start, "title": stories[hn]["title"],
                             "url": HN_ITEM.format(id=hn)})
    if commenter_start is not None:
        chapters.append({"startTime": commenter_start, "title": "From the threads", "url": None})
    chapters.append({"startTime": float(segs[-1].start_seconds or 0.0), "title": "Wrap-up", "url": None})

    chapters.sort(key=lambda c: c["startTime"])
    cleaned: List[dict] = []
    for c in chapters:
        if cleaned and c["startTime"] <= cleaned[-1]["startTime"] + MIN_GAP:
            continue
        cleaned.append(c)
    return cleaned


def write_chapters_json(chapters: List[dict], out_dir: Path) -> Path:
    """Podcasting 2.0 chapters file (application/json+chapters)."""
    data = {"version": "1.2.0", "chapters": [
        {"startTime": round(c["startTime"], 2), "title": c["title"],
         **({"url": c["url"]} if c.get("url") else {})} for c in chapters]}
    path = out_dir / "chapters.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _ffmeta_escape(text: str) -> str:
    for a, b in (("\\", "\\\\"), ("=", "\\="), (";", "\\;"), ("#", "\\#")):
        text = text.replace(a, b)
    return text.replace("\n", " ")


def _ffmetadata(chapters: List[dict], duration_s: float) -> str:
    lines = [";FFMETADATA1"]
    for i, c in enumerate(chapters):
        start_ms = int(c["startTime"] * 1000)
        nxt = chapters[i + 1]["startTime"] if i + 1 < len(chapters) else duration_s
        end_ms = max(int(nxt * 1000), start_ms + 1000)
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}",
                  f"title={_ffmeta_escape(c['title'])}"]
    return "\n".join(lines) + "\n"


def to_mp3_with_chapters(wav_path: Path, chapters: List[dict], duration_s: float, mp3_path: Path) -> Path:
    """Transcode the episode WAV to MP3 with the chapters embedded as ID3 CHAP frames (needs ffmpeg)."""
    ffmeta = wav_path.parent / "chapters.ffmeta"
    ffmeta.write_text(_ffmetadata(chapters, duration_s))
    if not wav_path.exists():
        # Worth its own error: the deployed image excludes episode.wav (.dockerignore), so an
        # episode seeded from the image has no source audio on the volume. ffmpeg's own message for
        # this is a bare exit 254 that says nothing useful.
        raise FileNotFoundError(
            f"{wav_path} is missing, so there is no source audio to transcode. Episodes seeded "
            "from the container image have no WAV (see .dockerignore); only episodes generated on "
            "the machine do."
        )
    try:
        proc = subprocess.run(
            # -ar 44100 is load-bearing for distribution, not a quality knob. The pipeline's WAV is
            # 24 kHz (what Deepgram returns), and libmp3lame at 24 kHz emits MPEG-2 Layer III. That
            # is legal MP3 but outside what Apple Podcasts and many clients expect, and such
            # episodes tend to list in a player and then refuse to play. Resampling to 44.1 kHz
            # emits MPEG-1 Layer III instead. Bitrate fixes the file size, so this costs ~nothing.
            # -id3v2_version 3 for the same reason: ffmpeg defaults to v2.4, Apple prefers v2.3.
            ["ffmpeg", "-y", "-i", str(wav_path), "-i", str(ffmeta),
             "-map", "0:a", "-map_metadata", "1", "-c:a", "libmp3lame", "-b:a", "128k",
             "-ar", "44100", "-id3v2_version", "3", str(mp3_path)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            # capture_output hides ffmpeg's diagnostics, and check=True raises without them, so a
            # failure used to surface as an exit code and an argv list with no reason attached.
            # Debugging one over SSH cost a full round trip. Put the last of stderr in the message.
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
            raise RuntimeError(
                f"ffmpeg failed (exit {proc.returncode}) writing {mp3_path.name}:\n{tail}"
            )
    finally:
        ffmeta.unlink(missing_ok=True)
    return mp3_path
