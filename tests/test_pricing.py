"""What an episode costs, and the two ways that number can lie.

The arithmetic is three lines and does not need much defending. These tests are aimed at the two
places a cost figure goes wrong quietly:

  - THE SELF-REFERENCE. The show now says its own cost in its own outro, so the line quoting the
    figure is billable text inside the episode it is describing. `resolve_cost_sentence` solves
    that by iteration, and the property that has to hold is not "it converges" but "it never
    quotes less than the episode actually costs" -- a number that flatters the product is the one
    failure mode worth a test sweep.
  - THE ARCHIVE. 58 episodes aired before pricing existed, and they are priced from the
    `script.json` sitting next to them. That is exact rather than estimated ONLY because the text
    on disk is the text that was POSTed, so the test that matters is that a script's characters
    and a render's characters are the same count.
"""

from __future__ import annotations

import json

import pytest

from hn_radio import manifest, pipeline, pricing, publish
from hn_radio.cast import DEFAULT_CAST
from hn_radio.models import Episode, ScriptSegment


# --- the price list ----------------------------------------------------------------------------

def test_the_published_rates_are_what_the_pricing_page_says():
    """Pins the numbers read off https://deepgram.com/pricing on 2026-09-16.

    A test and not just a constant, because these are the one thing in the module that is a fact
    about the world rather than a consequence of the code. When Deepgram reprices Flux, THIS is
    the failure that should make someone update `PRICING_AS_OF` in the same commit.
    """
    assert pricing.RATES_USD_PER_1K == {"payg": 0.0450, "growth": 0.0405}
    assert pricing.FREE_CREDIT_USD == 200.0
    assert pricing.PRICING_AS_OF == "2026-09-16"


def test_pay_as_you_go_is_the_default_plan(monkeypatch):
    """The figure is aimed at a reader who just signed up, and that reader is on pay-as-you-go."""
    monkeypatch.delenv("HN_RADIO_TTS_PLAN", raising=False)
    assert pricing.plan() == "payg"
    assert pricing.rate_usd_per_1k() == 0.045


def test_an_unknown_plan_falls_back_instead_of_raising(monkeypatch):
    """A typo in a deploy secret must not take the nightly render down over a web-page figure."""
    monkeypatch.setenv("HN_RADIO_TTS_PLAN", "enterprisey")
    assert pricing.plan() == "payg"


def test_growth_can_be_selected(monkeypatch):
    monkeypatch.setenv("HN_RADIO_TTS_PLAN", "growth")
    assert pricing.rate_usd_per_1k() == 0.0405


# --- the arithmetic ----------------------------------------------------------------------------

def test_cost_is_characters_times_the_rate():
    assert pricing.cost_usd(1000) == pytest.approx(0.045)
    assert pricing.cost_usd(5426) == pytest.approx(0.24417)


def test_nothing_is_stripped_before_billing():
    """Whitespace and punctuation are characters the request body carries, so they are billed.

    Guessing they are free is the obvious way to understate a bill, and it would be invisible:
    the number would just be a few percent low on every episode forever.
    """
    assert pricing.characters(["  a  b  "]) == 8
    assert pricing.characters(["one", "two"]) == 6
    assert pricing.characters(["", None]) == 0


def test_script_characters_reads_segments_and_dicts_the_same():
    """The pipeline holds ScriptSegments; the backfill holds dicts off disk. One count, or the
    archive and the live render disagree about what an episode cost."""
    segs = [ScriptSegment(order=0, role="anchor", speaker_key="A", text="hello"),
            ScriptSegment(order=1, role="anchor", speaker_key="A", text="world!")]
    as_dicts = [s.to_dict() for s in segs]
    assert pricing.script_characters(segs) == pricing.script_characters(as_dicts) == 11


def test_a_script_is_billed_for_exactly_what_render_would_send(monkeypatch):
    """The load-bearing claim behind pricing the archive: the text on disk IS the request body.

    `render.render_segment` posts `{"text": seg.text}` and nothing else, so capturing those bodies
    and counting them must match what `script_characters` says about the same segments. If a
    future change ever wraps or pads the text on its way out, this fails and the archive's prices
    stop being exact -- which is the moment to find out, not later.
    """
    from hn_radio import render

    sent = []
    monkeypatch.setattr(render, "post_json_for_bytes",
                        lambda url, body, headers, **kw: sent.append(body["text"]) or b"\x00\x00")
    segs = [ScriptSegment(order=0, role="anchor", speaker_key="A", text="Good morning.",
                          voice_id="flux-alexis-en"),
            ScriptSegment(order=1, role="anchor", speaker_key="A", text="That's the front page.",
                          voice_id="flux-alexis-en")]
    render.render_all(segs, "key")
    assert pricing.characters(sent) == pricing.script_characters(segs)


def test_the_credit_floors_rather_than_rounds():
    """A count rounded up is one episode the reader's credit cannot actually pay for."""
    # $200 at 30 cents is 666.67 episodes. 666 of them are affordable; 667 is not.
    assert pricing.episodes_per_credit(0.30) == 666


def test_a_zero_cost_episode_is_not_an_infinite_supply():
    """An empty script is a broken render, not free episodes forever. No ZeroDivisionError."""
    assert pricing.episodes_per_credit(0.0) == 0
    assert pricing.episodes_per_credit(-1.0) == 0


# --- the cost block ----------------------------------------------------------------------------

def test_the_block_stores_the_rate_beside_the_figure():
    """An episode.json outlives the price list, so a dollar figure alone is not legible later."""
    block = pricing.episode_cost(5000)
    assert block["characters"] == 5000
    assert block["usd"] == pytest.approx(0.225)
    assert block["rate_usd_per_1k"] == 0.045
    assert block["plan"] == "payg"
    assert block["pricing_as_of"] == "2026-09-16"
    assert block["episodes_per_credit"] == 888


def test_billed_characters_default_to_the_whole_script():
    """A fresh render pays for every line, so the two counts agree and the page shows one number."""
    block = pricing.episode_cost(5000)
    assert block["billed_characters"] == block["characters"] == 5000
    assert block["billed_usd"] == block["usd"]


def test_a_cache_hit_lowers_what_the_run_billed_but_not_what_the_episode_costs():
    """The distinction the page has to keep: a recast is cheap, the EPISODE is not.

    Reporting only the billed figure would tell a reader an episode costs four cents. Reporting
    only the script figure would hide the per-segment cache, which is one of the more interesting
    things about how this show is built.
    """
    block = pricing.episode_cost(5000, billed_chars=1000)
    assert block["characters"] == 5000
    assert block["usd"] == pytest.approx(0.225)
    assert block["billed_characters"] == 1000
    assert block["billed_usd"] == pytest.approx(0.045)


# --- saying it out loud ------------------------------------------------------------------------

@pytest.mark.parametrize("n,said", [
    (0, "zero"), (1, "one"), (13, "thirteen"), (20, "twenty"), (22, "twenty-two"),
    (90, "ninety"), (99, "ninety-nine"), (100, "one hundred"), (101, "one hundred one"),
    (890, "eight hundred ninety"), (1000, "one thousand"),
    (1234, "one thousand two hundred thirty-four"),
])
def test_integers_are_read_as_american_english(n, said):
    """No "and" between the hundreds and the tens: "eight hundred ninety", not "...and ninety"."""
    assert pricing.say_int(n) == said


def test_say_int_refuses_a_negative():
    """There is no reading of a negative episode count, so this is a bug, not a number."""
    with pytest.raises(ValueError):
        pricing.say_int(-1)


@pytest.mark.parametrize("usd,said", [
    (0.01, "one cent"), (0.02, "two cents"), (0.2442, "twenty-four cents"),
    (0.2574, "twenty-six cents"), (0.99, "ninety-nine cents"),
    (1.00, "one dollar"), (1.04, "one dollar and four cents"),
    (2.50, "two dollars and fifty cents"),
])
def test_money_is_read_the_way_a_person_says_it(usd, said):
    """Flux reads plain text with no markup, so "$0.26" is not written as digits and hoped over:
    at least one plausible reading of it is "zero point two six dollars"."""
    assert pricing.say_money(usd) == said


@pytest.mark.parametrize("n,rounded", [
    (5, 5), (99, 99), (100, 100), (104, 100), (892, 890), (987, 990), (1992, 2000),
])
def test_speech_rounds_to_something_a_person_would_say(n, rounded):
    """The spoken line hedges with "about", and "about eight hundred ninety-two" attaches a
    precision nobody asked for to a word that disclaims it. The page prints the exact integer."""
    assert pricing.round_for_speech(n) == rounded


# --- the self-reference ------------------------------------------------------------------------

def test_the_spoken_line_names_flux_the_cost_and_the_credit():
    """The three facts the ask asked for, in the copy. Pinned because this is read out loud on
    every episode and a silent rewording is a change to the show, not to a string."""
    text, _total, _usd = pricing.resolve_cost_sentence(5000)
    assert "Deepgram Flux" in text
    assert "text to speech" in text
    assert "cost about" in text
    assert "two hundred dollars in credit" in text
    assert "more episodes like this one" in text


def test_the_returned_total_includes_the_line_itself():
    """The whole point of the fixed point: the figure covers the sentence that states it."""
    base = 5426
    text, total, usd = pricing.resolve_cost_sentence(base)
    assert total == base + len(text)
    assert usd == pytest.approx(pricing.cost_usd(total), abs=0.005)


def test_the_line_quotes_its_own_total_on_a_real_episode_length():
    """A typical episode: the words say the cost of the script INCLUDING these words."""
    text, total, _usd = pricing.resolve_cost_sentence(5426)
    assert pricing.say_money(pricing.cost_usd(total)) in text


@pytest.mark.parametrize("base", list(range(1000, 12001, 137)))
def test_the_line_never_quotes_less_than_the_episode_costs(base):
    """THE property, swept across every plausible episode length including the rounding boundaries.

    Convergence is not the guarantee worth having; honesty in one direction is. The quoted figure
    is rounded to the cent, so a base near a boundary can make the iteration oscillate, and on a
    cycle `resolve_cost_sentence` returns the candidate that OVERSTATES. A show that says it cost
    a cent more than it did is modest about a number nobody can check. One that says a cent less
    is wrong in the direction that flatters the product.
    """
    text, total, _usd = pricing.resolve_cost_sentence(base)
    assert total == base + len(text)
    actual_cents = round(pricing.cost_usd(total) * 100)
    quoted = next(c for c in range(1, 2000)
                  if f"cost about {pricing.say_money(c / 100.0)} to make" in text)
    assert quoted >= actual_cents, f"understated: says {quoted}c, costs {actual_cents}c"
    assert quoted - actual_cents <= 1, f"overstated by more than a cent at base={base}"


def test_pricing_the_script_without_the_line_understates_it():
    """Why the fixed point is not over-engineering. The line is ~290 characters, over a cent, and
    on a typical episode that is the difference between "24 cents" and "26 cents" -- a figure a
    reader sees, not a rounding artifact."""
    base = 5426
    _text, total, usd = pricing.resolve_cost_sentence(base)
    assert pricing.cost_usd(base) < usd
    assert round(pricing.cost_usd(base) * 100) != round(usd * 100)


# --- the pipeline ------------------------------------------------------------------------------

def _script(n_lines=6):
    return [ScriptSegment(order=i, role="anchor", speaker_key="Alexis", desk="anchor",
                          text=f"Line {i} of the show, with enough words to be a real segment.")
            for i in range(n_lines)]


def test_the_cost_line_lands_before_the_sign_off():
    """Second-to-last, not last. The sign-off has closed every episode since the first one, so the
    cost is a footnote BEFORE the goodbye rather than something said after it."""
    segments = _script() + pipeline._outro_segments(DEFAULT_CAST)
    priced = pipeline._with_cost_line(segments, DEFAULT_CAST)
    assert len(priced) == len(segments) + 1
    assert "we'll talk to you tomorrow" in priced[-1].text
    assert "cost about" in priced[-2].text


def test_the_cost_line_is_spoken_by_the_anchor():
    """It is show copy read by the host, not a disembodied announcement, so it needs a seat or
    `voices.assign_voices` drops it through to the v1 hash and picks a voice the show never cast."""
    priced = pipeline._with_cost_line(_script() + pipeline._outro_segments(DEFAULT_CAST),
                                      DEFAULT_CAST)
    line = priced[-2]
    assert line.desk == "anchor"
    assert line.role == "anchor"
    assert line.speaker_key == DEFAULT_CAST.anchor.name


def test_the_cost_line_prices_the_normalized_script():
    """`normalize_segments` turns `HN` into `Hacker News`: eleven billable characters per hit.

    Pricing before that expansion would quote a figure for text that was never sent. This is the
    ordering requirement `_with_cost_line` removes by normalizing itself.
    """
    segments = [ScriptSegment(order=0, role="anchor", speaker_key="Alexis", desk="anchor",
                              text="HN " * 40)] + pipeline._outro_segments(DEFAULT_CAST)
    priced = pipeline._with_cost_line(segments, DEFAULT_CAST)
    assert "Hacker News" in priced[0].text and "HN " not in priced[0].text
    quoted = pricing.say_money(pricing.cost_usd(pricing.script_characters(priced)))
    assert quoted in priced[-2].text


def test_outro_segments_still_returns_exactly_one_segment():
    """Three tests and `scripts/frame_experiment.py` call it directly and read `[0]`. The cost
    line was added as a separate insert precisely so this stayed true."""
    assert len(pipeline._outro_segments(DEFAULT_CAST)) == 1


def test_an_empty_script_does_not_crash_the_insert():
    """`custom.py` can reach the pipeline with no stories picked, and a cost line is not the
    place for an IndexError."""
    priced = pipeline._with_cost_line([], DEFAULT_CAST)
    assert len(priced) == 1 and "cost about" in priced[0].text


# --- the archive -------------------------------------------------------------------------------

def _write_episode(episodes_dir, ep_id, texts, cost=None):
    d = episodes_dir / ep_id
    d.mkdir(parents=True, exist_ok=True)
    segs = [ScriptSegment(order=i, role="anchor", speaker_key="A", text=t)
            for i, t in enumerate(texts)]
    ep = Episode(id=ep_id, title=f"Episode {ep_id}", generated_at="2026-08-01T12:00:00Z",
                 segments=segs, audio_path=str(d / "episode.wav"), source_items=[],
                 duration_seconds=30.0, cost=cost or {})
    (d / "episode.json").write_text(json.dumps(ep.to_dict(), indent=2))
    (d / "script.json").write_text(json.dumps([s.to_dict() for s in segs], indent=2))
    return d


def _stored_cost(d):
    return json.loads((d / "episode.json").read_text()).get("cost") or {}


def test_the_backfill_prices_an_episode_that_aired_before_pricing_existed(tmp_path):
    """The 58-episode case, in miniature: no cost block, but the billed text is right there."""
    d = _write_episode(tmp_path, "2026-08-01", ["a" * 1000, "b" * 1000])
    pricing.backfill(tmp_path)
    cost = _stored_cost(d)
    assert cost["characters"] == 2000
    assert cost["usd"] == pytest.approx(0.09)


def test_the_backfill_is_idempotent(tmp_path):
    """`rebuild_site` calls it on every boot and every publish, so a second pass must be a no-op
    rather than a rewrite -- otherwise every publish dirties every episode.json on the volume."""
    _write_episode(tmp_path, "2026-08-01", ["a" * 1000])
    first = pricing.backfill(tmp_path)
    second = pricing.backfill(tmp_path)
    assert first["written"] == ["2026-08-01"]
    assert second["written"] == [] and second["skipped"] == ["2026-08-01"]


def test_a_block_priced_on_an_old_rate_is_recomputed(tmp_path):
    """A page showing two episodes priced on two different rate cards, with no visible reason, is
    worse than either figure alone."""
    d = _write_episode(tmp_path, "2026-08-01", ["a" * 1000],
                       cost={"characters": 1000, "usd": 0.030, "rate_usd_per_1k": 0.030,
                             "plan": "aura2-era"})
    pricing.backfill(tmp_path)
    assert _stored_cost(d)["rate_usd_per_1k"] == 0.045
    assert _stored_cost(d)["usd"] == pytest.approx(0.045)


def test_a_recompute_preserves_what_a_run_actually_billed(tmp_path):
    """`billed_characters` records what one render paid and cannot be recovered from the script.
    It is the one field in the block that is not derivable, so a reprice must not drop it."""
    d = _write_episode(tmp_path, "2026-08-01-recast", ["a" * 1000],
                       cost={"characters": 1000, "billed_characters": 200, "usd": 0.030,
                             "rate_usd_per_1k": 0.030, "plan": "old"})
    pricing.backfill(tmp_path)
    cost = _stored_cost(d)
    assert cost["billed_characters"] == 200
    assert cost["billed_usd"] == pytest.approx(0.009)
    assert cost["characters"] == 1000


def test_an_unreadable_episode_does_not_take_down_the_site_rebuild(tmp_path):
    """This runs inside `rebuild_site`, which runs on app startup. One corrupt file on the volume
    must not stop the app from serving the other 57 episodes."""
    good = _write_episode(tmp_path, "2026-08-01", ["a" * 1000])
    broken = tmp_path / "2026-08-02"
    broken.mkdir()
    (broken / "episode.json").write_text("{not json")
    (broken / "script.json").write_text("[]")
    result = pricing.backfill(tmp_path)
    assert result["written"] == ["2026-08-01"]
    assert [name for name, _why in result["failed"]] == ["2026-08-02"]
    assert _stored_cost(good)["characters"] == 1000


def test_an_episode_with_no_script_is_reported_not_guessed(tmp_path):
    """No script means no character count. A cost invented for it would be a made-up number on a
    page whose entire purpose is a checkable one."""
    d = tmp_path / "2026-08-01"
    d.mkdir()
    (d / "episode.json").write_text(json.dumps({"id": "2026-08-01"}))
    result = pricing.backfill(tmp_path)
    assert [name for name, _why in result["failed"]] == ["2026-08-01"]
    assert _stored_cost(d) == {}


# --- what the site publishes -------------------------------------------------------------------

def test_rebuild_site_prices_the_archive_and_publishes_it_in_the_manifest(tmp_path):
    """End to end on the path that actually runs: the app's startup hook calls `rebuild_site`, so
    deploying this code is the whole backfill. No migration step to remember."""
    _write_episode(tmp_path, "2026-08-01", ["a" * 2000])
    publish.rebuild_site(tmp_path)
    row = json.loads((tmp_path / "index.json").read_text())["episodes"][0]
    assert row["cost"]["characters"] == 2000
    assert row["cost"]["usd"] == pytest.approx(0.09)
    assert row["cost"]["rate_usd_per_1k"] == 0.045


def test_rebuild_site_still_returns_only_paths(tmp_path):
    """Pinned because the backfill was briefly reported in this dict. Every value here is
    something a caller can open, and three characterization tests assert that set."""
    written = publish.rebuild_site(tmp_path)
    assert sorted(written) == ["feed_xml", "index_json", "voices_json"]
    assert all(isinstance(v, str) for v in written.values())


def test_the_manifest_never_invents_a_cost(tmp_path):
    """An episode that could not be priced publishes `{}`, and the page hides the receipt. A row
    of zeros would read as "this episode was free", which is a claim, not an absence."""
    _write_episode(tmp_path, "2026-08-01", ["a" * 100])
    manifest.build_manifest(tmp_path)   # directly, so no backfill runs first
    row = json.loads((tmp_path / "index.json").read_text())["episodes"][0]
    assert row["cost"] == {}


# --- the render path ---------------------------------------------------------------------------

def _finalized(tmp_path, monkeypatch, segments, **kw):
    """Run the real `_finalize` against a tmp episodes dir. No network, no renderer, no ffmpeg.

    The MP3 step is stubbed rather than skipped-if-ffmpeg-is-missing (the pattern in
    test_music.py), because nothing here is about audio: these two tests exist to prove the cost
    block reaches `episode.json`, and making that coverage conditional on a system binary is how
    it silently stops running in CI.
    """
    from hn_radio import chapters as chapters_mod, config as cfg, status

    monkeypatch.setattr(cfg, "EPISODES_DIR", tmp_path)
    for name in ("begin", "stage", "done"):
        monkeypatch.setattr(status, name, lambda *a, **k: None)
    monkeypatch.setattr(chapters_mod, "to_mp3_with_chapters", lambda *a, **k: None)

    for s in segments:
        s.voice_id = "flux-alexis-en"
    # A tenth of a second of silence per segment: enough for pacing and stitching to be real.
    pcm = [b"\x00\x00" * 2400 for _ in segments]
    episode = pipeline._finalize(
        segments, pcm, episode_id="test-ep", title="t", source_items=[],
        edition="frontpage", with_music=False, log=lambda *a: None, **kw)
    return episode, tmp_path / "test-ep"


def test_a_finished_episode_carries_its_cost_into_episode_json(tmp_path, monkeypatch):
    """The figure is written at render time by the only code that knows what the run billed."""
    segments = _script(4)
    expected = pricing.script_characters(segments)
    episode, out = _finalized(tmp_path, monkeypatch, segments)

    assert episode.cost["characters"] == expected
    on_disk = json.loads((out / "episode.json").read_text())["cost"]
    assert on_disk["characters"] == expected
    assert on_disk["usd"] == pytest.approx(pricing.cost_usd(expected))
    assert on_disk["rate_usd_per_1k"] == 0.045


def test_a_recast_records_both_what_it_billed_and_what_the_episode_costs(tmp_path, monkeypatch):
    """`render_recast` passes the characters it actually re-rendered. Both numbers survive to disk,
    because the run being cheap and the episode being cheap are different claims."""
    segments = _script(4)
    full = pricing.script_characters(segments)
    episode, out = _finalized(tmp_path, monkeypatch, segments, billed_chars=120)

    on_disk = json.loads((out / "episode.json").read_text())["cost"]
    assert on_disk["characters"] == full
    assert on_disk["billed_characters"] == 120
    assert on_disk["billed_usd"] == pytest.approx(pricing.cost_usd(120))
    assert on_disk["billed_usd"] < on_disk["usd"]


def test_the_backfill_does_not_overwrite_a_fresh_render(tmp_path, monkeypatch):
    """The two writers of the `cost` block have to agree, or every publish flips the value.

    `_finalize` writes it, then `publish.publish` calls `rebuild_site`, which runs the backfill
    over the episode that was just written. The backfill must recognize that block as current and
    leave `billed_characters` alone.
    """
    segments = _script(4)
    _finalized(tmp_path, monkeypatch, segments, billed_chars=120)
    before = json.loads((tmp_path / "test-ep" / "episode.json").read_text())["cost"]

    result = pricing.backfill(tmp_path)
    after = json.loads((tmp_path / "test-ep" / "episode.json").read_text())["cost"]
    assert result["skipped"] == ["test-ep"]
    assert after == before
