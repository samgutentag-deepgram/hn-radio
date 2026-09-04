"""The deploy switch: shipping the code without shipping the music.

`_finalize` has taken a `with_music` argument since the music work landed, and `music.apply` has
taken `enabled`. Neither was reachable from outside the process: all three production callers of
`_finalize` used the default, so music was on in every real render and the only way to turn it off
was to edit the source. That is the whole reason a deploy was blocked on the beds being finished.

This pins the switch itself: `config.music_enabled()` reads the environment, `_finalize` consults
it when the caller says nothing, and an explicit argument still wins over the environment.

The behavioral end of it (a dry episode really has no cues) lives in test_music.py, which owns the
audio invariants. This file only proves the wire reaches the pipeline.
"""

from __future__ import annotations

import inspect

import pytest

from hn_radio import config, pipeline


@pytest.fixture
def no_music_env(monkeypatch):
    """Nothing set, anywhere. `music_enabled` reads .env too, so mask that path as well."""
    monkeypatch.delenv("HN_RADIO_MUSIC", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)


def test_music_is_on_when_nothing_is_set(no_music_env):
    """The show has music. Turning it off is the deliberate act, not turning it on."""
    assert config.music_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "FALSE", "no", "off", "  off  "])
def test_the_documented_off_spellings_all_turn_music_off(monkeypatch, value):
    monkeypatch.setenv("HN_RADIO_MUSIC", value)
    assert config.music_enabled() is False, f"{value!r} should read as off"


def test_an_empty_value_reads_as_unset(monkeypatch, no_music_env):
    """`HN_RADIO_MUSIC=` is not "off".

    `_read_env_var` cannot tell an empty variable from a missing one -- it treats any falsy value
    as absent and falls through to `.env`. So empty means unset, and unset means the show has
    music. Documented here rather than fixed, because every other setting in config behaves the
    same way and changing that contract for one variable is worse than the ambiguity.
    """
    monkeypatch.setenv("HN_RADIO_MUSIC", "")
    assert config.music_enabled() is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_documented_on_spellings_all_turn_music_on(monkeypatch, value):
    monkeypatch.setenv("HN_RADIO_MUSIC", value)
    assert config.music_enabled() is True, f"{value!r} should read as on"


def test_an_unrecognized_value_leaves_music_on(monkeypatch):
    """Fail toward the show sounding right.

    A typo in a Fly secret (`HN_RADIO_MUSIC=flase`) should not silently ship a bare-speech show to
    subscribers. The failure mode of guessing wrong here is asymmetric: an unwanted bed is
    obvious the moment anyone listens, while unwanted silence sounds like a normal podcast and
    could run for days before someone notices the theme never played.
    """
    monkeypatch.setenv("HN_RADIO_MUSIC", "flase")
    assert config.music_enabled() is True


def test_finalize_asks_config_when_the_caller_says_nothing(monkeypatch):
    """The default is None, meaning "ask", not True.

    This replaces an older test that asserted the signature default was literally `True`. That
    pinned the mechanism rather than the behavior, and the behavior it cared about -- an
    unconfigured render has music -- is covered by the two tests below plus
    `test_music_is_on_when_nothing_is_set`.
    """
    assert inspect.signature(pipeline._finalize).parameters["with_music"].default is None


class _Stop(Exception):
    """Raised by the spy so `_finalize` stops before it needs ffmpeg."""


def _music_enabled_seen_by_finalize(monkeypatch, tmp_path, **kw):
    """Run `_finalize` far enough to capture what it passed to `music.apply`, then stop.

    Deliberately not a full render: this is about the wire, and stitching real audio would drag
    in ffmpeg for a question that is answered a few lines into the function.
    """
    seen = {}

    def _spy(segments, pcm, gaps, *, enabled=True, **rest):
        seen["enabled"] = enabled
        raise _Stop

    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    monkeypatch.setattr(pipeline.music, "apply", _spy)
    monkeypatch.setattr(pipeline.status, "stage", lambda *a, **k: None)
    with pytest.raises(_Stop):
        pipeline._finalize([], [], episode_id="x", title="t", source_items=[],
                           edition="frontpage", log=(lambda *a, **k: None), **kw)
    return seen["enabled"]


def test_the_environment_reaches_music_apply(monkeypatch, tmp_path):
    monkeypatch.setenv("HN_RADIO_MUSIC", "0")
    assert _music_enabled_seen_by_finalize(monkeypatch, tmp_path) is False


def test_an_explicit_argument_beats_the_environment(monkeypatch, tmp_path):
    """`--no-music` on one run must not need the deploy's setting changed, or the reverse."""
    monkeypatch.setenv("HN_RADIO_MUSIC", "0")
    assert _music_enabled_seen_by_finalize(monkeypatch, tmp_path, with_music=True) is True


def test_the_suite_does_not_inherit_a_developers_dot_env(monkeypatch):
    """Same failure the host fixture exists to prevent, one variable over.

    If Sam sets `HN_RADIO_MUSIC=0` in `.env` to work on the script without waiting for beds, every
    music test would start reading his laptop's preference. conftest masks it for the same reason
    it masks DEEPGRAM_API_HOST: 29 tests broke that way once.
    """
    monkeypatch.delenv("HN_RADIO_MUSIC", raising=False)
    assert config.music_enabled() is True


def test_run_panel_threads_the_switch_all_the_way_down(monkeypatch, tmp_path):
    """The chain is run_panel -> render_panel -> _finalize, and the middle link was missing.

    This test exists because of a real failure, not a hypothetical one. The switch was first wired
    by adding `with_music=with_music` to the `_finalize` call, which lives in `render_panel` --
    but the new parameter was added to `run_panel`, one function up. `render_panel` referenced a
    name it did not have, so every call through the front door raised NameError at the moment it
    handed off to `_finalize`.

    The whole suite stayed green, because nothing exercised `render_panel`: the music tests call
    `_finalize` directly and the pipeline tests stop before the render. It took a live run, and
    about twenty-five wasted Flux calls, to find a one-word bug. So walk the real chain here with
    the two expensive stages stubbed out.
    """
    seen = {}

    def _fake_render_all(segments, api_key, on_progress=None):
        return [b"\x00\x00" * 100 for _ in segments]

    def _fake_finalize(segments, pcm, **kw):
        seen["with_music"] = kw.get("with_music")
        return "episode"

    monkeypatch.setattr(pipeline.render, "render_all", _fake_render_all)
    monkeypatch.setattr(pipeline.config, "get_api_key", lambda: "k")
    monkeypatch.setattr(pipeline, "_finalize", _fake_finalize)
    monkeypatch.setattr(pipeline.status, "stage", lambda *a, **k: None)

    from hn_radio.cast import DEFAULT_CAST
    from hn_radio.models import ScriptSegment

    segs = [ScriptSegment(order=0, role="anchor", speaker_key="Haley", text="hi", desk="anchor")]
    pipeline.render_panel(segs, episode_id="x", title="t", source_items=[], cast=DEFAULT_CAST,
                          with_music=False, log=(lambda *a, **k: None))
    assert seen["with_music"] is False, "render_panel dropped the switch on the floor"

    pipeline.render_panel(segs, episode_id="x", title="t", source_items=[], cast=DEFAULT_CAST,
                          log=(lambda *a, **k: None))
    assert seen["with_music"] is None, "an unset switch must reach _finalize as None, not True"
