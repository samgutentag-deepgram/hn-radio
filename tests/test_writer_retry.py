"""The LLM writer gets a second try, and the second try is told what went wrong.

`tests/test_writer_retry.py` is named in the ledger (2026-09-04) and did not exist in this repo:
it was written against the uncommitted work and lost with it. This is a fresh file covering the
retry as it now stands, including the `retry_note` added on 2026-09-16.

WHY THE NOTE MATTERS MORE THAN THE RETRY. `WRITER_ATTEMPTS = 2` landed because replaying a
fallback night showed the de-slop gate, not the API, was what handed the show to canned copy --
and the canned copy then read a README's markdown aloud. But the second attempt was a byte-for-byte
identical request, so it differed from the first only by sampling: a reroll, not a correction. The
gate already says exactly what it objected to, so handing that over costs nothing and turns the
retry into an edit.
"""

from __future__ import annotations

import sys
import types

import pytest

from hn_radio import config, deslop, pipeline
from hn_radio.models import ScriptSegment
from hn_radio.writers import ClaudeWriter, PanelWriter


class _Sent(Exception):
    """Raised from the fake client once the request is captured. Nothing here needs a response:
    every assertion is about the prompt that went OUT."""


@pytest.fixture
def capture(monkeypatch):
    """Run `ClaudeWriter.write` far enough to capture the request, and no further.

    Stubs three things and no more: the key (so a machine without one can run this), the prompt
    builder (so the assertions are about what `write` ADDS rather than about the 6,000-character
    system prompt), and the client.
    """
    sent = {}
    monkeypatch.setattr(config, "get_anthropic_key", lambda: "test-key")

    fake = types.ModuleType("anthropic")
    fake.APIError = type("APIError", (Exception,), {})

    def _anthropic(**_kw):
        class _Client:
            class messages:
                @staticmethod
                def stream(**kwargs):
                    sent["system"] = kwargs["system"]
                    sent["user"] = kwargs["messages"][0]["content"]
                    raise _Sent()
        return _Client()

    fake.Anthropic = _anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    def _run(writer):
        monkeypatch.setattr(writer, "_build_prompt",
                            lambda *a, **k: ("SYSTEM PROMPT BODY", "USER PROMPT BODY"))
        with pytest.raises(_Sent):
            writer.write([], None, [], None, "frontpage", None)
        return sent

    return _run


def test_a_claude_writer_starts_with_no_retry_note():
    """A fresh writer has nothing to apologize for, so nothing is appended to a first prompt."""
    assert ClaudeWriter().retry_note is None


def test_a_first_attempt_sends_the_prompt_unchanged(capture):
    writer = ClaudeWriter()
    sent = capture(writer)
    assert sent["user"] == "USER PROMPT BODY"
    assert "PREVIOUS DRAFT" not in sent["user"]


def test_a_retry_carries_the_gate_s_own_words(capture):
    """Whatever `deslop.gate` raised is what the model reads. That is the contract between the
    two halves, and it is why the gate's message has to name the rule and the reason."""
    writer = ClaudeWriter()
    writer.retry_note = "de-slop gate failed: load-bearing x1 (nothing is 'load-bearing')"
    sent = capture(writer)
    assert sent["user"].startswith("USER PROMPT BODY")
    assert "YOUR PREVIOUS DRAFT" in sent["user"]
    assert "load-bearing" in sent["user"]


def test_the_note_does_not_touch_the_system_prompt(capture):
    """The rules are the same every night and live in the system prompt. This is a fact about one
    rejected draft; mixing the two would make the standing instructions read differently on a
    retry than on a first attempt."""
    writer = ClaudeWriter()
    writer.retry_note = "de-slop gate failed: worth-ing x1"
    sent = capture(writer)
    assert sent["system"] == "SYSTEM PROMPT BODY"
    assert "PREVIOUS DRAFT" not in sent["system"]


def test_the_note_forbids_writing_around_the_rule(capture):
    """A retry told only "you used a banned phrase" reaches for a synonym. It has to be told to
    drop the construction and say the thing plainly, and told that nothing else was wrong -- or
    the second draft is a different show."""
    writer = ClaudeWriter()
    writer.retry_note = "de-slop gate failed: assigned-side x1"
    sent = capture(writer)
    assert "reaching for a synonym" in sent["user"]
    assert "Same stories, same structure" in sent["user"]
    assert "not an instruction to change the show" in sent["user"]


def test_the_deterministic_writer_has_no_note_to_set():
    """`PanelWriter` is deterministic, so a retry would fail identically and it gets one go. It
    also has no `retry_note`, which is why `run_panel` guards with `hasattr` rather than assigning
    unconditionally -- `ScriptWriter` is a shared interface and this is not part of it."""
    assert not hasattr(PanelWriter(), "retry_note")
    assert pipeline.WRITER_ATTEMPTS >= 2


def test_the_pipeline_sets_the_note_from_the_failure_and_clears_it_after():
    """Set on failure so the retry sees it; cleared on success so a writer reused across runs --
    `scripts/backfill.py` builds one and loops over dates -- cannot carry one night's rejection
    into the next night's first attempt."""
    import inspect
    body = inspect.getsource(pipeline.run_panel)
    assert 'writer.retry_note = str(e)' in body, "the retry is not told why it is retrying"
    assert 'writer.retry_note = None' in body, "the note is never cleared"
    assert body.index('writer.retry_note = str(e)') < body.index('writer.retry_note = None')


def test_the_gate_message_is_useful_to_the_model_it_is_handed_to():
    segs = [ScriptSegment(order=0, role="anchor", speaker_key="A",
                          text="That detail is load-bearing.")]
    with pytest.raises(RuntimeError) as exc:
        deslop.gate(segs)
    message = str(exc.value)
    assert "load-bearing" in message
    assert "say what the thing actually does" in message
