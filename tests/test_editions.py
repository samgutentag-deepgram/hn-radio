import pytest

from hn_radio import editions
from hn_radio.models import Story


def _story(id, title, url=None, points=100):
    return Story(id=id, title=title, url=url, points=points, author="a",
                 num_comments=10, rank=id, kids=[])


def test_frontpage_is_pure_popularity_order():
    stories = [_story(1, "A", points=50), _story(2, "B", points=300), _story(3, "C", points=150)]
    out = editions.select_stories(stories, "frontpage", n=3)
    assert [s.id for s in out] == [2, 3, 1]


def test_makers_demotes_launch_and_promotes_repo():
    launch = _story(1, "BigCorp launches new AI platform", points=400)
    repo = _story(2, "My tiny git in Rust", url="https://github.com/me/minigit", points=120)
    out = editions.select_stories([launch, repo], "makers", n=2)
    # despite fewer points, the personal repo should outrank the launch under Makers
    assert out[0].id == 2
    assert out[0].rank == 1


def test_ai_edition_promotes_ai_stories():
    ai = _story(1, "A new transformer model for inference", points=80)
    other = _story(2, "Gardening tips for July", points=120)
    out = editions.select_stories([ai, other], "ai", n=2)
    assert out[0].id == 1


def test_security_edition_promotes_security_stories():
    sec = _story(1, "Critical CVE lets attackers bypass TLS", points=90)
    other = _story(2, "A nice recipe blog", points=200)
    out = editions.select_stories([sec, other], "security", n=2)
    assert out[0].id == 1


def test_unknown_edition_raises():
    with pytest.raises(ValueError):
        editions.select_stories([_story(1, "x")], "nonsense", n=1)
