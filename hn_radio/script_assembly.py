"""Stage 2 - Script. Turn HN data into an ordered, plain-text radio script.

Plain text is the whole point: expression must come from Flux reading the words, not from
markup or voice tags. So this stage produces bare strings tagged with a speaker role, nothing more.

WHAT THIS MODULE IS NOW, decided rather than left implicit. Three helpers here are live and
imported by `writers.py`; `TemplateAssembler` is NOT. Its last caller was `pipeline.run`,
the v1 `--legacy` path, deleted along with the `ScriptAssembler` ABC it used to sit behind, because
a traced run wrote an episode with no pacing, music, chapters, MP3 or VTT and `publish()` still put
it in the feed as a real episode for subscribers.

`TemplateAssembler` is kept deliberately, as the single-narrator reference script and the fixture
five tests read. It is not a production path and must not become one: a Claude-backed assembler was
the reason for the ABC, and that never happened. If it ever gains a caller again, route it through
`_finalize` first.

It is a SINGLE-NARRATOR script: one voice reads the headlines and leads into the comments, with no
Cast involved at all. The show that ships is the two-person panel in `writers.py`. Two things
follow, and they are deliberate rather than oversights:

  - the narrator introduces herself, matching what the panel show's fixed intro does, because that
    is a property of the show and not of one writer;
  - performed comments here still get a voice from `config.COMMENTER_VOICES` via the v1 hash in
    `voices.assign_voice`. The panel show stopped using a separate comment voice because it has
    two regulars to read them with. This path has one narrator, so removing the
    contrast would leave the host quoting other people in her own voice with nothing to mark it.
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import List, Optional

from . import config
from .models import Comment, ScriptSegment, Story

# A small starter block-list. HN is generally tame, but output is published under the
# company name, so a comment containing any of these is dropped entirely. Expand as needed.
_BLOCKED = {"fuck", "shit", "cunt", "bitch", "bastard", "slut", "faggot", "retard"}
_BLOCK_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _BLOCKED) + r")\b", re.IGNORECASE)

_PRE_RE = re.compile(r"<pre\b.*?</pre>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")

MAX_COMMENT_CHARS = 600  # keep a single comment listenable; trim at a sentence boundary


def clean_comment_html(raw: str) -> str:
    """Convert HN comment HTML into speakable plain text, keeping the wording/voice intact."""
    text = _PRE_RE.sub(" ", raw)          # drop code blocks (do not read code aloud)
    text = re.sub(r"<p>", "  ", text, flags=re.IGNORECASE)  # HN paragraph breaks
    text = _TAG_RE.sub("", text)          # strip remaining tags
    text = html.unescape(text)            # &#x27; -> '  , &gt; -> >  , etc.
    text = _URL_RE.sub("a link", text)    # reading a raw URL aloud is terrible
    text = _WS_RE.sub(" ", text).strip()
    return text


def is_safe(text: str) -> bool:
    return _BLOCK_RE.search(text) is None


def _trim_to_sentence(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut > limit * 0.5:
        return window[: cut + 1]
    return window.rstrip() + "..."


class TemplateAssembler:
    """Deterministic script: intro -> ranked headlines, with the comment theater playing right
    after the headline for the thread the comments came from -> sign-off."""

    def assemble(
        self,
        stories: List[Story],
        top_story: Optional[Story],
        comments: List[Comment],
        episode_date: date,
    ) -> List[ScriptSegment]:
        segments: List[ScriptSegment] = []
        order = 0

        def host(text: str, source: Optional[int] = None) -> None:
            nonlocal order
            segments.append(ScriptSegment(order=order, role="host", speaker_key="host",
                                          text=text, source_hn_id=source))
            order += 1

        weekday = episode_date.strftime("%A")
        pretty = episode_date.strftime("%B %-d") if hasattr(episode_date, "strftime") else str(episode_date)
        # The narrator names herself, matching what the panel show's fixed intro does.
        # Read from the configured host voice rather than hardcoded: this path has no
        # Cast at all (that is what makes it v1), so `config.host_voice()` is the only thing that
        # knows who is reading, and a literal name here would go stale the moment HOST_VOICE
        # changed -- which is exactly the drift that made HOST_VOICE say Haley while the panel
        # show cast Alexis for weeks.
        #
        # There is no co-host to introduce and no comment-theater change here. This is the retired
        # single-narrator path: one voice reads everything, so there is nobody to hand to.
        who = config.voice_name(config.host_voice())
        opener = f"Hi, this is {who}. " if who else "Good morning. "
        host(f"{opener}It's {weekday}, {pretty}, and here's what's on the Hacker News front page.")

        clean_comments = self._prepare_comments(comments)

        def comment_theater(inline: bool) -> None:
            """The performed comments, led in by the host. No-op if there is nothing to perform."""
            nonlocal order
            if top_story is None or not clean_comments:
                return
            if inline:
                # The headline for this story was just read, so repeating the title here is the
                # second time in two lines. Point back at it instead.
                host("That one has the busiest thread today. Here's what people are saying.",
                     source=top_story.id)
            else:
                # No headline for this story ran at all, so the title is the only context the
                # listener gets for whose thread this is.
                host(f"The busiest thread today is {top_story.title}. Here's what people are saying.",
                     source=top_story.id)
            for i, (comment, body) in enumerate(clean_comments):
                lead = "One comment stood out." if i == 0 else "Then someone jumps in."
                host(lead)
                segments.append(ScriptSegment(
                    order=order, role="commenter", speaker_key=comment.author,
                    text=body, source_hn_id=comment.id))
                order += 1

        # Every comment comes from the ONE busiest thread, so they play with that story instead
        # of after the whole rundown, where the listener heard the story minutes earlier. None
        # means no headline to attach to (no busiest thread, or one that is not in `stories`),
        # and the theater falls back to the end of the show, where it always used to play.
        inline_after = (top_story.id if top_story is not None
                        and any(s.id == top_story.id for s in stories) else None)

        # No invitation on the throw, unlike `PanelWriter._INVITATIONS`. The panel show gained a
        # named handoff at the top of every story, because the beat that was cutting from voice to
        # voice is a story's FIRST co-host turn. There is no co-host here and no
        # second turn: one narrator reads the whole rundown, so a handoff would either be the
        # narrator addressing herself or an invented second person. Same shape of reasoning as the
        # comment voice above -- the panel show's fix does not port to a path with one voice in it,
        # and the honest version of "consistent" is to say so rather than to fake the beat.
        for s in stories:
            points = "1 point" if s.points == 1 else f"{s.points} points"
            host(f"Number {s.rank}, with {points}: {s.title}.", source=s.id)
            if s.id == inline_after:
                comment_theater(inline=True)

        if inline_after is None:
            comment_theater(inline=False)

        host("That's your front page. Go touch grass.")
        return segments

    def _prepare_comments(self, comments: List[Comment]):
        """Clean, safety-filter, and trim comments. Returns [(comment, speakable_text)]."""
        prepared = []
        for c in comments:
            body = clean_comment_html(c.text)
            if not body or not is_safe(body):
                continue
            prepared.append((c, _trim_to_sentence(body, MAX_COMMENT_CHARS)))
        return prepared
