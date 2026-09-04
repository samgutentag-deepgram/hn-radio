"""The subscribe control's tap targets, guarded statically.

WHAT THIS PROVES AND WHAT IT DOES NOT. It reads `web/brand.css` and checks the declared minimums.
It cannot prove rendered geometry -- that needs a browser, and this suite deliberately runs with no
network, no key and no browser in about ten seconds. The rendered sizes were measured once, over
CDP at a 390x844 mobile viewport: button 111x44, each app row 318x44, close 44x44,
copy 57x44, feed field 255x44, and no horizontal overflow (`documentElement.scrollWidth == 390`).

So this is the regression guard for the specific failure that already happened, which was not a
layout accident but a deliberate edit: a media query that made the targets SMALLER on the narrow
viewport. The old subscribe row shipped six 1.3rem icons with `gap: 0` at <= 34rem, about 21 CSS px
and adjacent, under even the relaxed 24px target-size floor. A phone got the smallest targets on
the site, which is backwards.

44px is WCAG 2.5.5 (AAA), not the 24px 2.5.8 minimum, because these are the primary conversion
control on the page and one of them is a close button.
"""

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "web" / "brand.css"
HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"
MIN_REM = 2.75   # 44px at the browser default root size, which this project does not override


def _rule(selector: str, css: str) -> str:
    """The declaration block for `selector`, or "" when absent."""
    m = re.search(rf"(?:^|\}}|,)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.M)
    return m.group(1) if m else ""


def _min_height_rem(block: str):
    """The block's vertical floor in rem, from `min-height` or a fixed `height`.

    Both count. `.sd-close` is a fixed 2.75rem square with `flex: none`, so `height` IS its floor;
    the text rows use `min-height` because their content decides the rest. Accepting either keeps
    this guard about the floor rather than about which property spells it.
    """
    m = re.search(r"min-height:\s*([\d.]+)rem", block)
    if m:
        return float(m.group(1))
    m = re.search(r"(?<!min-)height:\s*([\d.]+)rem", block)
    return float(m.group(1)) if m else None


@pytest.fixture(scope="module")
def css():
    return CSS.read_text()


def test_the_root_font_size_is_not_overridden(css):
    """Every rem assertion below depends on 1rem being 16px."""
    assert not re.search(r"^\s*html\s*\{[^}]*font-size", css, re.M | re.S), (
        "brand.css sets a root font-size, so the rem-to-px assumption in this file is wrong"
    )


@pytest.mark.parametrize("selector", [".sd-app", ".sd-close", "#sd-copy", "#sd-url"])
def test_every_modal_target_declares_44px(css, selector):
    got = _min_height_rem(_rule(selector, css))
    assert got is not None, f"{selector} declares no min-height at all"
    assert got >= MIN_REM, f"{selector} is {got}rem, under the {MIN_REM}rem floor"


def test_the_close_button_is_square_and_44px(css):
    block = _rule(".sd-close", css)
    assert re.search(r"width:\s*2\.75rem", block), block
    assert re.search(r"height:\s*2\.75rem", block), block


def test_the_subscribe_button_grows_on_narrow_viewports(css):
    """THE ACTUAL REGRESSION. The old media query shrank the targets on a phone.

    Read the `max-width: 34rem` block and require that whatever it says about the button is not
    smaller than the desktop declaration.
    """
    desktop = _min_height_rem(_rule("#subscribe-open", css))
    assert desktop is not None and desktop >= 2.25

    m = re.search(r"@media\s*\(max-width:\s*34rem\)\s*\{(.*?)\n\}", css, re.S)
    assert m, "the narrow-viewport block is gone; check this guard still points at the right one"
    narrow = _min_height_rem(_rule("#subscribe-open", m.group(1)))
    assert narrow is not None, "the narrow block no longer sizes the subscribe button"
    assert narrow >= MIN_REM, f"narrow viewport gives the button {narrow}rem, under {MIN_REM}rem"
    assert narrow >= desktop, (
        f"the button SHRINKS on a phone ({desktop}rem -> {narrow}rem), which is the exact bug "
        "the six-icon row had"
    )


def test_the_old_icon_row_is_really_gone(css):
    """Its selectors surviving would mean two subscribe UIs, one of them the broken one."""
    for dead in (".subscribe-icon", ".subscribe-label"):
        assert dead not in css, f"{dead} is still styled"


def test_each_app_row_names_the_app_in_text():
    """Not colour, not logo shape. Both are unreliable cues and one is unusable for Sam.

    The old row hid its label on mobile, leaving brand hue and glyph silhouette as the only thing
    telling six adjacent targets apart.
    """
    html = HTML.read_text()
    assert "sd-name" in html, "the row template no longer renders a text name"
    assert "a.label" in html, "the name is not sourced from the app's label"


def test_the_dialog_is_a_native_dialog():
    """`showModal` brings the focus trap, Escape, backdrop and page inertness with it.

    Pinned because swapping in a hand-rolled overlay silently loses all four, and losing a focus
    trap is not visible in a screenshot.
    """
    html = HTML.read_text()
    assert "<dialog" in html
    assert "showModal()" in html
    assert 'aria-labelledby="subscribe-dialog-title"' in html
