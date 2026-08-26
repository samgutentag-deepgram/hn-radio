from datetime import date

from hn_radio import script_assembly as sa
from hn_radio.models import Comment, Story


def test_clean_strips_tags_and_unescapes_entities():
    raw = "<p>You could <i>just</i> use Postgres &amp; call it a day.</p>"
    out = sa.clean_comment_html(raw)
    assert "<" not in out and ">" not in out
    assert "&amp;" not in out
    assert "just use Postgres & call it a day" in out


def test_clean_drops_code_blocks_and_urls():
    raw = "See <pre><code>rm -rf /</code></pre> at https://evil.example.com/x now"
    out = sa.clean_comment_html(raw)
    assert "rm -rf" not in out
    assert "https://" not in out
    assert "a link" in out


def test_is_safe_blocks_profanity():
    assert sa.is_safe("this is a reasonable take")
    assert not sa.is_safe("this is shit")


def test_trim_keeps_within_limit_at_sentence_boundary():
    text = "One sentence here. " * 100
    out = sa._trim_to_sentence(text, 100)
    assert len(out) <= 101
    assert out.endswith(".")


def _story(i, kids=None):
    return Story(id=i, title=f"Story {i}", url="http://x", points=10 * i,
                 author="a", num_comments=len(kids or []), rank=i, kids=kids or [])


def test_assembler_orders_intro_headlines_comments_outro():
    stories = [_story(1, kids=[11]), _story(2)]
    top = stories[0]
    comments = [Comment(id=11, author="dang", text="<p>Nice one</p>")]
    segs = sa.TemplateAssembler().assemble(stories, top, comments, date(2026, 8, 3))

    assert segs[0].role == "host" and "front page" in segs[0].text
    assert segs[-1].role == "host" and "grass" in segs[-1].text
    # every segment is plain text
    assert all("<" not in s.text for s in segs)
    # the comment is performed by a commenter voice-role, attributed to its author
    commenter_segs = [s for s in segs if s.role == "commenter"]
    assert len(commenter_segs) == 1
    assert commenter_segs[0].speaker_key == "dang"
    assert commenter_segs[0].source_hn_id == 11


def test_unsafe_comment_is_dropped():
    stories = [_story(1, kids=[11])]
    comments = [Comment(id=11, author="x", text="this is shit")]
    segs = sa.TemplateAssembler().assemble(stories, stories[0], comments, date(2026, 8, 3))
    assert not any(s.role == "commenter" for s in segs)


def test_comments_follow_the_busiest_story_headline_not_the_last_one():
    """The legacy assembler is still reachable with `--legacy`, so it gets the same shape.

    Assert on order and `source_hn_id`, not wording: the lead-in is meant to be rewritable.
    """
    stories = [_story(1), _story(2, kids=[22]), _story(3)]
    top = stories[1]
    comments = [Comment(id=22, author="dang", text="<p>Nice one</p>")]
    segs = sa.TemplateAssembler().assemble(stories, top, comments, date(2026, 8, 20))

    commenters = [i for i, s in enumerate(segs) if s.role == "commenter"]
    assert len(commenters) == 1
    headline_two = next(i for i, s in enumerate(segs) if s.source_hn_id == 2)
    headline_three = next(i for i, s in enumerate(segs) if s.source_hn_id == 3)
    assert headline_two < min(commenters) < headline_three


def test_comments_fall_back_to_the_end_when_the_busiest_thread_is_not_listed():
    stories = [_story(1), _story(2)]
    absent = _story(99, kids=[22])
    comments = [Comment(id=22, author="dang", text="<p>Nice one</p>")]
    segs = sa.TemplateAssembler().assemble(stories, absent, comments, date(2026, 8, 20))

    commenters = [i for i, s in enumerate(segs) if s.role == "commenter"]
    assert len(commenters) == 1
    last_headline = max(i for i, s in enumerate(segs) if s.source_hn_id in {1, 2})
    assert min(commenters) > last_headline
