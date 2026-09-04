import json

from hn_radio import config, feed, publish


def test_default_base_url_points_at_a_host_that_serves_audio(monkeypatch):
    """With no HN_RADIO_BASE_URL set, the feed must still yield playable enclosure URLs.

    The old default was https://hn-radio.example.com, which does not resolve. A plain
    `make start` therefore rewrote episodes/feed.xml on startup with dead enclosures:
    podcast players listed every episode (titles/durations parse fine) and then failed
    to play any of them. The other tests in this file set the value, so they never
    covered this path.

    No `importlib.reload` any more. It was here because the value was read at import, so
    changing the environment could not affect the module already loaded -- and reloading
    config hands back a different module object than the rest of the process is using, which
    is a strange thing for the default-value test to be the only user of. `site_base_url()`
    reads on call, so clearing the environment is enough.
    """
    monkeypatch.delenv("HN_RADIO_BASE_URL", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    assert "example.com" not in config.site_base_url()
    assert config.site_base_url() == "http://localhost:8000/episodes"


def test_the_base_url_is_read_at_call_time_not_at_import(tmp_path, monkeypatch):
    """Setting HN_RADIO_BASE_URL must reach the feed without reloading the module.

    `SITE_BASE_URL` was `os.environ.get(...)` evaluated at import, which made it the one setting
    in config.py that a test, a notebook or an in-process caller could not change -- the trap
    `music_enabled`'s docstring names by hand. The workaround was `importlib.reload(config)`,
    which is a different object graph than the one the rest of the process is holding.
    """
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    monkeypatch.setenv("HN_RADIO_BASE_URL", "https://set-after-import.test/episodes")
    d = tmp_path / "2026-08-04"
    d.mkdir()
    (d / "episode.json").write_text(json.dumps({
        "id": "2026-08-04", "title": "T", "generated_at": "2026-08-04T12:00:00Z",
        "duration_seconds": 1, "source_items": []}))
    (d / "episode.mp3").write_bytes(b"\x00")

    xml = feed.rebuild_feed(tmp_path).read_text()
    assert "https://set-after-import.test/episodes/2026-08-04/episode.mp3" in xml
    assert "<link>https://set-after-import.test/</link>" in xml
    assert "localhost:8000" not in xml


def test_the_base_url_falls_back_to_the_dot_env_file(monkeypatch):
    """The other half of the trap: `os.environ.get` never looked at `.env` at all.

    Every other setting in config.py -- the two API keys, DEEPGRAM_API_HOST, HN_RADIO_MUSIC --
    goes through `_read_env_var`, which reads the environment and then the project `.env`. So a
    developer could put HN_RADIO_BASE_URL in `.env` next to their key, exactly as `sample.env`
    invites them to, and be the only setting in that file that did nothing.

    Asserted through `_read_env_var` rather than by writing a real `.env` into the repo root: the
    point is that this reader is the one being used, and a test that creates PROJECT_ROOT/.env
    would clobber the developer's own.
    """
    monkeypatch.delenv("HN_RADIO_BASE_URL", raising=False)
    monkeypatch.setattr(config, "_read_env_var",
                        lambda name: "https://from-dot-env.test/episodes"
                        if name == "HN_RADIO_BASE_URL" else None)
    assert config.site_base_url() == "https://from-dot-env.test/episodes"
    assert config.site_app_url() == "https://from-dot-env.test"


def test_feed_uses_base_url_cover_and_podcast_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    monkeypatch.setenv("HN_RADIO_BASE_URL", "https://example.test/episodes")
    d = tmp_path / "2026-08-04"
    d.mkdir()
    (d / "episode.json").write_text(json.dumps({
        "id": "2026-08-04", "title": "T", "generated_at": "2026-08-04T12:00:00Z",
        "duration_seconds": 300, "summary": "A summary.",
        "source_items": [{"hn_id": 1, "title": "Story", "url": "https://s.example"}],
    }))
    (d / "chapters.json").write_text(json.dumps({"chapters": [
        {"startTime": 0.0, "title": "Intro"},
        {"startTime": 10.0, "title": "Story", "url": "https://news.ycombinator.com/item?id=1"}]}))
    (d / "episode.mp3").write_bytes(b"\x00" * 1000)

    xml = feed.rebuild_feed(tmp_path).read_text()
    assert "hn-radio.example.com" not in xml  # no placeholder host regression
    assert "https://example.test/episodes/2026-08-04/episode.mp3" in xml
    assert 'type="audio/mpeg"' in xml
    assert '<podcast:chapters url="https://example.test/episodes/2026-08-04/chapters.json"' in xml
    assert 'href="https://example.test/episodes/cover.png"' in xml
    assert "<itunes:author" in xml and "<itunes:category" in xml and "<itunes:owner" in xml
    assert "A summary." in xml  # show notes in the item description


def test_feed_excludes_recasts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    for eid in ("2026-08-04", "2026-08-04-recast"):
        d = tmp_path / eid
        d.mkdir()
        (d / "episode.json").write_text(json.dumps({"id": eid, "title": eid, "generated_at": "",
                                                    "duration_seconds": 1, "source_items": []}))
        (d / "episode.mp3").write_bytes(b"\x00")
    xml = feed.rebuild_feed(tmp_path).read_text()
    assert "2026-08-04/</guid>" in xml
    assert "recast" not in xml


def test_build_vtt_transcript():
    from hn_radio.models import Episode, ScriptSegment
    segs = [
        ScriptSegment(order=0, role="anchor", speaker_key="Haley", text="Good morning.", start_seconds=0.0),
        ScriptSegment(order=1, role="commenter", speaker_key="dang", text="Hi.", start_seconds=5.0),
    ]
    ep = Episode(id="x", title="t", generated_at="", segments=segs, audio_path="",
                 source_items=[], duration_seconds=8.0)
    vtt = publish.build_vtt(ep)
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:05.000" in vtt
    assert "Haley: Good morning." in vtt
    assert "@dang: Hi." in vtt                      # commenters get the @ prefix
    assert "00:00:05.000 --> 00:00:08.000" in vtt   # last cue ends at the duration


# --- channel artwork cache-busting ------------------------------------------------------------

def test_the_cover_url_carries_a_content_hash(tmp_path):
    """New bytes at the same URL do not reach a podcast app. Pin that the URL moves with them.

    Overcast caches channel artwork server-side keyed on the URL, so unsubscribing and
    resubscribing returns the stored copy. The 36-orb cover shipped to the same
    `cover.png` path and was invisible to any client that had already seen the old one.
    """
    from hn_radio import feed

    (tmp_path / "cover.png").write_bytes(b"first art")
    first = feed.cover_url(tmp_path)
    assert "cover.png?v=" in first
    assert len(first.rsplit("=", 1)[1]) == 8

    (tmp_path / "cover.png").write_bytes(b"second art")
    assert feed.cover_url(tmp_path) != first, "new bytes must produce a new URL"

    (tmp_path / "cover.png").write_bytes(b"first art")
    assert feed.cover_url(tmp_path) == first, "the hash must be content-addressed, not a counter"


def test_a_missing_cover_does_not_break_the_feed(tmp_path):
    """Absent on a fresh checkout and in the container before the volume mounts."""
    from hn_radio import feed

    url = feed.cover_url(tmp_path)
    assert url.endswith("/cover.png"), url
    assert "?v=" not in url


def test_both_artwork_elements_use_the_same_hashed_url(tmp_path, monkeypatch):
    """`itunes:image` and the RSS `<image><url>` must not disagree about the artwork."""
    import re

    from hn_radio import feed

    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    monkeypatch.setenv("HN_RADIO_BASE_URL", "https://example.test/episodes")
    (tmp_path / "cover.png").write_bytes(b"art bytes")
    d = tmp_path / "2026-08-04"
    d.mkdir()
    (d / "episode.json").write_text(json.dumps({
        "id": "2026-08-04", "title": "T", "generated_at": "2026-08-04T12:00:00Z",
        "duration_seconds": 300, "source_items": [],
    }))
    (d / "episode.mp3").write_bytes(b"\x00" * 1000)

    xml = feed.rebuild_feed(tmp_path).read_text()
    hrefs = re.findall(r'<itunes:image href="([^"]+)"', xml)
    urls = re.findall(r"<image><url>([^<]+)</url>", xml)
    assert hrefs and urls
    assert set(hrefs) == set(urls), (hrefs, urls)
    assert "?v=" in hrefs[0]
