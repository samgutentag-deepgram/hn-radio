from hn_radio import config, voices
from hn_radio.models import ScriptSegment


def test_host_always_gets_host_voice():
    assert voices.assign_voice("host", "host") == config.HOST_VOICE


def test_commenter_voice_is_stable_for_a_username():
    a = voices.assign_voice("commenter", "dang")
    b = voices.assign_voice("commenter", "dang")
    assert a == b
    assert a in config.COMMENTER_VOICES


def test_different_usernames_can_get_different_voices():
    names = ["dang", "patio11", "tptacek", "jacquesm", "pg", "sctb"]
    assigned = {voices.assign_voice("commenter", n) for n in names}
    # not a guarantee of uniqueness, but the pool should spread these out
    assert len(assigned) > 1


def test_assign_voices_fills_every_segment():
    segs = [
        ScriptSegment(order=0, role="host", speaker_key="host", text="hi"),
        ScriptSegment(order=1, role="commenter", speaker_key="dang", text="hello"),
    ]
    voices.assign_voices(segs)
    assert segs[0].voice_id == config.HOST_VOICE
    assert segs[1].voice_id in config.COMMENTER_VOICES


# --- the recurring-character effect ------------------------------------------------------------
#
# THE GUEST-VOICE HASH TESTS WERE DELETED 2026-08-22 WITH THEIR SUBJECT. `voices.guest_voice_for`
# is gone: it was unreachable, and two of the three reasons recorded for keeping it were false.
# See the `voices.py` module docstring, which keeps the rule itself as the written record.
#
# What those six tests pinned, so it is not lost: a commenter kept one voice across episodes
# (hash the username), two commenters in one episode got different voices (walk past `taken`),
# nobody reused a voice while a seat was free, more commenters than voices reused rather than
# raising, the first commenter was not decided by position (the 2026-08-12 regression), and an
# empty pool raised with a message naming the config. They were asserted as PROPERTIES rather
# than as specific voice ids on purpose, which is why the GA rename never broke them.
#
# `config.GUEST_VOICES` is untouched and still live -- the recast picker's `presets.flux.guest`
# and `scripts/voice_preview.py` both read it. Only the function that hashed into it is gone.
