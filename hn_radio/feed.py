"""The podcast feed: RSS 2.0 plus the show notes that go inside it.

One job: turn every episode.json on disk into a feed a real podcast app can subscribe to.
Show notes live here rather than in a page module because they are not a page -- they are the
`<description>` of an RSS item, and `_rss_item` is their only caller.

Two URL shapes matter and they are not the same:
  - the ARTIFACT root, `{site_base_url()}/<id>/`, where the audio, chapters and transcript live
  - the PAGE, `{app}/episode.html?id=<id>`, where a human should land

Getting those two confused is what put a 404 in every published `<link>`. The
rule that sorts them: an `<enclosure>`, `<podcast:*>` url or `<image><url>` is an ASSET and lives
under the artifact root; a `<link>` is somewhere a person clicks and lives under the app root.
`<channel><image>` holds one of each, which is why the two halves of that one line disagree.
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from . import config
from .models import is_recast

# A recast is a voice-comparison render of an existing episode, not a new episode, so it never
# enters the feed. `build_manifest` applies the same rule; they are the only two enumerators
# left, and a test asserts they agree.


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _join_line(data: dict) -> str:
    """"Join {host} and {cohost} as they discuss..." -- names the voices reading the episode.

    Derived from `segments` rather than stored separately: every episode.json already carries
    each line's `role` and `speaker_key`, and a regular's display name IS its voice name (see
    `cast.Desk.name`, e.g. "Alexis" for `flux-alexis-en`), so there is no separate "voice name" to
    look up. The first "anchor" segment names the host; the first "desk" segment names the
    co-host. A solo cast (no "desk" segment at all, `custom.py` can build one) gets no co-host
    named rather than a made-up one.
    """
    segments = data.get("segments", [])
    host = next((s.get("speaker_key") for s in segments
                if s.get("role") == "anchor" and s.get("speaker_key")), "")
    cohost = next((s.get("speaker_key") for s in segments
                   if s.get("role") == "desk" and s.get("speaker_key")), "")
    title = data.get("title", "")
    if not host or not title:
        return ""
    who = f"{html.escape(host)} and {html.escape(cohost)}" if cohost else html.escape(host)
    return f"Join {who} as they discuss today's top stories: {html.escape(title)}."


def build_show_notes(data: dict, chapters: list) -> str:
    """HTML show notes: the summary, then each story with its submitter, links and timestamp.

    The byline is where `Story.author` surfaces. It was carried on the model and read by nothing,
    and the choice was to surface it rather than delete it: show notes are metadata, so naming
    the submitter here costs nothing, while having a desk say it would mean the show
    attributing a link to whoever posted it. `writers.py:139` still says nothing about authorship.
    """
    start_by_id = {}
    for c in chapters:
        # `or ""`, not `get("url", "")`: the default only applies when the key is ABSENT, and a
        # chapter carrying an explicit `"url": None` would return None and blow up on `in`.
        # `chapters.write_chapters_json` omits the key rather than writing null, so this cannot
        # fire from our own writer -- but the show notes should not be one foreign producer away
        # from a TypeError in the middle of building the feed.
        url = c.get("url") or ""
        if "id=" in url:
            start_by_id[url.rsplit("id=", 1)[-1]] = c["startTime"]

    parts = []
    join_line = _join_line(data)
    if join_line:
        parts.append(f"<p>{join_line}</p>")
    if data.get("summary"):
        parts.append(f"<p>{html.escape(data['summary'])}</p>")
    parts.append("<p><strong>In this episode:</strong></p><ol>")
    for it in data.get("source_items", []):
        hn = it["hn_id"]
        start = start_by_id.get(str(hn))
        stamp = f"[{_fmt_ts(start)}] " if start is not None else ""
        links = f'<a href="https://news.ycombinator.com/item?id={hn}">HN discussion</a>'
        if it.get("url"):
            links += f' &middot; <a href="{html.escape(it["url"])}">source</a>'
        # The submitter, as text. `.get`, because episodes published before this field existed
        # have `source_items` without an `author` key and the feed is rebuilt from those on startup.
        by = f' by {html.escape(it["author"])}' if it.get("author") else ""
        parts.append(f"<li>{stamp}{html.escape(it['title'])}{by} ({links})</li>")
    parts.append("</ol><p><em>Every word read by Deepgram Flux text to speech.</em></p>")
    return "".join(parts)


def _rss_item(data: dict, pub: datetime, audio_name: str, audio_bytes: int, audio_type: str,
              chapters: list) -> str:
    # Two different URLs, and the distinction is load-bearing.
    #
    # `ep_url` is the ARTIFACT root for this episode. Enclosures, chapters and the transcript all
    # live under it, and they resolve as real files.
    #
    # `page_url` is where a HUMAN should land. It used to be `ep_url` too, on the assumption that
    # episodes/ was the web root and `{ep_url}` would serve a generated index.html. That stopped
    # being true when `backend/app.py` began mounting `web/` at `/` and `episodes/` at `/episodes`
    # with no directory-index handling: every `<link>` in the published feed 404'd, verified live
    # on dg-devrel-hn-radio.fly.dev. The app's own episode page is the one that actually works.
    #
    # The GUID keeps `ep_url` VERBATIM even though it no longer resolves, because a guid is
    # episode IDENTITY, not an address. Every subscribed podcast app keys its "already
    # downloaded" state on this string; changing it would present the whole back catalogue as new
    # and re-download it on every device. `isPermaLink` drops to false, which is the honest
    # declaration for an opaque id and does not alter the id itself.
    ep_url = f"{config.site_base_url()}/{data['id']}/"
    page_url = f"{config.site_app_url()}/episode.html?id={data['id']}"
    return f"""    <item>
      <title>{html.escape(data['title'])}</title>
      <guid isPermaLink="false">{ep_url}</guid>
      <link>{page_url}</link>
      <pubDate>{format_datetime(pub)}</pubDate>
      <description><![CDATA[{build_show_notes(data, chapters)}]]></description>
      <itunes:summary>{html.escape(data.get('summary', ''))}</itunes:summary>
      <enclosure url="{ep_url}{audio_name}" length="{audio_bytes}" type="{audio_type}"/>
      <itunes:duration>{int(data.get('duration_seconds', 0))}</itunes:duration>
      <podcast:chapters url="{ep_url}chapters.json" type="application/json+chapters"/>
      <podcast:transcript url="{ep_url}transcript.vtt" type="text/vtt"/>
    </item>"""


def cover_url(episodes_dir: Path) -> str:
    """The channel artwork URL, with a content hash on it so a NEW cover actually reaches players.

    The bytes changing is not enough. Podcast apps cache channel artwork keyed on its URL, and
    Overcast caches it server-side, so a subscriber who unsubscribes and resubscribes gets the
    stored copy rather than a fresh fetch. `cover.png` never changed name, so a cover art update
    was invisible to a client that had already seen the old one -- correct `cache-control:
    no-cache` and a fresh ETag do not help for an asset the client never revalidates.

    Eight hex characters of the file's SHA-256, as `?v=`. A query string rather than a versioned
    filename so the file on the volume keeps one canonical name and nothing has to be copied or
    garbage-collected when the art changes; the static mount ignores the query. It also means this
    is self-solving from now on: regenerate the cover, and the URL changes on the next feed build
    without anyone remembering to bump anything.

    Falls back to the bare URL when the file is absent, which is the case on a fresh checkout, in
    the unit tests, and in the container before the volume is mounted. A missing cover must not
    take the feed build down.
    """
    base = f"{config.site_base_url()}/cover.png"
    cover = episodes_dir / "cover.png"
    if not cover.exists():
        return base
    return f"{base}?v={hashlib.sha256(cover.read_bytes()).hexdigest()[:8]}"


def rebuild_feed(episodes_dir: Path) -> Path:
    """Regenerate feed.xml from every canonical episode.json on disk (recasts excluded). Newest first."""
    art = cover_url(episodes_dir)
    items = []
    for ep_json in sorted(episodes_dir.glob("*/episode.json"), reverse=True):
        data = json.loads(ep_json.read_text())
        if is_recast(data["id"]):
            continue
        mp3 = ep_json.parent / "episode.mp3"
        if mp3.exists():
            audio_name, audio_bytes, audio_type = "episode.mp3", mp3.stat().st_size, "audio/mpeg"
        else:
            wav = ep_json.parent / "episode.wav"
            audio_name, audio_bytes = "episode.wav", wav.stat().st_size if wav.exists() else 0
            audio_type = "audio/wav"
        cj = ep_json.parent / "chapters.json"
        chapters = json.loads(cj.read_text()).get("chapters", []) if cj.exists() else []
        try:
            pub = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
        except ValueError:
            pub = datetime.now(timezone.utc)
        items.append(_rss_item(data, pub, audio_name, audio_bytes, audio_type, chapters))
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>{html.escape(config.SITE_TITLE)}</title>
    <link>{config.site_app_url()}/</link>
    <description>{html.escape(config.SITE_DESCRIPTION)}</description>
    <language>en-us</language>
    <itunes:author>{html.escape(config.SITE_AUTHOR)}</itunes:author>
    <itunes:summary>{html.escape(config.SITE_DESCRIPTION)}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="{html.escape(config.SITE_CATEGORY)}"/>
    <itunes:image href="{art}"/>
    <itunes:owner><itunes:name>{html.escape(config.SITE_AUTHOR)}</itunes:name><itunes:email>{config.SITE_OWNER_EMAIL}</itunes:email></itunes:owner>
    <image><url>{art}</url><title>{html.escape(config.SITE_TITLE)}</title><link>{config.site_app_url()}/</link></image>
{chr(10).join(items)}
  </channel>
</rss>
"""
    feed_path = episodes_dir / "feed.xml"
    feed_path.write_text(feed)
    return feed_path
