"""PanelWriter must match the LLM writer's SHAPE, so a fallback night is not a different show."""

from datetime import date

from hn_radio.cast import Cast, Desk
from hn_radio.models import Comment, Story
from hn_radio.writers import PanelWriter


def _cast():
    """The two-person show: a host and one co-host, who between them do everything.

    Was a host plus an "ai" desk plus a "drama" desk until the two-person show. Both of those seats were
    deleted from the product, so a fixture that still built them would be testing PanelWriter
    against a cast the pipeline can no longer produce.
    """
    return Cast(
        anchor=Desk(role="anchor", name="Alexis", voice_id="v1", beat="hosts", persona="warm"),
        desks=[Desk(role="cohost", name="Wade", voice_id="v2", beat="second chair",
                    persona="curious")],
        default_role="cohost")


def _stories(n=3):
    return [Story(id=i, title=f"Story number {i}", url="https://example.com", points=10 * i,
                  author="a", num_comments=1, rank=i, kids=[]) for i in range(1, n + 1)]


def test_panel_writer_opens_with_a_cold_open_covering_every_story():
    segs = PanelWriter().write(_stories(), _stories()[0], [], _cast(), "frontpage",
                               date(2026, 8, 8))
    cold_open = [s for s in segs if s.desk == "anchor"][0]
    for story in _stories():
        assert story.title in cold_open.text


def test_panel_writer_cold_open_names_every_story_it_was_handed():
    """The cold open is the episode's table of contents, so it must list what actually airs.

    PanelWriter is the ONLY writer for build-your-own editions (custom.MAX_STORIES = 6), so a
    cap here would name three of six while chapters, title and source_items listed all six.
    """
    stories = _stories(5)
    segs = PanelWriter().write(stories, stories[0], [], _cast(), "frontpage", date(2026, 8, 8))
    cold_open = [s for s in segs if s.desk == "anchor"][0]
    for story in stories:
        assert story.title in cold_open.text


def test_panel_writer_skips_the_cold_open_when_there_are_no_stories():
    """custom.py reaches write() with an empty pick when its source pool has rotated.

    Without the guard the episode opens with a rendered segment whose entire text is "Today: ".
    """
    segs = PanelWriter().write([], None, [], _cast(), "custom", date(2026, 8, 8))
    assert segs == []


def test_panel_writer_uses_only_the_seats_in_the_cast():
    segs = PanelWriter().write(_stories(), _stories()[0], [], _cast(), "frontpage",
                               date(2026, 8, 8))
    assert {s.desk for s in segs if s.desk} <= {"anchor", "cohost"}


def test_panel_writer_covers_every_story_a_custom_edition_hands_it():
    """A 6-story custom edition must get six covered stories, not three.

    The cap belongs to the caller: `run_panel` hands over exactly `n_stories`, `custom.py`
    hands over up to `MAX_STORIES` (6). A cap inside the writer dropped half a custom episode
    from the audio while leaving it in the chapters, the title and source_items.
    """
    from hn_radio.custom import MAX_STORIES

    stories = _stories(MAX_STORIES)
    segs = PanelWriter().write(stories, stories[0], [], _cast(), "custom", date(2026, 8, 8))
    covered = {s.source_hn_id for s in segs if s.desk == "cohost"}
    assert covered == {s.id for s in stories}
    assert len(covered) == 6


# --- where the comment theater plays ------------------------------------------------------------
#
# Every comment comes from ONE thread (`ingest.pick_top_thread` picks the busiest and the
# pipeline fetches comments for that thread only), so the theater has exactly one story it
# belongs to and it plays there. These assert on segment ORDER and `source_hn_id`, never on the
# wording: the handoff lines are meant to be rewritten without breaking a test.

def _comments(story_id, n=2):
    return [Comment(id=100 + i, author=f"user{i}", text=f"<p>Comment number {i}.</p>") for i in range(n)]


def _commenters(segs):
    return [i for i, s in enumerate(segs) if s.role == "commenter"]


def _take(segs, story_id):
    """Index of the co-host's take on `story_id`.

    `min`, not `max`: the comment theater's closing line is also a co-host line carrying the
    story's id (deliberately, so `pacing.boundary_kind` reads the way out of the theater as a
    story change), so `max` would return that instead of the take and every ordering assertion
    below would compare the wrong two positions.
    """
    return min(i for i, s in enumerate(segs)
               if s.desk == "cohost" and s.source_hn_id == story_id)


def test_comment_theater_plays_immediately_after_the_busiest_story_take():
    """The whole point of this shape: the reactions land while the story is still in the ear.

    End-loaded, the show covered A, B, C and then jumped back to whichever one was busiest, so
    the listener heard the comments minutes after the story they belong to.
    """
    stories = _stories(3)
    top = stories[1]        # deliberately the MIDDLE story, so "inline" cannot pass by accident
    segs = PanelWriter().write(stories, top, _comments(top.id), _cast(), "frontpage",
                               date(2026, 8, 20))

    commenters = _commenters(segs)
    assert len(commenters) == 2
    opens_third = min(i for i, s in enumerate(segs) if s.source_hn_id == stories[2].id)
    assert _take(segs, top.id) < min(commenters)
    assert max(commenters) < opens_third


def test_a_story_that_is_not_the_busiest_thread_is_not_followed_by_comments():
    """Asymmetric on purpose: one story has reactions, the others have none."""
    stories = _stories(3)
    top = stories[1]
    segs = PanelWriter().write(stories, top, _comments(top.id), _cast(), "frontpage",
                               date(2026, 8, 20))

    commenters = _commenters(segs)
    opens_second = min(i for i, s in enumerate(segs) if s.source_hn_id == top.id)
    assert not any(_take(segs, stories[0].id) < i < opens_second for i in commenters)
    assert not any(i > _take(segs, stories[2].id) for i in commenters)


def test_the_theater_falls_back_to_the_end_of_the_show_for_an_uncovered_thread():
    """`top_story` can be a story this episode never covers: custom.py picks its stories from a
    rotating pool. Playing the comments late is worse than the new shape, but far better than
    silently dropping them."""
    stories = _stories(3)
    absent = Story(id=99, title="An uncovered thread", url="https://example.com", points=500,
                   author="a", num_comments=9, rank=9, kids=[])
    segs = PanelWriter().write(stories, absent, _comments(absent.id), _cast(), "custom",
                               date(2026, 8, 20))

    commenters = _commenters(segs)
    assert len(commenters) == 2
    last_story_line = max(i for i, s in enumerate(segs)
                          if s.source_hn_id in {s2.id for s2 in stories})
    assert min(commenters) > last_story_line


def test_no_theater_at_all_without_a_top_story():
    stories = _stories(3)
    segs = PanelWriter().write(stories, None, _comments(1), _cast(), "frontpage",
                               date(2026, 8, 20))
    assert not _commenters(segs)


def test_no_theater_segments_when_every_comment_is_filtered_out():
    """The safety filter can empty the theater. Inline placement must not leave a handoff to a
    drama desk that then has nothing to perform, and must not emit the block twice."""
    stories = _stories(3)
    top = stories[1]
    unsafe = [Comment(id=100, author="u", text="this is shit")]
    segs = PanelWriter().write(stories, top, unsafe, _cast(), "frontpage", date(2026, 8, 20))

    assert not _commenters(segs)
    plain = PanelWriter().write(stories, top, [], _cast(), "frontpage", date(2026, 8, 20))
    assert [(s.role, s.source_hn_id) for s in segs] == [(s.role, s.source_hn_id) for s in plain]


# --- what the move does to the cues and the gaps around it --------------------------------------

def _full_show(stories, top, comments):
    """The writer's segments wrapped in the fixed intro/outro the pipeline adds, renumbered.

    Music and pacing both see the WHOLE episode, and both special-case its first and last
    boundary, so checking either against the writer's output alone would check the wrong list.
    """
    from hn_radio import pipeline

    segs = (pipeline._intro_segments(_cast(), date(2026, 8, 20))
            + PanelWriter().write(stories, top, comments, _cast(), "frontpage", date(2026, 8, 20))
            + pipeline._outro_segments(_cast()))
    for i, seg in enumerate(segs):
        seg.order = i
    return segs


def test_the_inline_theater_still_yields_exactly_one_sting_per_story():
    """`music._sting_boundaries` keys on a story id's FIRST occurrence, so moving the theater
    into the middle of the rundown must not add, drop, or move a cue.

    Asserted here rather than in tests/test_music.py because the coupling being checked is to
    the WRITER's segment order: a story's segments are no longer contiguous, and comment
    segments now sit between one story's take and the next story's first line.
    """
    from hn_radio import music

    stories = _stories(3)
    top = stories[1]
    segs = _full_show(stories, top, _comments(top.id))
    story_ids = {st.id for st in stories}

    boundaries = music._sting_boundaries(segs, story_ids)
    firsts = {}
    for i, seg in enumerate(segs):
        if seg.source_hn_id in story_ids and seg.source_hn_id not in firsts:
            firsts[seg.source_hn_id] = i
    assert len(boundaries) == len(stories)
    assert sorted(boundaries) == sorted(i - 1 for i in firsts.values())
    # And nothing stings on the way into or out of the comment block: a comment id can never be
    # a story's first occurrence, which is what keeps the theater cue-free without a special case.
    assert not any(segs[b].role == "commenter" or segs[b + 1].role == "commenter"
                   for b in boundaries)


def test_coming_out_of_the_theater_into_the_next_story_still_reads_as_a_story_change():
    """The theater's closing line carries its story id for exactly this reason.

    Untagged, `pacing.boundary_kind` saw a plain exchange (0.16s) at the boundary into the next
    story instead of a story change (0.85s), so with music off the show sprinted out of the
    comments straight into a new headline.
    """
    from hn_radio import pacing

    stories = _stories(3)
    top = stories[1]
    segs = _full_show(stories, top, _comments(top.id))
    story_ids = {st.id for st in stories}

    last_comment = max(_commenters(segs))
    opens_next = min(i for i, seg in enumerate(segs) if seg.source_hn_id == stories[2].id)
    assert pacing.boundary_kind(segs[opens_next - 1], segs[opens_next],
                               story_ids) == "story_change"
    # The closing line is the only thing between them; if it ever moves, this test is wrong
    # rather than merely failing.
    assert opens_next - 1 == last_comment + 1


# --- handing each story TO the co-host ----------------------------------------------------------
#
# The 2026-08-20 episode cut straight from the host's headline into the co-host talking. Sam,
# having listened: it "cuts just from voice to voice", and he asked for a transition "especially
# at maybe the first story to say what do you think about this co-host".
#
# These pin the BEAT, never the wording. The invitations exist to be rewritten by ear, so an
# assertion on an exact sentence would break the moment anyone tunes one.

def _first_cohost_turn(segs, story_id):
    """Index of the co-host's FIRST line on `story_id`.

    `min`, like `_take` above and for the same reason: the comment theater's closing line is
    also a co-host line carrying a story id.
    """
    return min(i for i, s in enumerate(segs)
               if s.desk == "cohost" and s.source_hn_id == story_id)


def _invitation(segs, story):
    """The part of the host's throw that comes after the headline, i.e. the invitation itself.

    Split on the title rather than matched against a literal, so the invitations stay free to
    change. Everything before the title is the fixed "Top story"/"Next up" furniture.
    """
    lead = segs[_first_cohost_turn(segs, story.id) - 1]
    return lead.text.split(story.title)[-1].strip()


def test_every_story_is_handed_to_the_cohost_by_name():
    """Before this, only story one carried a name and every later story cut voice to voice."""
    stories = _stories(3)
    ep_cast = _cast()
    cohost = ep_cast.cohost.name
    segs = PanelWriter().write(stories, stories[0], [], ep_cast, "frontpage", date(2026, 8, 20))
    for story in stories:
        lead = segs[_first_cohost_turn(segs, story.id) - 1]
        assert lead.desk == "anchor", "the line before the co-host's first turn is the throw"
        assert cohost in lead.text, (
            f"story {story.id} cuts from {lead.text!r} straight into the co-host")


def test_the_invitation_is_a_different_line_on_every_story():
    """Three copies of one question would read as a template, which is WORSE than the hard cut
    it replaces: a hard cut merely sounds terse, a repeated formula sounds generated."""
    stories = _stories(3)
    segs = PanelWriter().write(stories, stories[0], [], _cast(), "frontpage", date(2026, 8, 20))
    invitations = [_invitation(segs, s) for s in stories]
    assert all(invitations), invitations
    assert len(set(invitations)) == len(invitations), invitations


def test_a_long_episode_never_repeats_an_invitation_back_to_back():
    """custom.py runs six stories through this writer, and the fixed set is smaller than that.

    Cycling is fine; two identical throws in a row is the thing a listener hears.
    """
    stories = _stories(6)
    segs = PanelWriter().write(stories, stories[0], [], _cast(), "custom", date(2026, 8, 20))
    invitations = [_invitation(segs, s) for s in stories]
    assert all(a != b for a, b in zip(invitations, invitations[1:])), invitations


def test_a_solo_cast_gets_no_invitation_at_all():
    """custom.py can build a cast with no second seat, and then the co-host IS the host. A host
    inviting herself by name is a glitch a listener can hear, which is why `solo` exists."""
    from hn_radio.cast import Cast, Desk

    host = Desk(role="anchor", name="Alexis", voice_id="v1", beat="hosts", persona="warm")
    solo = Cast(anchor=host, desks=[], default_role="anchor")
    stories = _stories(3)
    segs = PanelWriter().write(stories, stories[0], [], solo, "custom", date(2026, 8, 20))
    assert segs, "a solo cast still makes an episode"
    assert not any("Alexis" in s.text for s in segs), [s.text for s in segs]
