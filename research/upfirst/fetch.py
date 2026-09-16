"""Pull Up First episodes off the public NPR feed.

Stdlib only, same as hn_radio/ itself. Resumable: an episode already on disk is skipped,
so a re-run after a network failure costs nothing.

The feed carries several shows under one title. Mon-Fri is the three-story briefing with the
regular host pair. Saturday is the same shape but leans on guest hosts, and Sunday is The Sunday
Story, a long-form narrative documentary with none of the briefing's structure. Both would skew
the averages for different reasons, so the default is weekdays only.
"""
import argparse, json, re, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

FEED = "https://feeds.npr.org/510318/podcast.xml"
ITUNES = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
HERE = Path(__file__).parent
UA = "hn-radio-style-research/1.0 (private research; contact sam.gutentag@deepgram.com)"


def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def episodes(raw: bytes):
    """Every <item> in the feed, newest first, as flat dicts."""
    root = ET.fromstring(raw)
    out = []
    for it in root.findall(".//item"):
        enc = it.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        dur = it.findtext("itunes:duration", namespaces=ITUNES) or ""
        out.append({
            "date": pub.date().isoformat(),
            "weekday": pub.strftime("%a"),
            "title": (it.findtext("title") or "").strip(),
            "duration_s": int(dur) if dur.isdigit() else None,
            # Strip NPR's tracking segment; it 302s anyway and the bare URL is stable.
            "url": enc.get("url").split("?")[0],
            "page": (it.findtext("link") or "").strip(),
            # NPR's story id, the key to www.npr.org/transcripts/<id>.
            "story_id": _story_id(it.findtext("link") or ""),
        })
    return out


STORY_ID = re.compile(r"/(nx-s1-[0-9a-z]+|\d{6,})/")


def _story_id(link: str) -> str:
    m = STORY_ID.search(link or "")
    return m.group(1) if m else ""


def classify(ep) -> str:
    """Which of the three shows this is. Day of week is the reliable signal."""
    if ep["weekday"] == "Sun":
        return "sunday-story"
    if ep["weekday"] == "Sat":
        return "saturday"
    return "weekday"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-n", "--count", type=int, default=30,
                   help="how many episodes to download (default 30)")
    p.add_argument("--format", choices=["weekday", "saturday", "sunday-story", "all"],
                   default="weekday")
    p.add_argument("--manifest-only", action="store_true",
                   help="write the manifest, download nothing")
    a = p.parse_args()

    print(f"fetching {FEED}", file=sys.stderr)
    all_eps = episodes(_get(FEED))
    print(f"  {len(all_eps)} items in feed", file=sys.stderr)

    picked = [e for e in all_eps if a.format == "all" or classify(e) == a.format][:a.count]
    for e in picked:
        e["format"] = classify(e)
        e["audio"] = f"audio/{e['date']}.mp3"

    total_min = sum(e["duration_s"] or 0 for e in picked) / 60
    print(f"  selected {len(picked)} {a.format} episodes, {total_min:.0f} min of audio",
          file=sys.stderr)

    (HERE / "manifest.json").write_text(json.dumps(picked, indent=2) + "\n")
    if a.manifest_only:
        return

    for i, e in enumerate(picked, 1):
        dest = HERE / e["audio"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  [{i}/{len(picked)}] {e['date']} cached", file=sys.stderr)
            continue
        try:
            dest.write_bytes(_get(e["url"]))
            print(f"  [{i}/{len(picked)}] {e['date']} {dest.stat().st_size/1e6:.1f}MB",
                  file=sys.stderr)
        except Exception as exc:
            # One bad enclosure must not kill a 30-episode pull.
            print(f"  [{i}/{len(picked)}] {e['date']} FAILED: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
