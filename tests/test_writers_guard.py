"""The out-of-cast desk guard: a desk the episode does not have must not become the anchor."""

from hn_radio.cast import Cast, Desk
from hn_radio.writers import ClaudeWriter


def _two_desk_cast():
    anchor = Desk(role="anchor", name="Alexis", voice_id="flux-alexis-en",
                  beat="hosts", persona="warm")
    ai = Desk(role="ai", name="Priya", voice_id="flux-priya-en", beat="models",
              persona="precise")
    drama = Desk(role="drama", name="Cole", voice_id="flux-cole-en", beat="comments",
                 persona="deadpan")
    return Cast(anchor=anchor, desks=[ai, drama], default_role="ai")


def test_an_out_of_cast_desk_is_remapped_to_the_correspondent():
    ep_cast = _two_desk_cast()
    raw = [{"role": "desk", "desk": "maker", "speaker_key": "", "text": "a take",
            "source_hn_id": 1}]
    segs = ClaudeWriter()._to_segments(raw, ep_cast)
    assert segs[0].desk == "ai"
    assert segs[0].speaker_key == "Priya"
    # The real damage the guard prevents: this line rendering in the anchor's voice.
    assert ep_cast.voice_for(segs[0].desk) == "flux-priya-en"


def test_a_desk_in_the_cast_is_left_alone():
    """Uses 'drama', which is in the cast but is NOT default_role.

    If the guard ever remapped unconditionally instead of only when the desk is missing,
    this line would come back as the correspondent ('ai'/Priya) instead of Cole.
    """
    ep_cast = _two_desk_cast()
    raw = [{"role": "desk", "desk": "drama", "speaker_key": "", "text": "here's the range",
            "source_hn_id": 1}]
    segs = ClaudeWriter()._to_segments(raw, ep_cast)
    assert segs[0].desk == "drama"
    assert segs[0].speaker_key == "Cole"
    assert ep_cast.voice_for(segs[0].desk) == "flux-cole-en"


def test_the_correspondent_desk_is_left_alone():
    ep_cast = _two_desk_cast()
    raw = [{"role": "desk", "desk": "ai", "speaker_key": "", "text": "a take",
            "source_hn_id": 1}]
    segs = ClaudeWriter()._to_segments(raw, ep_cast)
    assert segs[0].desk == "ai"
    assert segs[0].speaker_key == "Priya"


def test_anchor_lines_are_untouched():
    ep_cast = _two_desk_cast()
    raw = [{"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "hello",
            "source_hn_id": 0}]
    segs = ClaudeWriter()._to_segments(raw, ep_cast)
    assert segs[0].desk == "anchor"
    assert segs[0].speaker_key == "Alexis"


def test_the_remap_never_falls_through_to_the_anchor():
    """A cast whose default_role is not among its desks must still avoid the anchor voice."""
    ep_cast = _two_desk_cast()
    ep_cast.default_role = "security"   # deliberately not one of this cast's desks
    raw = [{"role": "desk", "desk": "maker", "speaker_key": "", "text": "a take",
            "source_hn_id": 1}]
    segs = ClaudeWriter()._to_segments(raw, ep_cast)
    assert segs[0].desk != "anchor"
    assert ep_cast.voice_for(segs[0].desk) != ep_cast.anchor.voice_id
