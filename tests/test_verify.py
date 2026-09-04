"""The verification pass: a take that rendered is not automatically an episode.

Pinned against the episode that shipped at 173 seconds with `PanelWriter` reading a
markdown image tag and an S3 URL aloud. Two gates, two places, and the scheduled run re-runs on
either. See `hn_radio/verify.py` for why each sits where it does.
"""

from __future__ import annotations

import pytest

from hn_radio import config, pipeline, verify
from hn_radio.models import ScriptSegment


def _seg(text, order=0, who="Alexis"):
    return ScriptSegment(order=order, role="anchor", speaker_key=who, text=text, desk="anchor")


# --- the script gate ------------------------------------------------------------------------------

def test_the_fallback_lines_that_aired_are_caught():
    """The two lines that actually aired. Both must fail, and the reason must name the segment."""
    segs = [
        _seg("Next up, 1103 points: Audacity 4.0. Wade, what do you make of it?", 12),
        _seg("[![Coverage](https://s3.us-east-1.amazonaws.com/extensions.musescore.org/x.svg)]"
             "(https://example.org) Audacity 4.0 has arrived.", 13, "Wade"),
        _seg("Related: OpenAI begins rolling out GPT-6 Astra - a link https://openai.com/x", 8, "Wade"),
    ]
    problems = verify.script_problems(segs)
    assert len(problems) == 2, problems
    assert any("segment 13" in p and "Wade" in p for p in problems)
    assert any("segment 8" in p for p in problems)
    with pytest.raises(verify.VerificationError) as e:
        verify.gate_script(segs)
    assert e.value.problems == problems


@pytest.mark.parametrize("bad", [
    "See https://example.com/path for the details.",
    "The repo is at www.github.com/foo/bar and it is small.",
    "![alt text](image.png) is the whole README.",
    "Read [the post](https://blog.example) first.",
    "<p>Paragraph from a scraped page</p>",
    "They said &quot;no&quot; to the whole idea.",
    "# A Heading That Was Never Meant To Be Spoken",
    "Run ```pip install thing``` and go.",
    "This is **very** important, the README says.",
])
def test_unspeakable_syntax_is_a_problem(bad):
    assert verify.script_problems([_seg(bad)]), bad


@pytest.mark.parametrize("fine", [
    "Verisign is shutting down dot name, the registry nobody used.",
    "It's at github dot com slash simonw, if you want to follow along.",
    "The thread hit 1,822 points. Wade, where do you want to start?",
    "That's the front page this morning. From me and Wade, on Deepgram Flux, we'll talk to you this afternoon.",
    "She asked: is that confidence, or is that quiet?",
    "The note is mostly about why rather than how much, and the pitch under it is #1 on the page.",
    "AT&T and Verizon both raised prices again.",
])
def test_ordinary_speech_is_not_a_problem(fine):
    """High precision is the contract: a false positive throws away a good take and costs a re-run."""
    assert verify.script_problems([_seg(fine)]) == [], fine


def test_one_reason_per_segment_and_every_bad_segment_reported():
    segs = [_seg("ok line"), _seg("https://a.example and <b>bold</b>", 1), _seg("![x](y)", 2)]
    problems = verify.script_problems(segs)
    assert len(problems) == 2
    assert "segment 1" in problems[0] and "segment 2" in problems[1]


# --- the duration gate ----------------------------------------------------------------------------

def test_the_floor_sits_between_every_fallback_night_and_every_claude_night():
    """173s and 174s were fallback nights; 325s was the shortest real one on the feed."""
    assert 174 < config.MIN_EPISODE_SECONDS < 325
    with pytest.raises(verify.VerificationError) as e:
        verify.gate_duration(172.6, config.MIN_EPISODE_SECONDS)
    assert "173s" in str(e.value) and f"{config.MIN_EPISODE_SECONDS:.0f}s" in str(e.value)
    verify.gate_duration(325.13, config.MIN_EPISODE_SECONDS)  # does not raise


def test_the_floor_is_inclusive_at_exactly_the_minimum():
    verify.gate_duration(config.MIN_EPISODE_SECONDS, config.MIN_EPISODE_SECONDS)


# --- where the gates sit in the pipeline ----------------------------------------------------------

def _finalize_with(monkeypatch, tmp_path, duration, **kw):
    """Drive `_finalize` with a fake stitch so the duration is whatever the test says."""
    from hn_radio import music, pacing, stitch, status
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    monkeypatch.setattr(status, "done", lambda *a, **k: None)
    segs = [_seg("Hi.", 0), _seg("Bye.", 1)]
    pcm = [b"\x00\x00" * 2400, b"\x00\x00" * 2400]
    monkeypatch.setattr(pipeline, "_space_cold_open", lambda s, p, log=print: list(p))
    monkeypatch.setattr(pacing, "apply", lambda s, t, story_ids=None: (list(t), [0.0]))
    monkeypatch.setattr(music, "apply", lambda s, p, g, story_ids=None, enabled=True, log=print:
                        (list(p), g, [0.0, 0.2]))
    monkeypatch.setattr(stitch, "stitch", lambda pieces, path, gaps: duration)
    published = {}

    def fake_publish(episode, out_dir):
        published["episode"] = episode
        return {}
    import hn_radio.publish as publish_mod
    import hn_radio.chapters as chapters_mod
    monkeypatch.setattr(publish_mod, "publish", fake_publish)
    monkeypatch.setattr(publish_mod, "build_vtt", lambda ep: "WEBVTT\n")
    monkeypatch.setattr(chapters_mod, "build_chapters", lambda ep: [])
    monkeypatch.setattr(chapters_mod, "write_chapters_json", lambda c, d: None)
    monkeypatch.setattr(chapters_mod, "to_mp3_with_chapters", lambda *a, **k: None)
    result = pipeline._finalize(segs, pcm, episode_id="2026-09-05-am", title="t",
                                source_items=[], edition="frontpage", with_music=False,
                                log=lambda *a, **k: None, **kw)
    return result, published


def test_a_short_take_is_rejected_before_anything_is_published(monkeypatch, tmp_path):
    """The gate sits after the stitch and before publish, so a bad take never reaches the feed."""
    with pytest.raises(verify.VerificationError):
        _finalize_with(monkeypatch, tmp_path, 172.6, min_seconds=300)
    assert not (tmp_path / "2026-09-05-am" / "episode.json").exists()
    assert not (tmp_path / "feed.xml").exists()
    # The archive IS written: the segment cache is what a re-render would start from.
    assert (tmp_path / "2026-09-05-am" / "segments" / "0.pcm").exists()


def test_a_long_enough_take_publishes(monkeypatch, tmp_path):
    result, published = _finalize_with(monkeypatch, tmp_path, 361.0, min_seconds=300)
    assert published["episode"] is result
    assert result.duration_seconds == 361.0


def test_no_floor_means_no_gate(monkeypatch, tmp_path):
    """`python -m hn_radio --stories 1` is legitimately short and must keep working."""
    result, published = _finalize_with(monkeypatch, tmp_path, 40.0)
    assert published["episode"] is result


def test_the_floor_threads_from_run_panel_to_finalize():
    """The music switch was once added to `run_panel` and not to `render_panel`; same shape here."""
    import inspect
    for fn in (pipeline.run_panel, pipeline.render_panel, pipeline._finalize):
        assert "min_seconds" in inspect.signature(fn).parameters, fn.__name__


def test_run_panel_rejects_an_unspeakable_script_before_the_render(monkeypatch, tmp_path):
    """The cheap gate: a URL in the script costs a re-run, not a Flux render."""
    from hn_radio import ingest, sources, status
    from hn_radio.models import Story
    from hn_radio.writers import PanelWriter

    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    stories = [Story(id=1, title="A story", url="https://example.com", points=99, author="a",
                     num_comments=1, rank=1, kids=[])]
    monkeypatch.setattr(ingest, "fetch_front_page_for_date", lambda d, n: list(stories))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    rendered = []
    monkeypatch.setattr(pipeline, "render_panel", lambda segments, **kw: rendered.append(1))

    def markdown_write(self, *a, **k):
        return [_seg("[![Coverage](https://s3.example/x.svg)](https://x) arrived.", 0, "Wade")]
    monkeypatch.setattr(PanelWriter, "write", markdown_write)

    with pytest.raises(verify.VerificationError) as e:
        pipeline.run_panel(edition="frontpage", log=lambda *a, **k: None)
    assert "markdown" in str(e.value) or "url" in str(e.value)
    assert rendered == [], "an unspeakable script must not reach the renderer"
