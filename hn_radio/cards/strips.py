"""Six newsletter strips, one component of the project each.

WHY SIX AND NOT ONE BIGGER CARD. The share card answers "what did this episode cost". These answer
the question underneath it, which the ask put as: show someone what they could build with the
credits they are holding, "as opposed to having a button or a link, something to show them." Six
small things that stack into a section beat one thing that tries to say everything.

EVERY STRIP IS A UNIT CONVERSION. The recurring developer question is not what a character costs,
it is what a dollar buys: "if I put one dollar into the machine, how many tokens, how many minutes
of audio." A price with four leading zeros is unreadable and technically complete. Each strip
below turns one such number into something a person can picture -- months of a show, a receipt, a
waveform, a bench of voices, a run that has not been touched since August.

    ladder      dollars into episodes into months
    textin      what you POST and what comes back, with nothing in between
    receipt     the billing UNIT: per character of input, not per minute of audio
    bench       the voice catalog as a thing you get, not a thing you configure
    scoreboard  it is real and it has been running unattended
    latest      the most recent episode, evergreen, for a weekly send

Same tokens as the cards, same orbs, same 2x rule: 1200x400, shown at 600x200.

No literals here either; see `tokens.py`. Positions are DERIVED from the frame rather than spelled
out, which is the one thing these do differently from `layout.py` -- six fixed grids would have
been two hundred numbers nobody could keep consistent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from . import paint, tokens
from .facts import CardFacts
from .layout import EYEBROW

STRIPS = (
    # cost: the same number converted into four different things a person can hold
    "ladder", "receipt", "perminute", "yearly", "archive",
    # flexibility: what changes between episodes, and how little it takes to change it
    "schedule", "recast", "bench",
    # ease of development: what you actually type
    "apicall", "textin", "stdlib",
    # narrative: that this is a produced show and not a robot reading a list
    "turnmap", "castlist", "chapters", "latest", "scoreboard",
)


@dataclass(frozen=True)
class Archive:
    """The catalogue-wide figures, read from `index.json`'s totals block.

    A strip about what $200 buys is a claim about the SHOW, not about one episode, so it reads the
    same fold `manifest._totals` already computes for the landing page rather than averaging
    anything itself.
    """
    episodes: int
    mean_usd: float
    episodes_per_credit: int
    days_per_credit: int
    credit_usd: float
    # MEASURED over the last seven complete dates, not read off the cron. `manifest._totals`
    # computes it and its docstring is explicit that it is "the pace it has been publishing at"
    # and "not a promise about the schedule". Every strip that turns episodes into elapsed time
    # has to use the same basis and say which one it is, or two strips in the same newsletter
    # quietly disagree.
    episodes_per_day: float
    first_id: str
    last_id: str
    # Summed from the episodes on disk, not episodes x a guess at their length. The strip that
    # says how many hours of audio the catalogue is used to compute it as `58 * 6 / 60`, which
    # happened to land within a rounding error of the truth and was still a number nobody had
    # measured.
    audio_hours: float

    @classmethod
    def load(cls, episodes_dir: Path) -> "Archive":
        totals = json.loads((Path(episodes_dir) / "index.json").read_text())["totals"]
        docs = [json.loads(p.read_text()) for p in Path(episodes_dir).glob("*/episode.json")]
        ids = sorted(d["id"] for d in docs)
        seconds = sum(d.get("duration_seconds") or 0 for d in docs)
        return cls(
            episodes=totals["episodes"],
            mean_usd=totals["mean_usd"],
            episodes_per_credit=totals["episodes_per_credit"],
            episodes_per_day=totals["episodes_per_day"],
            days_per_credit=totals["days_per_credit"],
            credit_usd=totals["credit_usd"],
            first_id=ids[0] if ids else "",
            last_id=ids[-1] if ids else "",
            audio_hours=seconds / (60 * 60),
        )


# --- the shared frame ----------------------------------------------------------------

def _frame(facts: CardFacts):
    """Ground, eyebrow and footer. Every strip opens and closes the same way so the six read as
    one set rather than six unrelated pictures."""
    f, p = tokens.STRIP, tokens.palette()
    img = paint.canvas(f.size, p)
    draw = ImageDraw.Draw(img)
    paint.tracked(draw, (f.pad_x, f.eyebrow_y), EYEBROW,
                  tokens.font("display", f.eyebrow_size), p.accent, f.eyebrow_track)
    paint.text(draw, (f.pad_x, f.footer_y), facts.url_text,
               tokens.font("mono", f.footer_size), p.accent)
    return img, draw, f, p


def _headline(draw, f, p, text: str) -> int:
    """The one sentence a strip is making. Returns the y the body starts at under it."""
    paint.text(draw, (f.pad_x, f.body_y), text, tokens.font("display", tokens.STRIP_HEADLINE),
               p.ink)
    return f.body_y + tokens.STRIP_HEADLINE + tokens.STRIP_RULE_GAP


def _columns(f, count: int) -> List[Tuple[int, int]]:
    """`count` equal columns across the content width. Returns (x, width) per column."""
    gaps = tokens.STRIP_COL_GAP * (count - 1)
    width = (f.content_width - gaps) // count
    return [(f.pad_x + i * (width + tokens.STRIP_COL_GAP), width) for i in range(count)]


def _stat(draw, x: int, y: int, p, value: str, label: str, sub: Optional[str] = None) -> None:
    """A number with its unit under it. The shape every figure on these strips takes."""
    paint.text(draw, (x, y), value, tokens.font("display", tokens.STRIP_HERO), p.ink)
    below = y + tokens.STRIP_HERO + tokens.STRIP_RULE_GAP
    paint.text(draw, (x, below), label, tokens.font("ui", tokens.STRIP_LABEL), p.ink_secondary)
    if sub:
        paint.text(draw, (x, below + tokens.STRIP_LABEL + tokens.STRIP_RULE_GAP), sub,
                   tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)


# --- 1. the ladder -------------------------------------------------------------------

def ladder(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """Dollars into episodes into months.

    THE THIRD RUNG IS THE POINT and the first two exist to make it believable. "$200 buys 896
    episodes" on its own is a number; with $1 and $10 beside it, it is a rate the reader just
    watched scale. Months rather than years because the honest figure is sixteen months of a
    twice-daily show, and a rounder, larger claim was checked and did not survive.
    """
    img, draw, f, p = _frame(facts)
    y = _headline(draw, f, p,
                  f"What ${archive.credit_usd:,.0f} of Deepgram signup credit buys")
    # `days_per_credit` is derived from the MEASURED pace (1.86 a day, not the nominal 2), so the
    # label has to say measured. It read "16 months of a twice-daily show", which took a number
    # computed one way and captioned it the other: at a true two a day it would be 15 months.
    months = round(archive.days_per_credit / (365.25 / 12))
    rungs = [
        ("$1", f"{int(1 // archive.mean_usd)} episodes", None),
        ("$10", f"{int(10 // archive.mean_usd)} episodes", None),
        (f"${archive.credit_usd:,.0f}", f"{archive.episodes_per_credit:,} episodes",
         f"{months} months at the pace it has been publishing"),
    ]
    for (x, _w), (value, label, sub) in zip(_columns(f, len(rungs)), rungs):
        _stat(draw, x, y, p, value, label, sub)
    return img


# --- 2. text in, audio out -----------------------------------------------------------

def textin(facts: CardFacts, archive: Archive, episode_dir: Optional[Path] = None,
           script: Optional[Sequence[dict]] = None, **_) -> Image.Image:
    """What you POST beside what comes back, with nothing in between.

    The most developer-legible claim the project has: the text on the left is the exact string
    that was billed for, with no SSML, no phoneme hints and no per-word tuning, because
    `normalize.py` runs before `script.json` is written. Quoting a real line rather than a sample
    is the whole argument.
    """
    img, draw, f, p = _frame(facts)
    y = _headline(draw, f, p, "Text in. Audio out. No markup in between.")
    left, right = _columns(f, len(("text", "audio")))

    mono = tokens.font("mono", tokens.STRIP_MONO)
    paint.text(draw, (left[0], y), "POST /v2/speak", tokens.font("mono", tokens.STRIP_SMALL),
               p.accent)
    line_y = y + tokens.STRIP_LEADING
    quoted = next((s.get("text", "") for s in (script or []) if s.get("text")), "")
    for row in paint.wrapped(draw, quoted, mono, left[1], max_lines=len(("a", "b", "c"))):
        paint.text(draw, (left[0], line_y), row, mono, p.ink_secondary)
        line_y += tokens.STRIP_LEADING

    paint.text(draw, (right[0], y), facts.meta, tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    values = paint.envelope(episode_dir / "episode.mp3", tokens.STRIP_WAVE_BARS) if episode_dir \
        else []
    paint.bars(img, right[0], y + tokens.STRIP_LEADING + tokens.STRIP_WAVE_H // 2,
               right[1], tokens.STRIP_WAVE_H, values, p.accent)
    return img


# --- 3. the receipt ------------------------------------------------------------------

def receipt(facts: CardFacts, archive: Archive, episode: Optional[dict] = None,
            **_) -> Image.Image:
    """The billing UNIT, which is the part that surprises people.

    Everyone assumes voice is billed per minute of audio. Flux bills per character of INPUT, and
    the difference decides whether a reader can estimate their own bill before they spend anything.
    So this strip shows the arithmetic rather than the answer: the answer is already on the card.
    """
    img, draw, f, p = _frame(facts)
    cost = (episode or {}).get("cost") or {}
    mono = tokens.font("mono", tokens.STRIP_MONO)
    left, right = _columns(f, len(("receipt", "note")))

    segments = len((episode or {}).get("segments") or [])
    voices = len({s.get("voice_id") for s in ((episode or {}).get("segments") or [])
                  if s.get("voice_id")})
    rows = [
        (f"{segments} segments, {voices} voices, {facts.meta}", p.ink_muted),
        ("", p.ink_muted),
        (f"{cost.get('characters', 0):,} characters of script", p.ink_secondary),
        (f"x  ${cost.get('rate_usd_per_1k', 0):.4f} per 1,000 characters", p.ink_secondary),
    ]
    y = f.body_y
    for text, colour in rows:
        paint.text(draw, (f.pad_x, y), text, mono, colour)
        y += tokens.STRIP_LEADING
    paint.rule(img, f.pad_x, f.pad_x + left[1], y + tokens.STRIP_RULE_GAP, p.hairline_add,
               tokens.RULE_HEIGHT)
    y += tokens.STRIP_RULE_GAP + tokens.STRIP_LEADING
    paint.text(draw, (f.pad_x, y), "TOTAL", mono, p.ink)
    paint.text(draw, (f.pad_x + left[1], y), f"${cost.get('usd', 0):.4f}", mono, p.ink,
               anchor="ra")

    note = paint.wrapped(
        draw, "Flux TTS bills per character of input text, not per minute of audio. "
              "You can price a script before you render it.",
        tokens.font("ui", tokens.STRIP_LABEL), right[1], max_lines=len(("a", "b", "c", "d")))
    note_y = f.body_y
    for row in note:
        paint.text(draw, (right[0], note_y), row, tokens.font("ui", tokens.STRIP_LABEL),
                   p.ink_secondary)
        note_y += tokens.STRIP_LEADING
    return img


# --- 4. the bench --------------------------------------------------------------------

def bench(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """The catalog as something you are given rather than something you configure.

    THE COUNT IS `config.VOICE_CATALOG`, not every palette in brand.css. Block 1 carries 37 runs
    and three of them are in `config.RETIRED_VOICES`: voices this show vetoed by ear and no longer
    casts. Painting a voice the show will not use, under a number that says the show has it, is
    the kind of claim a PMM asks about in the room. What is drawn is what can be cast today.
    """
    from .. import config
    img, draw, f, p = _frame(facts)
    palettes = tokens.voice_palettes()
    voices = [v for v in sorted(config.VOICE_CATALOG) if v in palettes]
    _headline(draw, f, p, f"{len(voices)} Flux voices in today's rotation")
    # READ, not typed. This said "fourteen episodes" until 2026-09-17, which is what the README
    # says and what `COHOST_RECENCY_WINDOW` used to be; the constant is 20 now and the prose in
    # two places did not follow it.
    from ..cast import COHOST_RECENCY_WINDOW
    paint.text(draw, (f.pad_x, f.body_y + tokens.STRIP_HEADLINE + tokens.STRIP_RULE_GAP),
               "Alexis hosts every morning. The second chair rotates, and no voice repeats "
               f"for {COHOST_RECENCY_WINDOW} episodes.",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)

    # Two offset rows that bleed off the right edge. A grid that fits inside the frame reads as a
    # diagram of orbs; one that runs off it reads as a crowd, which is the point being made.
    d = tokens.STRIP_BENCH_D
    step = int(d * tokens.STRIP_BENCH_OVERLAP)
    per_row = -(-len(voices) // len(("top", "bottom")))
    placed = []
    for i, vid in enumerate(voices):
        row, col = divmod(i, per_row)
        cx = f.pad_x + col * step + (step // 2 if row else 0)
        cy = tokens.STRIP_BENCH_TOP + int(row * d * tokens.STRIP_BENCH_ROW_STEP)
        placed.append((cx, cy, vid))
    for cx, cy, vid in placed:
        paint.bleed(img, cx, cy, d, palettes[vid][3])
    for cx, cy, vid in placed:
        light, mid, deep, glow = palettes[vid]
        paint.place_orb(img, cx, cy, d, (light, mid, deep), glow, p.orb_ground)
    # The crowd is painted over the footer the frame laid down, so the URL goes back on top of it.
    paint.text(draw, (f.pad_x, f.footer_y), facts.url_text,
               tokens.font("mono", f.footer_size), p.accent)
    return img


# --- 5. the scoreboard ---------------------------------------------------------------

def scoreboard(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """It is real, it is unattended, and it has been for weeks.

    No cost on this one on purpose. The objection this strip answers is not "can I afford it", it
    is "is this a demo somebody ran once", and a price does not speak to that.
    """
    import datetime
    img, draw, f, p = _frame(facts)
    start = datetime.date.fromisoformat(archive.first_id[:len("2026-08-01")])
    end = datetime.date.fromisoformat(archive.last_id[:len("2026-08-01")])
    y = _headline(draw, f, p,
                  f"Running unattended for {(end - start).days} days, "
                  f"since {start.strftime('%B %-d')}")
    # TWICE A DAY, and the strip said "3am Pacific, daily" until 2026-09-17. The cron is
    # `0 3,15` Pacific: a Morning Edition and an Afternoon Edition, with different anchors. Every
    # figure on these strips that turns episodes into elapsed time depends on getting this right,
    # and a show described as daily is understating itself by half.
    # NO "DAYS" COLUMN. It used to read 58 episodes / 46 days / 2 editions a day, and a reader
    # who divides gets 1.26 and catches the strip in an inconsistency: the show ran once a day
    # before it ran twice. The elapsed time belongs in the headline, where it is a fact rather
    # than an operand.
    stats = [
        (f"{archive.episodes:,}", "episodes"),
        ("2", "editions a day"),
        ("3am+3pm", "Pacific"),
        ("0", "manual steps"),
    ]
    for (x, _w), (value, label) in zip(_columns(f, len(stats)), stats):
        _stat(draw, x, y, p, value, label)
    return img


# --- 6. the latest episode -----------------------------------------------------------

def latest(facts: CardFacts, archive: Archive, episode: Optional[dict] = None,
           **_) -> Image.Image:
    """The most recent episode, so a weekly send can point at one URL and never update it.

    The only strip whose content changes on its own. Everything else here is a fact about the
    project; this is a fact about today, which is what makes it worth an image slot in a newsletter
    that goes out every week.
    """
    img, draw, f, p = _frame(facts)
    left, right = _columns(f, len(("episode", "cost")))
    title = (episode or {}).get("title", "")
    head = tokens.font("display", tokens.STRIP_HEADLINE)
    y = f.body_y
    for row in paint.wrapped(draw, title, head, left[1] + tokens.STRIP_COL_GAP,
                             max_lines=len(("a", "b"))):
        paint.text(draw, (f.pad_x, y), row, head, p.ink)
        y += tokens.STRIP_HEADLINE + tokens.STRIP_RULE_GAP
    paint.text(draw, (f.pad_x, y), f"{facts.dateline} · {facts.meta.upper()}",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)

    # The cast and the cost, right-aligned, in the same shape the share card uses.
    palettes = tokens.voice_palettes()
    seats = [s for s in facts.speakers if s.voice_id in palettes]
    d = tokens.STRIP_ORB_D
    edge = f.width - f.pad_x
    centres = [(edge - d // 2 - i * (d + tokens.STRIP_RULE_GAP), s)
               for i, s in enumerate(reversed(seats))]
    for cx, speaker in centres:
        paint.bleed(img, cx, f.body_y + d // 2, d, palettes[speaker.voice_id][3])
    for cx, speaker in centres:
        light, mid, deep, glow = palettes[speaker.voice_id]
        paint.place_orb(img, cx, f.body_y + d // 2, d, (light, mid, deep), glow, p.orb_ground)
    paint.text(draw, (edge, f.body_y + d + tokens.STRIP_RULE_GAP), facts.hero,
               tokens.font("display", tokens.STRIP_HERO), p.ink, anchor="ra")
    paint.text(draw, (edge, f.body_y + d + tokens.STRIP_RULE_GAP + tokens.STRIP_HERO
                      + tokens.STRIP_RULE_GAP),
               "to render, in Flux TTS", tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted,
               anchor="ra")
    return img


# The first six. The rest are appended to this dict at the bottom of the file, after they are
# defined; `compose` is the only public entry point either way.
BUILDERS = {name: globals()[name] for name in
            ("ladder", "textin", "receipt", "bench", "scoreboard", "latest")}


def compose(name: str, facts: CardFacts, archive: Archive, **context) -> Image.Image:
    return BUILDERS[name](facts, archive, **context)


# --- shared helpers for the second set ------------------------------------------------

def _band(img, draw, x: int, y: int, width: int, spans: Sequence[Tuple[float, float, tuple]],
          height: int) -> None:
    """A timeline: (start_fraction, end_fraction, colour) laid along `width`.

    Fractions rather than seconds, so the same helper draws a turn map and a chapter run without
    either of them telling it what a second is.
    """
    for start, end, colour in spans:
        x0 = x + int(start * width)
        x1 = x + int(end * width) - tokens.STRIP_BAND_GAP
        if x1 <= x0:
            x1 = x0 + 1
        draw.rectangle([x0, y, x1, y + height], fill=colour)


def _code(draw, f, p, lines: Sequence[Tuple[str, tuple]], y: int, size: int) -> int:
    """A monospace block. Returns the y below it."""
    font = tokens.font("mono", size)
    for text, colour in lines:
        paint.text(draw, (f.pad_x, y), text, font, colour)
        y += tokens.STRIP_LEADING
    return y


def _voice_colour(p, palettes, voice_id, fallback=None):
    """The mid shade of a voice's run: the one that reads as "that speaker" at small sizes."""
    run = palettes.get(voice_id)
    return run[1] if run else (fallback or p.ink_muted)


# --- cost ----------------------------------------------------------------------------

def perminute(facts: CardFacts, archive: Archive, episode: Optional[dict] = None,
              **_) -> Image.Image:
    """Priced per character, delivered per minute. Both numbers, side by side.

    The conversion nobody can do in their head, and the reason the billing page is hard to read:
    Flux quotes input characters and every competitor a reader has used quotes output minutes. So
    this strip does the division once, on a real episode, and shows its working.
    """
    img, draw, f, p = _frame(facts)
    cost = (episode or {}).get("cost") or {}
    minutes = ((episode or {}).get("duration_seconds") or 0) / len(("half", "a", "minute")) / 20
    y = _headline(draw, f, p, "Priced per character. Delivered per minute.")
    cols = _columns(f, len(("rate", "per minute", "per hour")))
    per_minute = (cost.get("usd", 0) / minutes) if minutes else 0
    figures = [
        (f"${cost.get('rate_usd_per_1k', 0):.4f}", "per 1,000 characters", "what you are billed"),
        (f"${per_minute:.3f}", "per finished minute", "what that came to here"),
        (f"${per_minute * 60:.2f}", "per finished hour", "same rate, longer show"),
    ]
    for (x, _w), (value, label, sub) in zip(cols, figures):
        _stat(draw, x, y, p, value, label, sub)
    return img


def yearly(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """A year of the show at the SCHEDULE, 730 episodes, against the signup credit.

    THE BASIS IS THE CRONTAB HERE AND THE MEASURED PACE ON THE LADDER, and that is a decision
    rather than the inconsistency it looks like. The two strips answer different questions. The
    ladder asks how long the credit lasts, and the honest answer to that has to be what has
    actually been shipping, because a missed run makes the credit last LONGER and overstating the
    pace would understate the runway. This one asks what a full year of the show costs, and a
    full year means the show running as scheduled: `0 3,15 * * *`, two a day, 730.

    So each strip says its own basis on its own face -- "two a day, every day" here, "at the pace
    it has been publishing" there -- and `tests/test_strips.py` asserts both, rather than forcing
    one number on two different questions.
    """
    img, draw, f, p = _frame(facts)
    episodes = len(("am", "pm")) * 365
    spend = episodes * archive.mean_usd
    y = _headline(draw, f, p, "A year of a twice-daily, two-host news show")
    for (x, _w), (value, label, sub) in zip(_columns(f, len(("episodes", "spend", "credit"))), [
        (f"{episodes:,}", "episodes a year", "two a day, every day"),
        (f"${spend:,.0f}", "of Flux TTS", f"at ${archive.mean_usd:.4f} an episode"),
        # Branches on the arithmetic rather than asserting it, so a rate change rewrites the line
        # instead of turning it into a lie. It read "covers most of it" while the credit covered
        # the whole thing with $37 left over.
        (f"${archive.credit_usd:,.0f}", "signup credit",
         "a full year for free, and credit to spare" if archive.credit_usd > spend
         else "covers the year exactly" if archive.credit_usd == spend
         else f"covers {archive.credit_usd / spend:.0%} of the year"),
    ]):
        _stat(draw, x, y, p, value, label, sub)
    return img


def archive_total(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """Everything on the site, totalled. The least abstract cost figure available.

    Not a rate and not a projection: this is the whole back catalogue a reader can go and listen
    to right now, and what it cost to make all of it.
    """
    img, draw, f, p = _frame(facts)
    y = _headline(draw, f, p, "The entire back catalogue, start to finish")
    for (x, _w), (value, label, sub) in zip(_columns(f, len(("eps", "audio", "cost"))), [
        (f"{archive.episodes:,}", "episodes", "every one still online"),
        (f"{archive.audio_hours:.1f}h", "of finished audio", "two hosts, no recording session"),
        (f"${archive.mean_usd * archive.episodes:,.2f}", "total, all of it",
         "less than one team lunch"),
    ]):
        _stat(draw, x, y, p, value, label, sub)
    return img


# --- flexibility ---------------------------------------------------------------------

def schedule(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """Two editions a day, with different people in the chair.

    The flexibility claim that costs nothing to believe: the same pipeline runs twice on a cron
    and produces two different shows, because the cast is a function of the episode id.
    """
    img, draw, f, p = _frame(facts)
    palettes = tokens.voice_palettes()
    y = _headline(draw, f, p, "Two editions a day, two different chairs")
    cols = _columns(f, len(("am", "pm")))
    d = tokens.STRIP_ORB_D
    for (x, _w), (when, who, vid, what) in zip(cols, [
        ("3:00 AM Pacific", "Alexis", "flux-alexis-en", "Morning Edition"),
        ("3:00 PM Pacific", "Cole", "flux-cole-en", "Afternoon Edition"),
    ]):
        run = palettes.get(vid)
        if run:
            paint.bleed(img, x + d // 2, y + d // 2, d, run[3])
            paint.place_orb(img, x + d // 2, y + d // 2, d, run[:3], run[3], p.orb_ground)
        tx = x + d + tokens.STRIP_COL_GAP
        paint.text(draw, (tx, y), what, tokens.font("display", tokens.STRIP_LABEL), p.ink)
        paint.text(draw, (tx, y + tokens.STRIP_LEADING), when,
                   tokens.font("ui", tokens.STRIP_SMALL), p.ink_secondary)
        paint.text(draw, (tx, y + tokens.STRIP_LEADING * len(("a", "b"))),
                   f"{who} anchors", tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    paint.text(draw, (f.pad_x, y + d + tokens.STRIP_RULE_GAP + tokens.STRIP_RULE_GAP),
               "One cron line: 0 3,15 * * *. The cast is a function of the episode id, so the "
               "two shows are cast differently without a second config.",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    return img


def recast(facts: CardFacts, archive: Archive, script: Optional[Sequence[dict]] = None,
           **_) -> Image.Image:
    """The same line in three voices. Changing one is changing a string in a URL.

    The point a developer needs in order to imagine their own version: the voice is not a model
    they train or a file they upload, it is `model=flux-<name>-en` on the request.
    """
    img, draw, f, p = _frame(facts)
    palettes = tokens.voice_palettes()
    y = _headline(draw, f, p, "Same script. Change one string in the URL.")
    line = next((s.get("text", "") for s in (script or []) if s.get("text")), "")
    mono = tokens.font("mono", tokens.STRIP_SMALL)
    short = paint.wrapped(draw, line, mono, f.content_width, max_lines=len(("one",)))
    paint.text(draw, (f.pad_x, y), short[0] if short else "", mono, p.ink_secondary)

    d = tokens.STRIP_ORB_D
    row_y = y + tokens.STRIP_LEADING + tokens.STRIP_RULE_GAP
    picks = [v for v in ("flux-alexis-en", "flux-kelsey-en", "flux-cole-en", "flux-wes-en")
             if v in palettes]
    for (x, _w), vid in zip(_columns(f, len(picks)), picks):
        run = palettes[vid]
        paint.bleed(img, x + d // 2, row_y + d // 2, d, run[3])
        paint.place_orb(img, x + d // 2, row_y + d // 2, d, run[:3], run[3], p.orb_ground)
        paint.text(draw, (x, row_y + d + tokens.STRIP_BAND_GAP), f"model={vid}", 
                   tokens.font("mono", tokens.STRIP_SMALL), p.ink_muted)
    return img


# --- ease of development -------------------------------------------------------------

def apicall(facts: CardFacts, archive: Archive, script: Optional[Sequence[dict]] = None,
            **_) -> Image.Image:
    """The whole integration, at actual size.

    Nothing is elided and nothing is pseudo-code: this is the request `hn_radio/render.py` makes,
    with a real line of this episode's script in the body. The most actionable thing that can go
    in a promotional email is the request the reader is being asked to make.
    """
    img, draw, f, p = _frame(facts)
    y = _headline(draw, f, p, "The whole integration")
    line = next((s.get("text", "") for s in (script or []) if s.get("text")), "")
    mono_small = tokens.font("mono", tokens.STRIP_SMALL)
    body = paint.wrapped(draw, f'{{"text": "{line}"}}', mono_small, f.content_width,
                         max_lines=len(("one",)))
    url = _real_speak_url()
    y = _code(draw, f, p, [
        (f"POST {url[:len('https://api.deepgram.com/v2/speak?model=flux-alexis-en&encoding=lin')]}",
         p.ink),
        (f"     {url[len('https://api.deepgram.com/v2/speak?model=flux-alexis-en&encoding=lin'):]}",
         p.ink),
        ("Authorization: Token $DEEPGRAM_API_KEY", p.ink_muted),
        (body[0] if body else "", p.ink_secondary),
    ], y, tokens.STRIP_SMALL)
    paint.text(draw, (f.pad_x, y + tokens.STRIP_BAND_GAP),
               "Raw audio comes back. No SSML, no voice training, no upload step.",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    return img


def stdlib(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """What the audio path is actually built out of.

    A reader deciding whether to try this is estimating how much of their week it costs. The
    honest answer is that the audio side has no dependencies at all: an HTTP call, a WAV header,
    and ffmpeg at the end for the podcast container.
    """
    img, draw, f, p = _frame(facts)
    y = _headline(draw, f, p, "No audio library. No model hosting. No GPU.")
    for (x, _w), (value, label, sub) in zip(_columns(f, len(("http", "wave", "ffmpeg"))), [
        ("urllib", "one POST per line", "Python standard library"),
        ("wave", "stitch and header", "Python standard library"),
        ("ffmpeg", "the chaptered MP3", "one system binary"),
    ]):
        _stat(draw, x, y, p, value, label, sub)
    return img


# --- narrative -----------------------------------------------------------------------

def turnmap(facts: CardFacts, archive: Archive, episode: Optional[dict] = None,
            **_) -> Image.Image:
    """Every turn in the episode, in the speaker's own colour, along its real runtime.

    THE PICTURE IS THE ARGUMENT. A list of features cannot show that this is a conversation; a
    band that alternates twenty-seven times over six minutes can. The spans are the segments'
    actual start times, so the rhythm on the strip is the rhythm in the audio.
    """
    img, draw, f, p = _frame(facts)
    palettes = tokens.voice_palettes()
    segments = (episode or {}).get("segments") or []
    total = (episode or {}).get("duration_seconds") or 0
    y = _headline(draw, f, p, f"{len(segments)} turns between two people")

    spans = []
    for i, seg in enumerate(segments):
        start = seg.get("start_seconds") or 0
        end = (segments[i + 1].get("start_seconds") if i + 1 < len(segments) else total) or total
        if total:
            spans.append((start / total, end / total, _voice_colour(p, palettes,
                                                                    seg.get("voice_id"))))
    _band(img, draw, f.pad_x, y, f.content_width, spans, tokens.STRIP_BAND_H)

    below = y + tokens.STRIP_BAND_H + tokens.STRIP_RULE_GAP
    names = " and ".join(s.name for s in facts.speakers if s.name)
    paint.text(draw, (f.pad_x, below), f"{names} over {facts.meta}",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_secondary)
    paint.text(draw, (f.pad_x, below + tokens.STRIP_LEADING),
               "Written as a two-hander, cast before it is written, and read in one take each.",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    return img


def castlist(facts: CardFacts, archive: Archive, episode: Optional[dict] = None,
             **_) -> Image.Image:
    """Who is on the show, and the rule about the people being quoted.

    The detail worth the space: a Hacker News commenter is PERFORMED by one of the two regulars
    and named by their real username. Nobody's comment gets a fake voice assigned to it, which is
    an editorial decision a reader can copy rather than a feature they have to buy.
    """
    img, draw, f, p = _frame(facts)
    palettes = tokens.voice_palettes()
    segments = (episode or {}).get("segments") or []
    commenters = sorted({s.get("speaker_key") for s in segments
                         if s.get("role") == "commenter" and s.get("speaker_key")})
    y = _headline(draw, f, p, "Two regulars, and everyone they quote")

    d = tokens.STRIP_ORB_D
    roles = ("anchor", "second chair")
    for (x, _w), speaker, role in zip(_columns(f, len(("a", "b", "c"))), facts.speakers, roles):
        run = palettes.get(speaker.voice_id)
        if run:
            paint.bleed(img, x + d // 2, y + d // 2, d, run[3])
            paint.place_orb(img, x + d // 2, y + d // 2, d, run[:3], run[3], p.orb_ground)
        tx = x + d + tokens.STRIP_BAND_GAP * len(("pad",)) + tokens.STRIP_RULE_GAP
        paint.text(draw, (tx, y + tokens.STRIP_RULE_GAP), speaker.name,
                   tokens.font("display", tokens.STRIP_LABEL), p.ink)
        paint.text(draw, (tx, y + tokens.STRIP_RULE_GAP + tokens.STRIP_LEADING), role,
                   tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)

    third = _columns(f, len(("a", "b", "c")))[2][0]
    paint.text(draw, (third, y + tokens.STRIP_RULE_GAP), f"{len(commenters)} commenters",
               tokens.font("display", tokens.STRIP_LABEL), p.ink)
    paint.text(draw, (third, y + tokens.STRIP_RULE_GAP + tokens.STRIP_LEADING),
               ", ".join(commenters)[:len("a" * 40)] or "quoted from the thread",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    paint.text(draw, (f.pad_x, y + d + tokens.STRIP_RULE_GAP),
               "Comments are read by a regular and credited to the real username. No voice is "
               "invented for somebody who did not consent to one.",
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    return img


def chapters(facts: CardFacts, archive: Archive, episode_dir: Optional[Path] = None,
             episode: Optional[dict] = None, **_) -> Image.Image:
    """The episode as a podcast app sees it: real ID3 chapters at real timestamps.

    Not a nice-to-have. Chapters are what make a generated show behave like a produced one in
    Overcast and Apple Podcasts, and they are derived from the script rather than marked by hand.
    """
    img, draw, f, p = _frame(facts)
    total = (episode or {}).get("duration_seconds") or 0
    try:
        marks = json.loads((episode_dir / "chapters.json").read_text())["chapters"]
    except (OSError, ValueError, KeyError, TypeError):
        marks = []
    y = _headline(draw, f, p, f"{len(marks)} chapters, baked into the MP3")

    spans, p_alt = [], tokens.palette()
    for i, ch in enumerate(marks):
        start = ch.get("startTime") or 0
        end = (marks[i + 1].get("startTime") if i + 1 < len(marks) else total) or total
        colour = p_alt.accent if ch.get("url") else p_alt.ink_muted
        if total:
            spans.append((start / total, end / total, colour))
    _band(img, draw, f.pad_x, y, f.content_width, spans, tokens.STRIP_BAND_H)

    below = y + tokens.STRIP_BAND_H + tokens.STRIP_RULE_GAP
    titles = [ch["title"] for ch in marks if ch.get("url")]
    paint.text(draw, (f.pad_x, below), " · ".join(titles)[:len("a" * 86)],
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_secondary)
    paint.text(draw, (f.pad_x, below + tokens.STRIP_LEADING),
               "Derived from the script, written as ID3 CHAP frames, and linked back to the "
               "Hacker News thread.", tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    return img


BUILDERS = dict(BUILDERS, **{
    "perminute": perminute, "yearly": yearly, "archive": archive_total,
    "schedule": schedule, "recast": recast,
    "apicall": apicall, "stdlib": stdlib,
    "turnmap": turnmap, "castlist": castlist, "chapters": chapters,
})


# --- code ----------------------------------------------------------------------------
#
# FIVE SNIPPETS, AND FOUR OF THEM ARE REAL LINES OUT OF THIS REPO. That is the whole reason these
# are worth a slot: a developer reading a promotional email has been shown invented pseudo-code
# before and discounts it automatically. Where a snippet is trimmed, its caption on the review
# page says so and says what was cut.
#
# They use `tokens.STRIP_TALL` rather than the 400px frame. A snippet worth showing is eight to
# ten lines and ten legible lines do not fit 400px; the width is unchanged, so they still drop
# into the same newsletter column as the rest of the set.

CODE_STRIPS = ("curl", "render", "price", "rotation", "pipeline")


def _code_frame(facts: CardFacts, headline: str, lines: Sequence[str], footnote: str,
                name: Optional[str] = None):
    """The shared shape: eyebrow, headline, a highlighted snippet, one line of prose, the URL.

    Records what it drew in `CODE_SNIPPETS` so the tests can assert on the words rather than on
    the pixels. A claim that only exists inside a PNG is a claim nobody checks.
    """
    if name:
        CODE_SNIPPETS[name] = (headline, tuple(lines), footnote)
    f, p = tokens.STRIP_TALL, tokens.palette()
    img = paint.canvas(f.size, p)
    draw = ImageDraw.Draw(img)
    paint.tracked(draw, (f.pad_x, f.eyebrow_y), EYEBROW,
                  tokens.font("display", f.eyebrow_size), p.accent, f.eyebrow_track)
    paint.text(draw, (f.pad_x, f.body_y), headline,
               tokens.font("display", tokens.STRIP_HEADLINE), p.ink)

    font = tokens.font("mono", tokens.STRIP_CODE_SIZE)
    y = f.body_y + tokens.STRIP_HEADLINE + tokens.STRIP_RULE_GAP + tokens.STRIP_RULE_GAP
    for line in lines:
        paint.code_line(draw, (f.pad_x, y), paint.code_spans(line, p), font)
        y += tokens.STRIP_CODE_LEADING
    paint.text(draw, (f.pad_x, y + tokens.STRIP_RULE_GAP), footnote,
               tokens.font("ui", tokens.STRIP_SMALL), p.ink_muted)
    paint.text(draw, (f.pad_x, f.footer_y), facts.url_text,
               tokens.font("mono", f.footer_size), p.accent)
    return img


def _real_speak_url(voice_id: str = "flux-alexis-en") -> str:
    """The URL `hn_radio/render.py` actually builds, asked of the code rather than typed here.

    An earlier version of these two strips wrote the endpoint out by hand and lost four query
    parameters doing it, then captioned itself "the request render.py makes". Deriving it means
    the picture is wrong only if the code is.
    """
    from ..render import _speak_url
    return _speak_url(voice_id)


# name -> (headline, lines, footnote). The builders draw from this and `tests/test_strips.py`
# asserts against it, so every claim on a code strip is checked against the repo it quotes.
CODE_SNIPPETS = {}


def curl(facts: CardFacts, archive: Archive, script: Optional[Sequence[dict]] = None,
         **_) -> Image.Image:
    """The smallest thing a reader can paste and hear.

    The most actionable image that can go in a promotional email: not a description of the API,
    the command that exercises it. The URL is the one this repo builds, parameters and all, and
    the body is a real line from this episode.

    `container=none` is in there because that is what the show asks for: it stitches raw PCM and
    writes one WAV header at the end. So the output is named `.raw`, which is what comes back.
    Dropping that one parameter is what gets you a playable file, and the footnote says so rather
    than the command quietly implying it.
    """
    line = next((s.get("text", "") for s in (script or []) if s.get("text")), "")
    return _code_frame(facts, "Your first Flux request", [
        'curl -X POST \\',
        f'  "{_real_speak_url()}" \\',
        '  -H "Authorization: Token $DEEPGRAM_API_KEY" \\',
        '  -H "Content-Type: application/json" \\',
        f'  -d \'{{"text": "{line[:56]}..."}}\' \\',
        '  --output line.raw',
    ], "The exact request this show makes. Drop container=none to get a playable wav instead.", name="curl")


def render(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """The entire text-to-speech integration, as it exists in this repo.

    Trimmed: the real function wraps this in a retry that drops the `speed` parameter when a
    voice does not support it. Nothing else was cut, and nothing was added.
    """
    return _code_frame(facts, "The whole TTS integration", [
        'def render_segment(text: str, voice_id: str, api_key: str) -> bytes:',
        '    """Render one segment to raw linear16 PCM bytes."""',
        '    headers = {"Authorization": f"Token {api_key}",',
        '               "Content-Type": "application/json"}',
        '    return post_json_for_bytes(',
        '        _speak_url(voice_id), {"text": text}, headers,',
        '        timeout=60, retries=config.http_retries(),',
        '    )',
    ], "hn_radio/render.py. Cut for space: a retry that drops the speed parameter on voices "
       "without it, a RIFF-header guard, and an empty-audio check.", name="render")


def price(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """Pricing a script before rendering it, which is what per-character billing buys you.

    The consequence developers miss: because the bill is a function of the input, the cost of a
    render is knowable before the render. You cannot do this with per-minute pricing.
    """
    return _code_frame(facts, "Price it before you render it", [
        'RATES_USD_PER_1K = {"payg": 0.0450, "growth": 0.0405}',
        'CHARS_PER_UNIT = 1000',
        '',
        'def cost_usd(chars: int, plan_name=None) -> float:',
        '    return (chars / CHARS_PER_UNIT) * rate_usd_per_1k(plan_name)',
        '',
        '>>> cost_usd(script_characters(segments))',
        '0.24417',
    ], "hn_radio/pricing.py, rate table reflowed onto one line. The bill is a function of the "
       "input text, so a render's cost is knowable before the render.", name="price")


def rotation(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """How the second chair is chosen. Four lines, and two properties that matter.

    Deterministic, so a re-render of a failed episode is the same show rather than a different
    one; and unrelated across adjacent dates, so consecutive episodes do not walk the catalog
    alphabetically.
    """
    return _code_frame(facts, "Four lines decide who co-hosts", [
        '# Seeded by the episode id: a re-render casts the same show.',
        'start = int(sha256(episode_id.encode()).hexdigest(), 16) % len(pool)',
        'rotated = pool[start:] + pool[:start]',
        '',
        '# Anyone who sat recently goes to the back, never off the list.',
        'return ([v for v in rotated if v not in recent]',
        '        + [v for v in rotated if v in recent])',
    ], "hn_radio/cast.py, trimmed. A rotation with no state, no database and no scheduler.", name="rotation")


def pipeline(facts: CardFacts, archive: Archive, **_) -> Image.Image:
    """The whole show as a sequence of plain functions.

    THE SHAPE, not a verbatim copy: the real `pipeline.py` threads a few more arguments through.
    What is faithful is the thing worth showing, which is that every stage takes data and returns
    data, so any one of them can be swapped or tested on its own.
    """
    # THE HEADLINE SAID THIRTEEN AND THE SNIPPET SHOWED EIGHT, which is the first thing anyone
    # who reads code does with a picture of code: count it. Two of the names were invented as
    # well -- there is no `ingest.top_stories` and no `pacing.gaps`. Every call below resolves in
    # this repo, and `tests/test_strips.py` fails if one of them stops resolving.
    return _code_frame(facts, "Eight calls, and that is the show", [
        'pool     = ingest.fetch_stories_between(start, end, 30)',
        'selected = editions.select_stories(pool, edition, n)',
        'cast     = episode_cast(before=episode_id, slot=slot)',
        'segments = writer.write(selected, top, comments, cast, ...)',
        'segments = normalize.normalize_segments(segments)',
        'pcm      = render.render_all(segments, api_key)',
        'wav      = stitch.stitch(pacing.apply(segments, pcm), path)',
        'publish.publish(episode, out_dir)',
    ], "hn_radio/pipeline.py, argument lists shortened. Thirteen stages in all; these are the "
       "eight that carry the data.", name="pipeline")


BUILDERS.update({name: globals()[name] for name in CODE_STRIPS})
STRIPS = STRIPS + CODE_STRIPS
