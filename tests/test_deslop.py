"""The de-slop gate: what it rejects, and the one thing it must never reject.

`tests/test_deslop.py` is named in the ledger (2026-09-04) and did not exist in this repo: it was
written against the uncommitted work and lost with it. This is not that file recovered. It is a
fresh one, written when the `load-bearing` and `assigned-side` rules were added on 2026-09-16, and
it covers the rules and the corpus split rather than everything the original may have pinned.

THE RULE THAT MATTERS MOST HERE IS THE CARVE-OUT. A performed comment is a real person's words off
Hacker News, and `writers.py` HARD RULE 7 requires the show to read them unchanged. A threshold-0
rule that lints them rejects the episode for somebody else's writing, and a rejection costs a
retry and then the deterministic fallback, which on 2026-09-04 failed verification and read a
README's markdown aloud. So the expensive failure is a false positive, not a miss, and that is
what most of this file is about.
"""

from __future__ import annotations

import pytest

from hn_radio import deslop
from hn_radio.models import ScriptSegment


def _prose(*texts):
    """Regulars' lines: the writer's own prose, which every rule reads."""
    return [ScriptSegment(order=i, role="anchor" if i % 2 == 0 else "desk",
                          speaker_key="Alexis", text=t)
            for i, t in enumerate(texts)]


def _comment(text, author="hnuser"):
    return ScriptSegment(order=99, role="commenter", speaker_key=author, text=text)


def _names(hits):
    return sorted(name for name, _count, _why in hits)


# --- the rule table ---------------------------------------------------------------------------

def test_every_rule_declares_a_threshold_and_a_corpus():
    """The tuple shape IS the contract `lint` unpacks, and a rule missing its corpus flag would
    silently read performed comments."""
    assert deslop.RULES
    for name, pattern, why, max_allowed, prose_only in deslop.RULES:
        assert isinstance(name, str) and name
        assert hasattr(pattern, "findall")
        assert isinstance(why, str) and why
        assert isinstance(max_allowed, int) and max_allowed >= 0
        assert isinstance(prose_only, bool)


def test_a_clean_script_passes():
    segs = _prose("Valve put a number on the Steam Frame today.",
                  "Five hundred dollars, and the controllers are included.")
    assert deslop.lint(segs) == []
    deslop.gate(segs)  # does not raise


# --- load-bearing -----------------------------------------------------------------------------

def test_nothing_is_load_bearing():
    """Sam's call, 2026-09-16, verbatim: "nothing is load bearing".

    Six hits across six episodes on the feed, every one the metaphor rather than a wall. It says a
    detail matters without saying what it does, which is the `self-validation` failure wearing an
    engineering costume.
    """
    assert _names(deslop.lint(_prose("That clause is doing a lot of load-bearing work."))) \
        == ["load-bearing"]


@pytest.mark.parametrize("line", [
    "That last clause is the whole load-bearing bit.",
    "One word doing the load bearing: allegedly.",
    "Most orgs treat a past win as load-bearing identity.",
    "The word they use is Load Bearing here.",
])
def test_the_load_bearing_spellings_all_hit(line):
    """Real lines off the feed, plus the capitalized form. Hyphen, space and case all count, or
    the rule is a suggestion."""
    assert "load-bearing" in _names(deslop.lint(_prose(line)))


def test_one_load_bearing_is_enough_to_fail():
    """Zero allowed, unlike `digiorno`. There is no dose of this that reads as a person talking,
    so a threshold would only decide how many get through."""
    segs = _prose("The load-bearing detail is the date.")
    with pytest.raises(RuntimeError, match="load-bearing"):
        deslop.gate(segs)


# --- assigned-side ----------------------------------------------------------------------------

def test_the_debate_format_announcing_itself_is_rejected():
    """"I'll take the other side" opened a desk turn in six episodes, because the PROMPT asked for
    "a framing they can argue with, where they take the other side" and the writer handed the
    instruction back as dialogue. The shape is gone from the prompt; this is the gate that catches
    it coming back."""
    assert "assigned-side" in _names(deslop.lint(_prose(
        "I'll take the other side, actually, because the pitch is narrower than that.")))


@pytest.mark.parametrize("line", [
    "I'll take the other side, but set it up first, because the premise is great.",
    "I'm going to take the other side on this one.",
    "Let me play the other side for a second.",
    "I will argue the other side here.",
    "Let me steelman the argument.",
    "I'll play devil's advocate.",
])
def test_the_assigned_side_openers_all_hit(line):
    assert "assigned-side" in _names(deslop.lint(_prose(line)))


@pytest.mark.parametrize("line", [
    "There's another side to this that the filing doesn't address.",
    "The other side of the trade is that nobody wants to own the plumbing.",
    "They take the other trains instead.",
    "I read it the other way, and here is why.",
])
def test_ordinary_disagreement_is_not_the_debate_format(line):
    """Two people disagreeing is the show working. What the rule objects to is one of them being
    ASSIGNED the opposite position out loud, which is a narrow, announced construction."""
    assert "assigned-side" not in _names(deslop.lint(_prose(line)))


# --- the carve-out ----------------------------------------------------------------------------

def test_a_quoted_comment_cannot_fail_the_gate_on_a_prose_rule():
    """THE ONE THIS FILE EXISTS FOR.

    2026-08-17 really has "load bearing" inside a performed HN comment AND in a desk line. If the
    rule read comments, that episode would have been rejected partly for a stranger's words, which
    the show is required to perform unchanged.
    """
    segs = [_comment("every other line talks about how things are byte for byte identical "
                     "on the load bearing path or how the acceptance ladder is misleading")]
    assert deslop.lint(segs) == []
    deslop.gate(segs)  # does not raise


def test_a_comment_does_not_dilute_a_prose_hit_either():
    """The carve-out drops comments from the corpus; it does not soften the prose rules. A desk
    line still fails with a comment sitting next to it."""
    segs = _prose("The load-bearing word is allegedly.") + [_comment("load bearing, allegedly")]
    assert _names(deslop.lint(segs)) == ["load-bearing"]


def test_every_current_rule_is_prose_only():
    """Not a law, a record: as of 2026-09-16 every rule is about how the WRITER writes, so none of
    them reads performed comments. A future rule about, say, unspeakable characters would
    legitimately want the whole script -- this test is here to make adding one a decision.
    """
    assert all(prose_only for *_rest, prose_only in deslop.RULES)


def test_a_comment_only_script_is_never_the_reason_for_a_fallback():
    """Composite of the above, at the level the pipeline cares about: an episode whose only
    tell-bearing text is quoted must render."""
    segs = _prose("Aaron Patterson went and read the code in those junk gems.") + [
        _comment("it's not a supply chain attack, it's a supply chain surrender"),
        _comment("worth sitting with how load-bearing that distinction is"),
    ]
    deslop.gate(segs)


# --- thresholds -------------------------------------------------------------------------------

def test_digiorno_tolerates_one_and_rejects_a_cluster():
    """A count, not a zero-tolerance single: one "that's not X, it's Y" is a normal move in
    banter, and a cluster is what reads as machine-written."""
    one = _prose("That's not a bug, it's a feature.")
    assert "digiorno" not in _names(deslop.lint(one))
    many = _prose("That's not a bug, it's a feature.",
                  "This is not a filing change, that's a judgment call.",
                  "It's not the accuracy, it is the price tag.")
    assert "digiorno" in _names(deslop.lint(many))


def test_the_gate_names_every_rule_that_failed_not_just_the_first():
    """The message is fed to the retry (`pipeline.run_panel` sets `writer.retry_note`), so a
    partial list would send the second attempt back to fix one of two problems."""
    segs = _prose("The load-bearing clause is worth sitting with.")
    with pytest.raises(RuntimeError) as exc:
        deslop.gate(segs)
    assert "load-bearing" in str(exc.value)
    assert "worth-ing" in str(exc.value)


def test_the_failure_says_why_and_not_only_what():
    """It is read by a person at 3am in a cron log, and by the model on the retry. A rule name
    alone tells neither of them what to do instead."""
    with pytest.raises(RuntimeError) as exc:
        deslop.gate(_prose("That detail is load-bearing."))
    assert "say what the thing actually does" in str(exc.value)
