"""First-listen panel episode, written in-session (Claude as the writer) from the live
2026-08-03 front page. Grounded in the actual sources (the seangoedecke post and the Swiftlet
README were read, not guessed). This was the SessionWriter bridge until the Anthropic key landed:
a hand-authored panel script fed through the real render_panel path.

IT USED TO PUBLISH TO THE LIVE FEED, and that is fixed here. `episode_id` was
`f"{date.today()}-panel"` at the TOP LEVEL of `episodes/`, and the only filter `feed.py` and
`manifest.py` apply is `is_recast`. So running this script put a brand-new dated directory in the
catalogue root and the August 3 title into both `index.json` and `feed.xml`, as a real episode, for
every subscriber. It is namespaced under `_firstlisten/` now, the same shape
`scripts/frame_experiment.py` uses through the same function, so it cannot reach the feed no matter
who runs it.

Worth being explicit about the class of bug rather than just the instance: the publish gate is
ALLOW-BY-DEFAULT. Anything that is not a recast is treated as a catalogue episode, so the next
stray episode_id reaches subscribers too. An explicit `YYYY-MM-DD[-edition]` predicate in
`manifest.py` would close it properly; this fixes the one caller that was actually pointed at the
root.

Run: uv run python scripts/first_listen.py
"""

import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import ingest, pipeline
from hn_radio.cast import Cast, Desk
from hn_radio.models import ScriptSegment

# FROZEN CAST, and it has to be a literal here rather than `cast.DEFAULT_CAST`.
#
# This script is a dated artifact: a hand-authored script from 2026-08-03 whose WORDS name Haley,
# Priya and Cole and whose lines are tagged with the themed desks the show had then. Those desks
# were deleted (see hn_radio/cast.py) and Priya was retired by ear, so
# reading the live cast would resolve `desk="ai"` to nothing, fall through to the host's voice, and
# render Priya's lines as Haley talking to herself while the text still says "Priya, this one's
# yours".
#
# Pinning the cast keeps the artifact reproducible. Do NOT rewrite the prose to the two-person
# show: it is a record of one episode that aired, not a template.
#
# THE SECURITY SEAT WAS MISSING FOR A WHILE, so the claim above was false for one line.
# `Cast.voice_for` returns `self.anchor.voice_id` for a role it has no desk for, and this cast
# declared ai, maker and drama only, so Jack's `desk="security"` line rendered in HALEY's voice
# while the text around it introduces Jack by name. The seat is declared below now. There is no
# recording to stay bug-compatible with: none of these speakers appear anywhere in `episodes/`,
# `.recast-backup/` or `.superseded-local-episodes/`, so nothing is being contradicted by making
# the script do what its own words say.
FIRST_LISTEN_CAST = Cast(
    anchor=Desk(role="anchor", name="Haley", voice_id="flux-haley-en",
                beat="Hosts", persona="Warm, quick, lightly wry."),
    desks=[
        Desk(role="ai", name="Priya", voice_id="flux-priya-en",
             beat="Models, research, ML tooling", persona="Precise, a little skeptical of hype."),
        Desk(role="maker", name="Wade", voice_id="flux-wade-en",
             beat="Repos, Show HN, personal blogs", persona="Delighted by handmade things."),
        Desk(role="security", name="Jack", voice_id="flux-jack-en",
             beat="Breaches, CVEs, systems", persona="Dry. Relieved when nothing happened."),
        Desk(role="drama", name="Cole", voice_id="flux-cole-en",
             beat="Performs comments", persona="Deadpan."),
    ],
    default_role="maker",
)

# --- resolve real source metadata + comment ids from live HN ---
STORY_IDS = [49161518, 49158333, 49162086, 49157930]
source_items = []
for sid in STORY_IDS:
    it = ingest._item(sid) or {}
    source_items.append({"hn_id": sid, "title": it.get("title", "?"), "url": it.get("url")})

# find the two comment ids we perform, so the page links to the exact comments
WANT = {"sothatsit": None, "DrBazza": None}
thread = ingest._item(49157930) or {}
for kid in thread.get("kids", []):
    if all(WANT.values()):
        break
    c = ingest._item(kid) or {}
    if c.get("by") in WANT and WANT[c["by"]] is None:
        WANT[c["by"]] = c["id"]

# --- the script ---
S = []


def add(role, speaker, text, desk=None, source_hn_id=None, voice_id=None):
    S.append(ScriptSegment(order=len(S), role=role, speaker_key=speaker, text=text,
                           desk=desk, source_hn_id=source_hn_id, voice_id=voice_id))


add("anchor", "Haley",
    "Good morning, it's Monday, August 3rd. This is HN Radio, the Makers edition, and the desk "
    "is in. The front page cannot stop arguing about whether the machines are smarter than us "
    "yet, so let's get into it.",
    desk="anchor")

add("anchor", "Haley",
    "Top of the page, 669 points: LLMs reward expertise. Priya, this one's yours.",
    desk="anchor", source_hn_id=49161518)
add("desk", "Priya",
    "So this one's counterintuitive. Everybody says AI flattens the playing field. This post "
    "argues the opposite: the better you already know your field, the more you get out of the "
    "model. The line I keep coming back to is, the human is the bottleneck, not the model.",
    desk="ai", source_hn_id=49161518)
add("anchor", "Haley", "So the experts pull ahead, not the beginners.", desk="anchor")
add("desk", "Priya",
    "Right. The example is Terence Tao steering ChatGPT. A world-class mathematician gets a "
    "world-class answer, because he can push back and say, that isn't simpler, try again. You "
    "and I would just nod and accept it.",
    desk="ai", source_hn_id=49161518)

add("anchor", "Haley",
    "Cheerful. Wade, maker desk, you found the Show HN of the day, and it's a good one.",
    desk="anchor", source_hn_id=49158333)
add("desk", "Wade",
    "Oh, it's great. It's called Swiftlet. Someone got an 80B Qwen model running in 4.3 GB of "
    "RAM on a Mac. And a 35B on an iPhone 17.",
    desk="maker", source_hn_id=49158333)
add("anchor", "Haley", "That sounds impossible.", desk="anchor")
add("desk", "Wade",
    "The trick is these are mixture-of-experts models, so only about 3B parameters actually fire "
    "per token. Swiftlet streams the rest off disk on demand, repacks the experts for a single "
    "read, and keeps a little cache of the ones you hit most. All running on Metal.",
    desk="maker", source_hn_id=49158333)
add("desk", "Wade",
    "And the README is honest about the catch. Quote: these models chat and write like large "
    "models, but recall facts like small ones. No overselling. I respect it.",
    desk="maker", source_hn_id=49158333)

add("anchor", "Haley",
    "A benchmark that admits its own weakness. Rare. Jack, security desk, anything on your beat?",
    desk="anchor")
add("desk", "Jack",
    "Quiet morning. Nobody got breached, nobody leaked a database. Somebody did resurrect Windows "
    "XP on an Itanium and described the experience as, and I'm quoting the title, unbridled rage. "
    "That's the closest thing to a threat I've got.",
    desk="security", source_hn_id=49162086)

add("anchor", "Haley",
    "We'll take the quiet. Now the busiest thread on the site: 764 comments on ten advances in "
    "mathematics and theoretical computer science. Cole, drama desk, what's the room like?",
    desk="anchor", source_hn_id=49157930)
add("desk", "Cole",
    "The room is losing it, in the best way. It's a philosophy seminar that skipped its meds. "
    "Here's the range.",
    desk="drama", source_hn_id=49157930)
add("commenter", "sothatsit",
    "People argue whether we're at y minus 5, y, or y plus 5. Meanwhile we seem to be on a y "
    "equals 2 to the x exponential that keeps delivering more and more impressive results.",
    source_hn_id=WANT["sothatsit"], voice_id="flux-drew-en")
add("desk", "Cole", "Confident. And then the reply guy arrives.", desk="drama")
add("commenter", "DrBazza",
    "Replace philosophers with mathematicians and Douglas Adams was spot on again. Current models "
    "can't intuit and come up with conjectures. But give them time.",
    source_hn_id=WANT["DrBazza"], voice_id="flux-bruce-en")
add("desk", "Cole", "Never change, Hacker News.", desk="drama")

add("anchor", "Haley",
    "That's the front page. Experts win, phones run 80B models, and the mathematicians are "
    "nervous. Links to everything are in the show notes. I'm Haley, this was HN Radio. Go touch "
    "grass.",
    desk="anchor")

# --- render it ---
# `_firstlisten/` prefix, NOT a top-level dated id. See the module docstring: without it this
# script publishes to the live feed. Same shape scripts/frame_experiment.py uses.
pipeline.render_panel(
    S,
    episode_id=f"_firstlisten/{date.today().isoformat()}-panel",
    title="HN Radio - Aug 3: Experts win, phones run 80B models, mathematicians nervous",
    source_items=source_items,
    cast=FIRST_LISTEN_CAST,
    edition="makers",
)
