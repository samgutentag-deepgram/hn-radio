"""What an episode costs to render, in one place.

Flux TTS bills per CHARACTER of input text. Every character this show pays for is a
`ScriptSegment.text` handed to `render.render_segment`, so an episode's spend is a pure function
of its own script: no audio measurement, no invoice parsing, no network call. `script.json` is
written after `normalize.normalize_segments` has run, so the text on disk is byte-for-byte the
text that was POSTed -- which is what makes a cost computed from an archived script exact rather
than an estimate, and is the whole reason the backfill below can price 58 episodes that were
rendered before this module existed.

    published rates      https://deepgram.com/pricing   (read 2026-09-16)
    Flux TTS, pay-as-you-go    $0.0450 / 1k characters
    Flux TTS, Growth           $0.0405 / 1k characters
    new-account credit         $200

THREE THINGS THIS DELIBERATELY DOES NOT MODEL, because each would make a published number
wrong in a way a reader could not see:

  - The 1:1 Flux credit match ("for every $1 you spend, we'll add $1 in credits, up to $500",
    through 2026-12-31). It is a promotion with an end date and a cap, so folding it in would
    halve every figure on the site and then silently stop being true in January. The site quotes
    list price.
  - Anything that is not TTS. The nightly run also pays Anthropic for the script and pays nothing
    for HN or for the music beds, which are local files. `cost_usd` is the FLUX line item, and the
    page says so in those words rather than implying it is the total cost of the show.
  - Retries. `_http.post_json_for_bytes` retries a failed segment, and a retry is very likely
    billable. The script cannot know how many fired, so a cost computed from a script is the
    floor: what the episode would cost on a clean run. Stated on the page as "at list price".

Rates are module constants rather than `config.py` entries on purpose. `config` holds the knobs a
RUN reads -- keys, hosts, voices, whether music is on -- and none of those change what a character
costs. This is a published price list with a date on it, and it wants to be read as one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional, Sequence

# --- the price list -------------------------------------------------------------------

PRICING_URL = "https://deepgram.com/pricing"
PRICING_AS_OF = "2026-09-16"          # the day the rates below were read off that page

# USD per 1,000 characters of input text, per plan.
RATES_USD_PER_1K = {
    "payg": 0.0450,
    "growth": 0.0405,
}
DEFAULT_PLAN = "payg"                 # what a reader who just signed up is on

FREE_CREDIT_USD = 200.0               # the signup credit, in dollars

# The unit Deepgram bills in. Named so the arithmetic below reads as the price list.
CHARS_PER_UNIT = 1000


def plan() -> str:
    """Which published plan the site quotes. `payg` unless HN_RADIO_TTS_PLAN says otherwise.

    A function and not a constant, for the reason `config.music_enabled` is a function: read at
    call time, so a Fly secret, a `.env` line and a monkeypatched test all reach it the same way.

    Pay-as-you-go is the default because the number is aimed at a reader who has just made an
    account, and that reader is on pay-as-you-go by definition. An unrecognized value falls back
    rather than raising: a typo in a deploy secret must not take the nightly render down over a
    figure on a web page.
    """
    raw = (_env("HN_RADIO_TTS_PLAN") or "").strip().lower()
    return raw if raw in RATES_USD_PER_1K else DEFAULT_PLAN


def rate_usd_per_1k(plan_name: Optional[str] = None) -> float:
    return RATES_USD_PER_1K[plan_name or plan()]


def _env(name: str) -> Optional[str]:
    """Read an env var, falling back to the project `.env`, the way `config` does.

    Imports `config` lazily. `config` does not import this module and must not have to: it is
    imported by every stage, and a pricing module is not a dependency of rendering audio.
    """
    value = os.environ.get(name)
    if value is not None:
        return value
    try:
        from . import config
        return config._read_env_var(name)
    except Exception:
        return None


# --- the arithmetic ------------------------------------------------------------------

def characters(texts: Iterable[str]) -> int:
    """Billable characters in a run: the length of every string sent to /v2/speak.

    `len()` on the exact request body field, with nothing stripped. Whitespace and punctuation are
    characters the request carries, so guessing that they are free would understate the bill.
    """
    return sum(len(t or "") for t in texts)


def script_characters(segments: Sequence) -> int:
    """Billable characters for a whole script, from segments or from parsed `script.json` dicts.

    Accepts both shapes because the two callers genuinely have different objects: the pipeline
    holds `ScriptSegment`s, and the backfill holds dicts it just read off disk. One function so
    the two can never count differently.
    """
    return characters(
        (seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")) or ""
        for seg in segments
    )


def cost_usd(chars: int, plan_name: Optional[str] = None) -> float:
    """Unrounded dollars for `chars` characters. Round at the point of display, never here."""
    return (chars / CHARS_PER_UNIT) * rate_usd_per_1k(plan_name)


def episodes_per_credit(usd_per_episode: float, credit_usd: float = FREE_CREDIT_USD) -> int:
    """How many episodes at this cost the signup credit pays for.

    FLOOR, not nearest: the credit has to cover every episode counted, and a number rounded up is
    one episode the reader cannot actually render. Zero-cost input returns 0 rather than dividing
    by zero -- an episode with an empty script is a broken render, not an infinite supply.
    """
    if usd_per_episode <= 0:
        return 0
    return int(credit_usd // usd_per_episode)


def episode_cost(chars: int, billed_chars: Optional[int] = None,
                 plan_name: Optional[str] = None) -> dict:
    """The `cost` block that goes into episode.json and index.json.

    TWO CHARACTER COUNTS, and the difference is the point. `characters` is the whole script: what
    this episode costs to make, which is the number the page shows and the show says out loud,
    and it is the same whether the audio came from Flux or from the per-segment cache. `billed`
    is what THIS run actually paid -- lower on a recast or a custom episode, where
    `pipeline.render_recast` reuses any segment whose words and voice both went unchanged.

    Reporting only `billed` would tell a reader a recast is nearly free, which is true of the run
    and false of the episode. Reporting only `characters` would hide the cache, which is one of
    the more interesting things about how this show is built. Both, named.

    `rate` and `plan` are stored ALONGSIDE the dollar figure, not derived from it on read, so a
    cost written today stays legible after the price list changes: a stale entry can be spotted by
    comparing its rate to the current one, which is exactly what `backfill` does.
    """
    billed = chars if billed_chars is None else billed_chars
    name = plan_name or plan()
    usd = cost_usd(chars, name)
    return {
        "characters": chars,
        "billed_characters": billed,
        "usd": round(usd, 6),
        "billed_usd": round(cost_usd(billed, name), 6),
        "rate_usd_per_1k": rate_usd_per_1k(name),
        "plan": name,
        "credit_usd": FREE_CREDIT_USD,
        "episodes_per_credit": episodes_per_credit(usd),
        "pricing_url": PRICING_URL,
        "pricing_as_of": PRICING_AS_OF,
    }


# --- saying a number out loud --------------------------------------------------------
#
# The show reads plain text with no markup and no phonetic hints (see `normalize.py`), so a
# figure that has to be SPOKEN is written as words here rather than handed to Flux as digits and
# hoped over. "$0.26" has at least three plausible readings and one of them is "zero point two
# six dollars", which is not a thing a person says about money.

_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def say_int(n: int) -> str:
    """A non-negative integer as American English words, up to the millions.

    No "and" between hundreds and tens: "eight hundred ninety", which is the American reading.
    Hyphenated tens ("twenty-two") because that is how the words are spelled; Flux reads the
    hyphen as a word boundary, so it costs nothing in the audio and keeps the transcript correct.
    """
    if n < 0:
        raise ValueError(f"say_int is for non-negative integers, got {n}")
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    for size, word in ((1_000_000, "million"), (1_000, "thousand"), (100, "hundred")):
        if n >= size:
            count, rest = divmod(n, size)
            return f"{say_int(count)} {word}" + (f" {say_int(rest)}" if rest else "")
    raise AssertionError("unreachable")  # every n >= 100 is handled by the loop above


def say_money(usd: float) -> str:
    """A dollar amount as words: "twenty-six cents", "one dollar and four cents".

    Rounds to the nearest cent, which is the smallest unit anyone says out loud. An episode of
    this show lands between ten and thirty cents, so the sub-dollar branch is the one that runs;
    the dollar branch exists so a longer edition does not read as "two hundred forty cents".
    """
    cents = int(round(usd * 100))
    if cents < 100:
        return f"{say_int(cents)} cent" + ("" if cents == 1 else "s")
    dollars, rest = divmod(cents, 100)
    said = f"{say_int(dollars)} dollar" + ("" if dollars == 1 else "s")
    if rest:
        said += f" and {say_int(rest)} cent" + ("" if rest == 1 else "s")
    return said


def round_for_speech(n: int) -> int:
    """`n` rounded to something a person would actually say: 892 -> 890, 1992 -> 2000.

    Two significant figures above a hundred. The spoken line is already hedged with "about", and
    "about eight hundred ninety-two episodes" is a precision nobody asked for attached to a word
    that disclaims it. The page prints the exact integer.
    """
    if n < 100:
        return n
    if n < 1000:
        return int(round(n / 10.0) * 10)
    magnitude = 10 ** (len(str(n)) - 2)
    return int(round(n / magnitude) * magnitude)


# --- the line the show says ----------------------------------------------------------

# Read by the anchor immediately before the sign-off. Three facts, in the order the ask put them:
# what rendered the voices, what this episode cost, and what the signup credit buys at that rate.
#
# "about", twice, and both are load-bearing. The cost is rounded to the nearest cent and the
# episode count to two significant figures, so an unhedged figure would be precisely wrong; and
# the count assumes every future episode is the same length as this one, which is an average, not
# a promise.
COST_LINE = (
    "One more thing before we go. Every voice you just heard was rendered by Deepgram Flux "
    "text to speech, and this entire episode cost about {money} to make. Deepgram hands you "
    "{credit} in credit when you sign up, which is about {episodes} more episodes like this one."
)


def cost_sentence(usd: float, credit_usd: float = FREE_CREDIT_USD) -> str:
    """Fill `COST_LINE` for a cost of `usd`. Pure: same input, same words, every time."""
    episodes = round_for_speech(episodes_per_credit(usd, credit_usd))
    return COST_LINE.format(
        money=say_money(usd),
        credit=f"{say_int(int(credit_usd))} dollars",
        episodes=say_int(episodes),
    )


def resolve_cost_sentence(base_characters: int, plan_name: Optional[str] = None,
                          credit_usd: float = FREE_CREDIT_USD) -> tuple:
    """Solve the self-reference: the line quoting the episode's cost is itself billable.

    Returns `(text, total_characters, usd)`, where `total_characters` INCLUDES the returned text
    and `usd` is the cost of that total. So the episode says a number that is true of the episode
    that says it, which is the only version of this line worth shipping.

    A fixed point, found by iteration. The line runs about 260 characters, which is a little over
    a cent, so quoting the cost of the script WITHOUT this line understates the bill by enough to
    move the figure a reader sees -- on a typical 5,400-character episode, 24 cents to 26.

    Two ways out, and the second one matters. Usually the guess stabilizes in two or three passes
    and the returned text is exactly self-consistent. But the quoted value is rounded to the cent,
    so a base that sits near a cent boundary can flip back and forth forever: 25.6 cents reads as
    "26 cents", whose own length pushes the total to 26.4, which reads as "26"... or lands the
    other side and reads as "25". On a cycle this returns the candidate that OVERSTATES, never the
    one that understates. A show that says it cost a cent more than it did is modest about a
    number nobody can check; one that says a cent less is wrong in the direction that flatters the
    product, and this show is pointedly not doing that.
    """
    quoted = cost_usd(base_characters, plan_name)
    seen = {}  # text -> (quoted cents it says, total characters, actual cents that total costs)
    for _ in range(8):
        text = cost_sentence(quoted, credit_usd)
        total = base_characters + len(text)
        actual = cost_usd(total, plan_name)
        says, costs = int(round(quoted * 100)), int(round(actual * 100))
        if says == costs:
            return text, total, actual   # self-consistent: the line quotes its own total
        if text in seen:
            break                        # a repeat means the rounding is oscillating
        seen[text] = (says, total, costs)
        quoted = actual
    # Cycling. Ship the candidate that overstates by the least; if every one of them understates
    # (not reachable by the arithmetic, since a longer line only ever costs more, but this
    # function must not return an understatement even so) ship the dearest.
    ranked = [(t, total, costs, says) for t, (says, total, costs) in seen.items()]
    over = [r for r in ranked if r[3] >= r[2]]
    best = min(over, key=lambda r: r[3] - r[2]) if over else max(ranked, key=lambda r: r[2])
    return best[0], best[1], best[2] / 100.0


# --- pricing the archive -------------------------------------------------------------

def backfill(episodes_dir: Path, force: bool = False, log=None) -> dict:
    """Write a `cost` block into every episode.json on disk that is missing or has a stale one.

    THE BACKFILL IS THE WHOLE REASON COST IS DERIVED FROM `script.json` AND NOT MEASURED AT RENDER
    TIME. 58 episodes aired before this module existed, and every one of them still has the exact
    text it billed for sitting next to it on the volume, so pricing the archive is arithmetic on
    files that are already there rather than a migration anyone has to run by hand.

    Idempotent, and cheap: it reads two small JSON files per episode and writes only when the
    answer changes. `rebuild_site` calls it on every boot and every publish, which is what makes
    a deploy the whole backfill -- there is no separate step to remember, and an episode restored
    from the image gets priced the first time the app comes up.

    STALE means the stored block was written against a different rate or plan than the one in
    force now. Recomputed rather than left alone, because a page showing two episodes priced on
    two different rate cards, with no visible reason, is worse than either number on its own.
    `billed_characters` is PRESERVED across a recompute: it records what a particular run paid and
    cannot be recovered from the script, so it is the one field here that is not derivable.

    Recasts are priced like anything else. They are not in the feed or the manifest
    (`models.is_recast`), but `<id>-recast/episode.json` is served and a reader can reach it, so
    it gets the same block rather than a missing one.
    """
    log = log or (lambda *a: None)
    written, skipped, failed = [], [], []
    current_rate, current_plan = rate_usd_per_1k(), plan()

    for episode_json in sorted(Path(episodes_dir).glob("*/episode.json")):
        script_json = episode_json.parent / "script.json"
        if not script_json.exists():
            failed.append((episode_json.parent.name, "no script.json"))
            continue
        try:
            episode = json.loads(episode_json.read_text())
            segments = json.loads(script_json.read_text())
        except (OSError, json.JSONDecodeError) as e:
            # One unreadable episode must not take down the site rebuild that called this.
            failed.append((episode_json.parent.name, f"{type(e).__name__}: {e}"))
            continue

        existing = episode.get("cost") or {}
        fresh = (existing.get("rate_usd_per_1k") == current_rate
                 and existing.get("plan") == current_plan
                 and existing.get("characters") == script_characters(segments))
        if fresh and not force:
            skipped.append(episode_json.parent.name)
            continue

        episode["cost"] = episode_cost(
            script_characters(segments),
            billed_chars=existing.get("billed_characters"),
        )
        from .jsonio import write_json
        write_json(episode_json, episode)
        written.append(episode_json.parent.name)

    log(f"[pricing] priced {len(written)} episode(s), {len(skipped)} already current"
        + (f", {len(failed)} could not be read" if failed else ""))
    for name, why in failed:
        log(f"[pricing] {name}: {why}")
    return {"written": written, "skipped": skipped, "failed": failed}
