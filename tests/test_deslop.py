"""The de-slop gate: catches the countable AI tells before a script reaches render."""

import pytest

from hn_radio import deslop
from hn_radio.models import ScriptSegment


def _seg(text):
    return ScriptSegment(order=0, role="desk", speaker_key="Cole", text=text)


def test_a_clean_script_passes():
    segments = [_seg("The thread mostly argues the fix is worse than the bug."),
                _seg("That's fair, but the maintainer's reply is the interesting part.")]
    assert deslop.lint(segments) == []
    deslop.gate(segments)  # does not raise


def test_worth_ing_is_zero_tolerance():
    segments = [_seg("That's worth sitting with for a second.")]
    hits = deslop.lint(segments)
    assert any(name == "worth-ing" for name, _, _ in hits)
    with pytest.raises(RuntimeError, match="de-slop gate failed"):
        deslop.gate(segments)


def test_one_digiorno_construct_is_allowed():
    """A single contrast is a normal rhetorical move in banter, not a tell -- only a cluster is."""
    segments = [_seg("This is not a bug, it's a design choice, and it's a defensible one.")]
    assert deslop.lint(segments) == []


def test_a_digiorno_cluster_flags():
    segments = [_seg("This is not a bug, and that is not the point, and this is not the whole "
                      "story either.")]
    hits = deslop.lint(segments)
    assert any(name == "digiorno" for name, _, _ in hits)


def test_self_validation_allows_one_but_not_two():
    segments = [_seg("Here's the thing: nobody actually read the RFC."),
                _seg("And here's the thing about the fallback path too.")]
    hits = deslop.lint(segments)
    assert any(name == "self-validation" and count == 2 for name, count, _ in hits)


def test_toolkit_is_not_flagged():
    """The skill's own warning: a domain term (this show discusses dev tooling daily) is not a tell."""
    segments = [_seg("It's a solid toolkit if you're already on that stack.")]
    assert deslop.lint(segments) == []
