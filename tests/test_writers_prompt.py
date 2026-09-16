"""The prompt is the product here, so assert on what it does and does not instruct."""

from datetime import date

from hn_radio.cast import Cast, Desk
from hn_radio.writers import ClaudeWriter
from hn_radio.models import Story


def _cast():
    return Cast(
        anchor=Desk(role="anchor", name="Alexis", voice_id="v1", beat="hosts", persona="warm"),
        desks=[Desk(role="ai", name="Priya", voice_id="v2", beat="models", persona="precise"),
               Desk(role="drama", name="Cole", voice_id="v3", beat="comments",
                    persona="deadpan")],
        default_role="ai")


def _stories():
    return [Story(id=i, title=f"Story {i}", url="https://example.com", points=100,
                  author="a", num_comments=1, rank=i, kids=[]) for i in range(1, 4)]


def _stories_n(n):
    return [Story(id=i, title=f"Story {i}", url="https://example.com", points=100,
                  author="a", num_comments=1, rank=i, kids=[]) for i in range(1, n + 1)]


def _system(stories=None):
    w = ClaudeWriter()
    stories = _stories() if stories is None else stories
    system, _ = w._build_prompt(stories, stories[0], [], _cast(), "frontpage",
                                date(2026, 8, 8))
    return system


def test_prompt_no_longer_mandates_named_handoffs():
    s = _system()
    assert "Over to you" not in s
    assert "Back to you" not in s
    assert "NAMED, back-and-forth hand-offs" not in s


def test_prompt_states_the_rule_that_replaced_the_named_handoff():
    """The absence assertions above pass if HARD RULE 2 is DELETED, which is not the fix.

    This branch exists to replace a mandate with a different instruction, so pin the
    replacement's presence, not just the old string's absence.
    """
    s = _system()
    assert "WRITE HOW PEOPLE ACTUALLY TALK" in s
    assert "sounds like a relay" in s
    assert "Do NOT use a formal hand-off on every exchange" in s
    assert "Never have anyone introduce themselves" in s


def test_prompt_asks_for_a_cold_open():
    assert "COLD OPEN" in _system()


def test_prompt_tells_the_cold_open_not_to_claim_a_story_id():
    """A cold-open line previews a story; it is not that story's coverage.

    chapters.build_chapters opens a chapter at the FIRST segment carrying a story id, so a
    cold open tagged with real ids puts every chapter mark in the first fifteen seconds.
    """
    # Use the block helper, not a first-line grab: the rule sits on a continuation line.
    assert "source_hn_id 0" in _cold_open_line()


def _cold_open_line(s=None):
    """The COLD OPEN instruction, including its indented continuation lines.

    It used to be one concatenated line. The word cap pushed the
    source_hn_id rule onto its own indented line, so grabbing only the first line silently
    dropped half the instruction from every assertion below.
    """
    lines = (s or _system()).splitlines()
    i = next(n for n, ln in enumerate(lines) if "COLD OPEN" in ln)
    block = [lines[i]]
    for ln in lines[i + 1:]:
        if ln.startswith("   ") and not ln.lstrip().startswith(("2.", "3.")):
            block.append(ln)
        else:
            break
    return "\n".join(block)


def test_the_cold_open_asks_for_one_SHORT_sentence_per_story():
    """A cold open that runs long stops being a cold open. Heard on a real episode: the previews
    were full sentences with context, so the top of the show restated the whole episode."""
    line = _cold_open_line()
    # "Short" alone did not hold: Sam had to raise the length twice, so the cap is a NUMBER now.
    assert "TEN WORDS OR FEWER" in line
    assert "That cap is hard." in line


def test_the_cold_open_is_told_these_are_headlines_not_summaries():
    """"Short" alone is a length hint the model will argue with. Name the FORM instead."""
    line = _cold_open_line()
    assert "HEADLINES" in line
    assert "roughly the title of the article" in line


def test_the_cold_open_still_counts_every_story_after_being_shortened():
    """Shorter must not become fewer: the cold open is still the episode's table of contents."""
    assert "all 5" in _cold_open_line(_system(_stories_n(5)))


def test_prompt_asks_for_exactly_one_follow_up_per_story():
    s = _system()
    assert "ONE real follow-up" in s
    # and it must be substantive, not filler
    assert "Never a prompt to keep talking" in s
    # ...and it must read as the conversation continuing rather than as a second interview
    # question. Sam, 2026-09-16: "each pass should be more of a conversation".
    assert "the conversation continuing" in s
    assert "second interview question" in s


def test_prompt_permits_at_most_one_callback():
    s = _system()
    assert "callback" in s.lower()
    assert "at most one" in s
    # permitted, not mandated
    assert "You MAY use a callback" in s


def test_prompt_constrains_the_desk_enum_to_the_episode_cast():
    s = _system()
    assert "'ai' | 'drama'" in s
    assert "'maker'" not in s
    assert "'security'" not in s


def test_prompt_asks_for_exactly_the_stories_it_was_handed():
    assert "exactly 3 stories" in _system()


def test_prompt_story_count_follows_the_stories_given_not_a_literal():
    """`python -m hn_radio --stories 5` selects five; the prompt used to still say three."""
    s = _system(_stories_n(5))
    assert "exactly 5 stories" in s
    assert "exactly 3 stories" not in s
    assert "all 5" in s          # the cold open counts the same way
    assert "all three" not in s


def test_prompt_says_story_singular_for_a_one_story_episode():
    s = _system(_stories_n(1))
    assert "exactly 1 story," in s


def test_prompt_mentions_a_substitution_only_when_there_is_one():
    plain = ClaudeWriter()
    s1, _ = plain._build_prompt(_stories(), _stories()[0], [], _cast(), "frontpage",
                                date(2026, 8, 8))
    assert "is out today" not in s1

    sub = ClaudeWriter(substitutions={"anchor": "Alexis"})
    s2, _ = sub._build_prompt(_stories(), _stories()[0], [], _cast(), "frontpage",
                              date(2026, 8, 8))
    assert "is out today" in s2
    assert "Alexis" in s2


def test_an_explicitly_configured_writer_reports_that_the_caller_set_it():
    assert ClaudeWriter(substitutions={"anchor": "Alexis"})._caller_set_substitutions is True
    assert ClaudeWriter(substitutions={})._caller_set_substitutions is True


def test_the_cold_open_carries_a_hard_word_cap():
    """Sam had to ask twice. A qualitative 'short' did not hold; the cap is the fix."""
    s = _system()
    assert "TEN WORDS OR FEWER" in s
    assert "That cap is hard." in s
    assert "NO SUBORDINATE CLAUSES" in s
    # The rule that actually catches the failure mode he described by ear.
    assert "if the sentence needs a comma it is already too long" in s.lower()


def test_the_cold_open_shows_both_a_right_and_a_wrong_example():
    """The wrong example is the real line he flagged, so the model sees the exact failure."""
    s = _system()
    assert "Example of the RIGHT length" in s
    assert "Example of TOO LONG" in s
    assert "New Mexico court" in s


def test_the_cold_open_requires_the_final_item_to_start_with_and():
    """N headline sentences in a row just stop; the last one needs to signal it is the last.

    Heard on a real episode: a three-story open read as three unrelated announcements rather than
    a list, because nothing marked the final sentence as the end of one."""
    line = _cold_open_line()
    assert "FINAL cold-open sentence must start with" in line
    assert '"And"' in line
    # It has to fit inside the existing cap, not get a special exemption from it.
    assert "counts toward" in line and "ten-word cap" in line


# --- where the prompt puts the comment segment --------------------------------------------------

def test_the_comment_segment_is_placed_inside_the_busiest_story_not_at_the_end():
    """The comments all belong to one thread, so the show must not park them after every story.

    End-loaded, an episode covered A, B, C and then jumped back to whichever was busiest.
    """
    s = _system()
    assert "THE COMMENT SEGMENT PLAYS INSIDE THAT STORY'S COVERAGE" in s
    assert "only then move on to the next story" in s
    assert "Do NOT hold them until the end of the show" in s
    # The old instruction made the comment segment step 3 of the show, after every story.
    assert "Then Cole runs the comment segment" not in s


def test_the_prompt_owns_the_asymmetry_instead_of_letting_claude_fix_it():
    """Only the busiest thread's comments are fetched, so two of three stories have none.

    Without saying so, the obvious repair for a model asked to be consistent is to write
    reactions for the other stories, which means fabricating comments.
    """
    s = _system()
    assert "The other stories have no comments" in s
    assert "do not invent reactions" in s


def test_the_faithful_performance_rules_survive_the_move():
    """Moving the segment must not cost the rules that keep real comments real."""
    s = _system()
    assert "do not fabricate comments or change their meaning" in s
    assert "Use the commenter's username as the speaker" in s
    assert "Ground every claim in the SOURCE MATERIAL provided" in s
    assert "Do not invent facts" in s


def test_the_source_material_labels_the_busiest_thread_with_where_its_comments_play():
    """The system prompt says "inside that story's coverage"; the data block has to agree about
    which story that is, or the model has to guess."""
    from hn_radio.models import Comment

    stories = _stories()
    comments = [Comment(id=77, author="dang", text="<p>Nice one</p>")]
    _, user = ClaudeWriter()._build_prompt(stories, stories[1], comments, _cast(), "frontpage",
                                          date(2026, 8, 20))
    assert "whose comments play inside its own coverage" in user
    assert f"[story {stories[1].id}]" in user
    assert "@dang [id 77]" in user


# --- handing each story to the other person -----------------------------------------------------
#
# The diagnosis from the first native two-person episode (2026-08-20): mid-story exchanges were
# fine, and every story's FIRST turn cut from voice to voice with no invitation at all. Names
# appeared twice in twenty-five segments. So the fix is one beat per story, not a per-exchange
# rule; the per-exchange rule was tried on this show and cut, and the tests above still pin that.

def test_the_prompt_requires_a_named_handoff_into_each_story():
    s = _system()
    assert "HAND EACH STORY OVER" in s
    assert "an invitation that says their name" in s
    # named for the model as the failure it is fixing, not as a style preference
    assert "cuts from voice to voice" in s


def test_the_handoff_is_capped_at_one_per_story():
    """The cap is what stops this collapsing back into the mandate that was already cut."""
    s = _system()
    assert "ONE per story" in s
    assert "not a license to name each other again inside the story" in s
    # and the handoff must not be spent as the story's one real follow-up
    assert "The handoff is also NOT the follow-up in HARD RULE 3" in s


def test_the_handoff_shapes_are_enumerated_so_they_cannot_all_be_the_same_question():
    """Asking for "variety" gets three rephrasings of one question. Naming three different
    FORMS and forbidding a repeat is the structural version of the same request."""
    s = _system()
    assert "USE A DIFFERENT SHAPE EACH TIME" in s
    assert "shapes, not synonyms" in s
    for shape in ("a real question about the substance",
                  "the one specific detail that surprised you",
                  "a thought you are already halfway through"):
        assert shape in s, shape


def test_the_handoff_is_never_a_debate_setup():
    """Sam, 2026-09-16: "we don't wanna do the segment where I set it up and you defend it or I
    set it up and you knock it down."

    Two of the three old shapes did exactly that -- "a framing they can argue with, where they
    take the other side" and "a lead-in they finish" -- and the writer handed the first one back
    as dialogue: "I'll take the other side" opens a desk turn in six episodes on the feed. Both
    are gone from the list, and the prompt now says so outright, because a shape that produced six
    episodes of the same move needs a named prohibition rather than a quiet deletion.
    """
    s = _system()
    assert "NOT A DEBATE FORMAT" in s
    assert "do not set it up for them to knock down" in s
    assert "they are allowed to agree" in s
    # The removed shapes must not be reachable as instructions any more. They appear only inside
    # the prohibition that names them, so each may occur at most once.
    for removed in ("a framing they can argue with", "a lead-in they finish"):
        assert s.count(removed) == 1, f"{removed!r} should survive only as a banned example"


def test_the_comment_segment_is_an_exchange_not_two_readings():
    """Sam, 2026-09-16: tighten the comment theater, the show is a back and forth between hosts.

    The old prompt asked them to read the comments "taking turns", which produced two recitals
    with a summary after. Now a comment is answered before the next one is read.
    """
    s = _system()
    assert "ONE EXCHANGE, NOT TWO READINGS" in s
    assert "answers THAT COMMENT" in s
    assert "not taking it in" in s and "turns to recite it" in s
    assert "No summing up the thread" in s


def test_the_handoff_does_not_have_to_be_a_question():
    """Three questions in three stories is the formula this is trying to avoid."""
    assert "It does not have to be a question" in _system()


def test_the_first_story_gets_the_most_conversational_handoff():
    """Sam singled it out: "especially at maybe the first story"."""
    s = _system()
    assert "The FIRST story" in s
    assert "the question, and give it the most room" in s


def test_the_handoff_rule_does_not_reintroduce_a_per_exchange_mandate():
    """The whole trap. This has to add a beat per STORY without turning names back into a rule
    that fires on every turn, which is what was removed for reading stilted."""
    s = _system()
    assert "Do NOT use a formal hand-off on every exchange" in s
    assert "when it feels natural" in s
    for mandate in ("every exchange must", "on each turn", "in every turn",
                    "use their name each", "always use a name"):
        assert mandate not in s.lower(), f"{mandate!r} turns names back into a rule"


# --- the two-part cold open and turn length (2026-09-16) ---------------------------------------


def test_the_cold_open_is_asked_for_in_two_parts():
    block = _cold_open_line()
    assert "TWO PARTS" in block
    assert "TEASE" in block and "PREVIEW" in block


def test_only_the_co_host_tease_line_may_ask_something():
    block = _cold_open_line()
    assert "the only question allowed in the cold open" in block
    # the preview is still the flat, matter-of-fact list it always was
    assert "MATTER-OF-FACT" in block


def test_the_preview_is_still_one_segment():
    """The audio reason did not change: separate renders put a pause between every headline that
    no punctuation asked for."""
    block = _cold_open_line()
    assert "WHOLE PREVIEW as a SINGLE segment" in block


def test_every_cold_open_segment_still_disclaims_a_story_id():
    assert "source_hn_id 0" in _cold_open_line()


def test_the_prompt_asks_for_uneven_turn_lengths():
    s = _system()
    assert "TURN LENGTH" in s
    assert "NEVER WRITE TWO MEDIUM TURNS IN A ROW" in s
    # the rule carries the measurement it came from, like the rest of this prompt
    assert "3.9x" in s and "23x" in s


def test_the_prompt_asks_one_of_them_to_carry_each_story_and_then_swap():
    s = _system()
    assert "ONE OF THEM CARRIES EACH STORY" in s
    assert "SWAP" in s
    # and it must not undo the two-equals rule the show was rebuilt around
    assert "neither is a beat reporter" in s


# --- fixes from the 2026-09-16 listen ---------------------------------------------------------


def test_the_prompt_forbids_inventing_a_pronoun():
    """The show called Canada's prime minister "she" for a whole segment."""
    s = _system()
    assert "PRONOUNS ARE A FACT LIKE ANY OTHER" in s
    assert "Never guess from a first name" in s


def test_the_first_story_may_not_point_back_at_the_preview():
    """The preview ends on the LAST story and the coverage starts with the FIRST, so a deictic
    hand-off there sends the listener to the wrong subject."""
    s = _system()
    assert "DO NOT POINT BACK AT THE PREVIEW" in s
    assert "let's start there" in s
