from hn_radio import normalize
from hn_radio.models import ScriptSegment


def test_expands_hn_to_hacker_news():
    assert normalize.normalize_text("This is HN Radio") == "This is Hacker News Radio"
    assert normalize.normalize_text("HN loves HN") == "Hacker News loves Hacker News"


def test_only_whole_word():
    assert normalize.normalize_text("CHN and HNRadio stay") == "CHN and HNRadio stay"


def test_idempotent():
    once = normalize.normalize_text("Top of HN today")
    assert normalize.normalize_text(once) == once


def test_normalize_segments_mutates_in_place():
    segs = [ScriptSegment(order=0, role="anchor", speaker_key="Haley", text="Welcome to HN Radio")]
    normalize.normalize_segments(segs)
    assert segs[0].text == "Welcome to Hacker News Radio"
