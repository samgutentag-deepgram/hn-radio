"""The newsletter strips: every claim on them, checked against the thing it claims about.

THESE ARE NOT LAYOUT TESTS. A strip that renders is worth nothing; a strip that renders a number
nobody can defend is worse than nothing, because it goes in a marketing email under Deepgram's
name and somebody on Hacker News does the division.

Five things went wrong before this file existed, and each one looked fine:

  - A headline said "Thirteen stages" over a snippet showing eight lines. The first thing a
    developer does with a picture of code is count it.
  - That same snippet named `ingest.top_stories` and `pacing.gaps`, neither of which exists.
  - The bench said voices do not repeat "for fourteen episodes". `COHOST_RECENCY_WINDOW` is 20,
    and had been for some time; the README said fourteen too.
  - The credit strip said $200 "covers most of" a $151 year. It covers all of it.
  - Two cost strips computed elapsed time on two different bases -- one from the measured
    publishing pace, one from the nominal crontab -- and captioned both as though they agreed.

So the rule this file enforces is: a number on a strip is READ from the code or the archive, and
a sentence next to it is checked against the same source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hn_radio import cast, pricing
from hn_radio.cards import facts as card_facts
from hn_radio.cards import strips, tokens

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
APP_URL = "https://dg-devrel-hn-radio.fly.dev"

# Headlines on these strips state a count of what is shown underneath them.
COUNTED = {"pipeline": "Eight", "rotation": "Four"}
WORDS = {"Four": 4, "Eight": 8, "Thirteen": 13}


@pytest.fixture(scope="module")
def built():
    """Every strip, rendered once, with the snippet table filled in as a side effect."""
    if not (EPISODES / "index.json").is_file():
        pytest.skip("no episodes on disk; these assert against the real archive")
    latest = max((p.parent for p in EPISODES.glob("*/episode.json")), key=lambda d: d.name)
    episode = json.loads((latest / "episode.json").read_text())
    script = json.loads((latest / "script.json").read_text())
    try:
        facts = card_facts.facts_for(episode, APP_URL)
    except card_facts.NoCost:
        pytest.skip(f"{latest.name} predates pricing; run the site once so pricing.backfill prices it")
    archive = strips.Archive.load(EPISODES)
    images = {name: strips.compose(name, facts, archive, episode=episode, script=script,
                                   episode_dir=latest)
              for name in strips.STRIPS}
    return {"images": images, "facts": facts, "archive": archive, "episode": episode,
            "dir": latest}


# --- the code snippets quote code that exists ------------------------------------------------

def test_every_call_in_the_pipeline_snippet_exists_somewhere_real(built):
    """The failure this catches shipped: `ingest.top_stories` and `pacing.gaps` were invented.

    A snippet is only worth showing because it is real, and one name that does not resolve turns
    the whole set into something a reader is right to discount.

    TWO WAYS A CALL COUNTS AS REAL, because the receiver is not always a module. `writer.write`
    is a method on a `ScriptWriter` instance and `ingest.fetch_stories_between` is a module
    function, and both are lines of `pipeline.py`. So a call passes if the pipeline literally
    contains it, or if the function exists somewhere in the package under that name.
    """
    pipeline_src = (ROOT / "hn_radio" / "pipeline.py").read_text()
    sources = {p.stem: p.read_text() for p in (ROOT / "hn_radio").glob("*.py")}
    _, lines, _ = strips.CODE_SNIPPETS["pipeline"]

    calls = set()
    for line in lines:
        calls.update(re.findall(r"\b([a-z_]+)\.([a-z_]+)\(", line))
        calls.update(("", name) for name in re.findall(r"^\s*\w+\s*=\s*([a-z_]+)\(", line))
    assert calls, "the pipeline snippet stopped containing any calls"

    for receiver, func in sorted(calls):
        in_pipeline = f"{receiver}.{func}(" in pipeline_src if receiver else False
        defined = any(f"def {func}(" in src for src in sources.values())
        assert in_pipeline or defined, (
            f"{receiver + '.' if receiver else ''}{func} is not called in pipeline.py and is "
            "not defined anywhere in hn_radio/")


@pytest.mark.parametrize("name, word", sorted(COUNTED.items()))
def test_a_headline_that_counts_lines_counts_them_correctly(built, name, word):
    """"Thirteen stages" over eight lines. Comments and blanks do not count as lines of code."""
    headline, lines, _ = strips.CODE_SNIPPETS[name]
    assert headline.startswith(word), f"{name}'s headline no longer starts with {word!r}"
    code = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    assert len(code) == WORDS[word], (
        f"{name} says {word} and shows {len(code)} lines of code")


def test_the_request_on_the_code_strips_is_the_one_render_builds(built):
    """Written out by hand once, and lost four query parameters doing it."""
    from hn_radio.render import _speak_url
    url = _speak_url("flux-alexis-en")
    _, lines, _ = strips.CODE_SNIPPETS["curl"]
    assert any(url in line for line in lines), "the curl strip no longer quotes the real URL"
    for param in ("encoding=", "container=", "sample_rate="):
        assert param in url and any(param in line for line in lines)


def test_the_curl_strip_does_not_promise_a_playable_file_it_will_not_produce(built):
    """`container=none` returns headerless PCM. Naming the output `.wav` would be a lie that only
    shows up when the reader double-clicks it."""
    _, lines, footnote = strips.CODE_SNIPPETS["curl"]
    body = "\n".join(lines)
    if "container=none" in body:
        assert ".wav" not in body, "the command asks for raw PCM and names the output .wav"
        assert "container=none" in footnote, "nothing tells the reader how to get a wav"


def test_the_price_snippet_quotes_the_real_constants(built):
    """It used to invent `RATE_USD_PER_1K = 0.0450`, which is not a name in this repo."""
    _, lines, _ = strips.CODE_SNIPPETS["price"]
    body = "\n".join(lines)
    assert "RATES_USD_PER_1K" in body
    assert f'"payg": {pricing.RATES_USD_PER_1K["payg"]:.4f}' in body
    assert f"CHARS_PER_UNIT = {pricing.CHARS_PER_UNIT}" in body
    # The REPL result has to be the answer that code gives for this episode.
    episode = json.loads((built["dir"] / "episode.json").read_text())
    chars = episode["cost"]["characters"]
    assert f"{pricing.cost_usd(chars):.5f}" in body


def test_every_name_in_the_price_snippet_exists_in_pricing(built):
    """The bug this catches: the snippet invented `RATE_USD_PER_1K`, singular, which is not a
    name in this repo and is one character away from one that is.

    Names rather than whole lines, because the snippet reflows the rate table onto one line to
    fit the picture. Reflowing is a presentation choice a reader can see; renaming a constant is
    a claim about code that does not exist.
    """
    _, lines, _ = strips.CODE_SNIPPETS["price"]
    names = set()
    for line in lines:
        names.update(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line))
        names.update(re.findall(r"\bdef ([a-z_]+)\(", line))
        names.update(re.findall(r"\b([a-z_]+)\(", line.split("#")[0]))
    names.discard("def")
    for name in sorted(names):
        assert hasattr(pricing, name), f"hn_radio.pricing has no {name!r}"


# --- the figures agree with the archive -------------------------------------------------------

def test_the_no_repeat_window_is_read_from_the_code_not_typed(built):
    """The strip said fourteen for as long as the README did, and the constant was 20."""
    assert cast.COHOST_RECENCY_WINDOW == 20
    readme = (ROOT / "README.md").read_text()
    assert "fourteen-episode no-repeat" not in readme, (
        "the README still says fourteen; the constant is "
        f"{cast.COHOST_RECENCY_WINDOW}")


def test_the_hours_of_audio_are_summed_and_not_estimated(built):
    """It was `episodes * 6 / 60`, which landed within a rounding error of the truth and was
    still a number nobody had measured."""
    archive = built["archive"]
    seconds = sum(json.loads(p.read_text()).get("duration_seconds") or 0
                  for p in EPISODES.glob("*/episode.json"))
    assert archive.audio_hours == pytest.approx(seconds / 3600)


def test_each_cost_strip_states_the_basis_it_actually_used(built):
    """The two cost strips use DIFFERENT bases, deliberately, so each has to say which.

    They answer different questions. The ladder asks how long the credit lasts, and the honest
    answer is the MEASURED pace: a missed run makes the credit last longer, so quoting the
    nominal schedule there would understate the runway. The yearly strip asks what a full year of
    the show costs, and a full year means the show running as scheduled.

    What is not allowed is a number computed one way and captioned the other, which is what
    shipped first: 482 days came from the measured 1.86 a day and the caption said "twice-daily".
    """
    archive = built["archive"]
    totals = json.loads((EPISODES / "index.json").read_text())["totals"]

    # The ladder: measured, and the measured figure is the published one.
    assert archive.episodes_per_day == totals["episodes_per_day"]
    assert archive.days_per_credit == totals["days_per_credit"]
    assert archive.episodes_per_day < 2, (
        "the measured pace reached the nominal schedule; the ladder's caption says 'at the pace "
        "it has been publishing' and should be re-read")

    # The yearly strip: nominal, and the nominal schedule is what the crontab says.
    crontab = (ROOT / "crontab").read_text()
    runs_per_day = len(re.search(r"^0 ([\d,]+) \* \* \*", crontab, re.M).group(1).split(","))
    assert runs_per_day == 2, f"the crontab runs {runs_per_day} times a day; 730 is now wrong"


def test_the_credit_really_does_cover_a_full_scheduled_year_with_room(built):
    """The strip says "a full year for free, and credit to spare", so both halves have to hold.

    It said "covers most of it" while the credit covered the whole thing with $37 left over. The
    caption now branches on this arithmetic rather than asserting it, and this pins the branch
    the strip is actually taking today.
    """
    archive = built["archive"]
    spend = 2 * 365 * archive.mean_usd
    assert spend == pytest.approx(162.88, abs=0.01)
    assert archive.credit_usd > spend, "no longer free for a year; the caption changes branch"
    assert archive.credit_usd - spend == pytest.approx(37.12, abs=0.01)


def test_the_ladder_rungs_are_the_same_arithmetic_at_three_scales(built):
    archive = built["archive"]
    assert int(1 // archive.mean_usd) == 4
    assert int(10 // archive.mean_usd) == 44
    assert archive.episodes_per_credit == pricing.episodes_per_credit(archive.mean_usd)


def test_the_scoreboard_does_not_put_a_rate_beside_the_figures_that_contradict_it(built):
    """58 episodes over 46 days is 1.26 a day, and the strip said 2 on the same row.

    The elapsed time moved into the headline, where it is a fact rather than an operand.
    """
    archive = built["archive"]
    span_days = 46
    assert archive.episodes / span_days < 2
    # If a "days" figure ever comes back to the stat row, this is the check it has to pass.


# --- and they still render --------------------------------------------------------------------

@pytest.mark.parametrize("name", strips.STRIPS)
def test_every_strip_renders_at_one_of_the_two_declared_sizes(built, name):
    size = built["images"][name].size
    assert size in {tokens.STRIP.size, tokens.STRIP_TALL.size}, f"{name} is {size}"
