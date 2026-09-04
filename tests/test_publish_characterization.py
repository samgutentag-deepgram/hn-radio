"""Characterization tests for `hn_radio.publish`: pin what it emits TODAY, byte for byte.

Why this file exists. `publish.py` is 568 lines doing four unrelated jobs (HTML pages, RSS, JSON
manifests, WebVTT) and five of its eight public functions had no test at all, including
`build_episode_page`, `build_manifest` and `publish` itself. Everything a listener actually saw
was unverified. Refactoring that safely needs a net first.

These are CHARACTERIZATION tests, not specifications. They assert only that output has not
changed, which is what makes a refactor provably behaviour-preserving.

They were written while `build_index` still existed, and originally pinned two of its bugs on
purpose: it did not filter `-recast` episodes the way `rebuild_feed` and `build_manifest` do, and
it wrote `episodes/index.html`, which stopped being the landing page when `backend/app.py` began
mounting `web/` at `/`. Both are moot now: the function was deleted rather than
refactored, since the cheapest way to fix a page nobody navigates to is to stop building it.
What survives is `test_every_enumerator_agrees_on_what_an_episode_is`, which is the guard against
a third enumerator growing back with its own idea of what counts.

A failure here is not automatically a regression. It means output moved, and you have to say
whether that was intended. To accept an intended change:

    HN_RADIO_UPDATE_GOLDEN=1 uv run pytest tests/test_publish_characterization.py

then READ `git diff tests/golden/` before committing. An unread golden update is worse than no
test, because it launders a change into a green suite.

Determinism is the whole game. Every input is fixed: episode ids, titles, timestamps, durations,
start times, voice ids, and audio file sizes. Config is monkeypatched so site metadata and the
voice catalogs cannot drift, and `api_host` is pinned so an environment variable cannot change
what the cast page emits.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from hn_radio import config, feed, manifest, publish, transcript
from hn_radio.models import Episode, ScriptSegment, is_recast

GOLDEN = Path(__file__).parent / "golden" / "publish"
UPDATING = os.environ.get("HN_RADIO_UPDATE_GOLDEN") == "1"


def _assert_golden(name: str, actual: str):
    """Compare against the recorded output, or record it when explicitly updating."""
    path = GOLDEN / name
    if UPDATING:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual)
        pytest.skip(f"recorded {name} ({len(actual)} chars)")
    if not path.exists():
        raise AssertionError(
            f"no golden for {name}. Record it with:\n"
            f"  HN_RADIO_UPDATE_GOLDEN=1 uv run pytest {__file__}"
        )
    expected = path.read_text()
    if actual != expected:
        # A whole-document diff in a pytest failure is unreadable, so point at the first
        # divergence instead. That is almost always enough to identify what moved.
        for i, (a, b) in enumerate(zip(actual.splitlines(), expected.splitlines()), start=1):
            if a != b:
                raise AssertionError(
                    f"{name} changed at line {i}\n"
                    f"  expected: {b[:160]}\n"
                    f"  actual:   {a[:160]}\n"
                    f"If intended: HN_RADIO_UPDATE_GOLDEN=1 pytest, then read git diff."
                )
        raise AssertionError(
            f"{name} changed in length only: {len(expected.splitlines())} -> "
            f"{len(actual.splitlines())} lines. If intended, re-record and read the diff."
        )


@pytest.fixture(autouse=True)
def frozen_config(monkeypatch):
    """Pin everything `publish` reads from config, so a golden cannot drift on unrelated edits."""
    # Through the environment rather than by replacing an attribute: `site_base_url()` reads on
    # call now, so this pins the golden by the same route a deploy uses.
    monkeypatch.setenv("HN_RADIO_BASE_URL", "https://example.test/episodes")
    monkeypatch.setattr(config, "SITE_TITLE", "HN Radio", raising=False)
    monkeypatch.setattr(config, "SITE_DESCRIPTION", "The front page, read aloud.", raising=False)
    monkeypatch.setattr(config, "SITE_AUTHOR", "Test Author", raising=False)
    monkeypatch.setattr(config, "SITE_CATEGORY", "Technology", raising=False)
    monkeypatch.setattr(config, "SITE_OWNER_EMAIL", "owner@example.test", raising=False)
    # Two Flux voices and one Aura voice is enough to exercise every branch of the pickers
    # without a golden that changes every time the real catalog does.
    monkeypatch.setattr(config, "VOICE_CATALOG", {
        "flux-alexis-en": ("Alexis", "American F, clear and fast"),
        "flux-cole-en": ("Cole", "American M, easy confidence"),
    }, raising=False)
    monkeypatch.setattr(config, "AURA_CATALOG", {
        "aura-2-thalia-en": ("Thalia", "American F, previous gen"),
    }, raising=False)
    monkeypatch.setattr(config, "active_voice_catalog",
                        lambda: dict(config.VOICE_CATALOG), raising=False)
    monkeypatch.setattr(config, "guest_voices", lambda: ["flux-cole-en"], raising=False)
    monkeypatch.setattr(config, "api_host", lambda: "api.deepgram.com", raising=False)


def _segments():
    """A script exercising every row type the page renders: anchor, desk, and commenter."""
    return [
        ScriptSegment(order=0, role="anchor", speaker_key="Alexis", text="Fixed intro line.",
                      desk="anchor", voice_id="flux-alexis-en", start_seconds=0.0),
        ScriptSegment(order=1, role="anchor", speaker_key="Alexis", text="And a short headline.",
                      desk="anchor", source_hn_id=0, voice_id="flux-alexis-en",
                      start_seconds=4.0),
        ScriptSegment(order=2, role="desk", speaker_key="Cole", text="A take with real substance.",
                      desk="drama", source_hn_id=101, voice_id="flux-cole-en", start_seconds=9.5),
        ScriptSegment(order=3, role="commenter", speaker_key="dang",
                      text="A quoted comment with <b>markup</b> & an ampersand.",
                      source_hn_id=9001, voice_id="flux-cole-en", start_seconds=15.25),
        ScriptSegment(order=4, role="anchor", speaker_key="Alexis", text="Fixed outro line.",
                      desk="anchor", voice_id="flux-alexis-en", start_seconds=20.0),
    ]


def _episode(ep_id="2026-08-01"):
    return Episode(
        id=ep_id, title="A title with <angle> & ampersand",
        generated_at="2026-08-01T12:00:00Z", segments=_segments(),
        audio_path=f"episodes/{ep_id}/episode.wav",
        source_items=[
            {"hn_id": 101, "title": "First story & its title", "url": "https://example.test/a"},
            {"hn_id": 202, "title": "Second story, no url", "url": None},
        ],
        duration_seconds=25.0, edition="frontpage",
        summary="A summary with an ampersand & a <tag>.",
    )


@pytest.fixture
def episodes_dir(tmp_path, monkeypatch):
    """Two canonical episodes plus a recast, with fixed-size audio so RSS lengths are stable."""
    root = tmp_path / "episodes"
    monkeypatch.setattr(config, "EPISODES_DIR", root, raising=False)
    for ep_id in ("2026-08-01", "2026-08-02", "2026-08-02-recast"):
        d = root / ep_id
        d.mkdir(parents=True)
        ep = _episode(ep_id)
        (d / "episode.json").write_text(json.dumps(ep.to_dict(), indent=2))
        (d / "script.json").write_text(json.dumps([s.to_dict() for s in ep.segments], indent=2))
        (d / "episode.mp3").write_bytes(b"\0" * 1024)   # fixed size -> stable enclosure length
        # Shaped exactly like `chapters.write_chapters_json` output: it OMITS `url` when falsy
        # rather than writing null (chapters.py:60). The null case is a real boundary but not a
        # real input, so the fixture must not invent it; it is covered on its own in
        # `test_show_notes_survives_an_explicit_null_chapter_url`.
        (d / "chapters.json").write_text(json.dumps({"version": "1.2.0", "chapters": [
            {"startTime": 0.0, "title": "Intro"},
            {"startTime": 9.5, "title": "First story & its title",
             "url": "https://news.ycombinator.com/item?id=101"},
        ]}))
    return root


# --- the HTML pages: everything a listener sees ------------------------------------------------

def test_publish_emits_data_and_feeds_only():
    """`build_episode_page` was deleted; rendering for humans is web/'s job now.

    It generated a full player page as an f-string in publish.py. Nothing in web/ linked to it,
    and its only referrer was the feed's <link>, which 404'd. Two implementations of one view,
    and the dead one was the one the feed advertised. This guard stops it growing back.
    """
    assert not hasattr(publish, "build_episode_page")
    assert not hasattr(publish, "_segment_row")
    assert not hasattr(publish, "_voice_options")


def test_every_enumerator_agrees_on_what_an_episode_is(episodes_dir):
    """There used to be three enumerators and two rules.

    `rebuild_feed` and `build_manifest` skip ids matching `-recast`; `build_index` did not, so a
    recast appeared on the landing page but not in the feed or the manifest. `build_index` was
    deleted (it wrote `episodes/index.html`, which stopped being the landing page
    when `backend/app.py` began mounting `web/` at `/`), which removed the only disagreeing
    enumerator. This test is what stops a third one growing back.
    """
    feed.rebuild_feed(episodes_dir)
    manifest.build_manifest(episodes_dir)

    xml = (episodes_dir / "feed.xml").read_text()
    ids = [e["id"] for e in json.loads((episodes_dir / "index.json").read_text())["episodes"]]

    assert "2026-08-02-recast" not in xml, "the feed must not carry recasts"
    assert "2026-08-02-recast" not in ids, "the manifest must not carry recasts"
    assert sorted(ids) == ["2026-08-01", "2026-08-02"]
    assert not hasattr(publish, "build_index"), (
        "build_index is gone; if it came back it needs the -recast filter the others have"
    )


def test_show_notes_are_unchanged():
    ep = _episode()
    chapters = [{"startTime": 0.0, "title": "Intro"},
                {"startTime": 9.5, "title": "First story",
                 "url": "https://news.ycombinator.com/item?id=101"}]
    _assert_golden("show_notes.html", feed.build_show_notes(ep.to_dict(), chapters))


def test_show_notes_survives_an_explicit_null_chapter_url():
    """An explicit `"url": None` must not crash the show notes.

    Written against `c.get("url", "")`, which only defaults when the key is ABSENT, so a null
    returned None and `"id=" in None` raised TypeError. `feed.py` now reads `c.get("url") or ""`
    and this passes; it stays as the regression guard. Our own writer still cannot produce the
    input -- `chapters.write_chapters_json` omits the key rather than writing null -- so this is
    about some other producer, not a live bug.
    """
    chapters = [{"startTime": 0.0, "title": "Intro", "url": None}]
    feed.build_show_notes(_episode().to_dict(), chapters)


def test_show_notes_print_the_submitter_when_source_items_carry_one():
    """`Story.author` surfaces here or nowhere. Pin both halves of the decision.

    The field was on the model and read by nothing. The 2026-08-22 call was to surface it as
    metadata rather than delete it: a byline in the show notes, and nothing in the script, because
    a desk saying it would mean the show attributing a link to whoever submitted it.

    Both directions matter. Every episode published before that date has `source_items` with no
    `author` key, and the app rebuilds the feed from those on startup, so the absent case must
    render exactly as it did rather than printing "by None".
    """
    ep = _episode().to_dict()
    for it in ep["source_items"]:
        it["author"] = "pg"
    notes = feed.build_show_notes(ep, [])
    assert " by pg (" in notes, notes

    bare = _episode().to_dict()
    for it in bare["source_items"]:
        it.pop("author", None)
    assert "by" not in feed.build_show_notes(bare, []).replace("Every word read by Deepgram", "")


def test_show_notes_escape_a_submitter_name():
    """HN usernames are user data reaching an HTML feed. Same treatment as the title."""
    ep = _episode().to_dict()
    ep["source_items"][0]["author"] = '<script>x</script>'
    notes = feed.build_show_notes(ep, [])
    assert "<script>x</script>" not in notes
    assert "&lt;script&gt;" in notes


def test_show_notes_join_line_names_host_and_cohost():
    notes = feed.build_show_notes(_episode().to_dict(), [])
    assert "Join Alexis and Cole as they discuss today's top stories:" in notes


def test_show_notes_join_line_omits_cohost_on_a_solo_cast():
    """No 'desk' segment at all (a solo custom.py cast) -- name the host, invent no co-host."""
    ep = _episode().to_dict()
    for s in ep["segments"]:
        if s["role"] == "desk":
            s["role"] = "anchor"
    notes = feed.build_show_notes(ep, [])
    assert notes.startswith("<p>Join Alexis as they discuss today's top stories:")
    assert "and" not in notes.split("as they discuss")[0]


def test_show_notes_join_line_escapes_the_title():
    notes = feed.build_show_notes(_episode().to_dict(), [])
    assert "&lt;angle&gt;" in notes
    assert "<angle>" not in notes


# --- the feed ----------------------------------------------------------------------------------

def test_feed_is_unchanged(episodes_dir):
    feed.rebuild_feed(episodes_dir)
    _assert_golden("feed.xml", (episodes_dir / "feed.xml").read_text())


# --- the JSON APIs -----------------------------------------------------------------------------

def test_manifest_is_unchanged(episodes_dir):
    manifest.build_manifest(episodes_dir)
    _assert_golden("index.json", (episodes_dir / "index.json").read_text())


def test_voices_json_is_unchanged(episodes_dir):
    manifest.build_voices_json(episodes_dir)
    _assert_golden("voices.json", (episodes_dir / "voices.json").read_text())


# --- the transcript ----------------------------------------------------------------------------

def test_vtt_is_unchanged():
    _assert_golden("transcript.vtt", publish.build_vtt(_episode()))


# --- the orchestrator --------------------------------------------------------------------------

def test_publish_writes_the_same_set_of_files(episodes_dir):
    """`publish()` had no test at all. Pin WHICH files it writes and what it returns.

    The set grew from three to five deliberately: `publish` used to rewrite only `feed.xml`
    site-wide and now does the whole `rebuild_site` trio, because `python -m hn_radio` was the
    one caller that never asked for the other two and its episodes stayed invisible in `web/`.
    See `test_publish_refreshes_the_whole_site_and_not_only_the_feed` for the reasoning.
    """
    out = episodes_dir / "2026-08-01"
    result = publish.publish(_episode(), out)
    assert sorted(result) == ["episode_json", "feed_xml", "index_json", "script_json",
                              "voices_json"]
    for key, path in result.items():
        assert Path(path).exists(), f"{key} was reported but not written"
    # Paths are relative to the episode dir, except the three site-wide artifacts.
    for key in ("feed_xml", "index_json", "voices_json"):
        assert Path(result[key]).parent == episodes_dir
    assert not (out / "index.html").exists(), "publish must no longer write a page"


def test_publish_refreshes_the_whole_site_and_not_only_the_feed(episodes_dir):
    """One episode's publish must leave the WEB app current too, not just podcast players.

    `publish()` always did a site-wide job: it reaches up to `out_dir.parent` and rewrites
    `feed.xml` for the whole catalogue. It just did one third of one. `index.json` and
    `voices.json` are the other two site-wide artifacts, and with no build step in `web/` those
    two files ARE the frontend's API -- so an episode missing from them is invisible on the site.

    Every caller that refreshed them did it by calling `rebuild_site` itself: the app's startup
    hook, `scripts/daily.py`, `scripts/backfill.py`, `scripts/add_chapters.py`,
    `scripts/build_site.py`. `hn_radio/__main__.py` did not, so `python -m hn_radio` shipped an
    episode into every subscribed podcast app and left the site showing yesterday's list.
    `scripts/local_episode.py` carried a hand-written `rebuild_site` call to paper over exactly
    that. Fixing the half-rebuild here is what makes the CLI whole and that workaround redundant.
    """
    out = episodes_dir / "2026-08-01"
    for stale in ("index.json", "voices.json", "feed.xml"):
        assert not (episodes_dir / stale).exists(), "fixture must start with no site artifacts"

    result = publish.publish(_episode(), out)

    for name in ("feed.xml", "index.json", "voices.json"):
        assert (episodes_dir / name).exists(), f"publish left {name} unwritten"
    # Reported, not just written. `publish` documents its return as "the paths written", and a
    # caller checking that dict is how `scripts/build_site.py` prints what it did.
    assert sorted(result) == ["episode_json", "feed_xml", "index_json", "script_json",
                             "voices_json"]
    assert _episode().id in (episodes_dir / "index.json").read_text()


def test_publish_output_is_deterministic(episodes_dir):
    """Two runs on identical input must agree, or a golden test can never be trusted."""
    out = episodes_dir / "2026-08-01"
    publish.publish(_episode(), out)
    first = (out / "episode.json").read_text(), (episodes_dir / "feed.xml").read_text()
    publish.publish(_episode(), out)
    second = (out / "episode.json").read_text(), (episodes_dir / "feed.xml").read_text()
    assert first == second


# --- guards on the goldens themselves ----------------------------------------------------------

def test_no_golden_leaked_a_real_deploy_host():
    """A golden recorded without the frozen fixture would bake a live base URL into the repo.

    Scoped to DEPLOY hosts on purpose. An earlier version of this guard rejected any mention of
    `deepgram.com` and failed on `episode_page.html`, which legitimately hardcodes a link to
    deepgram.com/product/text-to-speech as page copy. That is content, not configuration. What
    must never appear is a host that only exists because someone recorded against a real .env.
    """
    if UPDATING:
        pytest.skip("recording")
    for path in sorted(GOLDEN.glob("*")):
        text = path.read_text()
        leaked = re.findall(r"https?://[\w.-]*(?:fly\.dev|localhost|127\.0\.0\.1)[:\w/.-]*", text)
        assert not leaked, f"{path.name} contains a live deploy host: {leaked[:3]}"
        # api.deepgram.com is the frozen api_host value and belongs in voices.json only.
        hosts = set(re.findall(r"api\.deepgram\.com", text))
        assert not hosts or path.name == "voices.json", (
            f"{path.name} records an API host ({hosts}); only voices.json should"
        )


def test_the_frozen_base_url_is_what_got_recorded():
    """Positive counterpart: prove the fixture was actually in effect when recording."""
    if UPDATING:
        pytest.skip("recording")
    xml = (GOLDEN / "feed.xml").read_text()
    assert "https://example.test/episodes" in xml, (
        "feed.xml was recorded without the frozen_config fixture"
    )


def test_the_feed_links_humans_at_a_url_that_resolves(episodes_dir):
    """The 404 this branch fixes, asserted directly.

    The feed used to send listeners to `{site_base_url()}/{id}/`, i.e. `/episodes/<id>/`, which
    returns 404 because `episodes/` is mounted without directory indexes. Reproduced live on
    dg-devrel-hn-radio.fly.dev before the fix.
    """
    feed.rebuild_feed(episodes_dir)
    xml = (episodes_dir / "feed.xml").read_text()
    assert "<link>https://example.test/episode.html?id=2026-08-01</link>" in xml
    assert "<link>https://example.test/episodes/2026-08-01/</link>" not in xml
    # the channel link had the same problem
    assert "<link>https://example.test/</link>" in xml


def test_no_show_level_link_points_into_the_artifact_root(episodes_dir):
    """The same 404, one level up: the SHOW's own links, not an episode's.

    `<channel><link>` was fixed with the episode links. `<channel><image><link>`
    was not, and it kept pointing at `{site_base_url()}/`, i.e. `/episodes/`. That is the
    "website" button next to the artwork in a podcast app rather than an episode link, so it is
    cosmetic -- but it is the identical mistake, and the episode version of it shipped broken for
    twelve days, so leaving the last one in place is leaving a live 404 in a published file.

    Probed against the app with `TestClient(backend.app.app)` rather than guessed:
    `GET /` returns 200 text/html, because `web/` is mounted with `html=True` and serves
    `web/index.html` for a directory. `GET /episodes/` returns 404, because the catalog mount has
    no `html=True` and therefore no directory index. So the app root is the one show-level URL
    that resolves.

    Asserted structurally, over every `<link>` in the channel head, so a THIRD show-level link
    added later cannot quietly reintroduce this.
    """
    from xml.etree import ElementTree

    feed.rebuild_feed(episodes_dir)
    channel = ElementTree.parse(episodes_dir / "feed.xml").getroot().find("channel")
    links = [channel.findtext("link")] + [el.findtext("link") for el in channel.iter("image")]
    assert links == ["https://example.test/", "https://example.test/"], links
    for link in links:
        assert "/episodes" not in link, (
            f"a show-level link points into the artifact root ({link}); "
            "episodes/ is mounted without a directory index and 404s"
        )


def test_the_guid_is_unchanged_so_subscribers_do_not_re_download(episodes_dir):
    """A guid is IDENTITY, not an address.

    Every subscribed app keys "already downloaded" on this exact string. It keeps the old
    (now non-resolving) artifact URL on purpose; only isPermaLink drops to false, which
    describes the value honestly without altering it. Changing the value would present the
    entire back catalogue as new on every device.
    """
    feed.rebuild_feed(episodes_dir)
    xml = (episodes_dir / "feed.xml").read_text()
    assert '<guid isPermaLink="false">https://example.test/episodes/2026-08-01/</guid>' in xml


def test_rebuild_site_writes_all_three_artifacts(episodes_dir):
    """One entry point replacing a trio that five callers each spelled out.

    Four of the five listed all three artifacts; `add_chapters.py` listed only two, and it was
    not clear whether skipping voices.json was a decision or an omission. A single function makes
    "refresh the site" one idea, so a fourth artifact later means editing one place.
    """
    written = publish.rebuild_site(episodes_dir)
    assert sorted(written) == ["feed_xml", "index_json", "voices_json"]
    for key, path in written.items():
        assert Path(path).exists(), f"{key} reported but not written"


def test_rebuild_site_needs_no_network_or_key(episodes_dir, monkeypatch):
    """It runs on every app boot, so it must be a pure function of the episodes directory."""
    def explode(*a, **k):
        raise AssertionError("rebuild_site must not open a socket")
    monkeypatch.setattr("urllib.request.urlopen", explode)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    publish.rebuild_site(episodes_dir)


def test_the_split_modules_are_independently_importable():
    """The teaching property: each format module should be readable and usable on its own.

    `publish` is a table of contents that imports all three; none of the three may import
    `publish` back, or the reading order becomes circular and the "open one file and finish it"
    claim stops being true.
    """
    import inspect

    for mod in (feed, manifest, transcript):
        src = inspect.getsource(mod)
        assert "import publish" not in src and "from .publish" not in src, (
            f"{mod.__name__} imports publish; the dependency must point one way"
        )


def test_one_definition_of_what_a_recast_is():
    """The split briefly gave feed.py and manifest.py a private regex each.

    Two definitions of one rule, in exactly the two modules
    `test_every_enumerator_agrees_on_what_an_episode_is` asserts must agree. It now lives on the
    model as a named predicate, because it is a fact about what an episode id means.

    Single-definition is asserted by IDENTITY, not by grepping module source for the regex name.
    A source scan fails the moment a comment mentions the string -- `models.py` already names both
    modules and the rule in prose -- and it cannot see a second rule spelled a different way.
    Identity catches both: any module that reimplements the predicate stops being this object.
    """
    assert is_recast("2026-08-02-recast")
    assert is_recast("2026-08-02-recast-recast"), "the suffix can repeat"
    assert not is_recast("2026-08-02")
    assert not is_recast("2026-08-02-makers")

    for mod in (feed, manifest):
        assert mod.is_recast is is_recast, (
            f"{mod.__name__} has its own recast rule instead of the model's"
        )
