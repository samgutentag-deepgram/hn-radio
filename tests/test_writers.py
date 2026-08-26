from datetime import date


from hn_radio.cast import DEFAULT_CAST
from hn_radio.models import Comment, Story
from hn_radio.writers import ClaudeWriter, PanelWriter


def _story(id, title, url=None, summary=None, kind="none"):
    s = Story(id=id, title=title, url=url, points=100, author="a", num_comments=5, rank=id, kids=[])
    s.summary, s.source_kind = summary, kind
    return s


def test_panel_writer_shapes_a_full_episode():
    stories = [
        _story(1, "A tiny git in Rust", url="https://github.com/me/g",
               summary="It streams weights and stays small.", kind="repo"),
        _story(2, "Ask HN: gardening"),
    ]
    top = stories[0]
    comments = [Comment(id=11, author="dang", text="<p>Nice work</p>")]
    segs = PanelWriter().write(stories, top, comments, DEFAULT_CAST, "makers", date(2026, 8, 4))

    # the writer covers content only; the fixed intro/outro are added by the pipeline
    # segs[0] is now the cold open: one sentence per story, before any story is covered
    assert segs[0].role == "anchor" and segs[0].desk == "anchor"
    assert "A tiny git in Rust" in segs[0].text
    assert "Ask HN: gardening" in segs[0].text
    assert all("<" not in s.text for s in segs)          # plain text only
    # the repo story's take references the fetched source
    assert any(s.role == "desk" and "streams weights" in s.text for s in segs)
    # comment theater performed the comment as a commenter, in a REGULAR's voice, keeping the
    # real username. No guest voice any more; see tests/test_two_person_show.py.
    performed = [s for s in segs if s.role == "commenter"]
    assert performed and performed[0].speaker_key == "dang"
    assert performed[0].voice_id in {DEFAULT_CAST.anchor.voice_id,
                                     DEFAULT_CAST.desks[0].voice_id}


def test_panel_writer_handles_no_summary():
    """A sourceless story still gets a desk take; it just does not explain WHY it is short.

    This used to assert the word "headline" appeared in the line, which pinned the old text
    "No page to read on this one, just the headline, but the thread is busy." That sentence
    is the show narrating its own sourcing, so the assertion was holding the bug in place.
    What matters is that the desk still speaks. See tests/test_writers_frame.py.
    """
    stories = [_story(1, "Some headline only")]
    segs = PanelWriter().write(stories, None, [], DEFAULT_CAST, "frontpage", date(2026, 8, 4))
    desk_lines = [s for s in segs if s.role == "desk"]
    assert desk_lines and desk_lines[0].text.strip()


def test_claude_writer_maps_segments_without_calling_the_api():
    """_to_segments post-processes Claude's raw output: names from cast, guest voices, safety."""
    raw = [
        {"role": "anchor", "desk": "anchor", "speaker_key": "", "text": "Good morning.", "source_hn_id": 0},
        # "maker" is a desk the two-person show does not have, so the out-of-cast guard remaps it
        # to the co-host rather than letting it fall through to the host's voice.
        {"role": "desk", "desk": "maker", "speaker_key": "", "text": "A neat little tool.", "source_hn_id": 42},
        {"role": "commenter", "desk": "", "speaker_key": "dang", "text": "Please be kind.", "source_hn_id": 7},
        {"role": "desk", "desk": "cohost", "speaker_key": "", "text": "   ", "source_hn_id": 0},  # empty -> dropped
    ]
    segs = ClaudeWriter()._to_segments(raw, DEFAULT_CAST)
    assert [s.role for s in segs] == ["anchor", "desk", "commenter"]  # empty one dropped
    assert segs[0].speaker_key == "Alexis"                   # host name comes from the cast, not the text
    assert segs[1].desk == "cohost" and segs[1].speaker_key == "Wade"
    assert segs[1].source_hn_id == 42
    assert segs[2].speaker_key == "dang"
    assert segs[2].voice_id == DEFAULT_CAST.desks[0].voice_id   # co-host reads the first comment


def test_pipeline_intro_and_outro_signature():
    from hn_radio import pipeline
    intro = pipeline._intro_segments(DEFAULT_CAST, date(2026, 8, 4))
    outro = pipeline._outro_segments(DEFAULT_CAST)
    assert len(intro) == 1 and intro[0].role == "anchor"
    # August 5, not 4: the intro speaks the air date, the day after the front page it covers.
    assert "Hacker News Radio" in intro[0].text and "August 5" in intro[0].text
    # Both signature lines now name the people, which is why they live in the pipeline: only it
    # holds the episode cast. See tests/test_two_person_show.py for the naming assertions.
    assert DEFAULT_CAST.anchor.name in intro[0].text
    assert len(outro) == 1 and "tomorrow" in outro[0].text.lower()


def test_writers_report_no_title_before_writing():
    assert ClaudeWriter().episode_title() is None   # set only after a successful write
    assert PanelWriter().episode_title() is None     # deterministic writer never sets one
