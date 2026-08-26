from hn_radio import ingest
from hn_radio.models import Story

# A tiny fake HN dataset keyed by item id.
FAKE = {
    100: {"id": 100, "type": "story", "title": "Real story A", "score": 250,
          "by": "alice", "descendants": 40, "kids": [201, 202, 203], "url": "http://a"},
    101: {"id": 101, "type": "job", "title": "We are hiring", "score": 1, "by": "corp"},
    102: {"id": 102, "type": "story", "title": "Real story B", "score": 90,
          "by": "bob", "descendants": 5, "kids": [], "url": "http://b"},
    103: {"id": 103, "type": "story", "title": "Dead story", "dead": True},
    104: {"id": 104, "type": "story", "title": "Real story C", "score": 30,
          "by": "carol", "descendants": 2, "kids": [], "url": "http://c"},
    201: {"id": 201, "type": "comment", "by": "dang", "text": "<p>first</p>"},
    202: {"id": 202, "type": "comment", "by": "spam", "text": "x", "dead": True},
    203: {"id": 203, "type": "comment", "by": "eve", "text": "<p>third</p>"},
}


def _patch(monkeypatch, top_ids):
    monkeypatch.setattr(ingest, "_top_story_ids", lambda: top_ids)
    monkeypatch.setattr(ingest, "_item", lambda i: FAKE.get(i))


def test_fetch_front_page_skips_jobs_and_dead_and_ranks(monkeypatch):
    _patch(monkeypatch, [100, 101, 103, 102, 104])
    stories = ingest.fetch_front_page(2)
    assert [s.id for s in stories] == [100, 102]
    assert stories[0].rank == 1 and stories[1].rank == 2
    assert stories[0].points == 250 and stories[0].author == "alice"


def test_pick_top_thread_picks_most_comments_with_kids(monkeypatch):
    _patch(monkeypatch, [100, 102, 104])
    stories = ingest.fetch_front_page(3)
    top = ingest.pick_top_thread(stories)
    assert top is not None and top.id == 100  # 40 descendants and has kids


def test_pick_top_thread_none_when_no_comments(monkeypatch):
    only_empty = Story(id=9, title="x", url=None, points=1, author="a",
                       num_comments=0, rank=1, kids=[])
    assert ingest.pick_top_thread([only_empty]) is None


def test_fetch_top_comments_skips_dead_and_respects_limit(monkeypatch):
    _patch(monkeypatch, [100])
    story = ingest.fetch_front_page(1)[0]
    comments = ingest.fetch_top_comments(story, k=5)
    assert [c.id for c in comments] == [201, 203]  # 202 is dead
    assert comments[0].author == "dang"
