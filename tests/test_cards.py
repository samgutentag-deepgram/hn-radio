"""The share cards: the four facts, the two sizes, and the seal on where values live.

WHAT IS WORTH TESTING HERE IS NOT WHETHER PILLOW DRAWS. It is the three ways a generated brand
asset goes wrong quietly, because every one of them produces a file that opens and looks fine:

  - THE NUMBER STOPS BEING THE EPISODE'S. The cost is an input, read out of the `cost` block
    `pricing.py` computed for that episode. A constant, a stale copy or a plausible default would
    all render a beautiful card that lies about the product, in an email, at scale.
  - THE NUMBER STOPS FITTING. The rate is not confirmed. Real episodes run $0.10 to $0.28 and the
    ask named $0.06 and $0.20, which is 3,333 episodes per credit against 1,000 -- a longer string
    on the widest line on the card. Overrun is silent unless something checks.
  - A VALUE MOVES OUT OF `tokens.py`. Deepgram is mid-rebrand and the brand team owns the
    guidelines, so the promise is that a restyle is one file. A hex code that drifts into
    `layout.py` breaks that promise months before anyone finds out. `brand.css` protects its own
    seal with a grep in `make check`; this is the same gate for the raster.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from hn_radio import cards, pricing
from hn_radio.cards import facts as card_facts
from hn_radio.cards import layout as card_layout
from hn_radio.cards import tokens

APP_URL = "https://dg-devrel-hn-radio.fly.dev"


def _episode(usd: float = 0.24417, characters: int = 5426, **over) -> dict:
    """A priced two-hander, shaped exactly as `publish` writes one."""
    per_credit = pricing.episodes_per_credit(usd)
    episode = {
        "id": "2026-09-16-am",
        "title": "Morning Edition: Jev trades text for typed decisions, Wayback blocks bots",
        "duration_seconds": 374.91,
        "summary": "Three stories.",
        "source_items": [{"hn_id": 1}, {"hn_id": 2}, {"hn_id": 3}],
        "segments": [
            {"role": "anchor", "desk": "anchor", "speaker_key": "Alexis",
             "voice_id": "flux-alexis-en", "text": "Hi."},
            {"role": "desk", "desk": "cohost", "speaker_key": "Kelsey",
             "voice_id": "flux-kelsey-en", "text": "Hello."},
        ],
        "cost": {
            "characters": characters, "billed_characters": characters,
            "usd": usd, "billed_usd": usd, "rate_usd_per_1k": 0.045, "plan": "payg",
            "credit_usd": 200.0, "episodes_per_credit": per_credit,
            "pricing_url": pricing.PRICING_URL, "pricing_as_of": pricing.PRICING_AS_OF,
        },
    }
    episode.update(over)
    return episode


def _write(tmp_path: Path, episode: dict) -> Path:
    d = tmp_path / episode["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "episode.json").write_text(json.dumps(episode))
    return d


# --- the cost is an input ------------------------------------------------------------------

@pytest.mark.parametrize("usd, hero, per_credit", [
    (0.06, "$0.06", 3333),     # the low end of the unconfirmed rate
    (0.1004, "$0.10", 1992),   # the cheapest episode in the archive
    # 999 and not 1000, and it is not a rounding slip in the test. `episodes_per_credit` floors,
    # and 200.0 // 0.2 is 999.0 in binary floating point because 0.2 is a hair over a fifth. The
    # floor is deliberate -- the credit has to cover every episode counted -- so the conservative
    # answer is the right one to pin.
    (0.20, "$0.20", 999),      # the high end the ask named
    (0.2724, "$0.27", 734),    # the dearest episode in the archive
])
def test_the_card_quotes_the_episodes_own_cost(usd, hero, per_credit):
    """Both figures move with the episode, and they agree with `pricing`.

    The pair is the point. The dollar figure and the episodes-per-credit figure are the same claim
    stated twice, and a card showing $0.06 beside 819 episodes would be a picture of a bug.
    """
    f = card_facts.facts_for(_episode(usd=usd), APP_URL)
    assert f.hero == hero
    assert f.episodes_per_credit == per_credit == pricing.episodes_per_credit(usd)
    assert f"{per_credit:,} episodes" in f.credit_line


def test_an_unpriced_episode_gets_no_card_rather_than_a_plausible_one():
    """The one failure mode with no acceptable fallback: a number nobody can check, invented."""
    for broken in ({}, {"characters": 0}, {"characters": 5426, "usd": None}):
        with pytest.raises(card_facts.NoCost):
            card_facts.facts_for(_episode() | {"cost": broken}, APP_URL)


def test_alt_text_carries_the_cost_and_the_pitch_not_a_description_of_the_picture():
    """Many clients block images, and those readers are the ones a lifecycle email needs most."""
    f = card_facts.facts_for(_episode(usd=0.24417), APP_URL)
    assert "$0.24" in f.alt
    assert "Deepgram Flux text to speech" in f.alt
    assert "819 episodes" in f.alt
    assert f.url_text in f.alt
    assert "orb" not in f.alt.lower()


def test_the_printed_url_is_the_short_typeable_form():
    """It is printed on an image, so somebody retypes it. `episode.html?id=` is four more chances
    to get punctuation wrong, and `backend/app.py` serves `/e/<id>` for exactly this."""
    f = card_facts.facts_for(_episode(), APP_URL)
    assert f.url_text == "dg-devrel-hn-radio.fly.dev/e/2026-09-16-am"
    assert f.url_full == f"{APP_URL}/e/2026-09-16-am"
    assert "?" not in f.url_text


def test_an_episode_from_before_the_two_hander_still_gets_a_card():
    """The 2026-08 archive has three topic desks on one voice and no edition prefix. It degrades
    to one anchor plus one desk and drops the edition line, rather than inventing either."""
    old = _episode(
        id="2026-08-13", title="HN Radio Front Page - Aug 13: Gemini 3.7 Flash",
        segments=[
            {"role": "anchor", "desk": "anchor", "speaker_key": "Haley",
             "voice_id": "flux-alexis-en"},
            {"role": "desk", "desk": "ai", "speaker_key": "Priya",
             "voice_id": "flux-donovan-en"},
        ])
    f = card_facts.facts_for(old, APP_URL)
    assert f.dateline == "AUGUST 13, 2026"
    assert [s.name for s in f.speakers] == ["Haley", "Priya"]


# --- the two sizes -------------------------------------------------------------------------

@pytest.mark.parametrize("usd", [0.06, 0.24417, 0.2724])
@pytest.mark.parametrize("layout", tokens.LAYOUTS, ids=lambda L: L.name)
def test_every_card_renders_at_its_declared_size_across_the_cost_range(layout, usd):
    img = card_layout.compose(layout, card_facts.facts_for(_episode(usd=usd), APP_URL))
    assert img.size == (layout.width, layout.height)


def test_a_credit_line_that_cannot_fit_raises_rather_than_clipping():
    """`paint.fitted` steps the type down and then gives up. Giving up has to be loud: a clipped
    line reads as a design choice and a raised error reads as copy that is too long."""
    f = card_facts.facts_for(_episode(), APP_URL)
    too_long = type(f)(**{**f.__dict__, "credit_line": "word " * 200})
    with pytest.raises(card_layout.CopyTooWide):
        card_layout.compose(tokens.SOCIAL, too_long)


def test_the_social_card_is_the_open_graph_shape():
    """1200x630 is what Slack, LinkedIn, X and iMessage all crop toward, and it is what
    `backend.app.episode_page` publishes as og:image."""
    assert (tokens.SOCIAL.width, tokens.SOCIAL.height) == (1200, 630)


def test_the_email_card_is_rendered_at_twice_the_width_it_is_displayed_at():
    """A 600px raster is soft on every phone made since about 2014 and nobody can fix that at
    send time, so the file is 2x and `card.json` tells the template what to set."""
    assert tokens.EMAIL.width == 2 * tokens.EMAIL.css_width


# --- what lands on disk ----------------------------------------------------------------------

def test_building_writes_both_cards_and_a_sidecar_and_is_then_idempotent(tmp_path):
    """`publish.rebuild_site` calls this on every boot and every publish. The second call must
    cost nothing, or a deploy re-renders sixty-three episodes to produce identical bytes."""
    d = _write(tmp_path, _episode())
    doc = cards.build_cards(d, APP_URL)
    assert doc is not None
    for layout in tokens.LAYOUTS:
        assert (d / cards.card_filename(layout)).is_file()
    sidecar = json.loads((d / cards.CARD_JSON).read_text())
    assert sidecar["alt"] == doc["alt"]
    assert sidecar["cards"]["email"]["css_width"] == tokens.EMAIL.css_width

    assert cards.build_cards(d, APP_URL) is None          # nothing changed, nothing rebuilt
    assert cards.build_cards(d, APP_URL, force=True) is not None


def test_a_sidecar_with_no_image_beside_it_heals(tmp_path):
    """The volume filled once and 35 episodes lost their audio. A half-written directory must
    repair itself on the next boot rather than stay broken because a JSON file says it is fine."""
    d = _write(tmp_path, _episode())
    cards.build_cards(d, APP_URL)
    (d / cards.card_filename(tokens.SOCIAL)).unlink()
    assert cards.build_cards(d, APP_URL) is not None


def test_a_reprice_rebuilds_the_card_and_an_unrelated_edit_does_not(tmp_path):
    """The staleness key is what the card PAINTS, so a chapter backfill does not re-render the
    archive and a rate change does."""
    d = _write(tmp_path, _episode())
    cards.build_cards(d, APP_URL)

    episode = json.loads((d / "episode.json").read_text())
    episode["duration_seconds"] = 999.0                    # not on the card
    episode["segments"][0]["text"] = "different words"     # not on the card either
    (d / "episode.json").write_text(json.dumps(episode))
    assert cards.build_cards(d, APP_URL) is None

    episode["cost"]["usd"] = 0.06
    episode["cost"]["episodes_per_credit"] = pricing.episodes_per_credit(0.06)
    (d / "episode.json").write_text(json.dumps(episode))
    assert cards.build_cards(d, APP_URL) is not None


def test_a_restyle_rebuilds_the_whole_archive(tmp_path, monkeypatch):
    """`CARD_VERSION` is the designer's lever: bump it and every card on the volume is stale."""
    d = _write(tmp_path, _episode())
    cards.build_cards(d, APP_URL)
    monkeypatch.setattr(tokens, "CARD_VERSION", tokens.CARD_VERSION + 1)
    assert cards.build_cards(d, APP_URL) is not None


def test_backfill_never_raises_at_the_two_things_a_boot_will_hand_it(tmp_path):
    """It runs inside the app's startup hook. An unreadable episode and an unpriced one are both
    normal states on a volume, and neither may take the website down over a picture."""
    _write(tmp_path, _episode())
    _write(tmp_path, _episode(id="unpriced") | {"cost": {}})
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "episode.json").write_text("{ not json")

    result = cards.backfill(tmp_path, app_url=APP_URL)
    assert result["written"] == ["2026-09-16-am"]
    assert "unpriced" in result["skipped"]
    assert [name for name, _ in result["failed"]] == ["broken"]


# --- where a card says to go ------------------------------------------------------------------

def test_a_card_prints_the_public_origin_and_never_the_one_it_was_built_on():
    """A card is a picture that gets mailed to strangers. `site_app_url()` answers "where am I",
    which on a laptop is localhost -- correct for a feed enclosure and never correct here."""
    from hn_radio import config
    assert config.site_app_url().startswith("http://localhost")
    assert config.public_app_url() == config.DEFAULT_PUBLIC_APP_URL
    assert "localhost" not in config.public_app_url()


def test_a_real_deploy_names_itself_rather_than_the_constant(monkeypatch):
    """So renaming the Fly app is a fly.toml change and not a code change."""
    from hn_radio import config
    monkeypatch.setenv("HN_RADIO_BASE_URL", "https://hn.example/episodes")
    assert config.public_app_url() == "https://hn.example"
    monkeypatch.setenv("HN_RADIO_PUBLIC_URL", "https://vanity.example")
    assert config.public_app_url() == "https://vanity.example"


def test_the_public_origin_agrees_with_the_one_fly_actually_deploys():
    """The hostname is in two files and this is what keeps them honest.

    It cannot be read from fly.toml at runtime: `.dockerignore` excludes fly.toml, so an image
    that parsed it would work on a laptop and fail in the container. Duplicated deliberately,
    checked here, and this test is the thing that fails when the app is renamed.
    """
    from hn_radio import config
    fly = (Path(__file__).resolve().parent.parent / "fly.toml").read_text()
    declared = next(line.split("=", 1)[1].strip().strip('"')
                    for line in fly.splitlines()
                    if line.strip().startswith("HN_RADIO_BASE_URL"))
    assert declared.rstrip("/").removesuffix("/episodes") == config.DEFAULT_PUBLIC_APP_URL


# --- the seal --------------------------------------------------------------------------------

SEALED = ("layout.py", "paint.py")
CARDS = Path(__file__).resolve().parent.parent / "hn_radio" / "cards"
# Structural, not design: an index, a divisor for a radius, a pair, an opaque mask. Anything else
# placed on a card is a decision and belongs in tokens.py.
ALLOWED_NUMBERS = {0, 1, 2, 3, 0.0, 255}


def _code_constants(name: str):
    """Every literal in the CODE of a card module, docstrings and comments excluded.

    Parsed rather than grepped. These modules are mostly prose -- the reasoning is the point of
    them -- and a paragraph explaining why #fbfbff came out as #7a7a7c is documentation of a bug
    that was fixed, not a colour anyone can restyle. A grep cannot tell those apart.
    """
    tree = ast.parse((CARDS / name).read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and id(n) not in docstrings]


@pytest.mark.parametrize("name", SEALED)
def test_no_card_dimension_lives_outside_the_tokens_module(name):
    """The promise to a designer mid-rebrand: every colour, font size and spacing value is in one
    file, so a restyle is one file."""
    numbers = {n.value for n in _code_constants(name)
               if isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
    assert numbers <= ALLOWED_NUMBERS, (
        f"{name} carries the literal(s) {sorted(numbers - ALLOWED_NUMBERS)}. "
        "Put them in hn_radio/cards/tokens.py.")


@pytest.mark.parametrize("name", SEALED + ("facts.py", "__init__.py"))
def test_no_colour_literal_lives_outside_the_tokens_module(name):
    strings = [n.value for n in _code_constants(name) if isinstance(n.value, str)]
    for s in strings:
        assert not re.search(r"#[0-9a-fA-F]{6}\b", s), f"{name} carries a colour: {s!r}"
        assert "rgb(" not in s, f"{name} carries a colour: {s!r}"


def test_the_colours_come_from_brand_css_and_not_from_a_second_copy():
    """One source for the palette, so a card and the page it links to cannot disagree. This is the
    same block `scripts/make_cover.py` reads, through the same function, since 2026-09-16."""
    p = tokens.palette()
    css = tokens.BRAND_CSS.read_text()
    assert "--dg-accent: #13ef95;" in css
    assert p.accent == (0x13, 0xef, 0x95)
    assert len(tokens.voice_palettes()) >= 36


def test_a_voice_missing_from_brand_css_is_skipped_rather_than_drawn_grey(tmp_path):
    """Colour is reinforcement and never the only signal (brand.css block 1). An orb in a default
    palette under a real name would be a quiet lie about what the show sounds like."""
    d = _write(tmp_path, _episode(segments=[
        {"role": "anchor", "desk": "anchor", "speaker_key": "Nobody", "voice_id": "flux-ghost-en"},
    ]))
    assert cards.build_cards(d, APP_URL) is not None       # renders; it just has no orb on it
