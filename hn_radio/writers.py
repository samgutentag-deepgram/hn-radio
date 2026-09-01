"""Stage v2-5 - The panel writer. Turn sourced stories + comments into a two-hander script.

`ScriptWriter` is the seam. Two implementations share it:
  - `PanelWriter`  : deterministic, offline, uses the extractive source summaries. Works now.
  - `ClaudeWriter` : the LLM version (Claude Opus 5) for real banter. This is what ships nightly.

Both return the same thing: an ordered list of plain-text ScriptSegments tagged with a speaker
and a seat role. No markup, ever. Expression comes from Flux reading the words.

Two people as of 2026-08-20: the host and one co-host, both of whom cover every story AND perform
the comments. See `cast.py` for what that replaced. Two consequences live in this file:

  - There is no comment-theater voice any more. A performed comment is read by whichever regular
    has it, so the segment carries a REGULAR's `voice_id` while keeping the commenter's real
    username in `speaker_key` and the real comment id in `source_hn_id`. Those three fields are
    read by `transcript.build_vtt`, by the episode page, and by `chapters.build_chapters`, so
    collapsing `speaker_key` onto the performer would destroy the record of who actually said it
    while the audio still sounded fine. It is the one thing in here that must not drift.
  - The cold open is ONE segment, in both writers. See `_COLD_OPEN_NOTE` below; that is a real
    audio defect being fixed, not tidiness.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import List, Optional

from . import config
from .cast import COHOST_ROLE, Cast
from .models import Comment, ScriptSegment, Story
from .script_assembly import _trim_to_sentence, clean_comment_html, is_safe

MAX_TAKE_CHARS = 320       # keep a take listenable
MAX_COMMENT_CHARS = 320

# Why both writers emit the cold open as a single segment, and must keep doing so.
#
# One segment is one batch TTS call, so every pause inside the read comes from punctuation Flux is
# reading. Split across segments, the pauses come from the gaps `stitch` inserts BETWEEN renders
# instead, and each piece also carries its own sentence-final fall and its own onset.
#
# Measured on the 2026-08-20 episode, where ClaudeWriter emitted four cold-open segments (an
# opener plus one per headline): segment starts at 7.16, 15.407, 21.847, 26.263 -- roughly 2.9s of
# dead air between the first two beats, for a line that is about 3.5s of speech.
#
# An A/B on 2026-08-09 settled the direction by ear: "the one read feels most coherent... the issue
# with the three renders is that the inflection and the voice changes so much between them that it
# feels very stark jumps". So if the merged read ever comes back feeling too FAST, re-splitting it
# is the one repair that is known not to work -- separate renders are what causes the stark jumps.
# The remedy is to even out the silences Flux leaves between the sentences INSIDE this single
# segment, which is a spacing pass on rendered PCM: `pacing.set_internal_pauses`, wired in by
# `pipeline._space_cold_open`. `cold_open_index` and `cold_open_pause_count` below are the two
# things that pass reads out of the script, so it never re-derives either rule for itself.


def cold_open_index(segments) -> Optional[int]:
    """Index of the cold open in a finished script, or None if the episode has none.

    The cold open is the last leading segment that is the host talking and is NOT tagged with a
    story: the fixed intro is untagged too and comes first, and story coverage starts at the first
    segment carrying a `source_hn_id`. Structural, so a rewritten cold open cannot move it, which
    is the same rule `music.BED_SEGMENTS` relies on.

    Written to be called, not merely to document the invariant. A per-sentence spacing pass over
    the cold open's rendered PCM (see the comment above) needs exactly this index and nothing
    else, and re-deriving "which segment is the cold open" at that layer is how the two would
    drift apart. An episode with no stories (custom.py can produce one) has no cold open at all.
    """
    found = None
    for i, seg in enumerate(segments):
        if seg.source_hn_id:
            break
        if seg.desk == "anchor":
            found = i
    # Index 0 is the pipeline's fixed intro, never the cold open. A script that is nothing but
    # untagged anchor lines has no cold open to point at.
    return found if found else None


# A sentence ends at .!? plus any closing quote or bracket, and is followed by whitespace. The
# trailing full stop of the whole read is deliberately NOT a match: it is where the cold open
# ends, and the pacing gap after the segment already owns that boundary.
_SENTENCE_GAP = re.compile(r"[.!?][\"'\)\]]*\s")


def cold_open_pause_count(text: str) -> int:
    """How many pauses a cold-open read has INSIDE it: one per gap between its sentences.

    The spacing pass needs this and cannot get it from the story list. Both writers put one
    sentence per story in the read today, so the two counts happen to agree, but only one of them
    is the thing being spaced: an LLM that returns four sentences for three stories, or a
    `PanelWriter` shape that ever puts its "Today:" lead-in in a sentence of its own, would leave
    a real boundary unspaced while the story count still looked right. The audio follows the
    words, so the count does too. A one-story episode (`custom.py` can produce one) has no
    internal boundary at all and correctly counts zero.

    Counted rather than thresholded on purpose. The spacing pass stretches the N longest silence
    runs in the render, and on real audio a headline boundary (0.13s on 2026-08-20) can be shorter
    than another headline's internal breath (0.12s), so a length threshold would pick the wrong
    runs. N comes from the words; only the choice of WHICH runs comes from the audio.
    """
    return len(_SENTENCE_GAP.findall((text or "").strip()))


class ScriptWriter(ABC):
    @abstractmethod
    def write(self, stories: List[Story], top_story: Optional[Story], comments: List[Comment],
              cast: Cast, edition: str, episode_date: date) -> List[ScriptSegment]:
        ...

    def episode_title(self) -> Optional[str]:
        """A writer-chosen episode title, or None to fall back to the mechanical title."""
        return None

    def episode_summary(self) -> Optional[str]:
        """A 1-2 sentence episode summary for the show notes, or None if the writer has none."""
        return None


class PanelWriter(ScriptWriter):
    """Deterministic two-hander: cold open -> per-story exchange, with the comment theater playing
    inside the busiest story's coverage -> back to the host.

    Mirrors the LLM writer's SHAPE so a fallback night is the same show with plainer words. It
    cannot mirror its taste, and does not try to: where `ClaudeWriter` is told to use each other's
    names "when it feels natural", this uses them at fixed points only (the throw into each
    story, and the hand back out of the comments) and nowhere else. Simulated variety from a
    deterministic writer reads worse than obvious restraint, because the same three canned phrases
    arrive every night either way and only one of those options is honest about it.
    """

    # No lead-in. These used to be {"repo": "From the README: ", "article": "From the
    # write-up: "}, which put sourcing narration on EVERY desk line of a fallback night: eight
    # such lines shipped across 2026-08-05 and 2026-08-06. The show may attribute to a person
    # or a publication, but naming the artifact the pipeline fetched is the frame break. This
    # writer only has the HN submitter, never the article's author, so it cannot attribute
    # honestly and therefore says nothing rather than something false.

    # ONE invitation per story, keyed to the story's index and cycled. This is the beat the
    # 2026-08-20 episode was missing: the host read a headline and the co-host simply started
    # talking, so every story after the first cut straight from voice to voice. Sam, having
    # listened to it: it "cuts just from voice to voice".
    #
    # This replaces the older rule that only the FIRST throw of the show carried a name, on the
    # reasoning that every later throw could then be a bare "Next up" the way people actually
    # talk. That is true of a mid-story turn and false of a story opening: mid-story the two of
    # them are already in a conversation, whereas the top of a story is a change of direction
    # with nothing in front of it, so a bare headline there is a cut rather than a transition.
    #
    # A fixed set rotated by POSITION, not one string repeated and not a shuffle. Three stories
    # carrying three copies of one question would read WORSE than the cut it replaces: a cut
    # merely sounds terse, while a repeated formula sounds like a template, and a listener
    # forgives the first and not the second. It is the same structural alternation
    # `_comment_theater` already uses for its two lead-in forms, and for the same reason.
    #
    # Cycled rather than fixed at exactly three, because `custom.py` sends up to six stories
    # through here (MAX_STORIES). A form returning five stories later is inaudible; two
    # identical throws back to back is not, and cycling is what makes the second impossible.
    #
    # First position is a question with somewhere to go, because that is the throw Sam singled
    # out ("especially at maybe the first story to say what do you think about this co-host")
    # and it is where a listener decides whether this is two people or two recordings. Second
    # position is deliberately NOT a question: three questions in a row is the formula.
    #
    # Every one of these asks about the STORY. None asks what the show has to work with ("what
    # have we got on this one"), which is the frame break `scripts/frame_lint.py` exists to
    # catch and which this writer, holding only a title and an extractive summary, is the most
    # prone to of anything in the repo.
    _INVITATIONS = (
        "{cohost}, where do you want to start on this one?",
        "You first, {cohost}.",
        "{cohost}, what do you make of it?",
    )

    def write(self, stories, top_story, comments, cast, edition, episode_date):
        segments: List[ScriptSegment] = []

        def add(role, speaker, text, desk=None, source_hn_id=None, voice_id=None):
            segments.append(ScriptSegment(order=len(segments), role=role, speaker_key=speaker,
                                          text=text, desk=desk, source_hn_id=source_hn_id,
                                          voice_id=voice_id))

        host = cast.anchor  # the show's fixed intro/outro are added by the pipeline, not here
        # The second chair. `cast.cohost` prefers the "cohost" role and falls back to desks[0],
        # which is what keeps custom.py working: its build-your-own casts seat topic desks
        # (ai / maker / security) rather than a co-host, and the first one picked takes every
        # story here. That is a real reduction for custom editions -- they used to route each
        # story to the desk whose beat matched -- and it is the documented cost of deleting the
        # routing. The topic FILTER that decides which stories a custom episode contains is
        # untouched; only which chosen voice reads them has changed.
        cohost = cast.cohost or host
        # `role` on a segment is the coarse kind ("anchor" | "desk" | "commenter") while `desk` is
        # the seat. They have to agree, and the one case where they can disagree is a cast with no
        # second seat at all -- custom.py can build one -- where the co-host IS the host and her
        # lines must not be labelled "desk". `solo` also suppresses the two spoken names, since a
        # host saying her own name to hand to herself is a glitch a listener can hear.
        solo = cohost is host or cohost.role == host.role
        cohost_kind = "anchor" if solo else "desk"

        # No cap here. The caller decides how many stories an episode covers: `run_panel` hands
        # over exactly `n_stories`, and a build-your-own edition (custom.py, MAX_STORIES = 6)
        # hands over up to six. A cap inside the writer covered three of those six while the
        # chapters, title and source_items still listed all six, so half a custom episode
        # existed everywhere except the audio.
        covered = stories

        # Which story the comment theater follows. Every comment in `comments` comes from ONE
        # thread (the pipeline calls `ingest.fetch_top_comments(top, ...)` on the busiest one
        # only), so the theater has exactly one story it belongs to, and it plays there rather
        # than after the whole rundown: end-loaded, the show covered A, B and C and then jumped
        # back to whichever one was busiest, minutes after the listener heard it.
        #
        # None means there is no inline home for it: no busiest thread at all, or a busiest
        # thread that is not among the stories this episode covers (custom.py picks from a
        # rotating source pool, so the two can disagree). In that case the theater plays at the
        # end of the show, where it always used to. Late is worse than inline; silently dropping
        # real comments is worse than late.
        inline_after = None
        if top_story is not None and any(s.id == top_story.id for s in covered):
            inline_after = top_story.id

        # COLD OPEN: one sentence per story before any of them is covered, so the shape of the
        # episode arrives in the first fifteen seconds. ONE `add` call, which is the whole point;
        # see the note at the top of this module. Skipped entirely with no stories: custom.py can
        # reach here with an empty pick when the source pool has rotated, and a bare "Today: " is
        # a rendered segment that says nothing.
        if covered:
            headline = " ".join(f"{s.title}." for s in covered)
            add("anchor", host.name, f"Today: {headline}", desk="anchor")

        for i, story in enumerate(covered):
            points = "1 point" if story.points == 1 else f"{story.points} points"
            headline = (f"Top story, {points}: {story.title}." if i == 0
                        else f"Next up, {points}: {story.title}.")
            if solo:
                # Nobody to hand to. A host inviting herself by name is a glitch a listener can
                # hear, which is the same reason `solo` suppresses the theater's hand back.
                lead = headline
            else:
                # The invitation is another SENTENCE in the same segment, never a segment of its
                # own. One segment is one batch TTS call, so splitting a five-word question off
                # would buy it an inter-segment pacing gap and its own onset in exchange for
                # nothing. That is the cold open's lesson at a smaller scale; see the note at the
                # top of this module for the measurements.
                invite = self._INVITATIONS[i % len(self._INVITATIONS)].format(cohost=cohost.name)
                lead = f"{headline} {invite}"
            add("anchor", host.name, lead, desk="anchor", source_hn_id=story.id)

            if story.summary:
                # No _SOURCE_LEAD prefix. It opened every take with "From the write-up: ",
                # which names the artifact the pipeline fetched instead of the person who wrote
                # it; frame_lint flags four of them on 08-05 and 08-06 alone.
                add(cohost_kind, cohost.name, _trim_to_sentence(story.summary, MAX_TAKE_CHARS),
                    desk=cohost.role, source_hn_id=story.id)
            else:
                # Was "No page to read on this one, just the headline, but the thread is busy."
                # Same frame break as the lead-in above, armed on every sourceless story. The
                # replacement points at the comments, which is a true observation about the
                # story rather than a report on what the pipeline managed to fetch.
                add(cohost_kind, cohost.name, "The thread's the story on this one.",
                    desk=cohost.role, source_hn_id=story.id)

            if story.id == inline_after:
                self._comment_theater(add, host, cohost, top_story, comments,
                                      inline=True, solo=solo)

        if inline_after is None:
            self._comment_theater(add, host, cohost, top_story, comments,
                                  inline=False, solo=solo)
        return segments

    def _comment_theater(self, add, host, cohost, top_story, comments, *, inline: bool,
                         solo: bool):
        """The performed comments, read by the two regulars themselves.

        No third voice. Until 2026-08-20 a separate guest voice was hashed from the commenter's
        username, so a regular reader recognised @dang before the name was said; that hook is gone
        and the simplification is the point. What replaces it is the performer SAYING the username
        out loud before reading the words, because with no voice change to signal it a listener
        otherwise cannot tell a quote from the co-host's own opinion.

        The two regulars alternate rather than one taking them all: two comments read in one voice
        with only "and" between them is one person contradicting themselves, which is the same
        failure the old three-voice guest pool was widened to avoid.
        """
        clean = []
        for c in comments:
            body = clean_comment_html(c.text)
            if body and is_safe(body):
                clean.append((c, _trim_to_sentence(body, MAX_COMMENT_CHARS)))
        if not (top_story and clean):
            return
        if inline:
            # Playing right after that story's take, so the listener has just heard the title
            # twice (cold open, then the host's throw). Saying it a third time to introduce
            # the comments is what made the old shape sound like a recap. Point at the thread
            # instead and let the story it belongs to be the one the show is already on.
            add("anchor", host.name,
                "That thread was the busiest on the site. Let's read a couple.",
                desk="anchor", source_hn_id=top_story.id)
        else:
            # End-of-show fallback: this story was never covered, so the listener has no idea
            # which thread these comments come from. Here the title IS the context.
            add("anchor", host.name,
                f"The busiest thread was {top_story.title}. Let's read a couple.",
                desk="anchor", source_hn_id=top_story.id)

        for i, (comment, body) in enumerate(clean):
            # Co-host first. The host has just framed the segment, so starting with her again
            # would put three of her lines in a row at the top of the theater.
            performer = cohost if i % 2 == 0 else host
            kind = "anchor" if performer.role == "anchor" else "desk"
            # NAMING THE COMMENTER IS LOAD-BEARING, not flavour. This is the only cue a listener
            # gets that the next line is a quote and whose it is, now that the voice does not
            # change. Same voice as the quote itself, deliberately: one person reading somebody
            # else's words out is what a radio host actually does.
            #
            # Two forms, keyed to position rather than shuffled. The first one introduces the
            # segment's first quote; every later one is a reply arriving, and "X says this"
            # repeated verbatim is the relay cadence in miniature. This is the same kind of fixed
            # structural alternation as who performs the quote, not an attempt at variety.
            lead_in = (f"{comment.author} says this." if i == 0 else f"Then {comment.author}.")
            add(kind, performer.name, lead_in,
                desk=performer.role, source_hn_id=top_story.id)
            # role="commenter", the REAL username, and the REAL comment id, with a regular's
            # voice pinned on top. Everything downstream that records who said what reads those
            # three fields, and `voices.assign_voices` leaves a pinned voice alone.
            add("commenter", comment.author, body, source_hn_id=comment.id,
                voice_id=performer.voice_id)

        # Tagged with the story, unlike the quotes inside the theater: this is the last line of
        # that story's coverage, so `pacing.boundary_kind` must read the boundary into the NEXT
        # story as a story change (0.85s) rather than an ordinary exchange (0.16s). An untagged
        # line made the show sprint out of the comments straight into a new headline. It cannot
        # affect a sting or a chapter, both of which key on a story id's FIRST occurrence and
        # this is never the first.
        #
        # The co-host hands back BY NAME here, and this is the second and last fixed naming point.
        # It is the show's one genuine change of direction -- out of other people's words and back
        # into the rundown -- which is exactly where a name earns itself.
        closer = ("Never change, Hacker News." if solo else
                  f"Never change, Hacker News. Back to you, {host.name}.")
        add("anchor" if cohost.role == "anchor" else "desk", cohost.name, closer,
            desk=cohost.role, source_hn_id=top_story.id)


SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},    # a punchy "quick hits" episode headline
        "summary": {"type": "string"},  # 1-2 sentence show-notes summary
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["anchor", "desk", "commenter"]},
                    "desk": {"type": "string"},          # desk role for 'desk' lines; "anchor"; "" for commenter
                    "speaker_key": {"type": "string"},   # commenter username; "" otherwise
                    "text": {"type": "string"},
                    "source_hn_id": {"type": "integer"},  # story/comment id, or 0
                },
                "required": ["role", "desk", "speaker_key", "text", "source_hn_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["title", "summary", "segments"],
    "additionalProperties": False,
}

SOURCE_CHARS = 1600     # per-story source text handed to Claude
COMMENT_CHARS = 400
AIM_WPM = 140           # aim under the real pace so the (verbose) model lands in range
MAX_WPM = 160           # hard ceiling pace, so an overshoot still stays inside the target minutes


class ClaudeWriter(ScriptWriter):
    """LLM writer (Claude Opus 5). Writes a real, longer, grounded two-hander script.

    Hands Claude the sourced stories + the busiest thread's real comments + the two regulars,
    with the comments placed inside that thread's own coverage rather than after the rundown,
    and gets back a validated segment array via structured outputs. On any refusal, API error, or
    malformed output it raises RuntimeError so the caller can fall back to PanelWriter.
    """

    def __init__(self, model: str = "claude-opus-5", target_minutes: int = 5,
                 substitutions: Optional[dict] = None):
        self.model = model
        self.target_minutes = target_minutes
        # {role: name of the character the show wanted but could not cast today}
        self.substitutions = substitutions or {}
        # Whether the CALLER set substitutions. The pipeline fills this in per run, and must be
        # able to overwrite its own previous value without clobbering a caller's explicit one.
        self._caller_set_substitutions = substitutions is not None
        self._title: Optional[str] = None
        self._summary: Optional[str] = None

    def episode_title(self):
        return self._title

    def episode_summary(self):
        return self._summary

    def write(self, stories, top_story, comments, cast, edition, episode_date):
        import anthropic  # lazy: the LLM path is opt-in, so its dependency is too

        system, user = self._build_prompt(stories, top_story, comments, cast, edition, episode_date)
        try:
            client = anthropic.Anthropic(api_key=config.get_anthropic_key())
            with client.messages.stream(
                model=self.model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium",
                               "format": {"type": "json_schema", "schema": SEGMENT_SCHEMA}},
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIError as e:
            raise RuntimeError(f"Anthropic API error: {e}") from e

        if message.stop_reason == "refusal":
            raise RuntimeError("Claude declined to write this script (refusal).")
        text = next((b.text for b in message.content if b.type == "text"), None)
        if not text:
            raise RuntimeError("Claude returned no script text.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Claude script was not valid JSON: {e}") from e
        self._title = (data.get("title") or "").strip() or None
        self._summary = (data.get("summary") or "").strip() or None
        segments = self._to_segments(data.get("segments", []), cast)
        if len([s for s in segments if s.role == "anchor"]) < 1 or len(segments) < 4:
            raise RuntimeError("Claude script was too short or missing an anchor.")
        return segments

    def _build_prompt(self, stories, top_story, comments, cast, edition, episode_date):
        from .editions import EDITION_TITLES  # local import avoids a cycle

        words = self.target_minutes * AIM_WPM
        ceiling = self.target_minutes * MAX_WPM
        # Derived, never hardcoded: `--stories N` is a real CLI parameter, so a literal "three"
        # here told Claude to cover 3 while the pipeline handed it 5 and listed all 5 in the
        # chapters and the title.
        n_stories = len(stories)
        story_word = "story" if n_stories == 1 else "stories"
        anchor = cast.anchor
        cohost = cast.cohost or anchor
        desk_enum = " | ".join(f"'{d.role}'" for d in cast.desks) or f"'{COHOST_ROLE}'"
        regulars = "\n".join(f"- {d.name}: {d.beat} Tone: {d.persona}" for d in cast.desks)

        sub_line = ""
        if self.substitutions:
            who = "; ".join(f"{name} (the usual {role})"
                            for role, name in sorted(self.substitutions.items()))
            sub_line = (
                f"\nCAST NOTE: {who} is out today, so someone else is covering. Acknowledge "
                "this ONCE, early, in a single natural line, the way a real show mentions an "
                "absent host. Do not explain it, do not apologise for it, and never mention "
                "voices, models, or technology.\n")

        system = (
            "You are the head writer for HN Radio, a short daily podcast that reads the Hacker "
            "News front page as a produced show. You write the full spoken script.\n\n"
            f"It is a TWO-HANDER: {anchor.name} hosts ({anchor.persona}), and "
            f"{cohost.name} is in the second chair with her today.\n{regulars}\n"
            "They are two colleagues covering the same stories together, equals in the room. "
            f"{cohost.name} is not a beat reporter being handed a topic and is never introduced "
            "as one: both of them read, react, disagree and follow up on everything. The show "
            "has no specialist desks, so never write a line that assigns a subject to one of "
            f"them, and never have {anchor.name} thank {cohost.name} for a report.\n"
            f"{sub_line}\n"
            "STRUCTURE, in this order:\n"
            f"1. COLD OPEN: {anchor.name} gives ONE sentence per story, all {n_stories}, each "
            "TEN WORDS OR FEWER. That cap is hard. These are HEADLINES, roughly the title of "
            "the article and nothing else: name the thing and what happened to it, then stop. "
            "No context, no numbers, no figures, no analysis, no verdict, and NO SUBORDINATE "
            "CLAUSES. If the sentence needs a comma it is already too long; cut it rather than "
            "rephrase it. The story's own coverage carries every detail, so anything you are "
            "tempted to add here already has a home later. Example of the RIGHT length: "
            "\"A court fined Meta another half a billion dollars.\" Example of TOO LONG: "
            "\"A New Mexico court has ordered Meta to pay another five hundred and sixty-seven "
            "million dollars over harms to kids' mental health, pushing the total past nine "
            "hundred million.\"\n"
            "   Return the WHOLE cold open as a SINGLE segment: one 'anchor' entry whose text "
            "contains all of those sentences, not one entry per headline. This is an audio "
            "requirement, not a formatting preference. Each segment is rendered by a separate "
            "text-to-speech call, so splitting the cold open puts a pause between every headline "
            "that no punctuation asked for, and gives each one its own falling ending. Measured "
            "on a real episode: about 2.9 seconds of silence between two headlines that are 3.5 "
            "seconds of speech. Let the full stops between the sentences do the pacing.\n"
            "   Give the cold-open segment source_hn_id 0: it previews the stories, it is not "
            "any one story's coverage, and tagging it with a story id opens that chapter marker "
            "in the wrong place.\n"
            "   The FINAL cold-open sentence must start with \"And\": a run of headline "
            "sentences that just stops reads as cut off, and \"And\" is what tells the ear "
            "this is the last one, so the preview resolves as a list instead of trailing off. "
            "\"And\" counts toward that story's own ten-word cap, so trim the rest of the "
            "sentence to fit it in.\n"
            "   Read the cold open as MATTER-OF-FACT as possible. It is reporting information, "
            "not selling the episode: no build-up, no teasing what is coming, no verdict on any "
            "of it. The reactions belong in the coverage.\n"
            f"2. Then cover each story properly, {anchor.name} and {cohost.name} together.\n"
            # The handoff block. Added after the first native two-person episode (2026-08-20),
            # where the diagnosis from the actual script was narrow: mid-story exchanges were
            # good, and every story's FIRST co-host turn had no invitation in front of it at all.
            # Names appeared twice in twenty-five segments. So this adds a beat per STORY and
            # says so, rather than a rule per exchange, which is the thing this show already
            # tried and cut (HARD RULE 2, which is left standing and pointed at from here).
            #
            # Variety is asked for STRUCTURALLY, by enumerating three different forms and
            # forbidding a repeat, because "vary the wording" reliably returns one question
            # rephrased three ways. Naming a form the model has to abandon is a constraint it can
            # actually check itself against; naming an aspiration is not. The explicit "it does
            # not have to be a question" is there because two of the three forms are statements
            # and a model reading "invitation" will otherwise write three questions.
            "   HAND EACH STORY OVER. The second person to speak on a story does not just start "
            "talking: the line right before their first turn on it is an invitation that says "
            "their name and leaves them something to answer. Without it the episode cuts from "
            "voice to voice at the top of every story, which is what the last one did and is the "
            "first thing a listener notices.\n"
            "   ONE per story, and that is the whole quota. It is a fixed beat of the show, not "
            "a license to name each other again inside the story. Everything after the handoff "
            "is governed by HARD RULE 2 below, and inside a story a bare follow-up with no name "
            "in it is usually the better line. The handoff is also NOT the follow-up in HARD "
            "RULE 3: that one lands after their take, in reply to something the take raised. "
            "Every story needs both.\n"
            "   USE A DIFFERENT SHAPE EACH TIME, and shapes, not synonyms: one question "
            "rephrased three ways is still the same template three times. Three forms are on the "
            "table:\n"
            "     - a real question put to them about the substance of the story;\n"
            "     - a lead-in they finish, where you say the setup and stop, so their first "
            "words complete your sentence;\n"
            "     - a framing they can argue with, where you say what you think the story is and "
            "let them take the other side.\n"
            "   It does not have to be a question. Never use the same form on two stories in a "
            "row, and if this episode has more stories than there are forms, come back to a form "
            "only with genuinely different words.\n"
            f"   The FIRST story gets the question, and give it the most room: it is where a "
            f"listener decides whether this is two people or two recordings, so have "
            f"{anchor.name} actually ask {cohost.name} something, and have {cohost.name} open by "
            "answering the thing that was asked rather than starting a prepared take.\n"
            "3. THE COMMENT SEGMENT PLAYS INSIDE THAT STORY'S COVERAGE. The source material "
            "marks ONE story as the busiest thread and gives you its real comments. Run them "
            f"as soon as that story's coverage is finished, with {anchor.name} and "
            f"{cohost.name} reading the comments THEMSELVES and taking turns, and only then "
            "move on to the next story. Nobody else is on the show, so whichever of them is "
            "about to read a comment must SAY THE COMMENTER'S USERNAME first, in their own "
            "words, before the quoted line. Their voice does not change for a quote, so the "
            "name is the only thing telling a listener these are somebody else's words.\n"
            "   Do NOT hold them until the end of the show: by then the listener heard that "
            "story minutes ago, so "
            "coming back to it reads as jumping backwards through the episode. The other "
            "stories have no comments and get no comment segment, which makes the show "
            "deliberately lopsided; that is intended, so do not invent reactions to even it "
            "out.\n"
            "The show already has a FIXED intro and outro added automatically, so do NOT write "
            "a greeting or a sign-off.\n\n"
            "HARD RULES:\n"
            "1. Output PLAIN SPOKEN TEXT only. No markdown, no headings, no stage directions, "
            "no emoji, no SSML, no bracketed cues. Every character is spoken aloud by a TTS "
            "voice.\n"
            "2. WRITE HOW PEOPLE ACTUALLY TALK. Do NOT use a formal hand-off on every "
            "exchange. Real colleagues do not say each other's names every turn, and a script "
            "that does sounds like a relay rather than a conversation. This was tried on this "
            "show and cut for exactly that reason, so do not reintroduce it. The named "
            "handoff into each story, in STRUCTURE 2 above, is the one fixed exception, "
            "and it is once per STORY, at the beat where the show changes direction, "
            "not once per turn.\n"
            "   The two of them ARE free to use each other's names, and it is good when they "
            "do: it is what makes the show two people rather than a narrator with a second "
            "audio track. Use a name when it feels natural to say one out loud -- a real "
            "question put to the other person, a disagreement, a change of subject -- and leave "
            "it out the rest of the time. There is no target and no minimum. Read the line back "
            "and ask whether a person would say the name there; if the answer is no, cut it. "
            "Judge each one on its own rather than spacing them out to look varied.\n"
            "   Never have anyone introduce themselves. The show's fixed opening already names "
            "both of them before your first line, so a written introduction is the second one "
            "in twenty seconds.\n"
            "3. Each story gets ONE real follow-up: one of them asks something specific that "
            "the other's take raised and did not answer, and gets an answer. A real question "
            "about the substance, never a prompt to keep talking. Either of them can be the one "
            "asking; the host is not the only one allowed to be curious.\n"
            "4. You MAY use a callback: a later story referring to something from an earlier "
            "one in this episode. Use at most one, and only if the connection is real. A forced "
            "callback is worse than none.\n"
            "5. Ground every claim in the SOURCE MATERIAL provided. Do not invent facts, "
            "numbers, or quotes. ATTRIBUTE freely and by name, the way a real reporter "
            "does: \"the piece argues\", \"Berger writes\", \"Mistral's own claim is\". That "
            "is how the show carries a claim it did not verify itself.\n"
            "6. NEVER ASSESS THE SOURCE ITSELF. In the world of this show there are no "
            "sources, pages, write-ups, fetches, links or research: there are two people "
            "who know things. Never say how much material you had, that something was "
            "missing, short or thin, that you would rather not guess, or that this is all "
            "there is. When a story is thin, SAY LESS ABOUT IT and move on to the next one. "
            "A listener cannot hear the difference between a short take and a thin source, "
            "and must never be told there is one. Banned outright, in any wording: \"that's "
            "all the page gives us\", \"no fetched page\", \"just the headline\", \"not much "
            "beyond the headline\", \"I'd rather not speculate\", \"I'll leave it there\".\n"
            "7. Perform comments faithfully: you may lightly trim a real comment for length, "
            "but do not fabricate comments or change their meaning. Use the commenter's "
            "username as the speaker for those lines, exactly as given, including for a comment "
            "one of the regulars reads aloud. That username is the show's record of who said "
            "it.\n"
            "8. Lively, specific, fast. Warm and a little wry, never fawning. No AI cliches "
            "(no 'delve', 'leverage', 'in today's fast-paced world', 'buckle up').\n"
            f"9. LENGTH (strict): aim for about {words} words and DO NOT exceed {ceiling} "
            f"words (~{self.target_minutes} minutes read aloud). Cover exactly {n_stories} "
            f"{story_word}, every one you are given. Do not drop any, and do not pad.\n"
            "10. NEVER HAVE ONE OF THEM RESTATE WHAT THE OTHER JUST SAID. A reply must add a new "
            "fact, a new angle, or push back on something specific -- not paraphrase the "
            "previous line back in different words. This happens most on an easy story, where "
            "both of them independently land on the same obvious reaction. If they genuinely "
            "agree, one of them says so in a single beat and the conversation moves forward; it "
            "does not make the same point twice in a row.\n\n"
            f"Return the script as segments. Each: role ('anchor' for {anchor.name}'s lines, "
            f"'desk' for {cohost.name}'s, 'commenter' for a quoted HN comment); "
            f"desk ({desk_enum} for 'desk' lines; 'anchor' for anchor lines; '' for "
            "commenter lines); speaker_key (the commenter's username for 'commenter' lines, '' "
            "otherwise); text (the spoken line); source_hn_id (the story or comment id this "
            "line is about, or 0).\n\n"
            "Also return a 'title': a short, punchy quick-hits headline for the episode that "
            "names two to four of the day's standout items (or one standout line if it is stronger). No "
            "show name, no date, under about 12 words.\n"
            "And a 'summary': one or two sentences for the show notes describing what this episode "
            "covers, in the show's warm, wry voice."
        )

        edition_name = EDITION_TITLES.get(edition, "Front Page")
        # Both dates, spelled out, because handing over one invites the model to call the story day
        # "today" -- the exact off-by-one the fixed intro used to have (see `pipeline._INTRO`).
        # The episode covers one day and airs the next morning.
        air = episode_date + timedelta(days=1)
        lines = [f"Edition: {edition_name}.",
                 f"These stories are the front page of {episode_date.strftime('%A, %B %-d, %Y')}.",
                 f"This episode airs the next morning, {air.strftime('%A, %B %-d, %Y')}. So when a "
                 f"line says 'today' it means the air date, and the stories are YESTERDAY's. Do not "
                 f"call the story day 'today', and do not state either date outright: the host "
                 f"already says the date in the fixed intro.",
                 "", "STORIES (front-page order):"]
        for s in stories:
            pts = "1 point" if s.points == 1 else f"{s.points} points"
            # The placeholder is an INSTRUCTION, not a description. It used to read "(no page
            # fetched; headline only)", and the model quoted that framing straight back on air
            # ("We have a title and a link, and no fetched page", 2026-08-01). Handing the
            # writer the pipeline's vocabulary is handing it the words to break the frame with.
            src = ((s.source_text or "").strip()[:SOURCE_CHARS]
                   or "(none - cover this one briefly from what the title tells you; rule 6 applies)")
            # No "suggested desk" line any more: there are no beats to suggest one for, and both
            # regulars cover every story. Leaving it in would have told the model to assign
            # subjects to people, which is the exact shape the desks were deleted to remove.
            lines += [
                f"\n[story {s.id}] {s.title} ({pts}) [{s.source_kind}]",
                f"url: {s.url or '(none)'}",
                f"source: {src}",
            ]
        if top_story and comments:
            lines += ["", "BUSIEST THREAD, whose comments play inside its own coverage: "
                      f"[story {top_story.id}] {top_story.title}",
                      "REAL COMMENTS (perform these, using the username as speaker):"]
            for c in comments:
                body = clean_comment_html(c.text)
                if body and is_safe(body):
                    lines.append(f"- @{c.author} [id {c.id}]: {_trim_to_sentence(body, COMMENT_CHARS)}")
        return system, "\n".join(lines)

    def _to_segments(self, raw, cast) -> List[ScriptSegment]:
        segments: List[ScriptSegment] = []
        cohost = cast.cohost or cast.anchor
        # Alternates the two regulars across the episode's performed comments, co-host first, the
        # same order PanelWriter uses. Counted here rather than trusted from the model: the model
        # decides who FRAMES each comment, but who VOICES it has to be deterministic or two
        # consecutive quotes can land on one voice and read as one person arguing with themselves.
        performed = 0
        for item in raw:
            text = (item.get("text") or "").strip()
            if not text:
                continue
            role = item.get("role")
            hn_id = item.get("source_hn_id") or None
            if role == "commenter":
                if not is_safe(text):
                    continue  # drop anything the safety filter rejects
                # THE RECORD, not the audio. `speaker_key` stays the real HN username and
                # `source_hn_id` the real comment id, while `voice_id` is a REGULAR's. The
                # transcript, the episode page and the chapter metadata all read the first two;
                # setting speaker_key to the performer would leave the audio correct and quietly
                # destroy the record of who actually wrote the words.
                #
                # `desk` stays None. `recast` calls an untagged commenter line the "guest" slot,
                # `custom.py` reads `desk` to decide which lines are a story's coverage, and the
                # web page colours an untagged commenter row as a guest. Tagging it with the
                # performer's seat would break all three at once.
                speaker = item.get("speaker_key") or "guest"
                voice = cohost.voice_id if performed % 2 == 0 else cast.anchor.voice_id
                performed += 1
                segments.append(ScriptSegment(order=len(segments), role="commenter",
                                              speaker_key=speaker,
                                              text=text, source_hn_id=hn_id, voice_id=voice))
            else:
                desk_role = item.get("desk") or "anchor"
                if role == "anchor" or desk_role == "anchor":
                    speaker, desk_role = cast.anchor.name, "anchor"
                else:
                    d = cast.by_role(desk_role)
                    if d is None:
                        # A seat this episode does not have. Without this, speaker_key becomes
                        # the raw role string and `voice_for` falls through to the HOST's voice,
                        # so the line is spoken by the wrong person under a non-name. Remap
                        # rather than raise: raising drops the whole run into PanelWriter, which
                        # is a worse outcome than one reassigned line. This fires more often now
                        # than it used to -- the prompt no longer offers the model a beat to pick
                        # from, so a model that invents "ai" anyway lands here -- which is
                        # exactly what it is for.
                        #
                        # Falling back to desks[0] before the anchor matters: reaching the anchor
                        # here would reproduce the very bug this guard exists to close.
                        d = cast.by_role(cast.default_role) or (cast.desks[0] if cast.desks
                                                                else cast.anchor)
                        desk_role = d.role
                    speaker = d.name
                segments.append(ScriptSegment(order=len(segments), role=role or "desk",
                                              speaker_key=speaker, text=text, desk=desk_role,
                                              source_hn_id=hn_id))
        return _merge_cold_open(segments)


def _merge_cold_open(segments: List[ScriptSegment]) -> List[ScriptSegment]:
    """Join a cold open the model split across several segments back into one.

    Belt to the prompt's braces, and the belt is the part that actually holds. The instruction
    asks for one segment; a model that returns four anyway produced the 2026-08-20 defect, and no
    amount of prompt wording makes that impossible. Merging here does.

    Merges only the LEADING run of host lines that carry no story id, which is what the cold open
    is and nothing else is:

      - it stops at the first segment with a `source_hn_id`, so an anchor throw into a story is
        never absorbed. Those open chapter markers (`chapters.build_chapters` uses a story id's
        first occurrence), and swallowing one would move the mark.
      - it only looks at the top of the script, so two adjacent untagged host lines later in the
        rundown stay separate. Mid-show those are two thoughts in a conversation, and joining
        them would read as one hurried sentence.
      - the co-host's lines end the run, because a cold open is the host alone.

    Joined with a single space, so the sentences' own full stops carry the pacing. That is the
    whole fix: the pauses now come from punctuation Flux is reading rather than from the silence
    `stitch` inserts between two separate renders.
    """
    run = 0
    for seg in segments:
        if seg.source_hn_id or seg.desk != "anchor":
            break
        run += 1
    if run < 2:
        return segments
    head = segments[0]
    head.text = " ".join(seg.text for seg in segments[:run])
    merged = [head] + segments[run:]
    for i, seg in enumerate(merged):
        seg.order = i
    return merged
