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
    segments = [_seg("This is not a bug, it's a feature. That is not the point, it is the whole "
                      "point. It's not slow, it's careful.")]
    hits = deslop.lint(segments)
    assert any(name == "digiorno" and count == 3 for name, count, _ in hits)


def test_ordinary_negation_is_not_a_digiorno():
    """Lines lifted from real Claude scripts that the old rule counted and a listener never would.

    The rule used to match every "is not <word>" and every ", not <word>", which is how two of
    four live episodes ended up on the deterministic fallback over a regex hit. The construct is a
    negated clause followed by the clause that replaces it; plain negation is just talking.
    """
    segments = [
        _seg("Honestly, Alexis, not much beyond the headline, and I am going to leave it there."),
        _seg("Ten thousand dollars, and he is not entirely sure what he got."),
        _seg("Specified down to the exact model of disks, not rented capacity from a big cloud."),
        _seg("Habhub, which was built for amateur balloons, not weather service ones."),
        _seg("Those talks are not ongoing. Right, nothing is signed."),
        _seg("The trend is the story, not the crown. Last one, and it's a good one."),
    ]
    assert deslop.lint(segments) == []


def test_the_construct_is_caught_in_its_real_shapes():
    """Three shapes from real scripts: comma + it's, period + It's, and 'not X, but Y'."""
    for line in ("The interesting figure there is not the accuracy, it is the price tag.",
                 "This is not sensible. It's why you switched away.",
                 "Not that a model can do this, but that it can do it on a laptop."):
        hits = {name: count for name, count, _ in deslop.lint([_seg(line)] * 3)}
        assert hits.get("digiorno") == 3, line


def test_self_validation_allows_one_but_not_two():
    segments = [_seg("Here's the thing: nobody actually read the RFC."),
                _seg("And here's the thing about the fallback path too.")]
    hits = deslop.lint(segments)
    assert any(name == "self-validation" and count == 2 for name, count, _ in hits)


def test_toolkit_is_not_flagged():
    """A domain term (this show discusses dev tooling daily), not a tell."""
    segments = [_seg("It's a solid toolkit if you're already on that stack.")]
    assert deslop.lint(segments) == []
