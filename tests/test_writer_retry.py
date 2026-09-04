"""An LLM writer gets a second try before the show falls back to canned copy.

Found on the 2026-09-03 replay: the fallback was not covering an API outage, it was covering the
de-slop gate rejecting a good Claude script for three "it's not X, it's Y" lines. Half the Claude
episodes since the gate landed shipped as PanelWriter. A second sample is one LLM call and no
render; a fallback episode is a full render plus the re-run that verification then forces.
"""

from __future__ import annotations

import pytest

from hn_radio import config, pipeline
from hn_radio.models import ScriptSegment, Story
from hn_radio.writers import ClaudeWriter, PanelWriter


def _wire(monkeypatch, tmp_path):
    from hn_radio import ingest, sources, status
    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    stories = [Story(id=i, title=f"story {i}", url="https://example.com", points=100 - i,
                     author="a", num_comments=1, rank=i, kids=[]) for i in range(1, 4)]
    monkeypatch.setattr(ingest, "fetch_front_page_for_date", lambda d, n: list(stories))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(pipeline, "render_panel",
                        lambda segments, **kw: captured.update(segments=segments) or "ep")
    return captured


def _good(cast):
    return [ScriptSegment(order=0, role="anchor", speaker_key=cast.anchor.name, desk="anchor",
                          text="A clean line about the first story.", source_hn_id=1)]


# Three "X is not Y" constructions; the gate's limit is two. The rule matches "is not"/"are not"
# followed by a word, so spell them out rather than contracting them.
DIGIORNO = ("This is not a bug, it is a feature. The code is not slow, it is careful. That is not a "
            "fix, it is a workaround.")


def test_a_de_slop_rejection_gets_a_second_claude_sample_not_the_fallback(monkeypatch, tmp_path):
    captured = _wire(monkeypatch, tmp_path)
    calls = []

    def flaky(self, stories, top, comments, cast, edition, when):
        calls.append(1)
        if len(calls) == 1:
            return [ScriptSegment(order=0, role="anchor", speaker_key=cast.anchor.name,
                                  desk="anchor", text=DIGIORNO, source_hn_id=1)]
        return _good(cast)
    monkeypatch.setattr(ClaudeWriter, "write", flaky)
    panel_calls = []
    real_panel = PanelWriter.write
    monkeypatch.setattr(PanelWriter, "write",
                        lambda self, *a, **k: panel_calls.append(1) or real_panel(self, *a, **k))

    logs = []
    assert pipeline.run_panel(edition="frontpage", writer=ClaudeWriter(), log=logs.append) == "ep"
    assert len(calls) == 2 and panel_calls == [], "second sample, no canned copy"
    assert any("trying it once more" in ln and "de-slop" in ln for ln in logs)
    assert "A clean line" in captured["segments"][1].text  # index 0 is the fixed intro


def test_two_failures_still_fall_back_so_the_show_stays_on_air(monkeypatch, tmp_path):
    captured = _wire(monkeypatch, tmp_path)
    calls = []

    def broken(self, *a, **k):
        calls.append(1)
        raise RuntimeError("Anthropic API error: overloaded")
    monkeypatch.setattr(ClaudeWriter, "write", broken)
    logs = []
    assert pipeline.run_panel(edition="frontpage", writer=ClaudeWriter(), log=logs.append) == "ep"
    assert len(calls) == pipeline.WRITER_ATTEMPTS == 2
    assert any("falling back to PanelWriter" in ln for ln in logs)
    assert len(captured["segments"]) > 2, "PanelWriter produced the show"


def test_the_deterministic_writer_is_not_retried(monkeypatch, tmp_path):
    """Same input, same output: a second PanelWriter call can only fail the same way."""
    _wire(monkeypatch, tmp_path)
    calls = []

    def broken(self, *a, **k):
        calls.append(1)
        raise RuntimeError("canned copy broke")
    monkeypatch.setattr(PanelWriter, "write", broken)
    with pytest.raises(RuntimeError, match="canned copy broke"):
        pipeline.run_panel(edition="frontpage", log=lambda *a, **k: None)
    assert len(calls) == 1
