"""The show must never narrate its own sourcing. One concept, three places it can leak.

Heard on air and confirmed by scanning every rendered script:

    2026-08-07  "That's genuinely all the page gives us, and I'd rather stop there than guess."
    2026-08-01  "We have a title and a link, and no fetched page, so what I can tell you is..."
    2026-08-05  "From the write-up: Automating discovery to accelerate science..."  (x8)

The first two are the LLM writer obeying an instruction that told it to be "honest" about a
thin story. The third is PanelWriter, where the narration was HARDCODED, so no prompt change
could ever have reached it. Sixteen flags across four of eight episodes.

The boundary Sam set: ATTRIBUTE, NEVER ASSESS. Naming who said a thing is good broadcast
practice and stays. Commenting on whether the source existed, how much of it there was, or
whether the show has enough to go on is the frame break.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from hn_radio.cast import Cast, Desk
from hn_radio.models import Story
from hn_radio.writers import ClaudeWriter, PanelWriter

_SPEC = importlib.util.spec_from_file_location(
    "frame_lint", Path(__file__).resolve().parent.parent / "scripts" / "frame_lint.py")
frame_lint = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frame_lint)


def _cast():
    return Cast(
        anchor=Desk(role="anchor", name="Alexis", voice_id="v1", beat="hosts", persona="warm"),
        desks=[Desk(role="ai", name="Priya", voice_id="v2", beat="models", persona="precise"),
               Desk(role="drama", name="Cole", voice_id="v3", beat="comments",
                    persona="deadpan")],
        default_role="ai")


def _stories(n=3, summary=None):
    return [Story(id=i, title=f"Story number {i}", url="https://example.com", points=10 * i,
                  author="a", num_comments=1, rank=i, kids=[], summary=summary,
                  source_kind="article")
            for i in range(1, n + 1)]


def _system(stories=None):
    stories = _stories() if stories is None else stories
    system, _ = ClaudeWriter()._build_prompt(stories, stories[0], [], _cast(), "frontpage",
                                             date(2026, 8, 8))
    return system


def _user(stories):
    _, user = ClaudeWriter()._build_prompt(stories, stories[0], [], _cast(), "frontpage",
                                           date(2026, 8, 8))
    return user


# --- the instruction that caused it -------------------------------------------------------

def test_the_prompt_no_longer_tells_the_writer_to_be_honest_about_a_thin_story():
    """"Keep the take short and honest" was read as a licence to say the story was thin.

    Both LLM-written violations came from stories with no source text, i.e. from the model
    obeying this rule rather than breaking it. The word doing the damage was "honest".
    """
    s = _system()
    assert "short and honest" not in s


def test_the_prompt_bans_assessing_the_source():
    s = _system()
    assert "NEVER ASSESS THE SOURCE ITSELF" in s
    assert "there are no sources, pages, write-ups, fetches, links or research" in s
    assert "SAY LESS ABOUT IT" in s


def test_the_prompt_names_the_actual_lines_that_aired():
    """A rule stated only in the abstract loses to a model's own judgment about honesty.

    These are the real strings, so the model sees the exact failure rather than a paraphrase.
    """
    s = _system()
    assert "that's all the page gives us" in s
    assert "no fetched page" in s
    assert "just the headline" in s


def test_the_prompt_still_permits_attribution():
    """Cutting sourcing narration must not cut citation with it: they are different things.

    Without this, the honest fix for "never mention the page" is "never say where anything
    came from", which strips "the piece argues" and "Berger writes" out of the show too.
    """
    s = _system()
    assert "ATTRIBUTE freely and by name" in s
    assert "the piece argues" in s


def test_a_sourceless_story_is_not_described_to_the_writer_in_pipeline_words():
    """The user prompt used to hand over the literal string "(no page fetched; headline only)".

    The 2026-08-01 take then said "We have a title and a link, and no fetched page" on air.
    The model was quoting its own input. Give it an instruction, not a status report.
    """
    user = _user(_stories(summary=None))
    assert "no page fetched" not in user
    assert "headline only" not in user
    assert "rule 6 applies" in user


def test_the_frame_rule_is_numbered_and_the_later_rules_shifted_with_it():
    """Rule 6 is referenced BY NUMBER from the sourceless-story placeholder in the user prompt.

    If the frame rule is ever renumbered without updating that reference, the placeholder
    points at whatever rule happens to be sixth, and the pointer silently means nothing.
    """
    s = _system()
    assert "6. NEVER ASSESS THE SOURCE ITSELF" in s
    assert "7. Perform comments faithfully" in s
    assert "8. Lively, specific, fast" in s
    assert "9. LENGTH (strict)" in s


# --- the hardcoded ones the prompt could never reach ---------------------------------------

def _panel_lines(stories):
    segs = PanelWriter().write(stories, stories[0], [], _cast(), "frontpage", date(2026, 8, 8))
    return [s.text for s in segs]


def test_panel_writer_does_not_prefix_takes_with_where_the_text_came_from():
    """"From the write-up: " opened EVERY desk line on 2026-08-05 and 2026-08-06: 8 lines.

    PanelWriter only ever has the HN submitter, never the article's author, so it cannot
    attribute honestly. It says nothing rather than naming the fetched artifact.
    """
    for line in _panel_lines(_stories(summary="A real extractive summary of the article.")):
        assert "From the write-up" not in line
        assert "From the README" not in line


def test_panel_writer_still_covers_a_story_that_has_no_summary():
    """Deleting the old sourceless line must not delete the desk's turn with it.

    Otherwise a sourceless story gets an anchor intro and then silence, which is a worse
    show than the frame break this change exists to remove.
    """
    stories = _stories(1, summary=None)
    segs = PanelWriter().write(stories, stories[0], [], _cast(), "frontpage", date(2026, 8, 8))
    desk_lines = [s for s in segs if s.role == "desk" and s.source_hn_id == stories[0].id]
    assert desk_lines, "a sourceless story still needs a desk take"
    assert "No page to read" not in desk_lines[0].text


def test_panel_writer_output_passes_the_frame_lint():
    """The end-to-end version of the two tests above, run through the real detector.

    Pinned against both branches: a story WITH a summary and a story WITHOUT one, because the
    two hardcoded violations lived one in each.
    """
    for stories in (_stories(summary="A real extractive summary of the article."),
                    _stories(summary=None)):
        for line in _panel_lines(stories):
            assert frame_lint.check(line) == [], f"frame break in PanelWriter output: {line!r}"


# --- the detector itself --------------------------------------------------------------------

def test_the_lint_catches_every_line_that_actually_aired():
    for line in [
        "That's genuinely all the page gives us, and I'd rather stop there than guess.",
        "Honestly, Haley, not much beyond the headline, and I am going to be disciplined "
        "about that. We have a title and a link, and no fetched page.",
        "From the write-up: Automating discovery to accelerate science and engineering.",
        "No page to read on this one, just the headline, but the thread is busy.",
    ]:
        assert frame_lint.check(line), f"lint missed a line that shipped: {line!r}"


def test_the_lint_leaves_real_attribution_alone():
    """Precision matters more than recall here: a detector that flags "the piece argues" would
    push the fix toward stripping citation, which is the opposite of the decision.

    Every line below is verbatim from a rendered episode and is staying in the show.
    """
    for line in [
        "The piece from OpenRSS argues Google ran a classic embrace, extend, extinguish play.",
        "JFrog's Afek Berger writes that a newly created GitHub repo published a batch of "
        "SQLite advisories.",
        "It claims to be lightweight, easy to grasp, and the site says its principles have "
        "been adopted in hundreds of documentation projects.",
        "Mistral's own argument is that the same content is fine in a cybersecurity research "
        "tool and harmful on a mental health platform.",
        "The author's bigger point is that RSS is not dead, it is still heavily used.",
    ]:
        assert frame_lint.check(line) == [], f"lint flagged legitimate attribution: {line!r}"


def test_the_lint_does_not_judge_performed_comments(tmp_path):
    """A quoted stranger is not the show speaking, so their words cannot break the show's frame.

    Linting them would flag the SOURCE MATERIAL rather than the script, and the only available
    "fix" would be censoring a real Hacker News commenter. The same sentence is asserted BOTH
    ways: it trips the rules on a desk line and is exempt on a commenter line, so the test
    fails if the role filter is dropped rather than merely passing because the text is clean.
    """
    import json

    said = "There's no page to read here, that's genuinely all we have."
    ep = tmp_path / "2026-08-09"
    ep.mkdir()
    (ep / "script.json").write_text(json.dumps([
        {"order": 0, "role": "commenter", "speaker_key": "someuser", "text": said},
        {"order": 1, "role": "desk", "speaker_key": "Priya", "text": said},
    ]))

    _, flagged = frame_lint.lint_episode(ep / "script.json")
    assert [seg["role"] for seg, _ in flagged] == ["desk"]
