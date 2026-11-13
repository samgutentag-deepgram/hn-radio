"""Cover and per-episode art (hn_radio/art.py), and how publishing and the feed use them."""

import json
from datetime import date

import pytest

from hn_radio import art, config, feed, publish

PIL = pytest.importorskip("PIL")


def _episode(dirpath, ep_id="2026-11-12-pm"):
    d = dirpath / ep_id
    d.mkdir(parents=True)
    segs = [
        {"order": 0, "role": "anchor", "speaker_key": "Cole", "text": "Hi.", "voice_id": "flux-cole-en", "desk": "anchor"},
        {"order": 1, "role": "desk", "speaker_key": "Elise", "text": "Hey.", "voice_id": "flux-elise-en", "desk": "cohost"},
        {"order": 2, "role": "desk", "speaker_key": "Heather", "text": "Yo.", "voice_id": "flux-heather-en", "desk": "cohost"},
    ]
    (d / "episode.json").write_text(json.dumps({
        "id": ep_id, "title": "Afternoon Edition: things", "generated_at": "2026-11-12T23:04:10Z",
        "duration_seconds": 300, "summary": "s", "segments": segs}))
    (d / "chapters.json").write_text(json.dumps({"chapters": [
        {"startTime": 0, "title": "Intro"}, {"startTime": 5, "title": "Jensen Huang resigns"},
        {"startTime": 60, "title": "From the threads"}, {"startTime": 90, "title": "Anthropic IPO"}]}))
    return d


def test_the_colorway_is_per_day_and_matches_felt_js():
    # felt.js: Math.floor(Date.UTC(y, m - 1, d) / 86400000) % 5 + 1
    for day in (date(2026, 11, 12), date(2026, 11, 13), date(1970, 1, 1)):
        assert art.colorway(day) == (day - date(1970, 1, 1)).days % 5 + 1
    assert art.colorway(date(2026, 11, 12)) != art.colorway(date(2026, 11, 13))


def test_the_host_sits_first_and_cardless_voices_are_left_out(tmp_path):
    d = _episode(tmp_path)
    segs = json.loads((d / "episode.json").read_text())["segments"]
    assert art.episode_cast(segs)[0] == "flux-cole-en"
    from PIL import Image
    img = Image.new("RGBA", (400, 400))
    seated = art.cluster(img, 200, 200, 300, art.episode_cast(segs))
    assert seated == ["cole", "heather"]          # Elise has no voice card


def test_a_cardless_host_keeps_the_seat(tmp_path):
    from PIL import Image
    seated = art.cluster(Image.new("RGBA", (400, 400)), 200, 200, 300, ["flux-elise-en", "flux-kai-en"])
    assert seated == ["elise", "kai"]


def test_generic_chapters_are_not_listed_as_stories(tmp_path):
    d = _episode(tmp_path)
    assert art._stories(d, {}) == ["Jensen Huang resigns", "Anthropic IPO"]


def test_episode_art_is_written(tmp_path):
    out = art.make_episode_art(_episode(tmp_path))
    from PIL import Image
    assert out.name == "art.jpg" and Image.open(out).size == (1400, 1400)


def test_the_cover_is_3000px_and_small_enough_for_directories(tmp_path):
    out = art.make_cover(tmp_path / config.COVER_FILE)
    from PIL import Image
    assert Image.open(out).size == (3000, 3000)
    assert out.stat().st_size <= 560 * 1024


def test_the_logo_rasterizes_with_its_counters_open():
    mark = art.logo(60)
    assert mark is not None and mark.width > 5 * mark.height


def test_an_art_failure_never_fails_a_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "make_episode_art", lambda d: (_ for _ in ()).throw(OSError("no felt")))
    lines = []
    assert art.try_make_episode_art(tmp_path, log=lines.append) is None
    assert lines and "no art" in lines[0]


def test_publish_draws_art_and_survives_when_it_cannot(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(art, "make_episode_art", lambda d: calls.append(d) or (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(publish, "rebuild_site", lambda d: {})

    class Ep:
        segments = []

        def to_dict(self):
            return {"id": "2026-11-12-pm"}

    out = publish.publish(Ep(), tmp_path / "2026-11-12-pm")
    assert calls and "episode_json" in out


def test_feed_items_carry_their_art_only_when_it_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "site_base_url", lambda: "https://example.test/episodes")
    with_art = _episode(tmp_path, "2026-11-12-pm")
    _episode(tmp_path, "2026-11-12-am")
    (with_art / "art.jpg").write_bytes(b"art")
    xml = feed.rebuild_feed(tmp_path).read_text()
    items = xml.split("<item>")[1:]
    by_id = {("pm" if "2026-11-12-pm/" in i else "am"): i for i in items}
    assert '<itunes:image href="https://example.test/episodes/2026-11-12-pm/art.jpg?v=' in by_id["pm"]
    assert "itunes:image" not in by_id["am"]
