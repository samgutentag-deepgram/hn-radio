"""run_panel must build the episode cast itself, and hand substitutions to the writer."""

import inspect

from hn_radio import config, pipeline


def test_three_stories_has_one_source_of_truth():
    """A literal in run_panel would silently disagree with the CLI default in __main__."""
    assert config.N_STORIES == 3
    assert inspect.signature(pipeline.run_panel).parameters["n_stories"].default == 3


def test_run_panel_casts_two_voices_and_covers_n_stories(monkeypatch, tmp_path):
    """The wiring: selection no longer needs a cast at all, the episode cast seats two, and
    nothing renders."""
    from hn_radio import pipeline, ingest, sources, config, status
    from hn_radio.models import Story

    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)

    stories = [Story(id=i, title=t, url="https://example.com", points=100 - i,
                     author="a", num_comments=1, rank=i, kids=[])
               for i, t in enumerate(["LLM model inference", "a nasty cve exploit",
                                      "my side project", "extra one", "extra two"], start=1)]
    monkeypatch.setattr(ingest, "fetch_front_page_for_date", lambda d, n: list(stories))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)

    captured = {}
    def fake_render_panel(segments, **kw):
        captured["cast"] = kw["cast"]
        captured["segments"] = segments
        captured["source_items"] = kw["source_items"]
        return "episode"
    monkeypatch.setattr(pipeline, "render_panel", fake_render_panel)

    result = pipeline.run_panel(edition="frontpage", log=lambda *a, **k: None)
    assert result == "episode"
    ep_cast = captured["cast"]
    assert len(ep_cast.desks) == 1                      # one co-host
    assert ep_cast.desks[0].role == "cohost"
    assert ep_cast.desks[0].voice_id != ep_cast.anchor.voice_id
    assert len(captured["source_items"]) == 3           # three stories, not five
    regulars = {s.speaker_key for s in captured["segments"] if s.role != "commenter"}
    assert len(regulars) == 2, f"a two-person show has two speakers, got {sorted(regulars)}"
    # The story cap lives HERE, in the caller, not inside PanelWriter: run_panel selects
    # exactly n_stories, so the writer covering everything it is handed still yields three.
    covered = {s.source_hn_id for s in captured["segments"] if s.source_hn_id}
    assert len(covered) == 3


def test_a_reused_writer_gets_fresh_substitutions_on_the_second_run(monkeypatch, tmp_path):
    """Run 1's substitutions must not survive into run 2 unchanged.

    REWORKED for the two-person show. It used to lean on the desks: a catalog with no Priya made
    the ai desk substitute, so run 1 recorded {"ai": "Priya"}. There are no desks to substitute now,
    and the co-host never reports one (its candidates are built from the live catalog), so the
    only seat that can announce an absence is the host.

    So run 1 uses a catalog with no Alexis at all -- Haley hosts and the show says so -- and run 2
    uses the real production catalog, where Alexis is available and there is nothing to announce.
    Order still matters and is still the point: under the old `not writer.substitutions` gate,
    run 2 would see a non-empty dict, skip the reassignment, and repeat run 1's stale note.
    Running the empty case first would let the buggy gate pass and prove nothing.
    """
    from hn_radio import pipeline, ingest, sources, config, status
    from hn_radio.models import ScriptSegment, Story
    from hn_radio.writers import ClaudeWriter

    monkeypatch.setattr(config, "EPISODES_DIR", tmp_path)
    stories = [Story(id=i, title=t, url="https://example.com", points=100 - i,
                     author="a", num_comments=1, rank=i, kids=[])
               for i, t in enumerate(["LLM model inference", "a nasty cve exploit",
                                      "my side project"], start=1)]
    monkeypatch.setattr(ingest, "fetch_front_page_for_date", lambda d, n: list(stories))
    monkeypatch.setattr(ingest, "populate_kids", lambda s: None)
    monkeypatch.setattr(ingest, "pick_top_thread", lambda s: None)
    monkeypatch.setattr(ingest, "fetch_top_comments", lambda t, n: [])
    monkeypatch.setattr(sources, "enrich_story", lambda s: None)
    monkeypatch.setattr(status, "begin", lambda *a, **k: None)
    monkeypatch.setattr(status, "stage", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "render_panel", lambda segments, **kw: "episode")

    seen = []
    writer = ClaudeWriter()

    def fake_write(self, stories_, top, comments, cast_, edition, episode_date):
        # capture what the writer was handed for THIS run
        seen.append(dict(self.substitutions))
        return [ScriptSegment(order=0, role="anchor", speaker_key=cast_.anchor.name,
                              text="a line", desk="anchor")]
    monkeypatch.setattr(ClaudeWriter, "write", fake_write)

    no_alexis = {k: v for k, v in config.VOICE_CATALOG.items() if "alexis" not in k}
    monkeypatch.setattr(config, "active_voice_catalog", lambda: no_alexis)
    pipeline.run_panel(edition="frontpage", writer=writer, log=lambda *a, **k: None)

    monkeypatch.setattr(config, "active_voice_catalog", lambda: dict(config.VOICE_CATALOG))
    pipeline.run_panel(edition="frontpage", writer=writer, log=lambda *a, **k: None)

    assert seen[0] == {"anchor": "Alexis"}, "no Alexis in the catalog, so the host substitutes"
    assert seen[1] == {}, (
        "production can cast Alexis, so run 2 must clear run 1's note rather than repeat it"
    )
