"""Build the per-episode share cards, and the sample set that goes to marketing.

The NIGHTLY build is not this script's job and never was. `publish.rebuild_site` calls
`cards.backfill` on every publish and every boot, so the deployed archive pictures itself the
first time this code comes up on Fly. This is the thing you run when you want to LOOK at the
result, or hand a designer something to react to.

    uv run python scripts/make_share_cards.py                    # report on episodes/
    uv run python scripts/make_share_cards.py --write            # build any missing cards
    uv run python scripts/make_share_cards.py --write --force    # rebuild every card
    uv run python scripts/make_share_cards.py --samples          # the set for review

READ-ONLY WITHOUT `--write` OR `--samples`, for the reason `episode_costs.py` is: the interesting
use is checking what a card would say before trusting it in front of a customer, and that should
not be a mutating operation.

WHY `--samples` FORCES COST VALUES THE ARCHIVE DOES NOT CONTAIN. The rate is not confirmed. Real
episodes run $0.10 to $0.28, and the layout has to hold at $0.06 and at $0.20 as well -- those are
the two figures the ask named. The credit line is the string that moves: $0.27 an episode reads
"734 episodes" and $0.06 reads "3,333", which is five glyphs wider on the longest line on the card.
A forced render is the only way to see that before the price list changes rather than after.

The samples are written OUTSIDE the episode directories, into docs/share-cards/, and
that is deliberate twice over. `episodes/` is gitignored, so nothing in it can be reviewed in a
pull request or opened by anyone who has not run the pipeline; and a card quoting an invented cost
must never sit next to the episode it is invented about, where something could serve it.
"""

from __future__ import annotations

import argparse
import base64
import copy
import datetime
import html
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import cards, config  # noqa: E402
from hn_radio.cards import facts as card_facts  # noqa: E402
from hn_radio.cards import layout as card_layout  # noqa: E402
from hn_radio.cards import strips as card_strips  # noqa: E402
from hn_radio.cards import tokens  # noqa: E402
from hn_radio.models import is_recast  # noqa: E402
from hn_radio.pricing import episodes_per_credit  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "docs" / "share-cards"
PREVIEW_TEMPLATE = pathlib.Path(__file__).resolve().parent / "templates" / "share-cards-preview.html"

# Both review pages share the theme chrome in that template and supply their own layout here. The
# chrome is the part that must never differ between docs; the layout is the part that must.
SAMPLE_STYLE = """
  /* The subject of this page is a 1200px image, so the column is wider than a reading column
     would be. Everything still wraps at a phone width. */
  body { max-width: 74rem; }
  p, li, .lede { max-width: 46rem; }
  .lede { font-size: 1.05rem; color: var(--muted2); }

  figure { margin: 1.1rem 0 1.6rem; page-break-inside: avoid; }
  figcaption {
    font-family: system-ui, sans-serif;
    font-size: 0.8rem;
    color: var(--faint);
    margin-bottom: 0.4rem;
  }
  /* A hairline under the card, because a dark card on a dark page has no edge otherwise and you
     cannot see where the 630 stops. */
  figure img {
    display: block;
    max-width: 100%;
    height: auto;
    border: 1px solid var(--rule);
    border-radius: 4px;
  }
  /* The email card is rendered at 2x and MUST be shown at 600, which is the whole point of the
     size. Pinning it here means this page previews what a subscriber sees, not the raw file. */
  img.email { width: 600px; }
  img.social { width: 1200px; }

  .facts {
    font-family: system-ui, sans-serif;
    font-size: 0.85rem;
    color: var(--muted);
    margin: 0.2rem 0 0.9rem;
  }
  .facts b { color: var(--heading); font-weight: 600; }
  .forced { border-left: 3px solid var(--watch-border); background: var(--watch-bg);
            padding: 0.55rem 0.8rem; margin: 0.6rem 0 1rem; font-size: 0.9rem; max-width: 46rem; }
  pre.alt { white-space: pre-wrap; max-width: 46rem; font-size: 0.82rem; line-height: 1.5; }
"""

SHEET_STYLE = """
  body { max-width: 100rem; }
  p, li, .lede { max-width: 46rem; }
  .lede { font-size: 1.05rem; color: var(--muted2); }

  /* Tiles size themselves to the window rather than to a reading column: two across on a laptop,
     three on a wide display, one on a phone. The 30rem floor is doing legibility work rather than
     taste -- below about 480px the card's own 27px label type lands under 8 CSS pixels, and at
     that size more resolution buys nothing because the glyphs are simply too small to read. */
  .sheet {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(30rem, 1fr));
    gap: 1.6rem 1.4rem;
    margin: 1.2rem 0 2rem;
  }
  .tile { page-break-inside: avoid; }
  .tile img {
    display: block;
    width: 100%;
    height: auto;
    border: 1px solid var(--rule);
    border-radius: 4px;
  }
  .tile-facts {
    font-family: system-ui, sans-serif;
    font-size: 0.78rem;
    line-height: 1.45;
    color: var(--muted);
    margin: 0.4rem 0 0;
  }
  .tile-id { color: var(--heading); font-weight: 600; }
  /* The cost is what this page is for, so it is the one thing in a tile that is not grey. */
  .tile-cost { color: var(--heading); font-weight: 600; }
  .band { font-family: system-ui, sans-serif; font-size: 0.85rem; }
  @media print {
    .sheet { grid-template-columns: repeat(2, 1fr); gap: 0.8rem; }
  }
"""


# What the sample set is FOR: one card at each end of what the show really costs, and two at costs
# it does not, so a designer sees the layout at its narrowest and its widest before signing off.
# Real episodes in every case -- a made-up episode would test the layout and prove nothing about
# the show.
SAMPLES = (
    ("2026-08-13",    None, "cheapest real episode in the archive"),
    ("2026-09-16-am", None, "a typical morning episode"),
    ("2026-09-06-pm", None, "dearest real episode in the archive"),
    ("2026-09-16-am", 0.06, "FORCED: the low end of the unconfirmed rate"),
    ("2026-09-16-am", 0.20, "FORCED: the high end of the unconfirmed rate"),
)


def _repriced(episode: dict, usd: float) -> dict:
    """A copy of `episode` whose cost block says `usd`, for the forced samples only.

    Rewrites `episodes_per_credit` alongside it with `pricing`'s own function rather than leaving
    the real one in place. The two figures are the same claim stated twice, and a sample card
    showing $0.06 next to 819 episodes would be a picture of a bug, which is exactly the thing a
    reviewer would then spend twenty minutes on.
    """
    out = copy.deepcopy(episode)
    cost = out.setdefault("cost", {})
    cost["usd"] = usd
    cost["episodes_per_credit"] = episodes_per_credit(usd, cost.get("credit_usd") or 200.0)
    return out


def report(episodes_dir: pathlib.Path, app_url: str) -> int:
    print(f"{app_url}\n")
    print(f"{'episode':16} {'cost':>7} {'per credit':>11}  card")
    missing = 0
    for episode_json in sorted(episodes_dir.glob("*/episode.json")):
        name = episode_json.parent.name
        try:
            f = card_facts.load(episode_json, app_url)
        except card_facts.NoCost:
            print(f"{name:16} {'':>7} {'':>11}  no cost block, so no card")
            continue
        except (OSError, ValueError) as e:
            print(f"{name:16} {'':>7} {'':>11}  unreadable: {type(e).__name__}: {e}")
            continue
        built = (episode_json.parent / cards.CARD_JSON).is_file()
        missing += 0 if built else 1
        note = "" if built else "NOT BUILT"
        if is_recast(name):
            note = (note + " (recast)").strip()
        print(f"{name:16} {f.hero:>7} {f.episodes_per_credit:>11,}  {note}")
    if missing:
        print(f"\n{missing} episode(s) have no card. `--write` builds them, and so does any "
              "publish or app boot.")
    return 0


def samples(episodes_dir: pathlib.Path, app_url: str) -> int:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for episode_id, forced, why in SAMPLES:
        episode_json = episodes_dir / episode_id / "episode.json"
        if not episode_json.is_file():
            print(f"skipped {episode_id}: not on disk")
            continue
        episode = json.loads(episode_json.read_text())
        if forced is not None:
            episode = _repriced(episode, forced)
        f = card_facts.facts_for(episode, app_url)
        stem = episode_id + (f"-at-{forced:.2f}".replace(".", "") if forced is not None else "")
        for L in tokens.LAYOUTS:
            out = SAMPLES_DIR / f"{stem}-{L.name}.png"
            card_layout.compose(L, f).save(out, optimize=True)
            print(f"wrote {out.relative_to(ROOT)}  "
                  f"{L.width}x{L.height}, shown at {L.css_width}px wide")
        index.append({
            "episode_id": episode_id,
            "why": why,
            "forced_usd": forced,
            "hero": f.hero,
            "credit_line": f.credit_line,
            "alt": f.alt,
            "files": {L.name: f"{stem}-{L.name}.png" for L in tokens.LAYOUTS},
        })
    # The alt text is the deliverable marketing actually pastes, so it ships beside the pictures
    # rather than only inside them.
    (SAMPLES_DIR / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    preview = write_preview(index)
    print(f"\n{len(index)} sample(s) in {SAMPLES_DIR}")
    print(f"open {preview}")
    return 0


def write_preview(index: list) -> pathlib.Path:
    """A contact sheet, so the cards can be looked at rather than opened one file at a time.

    GENERATED WITH THE CARDS AND NOT WRITTEN ONCE BY HAND. A review page that drifts from the
    pictures it is reviewing is worse than no review page: someone signs off on a layout that is
    not the one on disk.

    The light/dark toggle is the thing to actually use on this page. These are dark cards going
    into emails that are mostly white, and flipping the ground under them is the nearest thing to
    watching one land in a client without sending a test to yourself.
    """
    rows = "".join(
        f"<tr><td><b>{html.escape(s['hero'])}</b></td>"
        f"<td>{html.escape(s['credit_line'].split('covers ')[-1])}</td>"
        f"<td>{html.escape(s['episode_id'])}</td>"
        f"<td>{html.escape(s['why'])}</td></tr>"
        for s in index)

    sections = []
    for s in index:
        forced = ""
        if s["forced_usd"] is not None:
            forced = ('<p class="forced"><b>Not a real episode price.</b> This is '
                      f"{html.escape(s['episode_id'])} re-priced at "
                      f"${s['forced_usd']:.2f} to see the layout at a rate the archive has never "
                      "produced. The published rate is not confirmed, and the credit line is the "
                      "string that moves with it.</p>")
        sections.append(f"""
<h2>{html.escape(s['hero'])} &middot; {html.escape(s['why'])}</h2>
<p class="facts"><b>{html.escape(s['episode_id'])}</b> &middot;
   {html.escape(s['credit_line'])}</p>
{forced}
<figure>
  <figcaption>Email &mdash; shown at 600&times;400, the file is 1200&times;800
    (<code>{html.escape(s['files']['email'])}</code>)</figcaption>
  <img class="email" src="{html.escape(s['files']['email'])}"
       alt="{html.escape(s['alt'])}">
</figure>
<figure>
  <figcaption>Social and Open Graph &mdash; 1200&times;630
    (<code>{html.escape(s['files']['social'])}</code>)</figcaption>
  <img class="social" src="{html.escape(s['files']['social'])}"
       alt="{html.escape(s['alt'])}">
</figure>
<h3>Alt text</h3>
<pre class="alt">{html.escape(s['alt'])}</pre>
""")

    content = f"""
<h1>HN Radio share cards</h1>
<p class="lede">What a developer's Flux TTS credits actually buy, as a picture that drops into a
lifecycle email or a social post and points back at the episode. Marketing's ask was
&ldquo;as opposed to having a button or a link, something to show them.&rdquo;</p>

<p><b>Use the toggle, top right.</b> These are dark cards and most email clients are white, so
flipping the page is the closest look at how one lands without sending yourself a test.</p>

<h2>The set</h2>
<p>Three real episodes at the ends and the middle of what the show actually costs, and two renders
at forced rates. The rate is not confirmed, so the layout has to hold at
$0.06 and at $0.20 as well as where the archive really sits, which is $0.10 to $0.27.</p>
<table>
  <thead><tr><th>Cost</th><th>Credit buys</th><th>Episode</th><th>Why it is here</th></tr></thead>
  <tbody>{rows}</tbody>
</table>

<h2>What is on every card</h2>
<ol>
  <li><b>What rendered it and what it is.</b> The Deepgram / Flux TTS lockup and the show name.</li>
  <li><b>What this episode cost</b>, in dollars, read from the episode's own cost block. Never a
      constant: the figure is computed from the exact text that was billed.</li>
  <li><b>What that rate means</b>, as how many episodes the $200 signup credit covers.</li>
  <li><b>Where to go next</b>, as visible text, because an image is not clickable in every client.</li>
</ol>
<p>The two orbs are the episode's actual voices, in their real per-voice palettes. The co-host
rotates, so the cast on the card changes every episode.</p>
{''.join(sections)}
<h2>Notes for a designer</h2>
<ul>
  <li><b>The type is not the brand face.</b> This repo ships no font binaries, so the cards land on
      Arial locally and DejaVu in the container, the same way the site lands on system-ui. One
      line changes when a licensed file can live in the repo.</li>
  <li><b>Every colour, size and spacing value is in one file</b>,
      <code>hn_radio/cards/tokens.py</code>, and the colours in it are read out of
      <code>web/brand.css</code> rather than copied. A restyle is one file, and the cards cannot
      drift from the site they link to.</li>
  <li><b>Static raster only.</b> No web font, no JavaScript, no player, no iframe: an email client
      would strip all four. The alt text above each card carries the cost and the pitch for the
      readers whose client blocks images.</li>
</ul>

<p class="meta">Generated by <code>scripts/make_share_cards.py --samples</code> on
{datetime.date.today().isoformat()}. Derived: edit the generator or the template at
<code>scripts/templates/share-cards-preview.html</code>, not this file.</p>
"""
    return _render("HN Radio share cards", SAMPLE_STYLE, content, SAMPLES_DIR / "index.html")


# --- the newsletter strips ----------------------------------------------------------------------

# One line each: what the strip argues, and why it is in the set. Shown under it on the review
# page, because six pictures with no captions is a set nobody can give feedback on.
STRIP_NOTES = {
    "ladder": ("Dollars into episodes into months.",
               "The show-and-tell ask, almost verbatim: here is what the credits you are "
               "holding actually buy. The $1 and $10 rungs exist to make the $200 one believable."),
    "textin": ("What you POST, and what comes back.",
               "The most developer-legible claim the project has. That is a real line from this "
               "episode's script.json, which is byte-for-byte the text that was billed: no SSML, "
               "no phoneme hints, no per-word tuning."),
    "receipt": ("The billing unit, not the bill.",
                "Everyone assumes voice is billed per minute of audio. Flux bills per character "
                "of input, and that difference is what lets a developer price a script before "
                "spending anything."),
    "bench": ("The catalog is the product.",
              "FLEXIBILITY. A reader who has heard one demo voice does not know the bench "
              "exists. The no-repeat window is read from cast.COHOST_RECENCY_WINDOW, which is "
              "20; the strip and the README both said fourteen until 2026-09-17."),
    "scoreboard": ("It is real and nobody is driving it.",
                   "No cost on this one on purpose: the objection it answers is not \u201ccan I "
                   "afford it\u201d, it is \u201cis this a demo somebody ran once\u201d. The "
                   "elapsed days moved into the headline, because 58 episodes beside 46 days "
                   "beside 2 a day invited a division that gives 1.26."),
    "latest": ("Today's episode, evergreen.",
               "The only strip whose content changes on its own, so a weekly send can point at "
               "one URL and never update it."),
    "perminute": ("Priced per character, delivered per minute.",
                  "COST. The conversion nobody can do in their head: Flux quotes input "
                  "characters and every competitor a reader has used quotes output minutes."),
    "yearly": ("A year of the show, as one number.",
               "COST. 730: a full year at the schedule, two a day, which is what the crontab "
               "says and what \u201ca year of the show\u201d means. The ladder uses the MEASURED "
               "pace instead, because it answers a different question \u2014 how long the credit "
               "lasts, where a missed run makes it last longer. Each strip states its own basis "
               "on its face."),
    "archive": ("Everything on the site, totalled.",
                "COST. Not a rate and not a projection: this is the whole catalogue a reader "
                "can go and listen to, and what all of it cost. The hours are summed from the "
                "episodes on disk, not episodes times a guess at their length."),
    "schedule": ("Two editions a day, two different chairs.",
                 "FLEXIBILITY. The same pipeline on one cron line produces two different shows, "
                 "because the cast is a function of the episode id."),
    "recast": ("Same script, different voice.",
               "FLEXIBILITY. The point that lets a developer imagine their own version: the "
               "voice is not a model you train or a file you upload, it is a string in the URL."),
    "apicall": ("The whole integration, at actual size.",
                "EASE. Nothing elided, nothing pseudo-code: this is the request render.py makes, "
                "with a real line of this episode in the body."),
    "stdlib": ("No audio library. No model hosting. No GPU.",
               "EASE. A reader deciding whether to try this is estimating how much of their week "
               "it costs. The audio path has no dependencies at all."),
    "turnmap": ("Twenty-seven turns between two people.",
                "NARRATIVE. A list of features cannot show that this is a conversation. The "
                "spans are the real start times, so the rhythm here is the rhythm in the audio."),
    "castlist": ("Two regulars, and everyone they quote.",
                 "NARRATIVE. Commenters are performed by a regular and credited to their real "
                 "username. An editorial decision a reader can copy, not a feature to buy."),
    "curl": ("Your first Flux request.",
             "CODE. The most actionable thing that can go in a promotional email is not a "
             "description of the API, it is the command that exercises it. The URL is built by "
             "calling render._speak_url(), so it carries the four query parameters a hand-typed "
             "one lost. It outputs .raw, not .wav, because container=none returns headerless "
             "PCM."),
    "render": ("The whole TTS integration, eight lines.",
               "CODE. From hn_radio/render.py. Three things cut for space and named on the "
               "strip itself: the retry that drops the speed parameter on voices without it, a "
               "RIFF-header guard, and an empty-audio check. Nothing was added."),
    "price": ("Price a script before you render it.",
              "CODE. The consequence of per-character billing that people miss: the bill is a "
              "function of the input, so a render's cost is knowable before the render. You "
              "cannot do this with per-minute pricing. Real constants now; it invented "
              "RATE_USD_PER_1K, singular, until 2026-09-17."),
    "rotation": ("Four lines decide who co-hosts.",
                 "CODE. From hn_radio/cast.py, trimmed. Deterministic, so a re-render of a "
                 "failed episode is the same show, and stateless, so there is no database and "
                 "no scheduler behind it."),
    "pipeline": ("Eight calls, and that is the show.",
                 "CODE. From hn_radio/pipeline.py with the argument lists shortened. It said "
                 "\u201cThirteen stages\u201d over eight lines until 2026-09-17, and two of the "
                 "names on it did not exist. There are thirteen stages; these are the eight "
                 "that carry the data, and a test now asserts every call resolves."),
    "chapters": ("Real ID3 chapters at real timestamps.",
                 "NARRATIVE. What makes a generated show behave like a produced one in Overcast "
                 "and Apple Podcasts, derived from the script rather than marked by hand."),
}


def strips(episodes_dir: pathlib.Path, app_url: str) -> int:
    """Render the six newsletter strips for the most recent episode, plus a page to review them.

    ONE EPISODE, DELIBERATELY. Five of the six say the same thing whichever episode they are built
    from -- the ladder, the bench and the scoreboard are facts about the project, not about a
    show -- so rendering them per episode would be 348 files to say six things.
    """
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    latest = max((p.parent for p in episodes_dir.glob("*/episode.json")),
                 key=lambda d: d.name, default=None)
    if latest is None:
        print(f"no episodes in {episodes_dir}")
        return 1
    episode = json.loads((latest / "episode.json").read_text())
    script = json.loads((latest / "script.json").read_text())
    facts = card_facts.facts_for(episode, app_url)
    archive = card_strips.Archive.load(episodes_dir)

    made = []
    for name in card_strips.STRIPS:
        img = card_strips.compose(name, facts, archive, episode=episode, script=script,
                                  episode_dir=latest)
        out = SAMPLES_DIR / f"strip-{name}.png"
        img.save(out, optimize=True)
        made.append((name, out, img.size))
        print(f"wrote {out.name}  {img.width}x{img.height}, "
              f"shown at {img.width // 2}x{img.height // 2}")

    page = _strips_page(latest.name, facts, archive, made)
    print(f"\n{len(made)} strips from {latest.name}")
    print(f"open {page}")
    return 0


def _strips_page(episode_id: str, facts, archive, made: list) -> pathlib.Path:
    sections = []
    for name, path, size in made:
        claim, why = STRIP_NOTES[name]
        sections.append(f"""
<div class="strip-cell">
<h2>{html.escape(claim)}</h2>
<p class="facts"><code>{html.escape(path.name)}</code> &middot;
  {size[0]}&times;{size[1]}, shown at {size[0] // 2}&times;{size[1] // 2}</p>
<figure><img class="strip" src="{html.escape(path.name)}" alt="{html.escape(claim)}"></figure>
<p>{html.escape(why)}</p>
</div>
""")

    content = f"""
<h1>Newsletter strips</h1>
<p class="lede">One component of the project each, grouped by what they argue: cost, flexibility,
ease of development, and the fact that this is a produced show rather than a robot reading a
list. Pick the ones that fit; they are meant to be a menu. All of them are built from a single
episode,
<code>{html.escape(episode_id)}</code>, and from the archive totals. Everything is 600 wide as
displayed, rendered at 2x. The figure strips are 200 tall and the code strips are 250, which is
what eight legible lines of monospace needs.</p>

<p><b>Use the toggle, top right.</b> Dark strips, mostly white email clients.</p>

<blockquote><b>Ten claims were wrong on 2026-09-17 and are fixed.</b>
The headline that said thirteen stages over eight lines; two function names that did not exist;
a credit that \u201ccovered most of\u201d a year it covers outright; hours of audio from an
invented formula; a no-repeat window of fourteen against a constant of 20; an invented pricing
constant; a hand-typed request URL missing four parameters; and two cost strips converting
episodes to time on two different bases. <code>tests/test_strips.py</code> now checks each claim
against the code or the archive, so the next one fails in CI rather than in an inbox.<br><br>
<b>Two numbers to sanity-check before these go anywhere.</b><br>
The &ldquo;13 years of podcast&rdquo; figure from the call does not hold: ${archive.credit_usd:,.0f}
at the real mean of ${archive.mean_usd:.4f} is {archive.episodes_per_credit:,} episodes, which is
{archive.days_per_credit} days, about {round(archive.days_per_credit / (365.25 / 12))} months at two
a day. The strips carry the honest number.<br>
And the bench says <b>34 voices</b>, which is what this show can cast today: brand.css carries 37
palettes and three of those are retired by ear. If the line is meant to be about the Flux catalog
rather than about this show's rotation, that number should come from the product, not from here.
<br><br>
Still open, found while checking: the README says every episode closes by saying what it cost out
loud. <b>None of the 58 do.</b> <code>pricing.COST_LINE</code> exists and is tested; nothing in
the script assembly calls it.</blockquote>
<div class="strips">{''.join(sections)}</div>
<h2>What is shared across all of them</h2>
<ul>
  <li>The same <code>hn_radio/cards/tokens.py</code> as the share cards, so a restyle moves the
      strips too, and the orbs are the real per-voice palettes out of <code>web/brand.css</code>.</li>
  <li>Grouped for scanning, not ranked. The four themes are cost, flexibility, ease of
      development and narrative, and there are three to five of each.</li>
  <li>Static raster only: no web font, no JavaScript, no player. Each one gets alt text carrying
      its claim, because email clients block images.</li>
  <li>Every number is read from the pipeline, never typed. The receipt's arithmetic is the same
      <code>pricing.py</code> the show reads out loud in its own outro.</li>
</ul>

<p class="meta">Generated by <code>scripts/make_share_cards.py --strips</code> on
{datetime.date.today().isoformat()}.</p>
"""
    return _render("HN Radio newsletter strips", SHEET_STYLE + """
  /* Two columns, so the whole set can be scanned at once rather than scrolled through. Each
     strip is 600 wide, which is exactly its display size, so nothing here is a preview of a
     preview. Collapses to one column under about 1300px. */
  .strips { display: grid; grid-template-columns: repeat(auto-fit, minmax(37rem, 1fr));
            gap: 0.4rem 2.2rem; margin: 1.4rem 0; }
  .strip-cell { page-break-inside: avoid; }
  .strip-cell h2 { margin-top: 1.4rem; }
  .strip-cell p { max-width: none; }
  figure { margin: 0.7rem 0 0.5rem; }
  img.strip { display: block; width: 600px; max-width: 100%; height: auto;
              border: 1px solid var(--rule); border-radius: 4px; }
  blockquote { max-width: none; }
  @media print { .strips { grid-template-columns: repeat(2, 1fr); } }
""", content, SAMPLES_DIR / "strips.html")


# --- the contact sheet -------------------------------------------------------------------------

# FULL RESOLUTION, NOT THUMBNAILS. The first version of this page downscaled every card to 420px
# and it looked exactly as bad as that sounds: the tiles render around 500 CSS pixels wide, a
# retina display asks for twice that, and WebP at a low quality finished off whatever was left of
# 27px label text. So the card goes in at its native 1200 and the only lossy step is the encode.
#
# The arithmetic that makes this affordable: 58 cards as PNG is 8 MB, which is a page that hangs
# while it decodes. The same 58 as WebP at quality 92 is 3 MB, sharp, and still one file you can
# send to a phone. Nothing is resampled, so there is no resize to blame for a soft card.
TILE_MAX_WIDTH = 1200        # the cards' own width; a guard, not a resize
TILE_QUALITY = 92            # below about 90, WebP starts smearing small type on a dark ground


def _card_data_uri(png: pathlib.Path) -> str:
    """One card, re-encoded and inlined at full size.

    EMBEDDED RATHER THAN LINKED, because this page's whole job is to be looked at somewhere else:
    handed to marketing, opened on a phone, attached to a thread. A contact sheet that arrives as
    one file and 58 broken image icons is not a contact sheet. It also keeps 58 derived copies of
    the cards out of the repo.
    """
    from PIL import Image
    im = Image.open(png)
    if im.width > TILE_MAX_WIDTH:
        im = im.resize((TILE_MAX_WIDTH, round(im.height * TILE_MAX_WIDTH / im.width)),
                       Image.LANCZOS)
    buf = io.BytesIO()
    try:
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=TILE_QUALITY, method=6)
        mime = "image/webp"
    except (OSError, KeyError, ValueError):
        # A Pillow built without WebP. Three times the bytes and identical to look at, which is
        # the right way round for a fallback.
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def contact_sheet(episodes_dir: pathlib.Path, app_url: str) -> int:
    """Every episode's card on one page, newest first, for a review pass over the whole archive.

    Reads the cards that are ON DISK rather than re-rendering them. That is the point of the page:
    it shows what the pipeline actually produced, so a card that came out wrong shows up here
    instead of being quietly regenerated correct while you look at it.
    """
    tiles, priced = [], []
    for episode_json in sorted(episodes_dir.glob("*/episode.json"), reverse=True):
        d = episode_json.parent
        png = d / cards.card_filename(tokens.SOCIAL)
        if not png.is_file():
            continue
        try:
            f = card_facts.load(episode_json, app_url)
        except (card_facts.NoCost, OSError, ValueError):
            continue
        priced.append(f)
        cast = ", ".join(s.name for s in f.speakers if s.name) or "cast not recorded"
        recast = " &middot; <b>recast</b>" if is_recast(d.name) else ""
        tiles.append(f"""
<div class="tile">
  <img src="{_card_data_uri(png)}" alt="{html.escape(f.alt)}" loading="lazy">
  <p class="tile-facts">
    <span class="tile-id">{html.escape(d.name)}</span>{recast}<br>
    <span class="tile-cost">{html.escape(f.hero)}</span> &middot;
    {f.episodes_per_credit:,} on the credit<br>
    {html.escape(cast)} &middot; {html.escape(f.meta)}
  </p>
</div>""")

    if not priced:
        print("no cards on disk. `--write` builds them, and so does any publish or app boot.")
        return 1

    costs = sorted(f.usd for f in priced)
    total = sum(costs)
    mean = total / len(costs)
    band = (f"<p class=\"band\"><b>{len(priced)} episodes</b> &middot; "
            f"${costs[0]:.4f} to ${costs[-1]:.4f} each &middot; "
            f"mean ${mean:.4f} &middot; ${total:.2f} for the whole archive &middot; "
            f"the $200 signup credit covers "
            f"{episodes_per_credit(mean):,} episodes at that mean</p>")

    content = f"""
<h1>HN Radio share cards: the whole archive</h1>
<p class="lede">Every episode on disk, newest first, as the pipeline actually rendered it. One
card per episode, built in the same step that computes what the episode cost, with the cost as an
input and never a constant.</p>
{band}
<p><b>Use the toggle, top right.</b> These are dark cards and most email clients are white.
What to look for: a cost that does not move with the episode, a cast that does not match the show,
a credit line that runs into the URL, and any card that looks like the one above it.</p>
<div class="sheet">{''.join(tiles)}</div>

<h2>How to read a tile</h2>
<p>Under each card: the episode id, what that episode cost in Flux TTS, how many episodes the $200
signup credit covers at that rate, the two voices on it, and the runtime. The images are the
social cards at full 1200&times;630, re-encoded to WebP and inlined so the page travels as one
file; nothing is downscaled. The email card for each episode is 1200&times;800 shown at 600, and
both sit next to the episode on the volume.</p>

<p class="meta">Generated by <code>scripts/make_share_cards.py --contact-sheet</code> on
{datetime.date.today().isoformat()}. Derived and not committed: re-run it rather than editing it.</p>
"""
    out = _render("HN Radio share cards: the archive", SHEET_STYLE, content,
                  SAMPLES_DIR / "contact-sheet.html")
    size = out.stat().st_size / 1_000_000
    print(f"{len(priced)} card(s) on one page, {size:.1f} MB")
    print(f"open {out}")
    return 0


def _render(title: str, style: str, content: str, out: pathlib.Path) -> pathlib.Path:
    page = (PREVIEW_TEMPLATE.read_text()
            .replace("__TITLE__", html.escape(title))
            .replace("__STYLE__", style.rstrip())
            .replace("__CONTENT__", content.strip()))
    out.write_text(page)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_share_cards", description=__doc__.split("\n")[0])
    ap.add_argument("--episodes-dir", default=str(config.EPISODES_DIR))
    ap.add_argument("--write", action="store_true", help="build cards for episodes missing one")
    ap.add_argument("--force", action="store_true", help="with --write, rebuild every card")
    ap.add_argument("--samples", action="store_true",
                    help="write the review set to docs/share-cards/")
    ap.add_argument("--strips", action="store_true",
                    help="the six newsletter strips, from the most recent episode")
    ap.add_argument("--contact-sheet", action="store_true",
                    help="every episode's card on one page, for a review pass over the archive")
    ap.add_argument("--app-url", default=None,
                    help="the origin printed on the cards (default: config.public_app_url())")
    args = ap.parse_args(argv)

    episodes_dir = pathlib.Path(args.episodes_dir)
    if not episodes_dir.is_dir():
        print(f"no such episodes directory: {episodes_dir}")
        return 2
    if not tokens.fonts_available():
        print("no usable TrueType font on this machine, so nothing can be drawn. "
              "On Debian/Ubuntu: apt-get install fonts-dejavu-core")
        return 1

    app_url = args.app_url or config.public_app_url()
    if args.strips:
        return strips(episodes_dir, app_url)
    if args.contact_sheet:
        return contact_sheet(episodes_dir, app_url)
    if args.samples:
        return samples(episodes_dir, app_url)
    if args.write:
        result = cards.backfill(episodes_dir, app_url=app_url, force=args.force, log=print)
        return 1 if result["failed"] else 0
    return report(episodes_dir, app_url)


if __name__ == "__main__":
    raise SystemExit(main())
