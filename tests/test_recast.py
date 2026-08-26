import json

from hn_radio import config, pipeline, recast, render
from hn_radio.cast import DEFAULT_CAST
from hn_radio.models import ScriptSegment


def _segs():
    return [
        ScriptSegment(order=0, role="anchor", speaker_key="Haley", text="hi",
                      desk="anchor", voice_id="flux-haley-en"),
        ScriptSegment(order=1, role="desk", speaker_key="Meena", text="ai take",
                      desk="ai", voice_id="flux-meena-en"),
        ScriptSegment(order=2, role="commenter", speaker_key="dang", text="a comment",
                      voice_id="flux-drew-en"),
    ]


def test_render_recast_reuses_unchanged_segments(tmp_path, monkeypatch):
    """Only segments whose voice changed should be re-rendered; the rest reuse cached PCM."""
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    monkeypatch.setattr(config, "get_api_key", lambda: "x")

    orig = tmp_path / "ep"
    (orig / "segments").mkdir(parents=True)
    orig_segs = [
        {"order": 0, "role": "anchor", "speaker_key": "Haley", "text": "a", "desk": "anchor",
         "voice_id": "flux-haley-en", "source_hn_id": None, "start_seconds": 0.0},
        {"order": 1, "role": "desk", "speaker_key": "Wade", "text": "b", "desk": "maker",
         "voice_id": "flux-wade-en", "source_hn_id": None, "start_seconds": 1.0},
    ]
    (orig / "script.json").write_text(json.dumps(orig_segs))
    (orig / "segments" / "0.pcm").write_bytes(b"CACHED0")
    (orig / "segments" / "1.pcm").write_bytes(b"CACHED1")

    calls = []
    monkeypatch.setattr(render, "render_segment",
                        lambda text, vid, key: calls.append(vid) or b"RENDERED")
    captured = {}
    monkeypatch.setattr(pipeline, "_finalize",
                        lambda segs, pcm, **kw: captured.update(pcm=pcm) or "EP")

    new_segs = [ScriptSegment(**d) for d in orig_segs]
    new_segs[0].voice_id = "flux-cole-en"  # change ONLY segment 0's voice

    pipeline.render_recast(new_segs, original_id="ep", episode_id="ep-recast", title="t",
                           source_items=[], cast=DEFAULT_CAST, edition="", log=lambda *a: None)

    assert captured["pcm"][0] == b"RENDERED"   # changed segment -> rendered
    assert captured["pcm"][1] == b"CACHED1"    # unchanged segment -> reused from cache
    assert calls == ["flux-cole-en"]           # exactly one render call, for the changed voice


def test_rewrite_names_updates_handoffs_but_not_comments():
    """Recasting a slot's voice rewrites its name in host/desk lines, never in a real comment."""
    from hn_radio import config
    segs = [
        ScriptSegment(order=0, role="anchor", speaker_key="Haley", desk="anchor",
                      text="Over to you, Wade, at the maker desk.", voice_id="flux-haley-en"),
        ScriptSegment(order=1, role="desk", speaker_key="Wade", desk="maker",
                      text="Thanks. Back to you, Haley.", voice_id="flux-wade-en"),
        ScriptSegment(order=2, role="commenter", speaker_key="dang", desk=None,
                      text="Wade makes a good point here.", voice_id="flux-drew-en"),
    ]
    orig_voice = {"anchor": "flux-haley-en", "maker": "flux-wade-en", "guest": "flux-drew-en"}
    segs[1].voice_id = "flux-meena-en"   # was `recast.apply_mapping`, deleted 2026-08-22
    recast.rewrite_names(segs, {"maker": "flux-meena-en"}, orig_voice)

    assert config.ALL_VOICES["flux-meena-en"][0] == "Meena"
    assert segs[0].text == "Over to you, Meena, at the maker desk."  # host hand-off updated
    assert segs[1].speaker_key == "Meena"                            # desk speaker renamed
    assert segs[2].text == "Wade makes a good point here."         # real comment untouched


def test_rewrite_names_sees_a_retired_voices_name():
    """The OLD voice on an archived script is routinely one the show can no longer cast.

    Marcus and Priya read several episodes on disk before being retired by ear, so they are
    absent from VOICE_CATALOG. A lookup narrow enough to miss them returns None, and a None
    makes the rewrite skip in SILENCE: the script keeps saying "Marcus" while Alexis reads it,
    which is worse than not recasting at all. `config.voice_name` is deliberately wider than the
    castable catalog for exactly this call site.
    """
    segs = [
        ScriptSegment(order=0, role="anchor", speaker_key="Haley", desk="anchor",
                      text="Marcus has the maker desk today.", voice_id="flux-marcus-en"),
        ScriptSegment(order=1, role="desk", speaker_key="Marcus", desk="maker",
                      text="Thanks.", voice_id="flux-marcus-en"),
    ]
    orig_voice = {"maker": "flux-marcus-en"}    # retired by ear, not castable
    mapping = {"maker": "flux-alexis-en"}

    assert "flux-marcus-en" not in config.VOICE_CATALOG  # the reason a narrow lookup fails
    assert config.voice_name("flux-marcus-en") == "Marcus"
    segs[1].voice_id = "flux-alexis-en"   # was `recast.apply_mapping`
    recast.rewrite_names(segs, mapping, orig_voice)

    assert segs[0].text == "Alexis has the maker desk today."
    assert segs[1].speaker_key == "Alexis"


def test_rewrite_names_leaves_an_unknown_voice_alone():
    """An id in neither catalog has no name to substitute; the script must be left untouched."""
    segs = [ScriptSegment(order=0, role="desk", speaker_key="Wade", desk="maker",
                          text="Marcus here.", voice_id="flux-wade-en")]
    recast.rewrite_names(segs, {"maker": "flux-not-a-voice-en"}, {"maker": "flux-wade-en"})
    assert segs[0].text == "Marcus here." and segs[0].speaker_key == "Wade"
