"""Guards on the two scripts that could destroy or publish something by accident.

Both bugs here were found by review on 2026-08-21, both were latent for weeks, and both are the
same shape: a rule that was correct when it was written and became wrong when the world around it
grew. Neither had a test, which is why neither was noticed.

These are cheap and they are not about the scripts' output. They pin the BLAST RADIUS.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import backfill  # noqa: E402

from hn_radio import config, feed, manifest  # noqa: E402


# --- scripts/backfill.py --clean ---------------------------------------------------------------
#
# The old rule was an allow-list: delete any directory whose name is not YYYY-MM-DD, except
# `samples`. Safe on 2026-08-04 when it was written and catastrophic by 08-19, because `episodes/`
# had grown `_ads`, `_coldopen`, `_music`, `_pace` and `_voices`. Measured against the real
# directory at the time of the fix: 366 MB, of which `_ads` and `_music` have no generator anywhere
# in the repo and are unrecoverable at any price.
#
# It is a delete-list now, so the test that matters is not "does it delete variants" but "is
# everything else safe by DEFAULT". A future directory nobody has thought of must survive.


def test_clean_only_ever_matches_a_variant_render(tmp_path):
    """A daily id with a suffix, and nothing else in any shape."""
    deletable = ["2026-08-04-recast", "2026-08-04-new", "2026-08-04_alt", "2026-08-04-a-b-c"]
    protected = [
        "2026-08-04",        # the episode itself
        "samples",           # the cast page's per-voice samples
        "_ads", "_music",    # paid renders with NO generator in the repo
        "_coldopen", "_pace", "_voices",
        "notadate",          # anything a human adds by hand
        "2026-08",           # a truncated date is not a daily id and not a variant
        "x-2026-08-04-recast",   # a date in the middle does not count; the regex is anchored
    ]
    for name in deletable + protected:
        (tmp_path / name).mkdir()

    got = sorted(c.name for c in backfill._variants(tmp_path))
    assert got == sorted(deletable)


def test_clean_leaves_an_unknown_directory_alone(tmp_path):
    """The whole point of inverting the rule: safe by default.

    This is the test the old allow-list would have failed. `_future_cache` does not exist today;
    the assertion is that inventing one tomorrow does not make it deletable.
    """
    (tmp_path / "2026-08-04").mkdir()
    (tmp_path / "_future_cache").mkdir()
    (tmp_path / "2026-08-04-recast").mkdir()

    assert backfill._clean(tmp_path, dry_run=False, assume_yes=True) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-08-04", "_future_cache"]


def test_clean_dry_run_removes_nothing_and_stops_the_caller(tmp_path):
    """--dry-run must not generate either, or it is not a dry run of the command."""
    for name in ("2026-08-04", "2026-08-04-recast"):
        (tmp_path / name).mkdir()

    assert backfill._clean(tmp_path, dry_run=True, assume_yes=True) is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-08-04", "2026-08-04-recast"]


def test_clean_refuses_on_anything_but_a_deliberate_yes(tmp_path, monkeypatch):
    """The prompt exists so a typo in a long command line cannot reach the destructive path.

    The contract is the WORD yes, case and surrounding whitespace forgiven. Deliberately not
    byte-exact: rejecting "YES" would only train people to reach for --yes, which is the flag that
    skips the prompt entirely. "y" is refused on purpose, because it is what a hand types on
    autopilot.
    """
    (tmp_path / "2026-08-04-recast").mkdir()
    for answer in ("", "y", "Y", "n", "no", "yes please", "ye", "1"):
        monkeypatch.setattr("builtins.input", lambda _="", a=answer: a)
        assert backfill._clean(tmp_path, dry_run=False, assume_yes=False) is False
        assert (tmp_path / "2026-08-04-recast").exists(), f"removed on answer {answer!r}"

    for answer in ("yes", "YES", " yes "):
        (tmp_path / "2026-08-04-recast").mkdir(exist_ok=True)
        monkeypatch.setattr("builtins.input", lambda _="", a=answer: a)
        assert backfill._clean(tmp_path, dry_run=False, assume_yes=False) is True
        assert not (tmp_path / "2026-08-04-recast").exists(), f"kept on answer {answer!r}"


def test_the_real_episodes_dir_has_nothing_deletable_by_accident():
    """Belt and braces against the actual archive on this disk.

    On a clean checkout this passes vacuously rather than skipping: `episodes/` is committed
    (`.gitkeep` and `cover.png`), so the directory exists and simply holds no variants. The skip
    below is only for a checkout where `episodes/` has been deleted outright.
    """
    if not config.EPISODES_DIR.is_dir():
        pytest.skip("no episodes/ directory")
    doomed = {c.name for c in backfill._variants(config.EPISODES_DIR)}
    unrecoverable = {"_ads", "_music", "_coldopen", "_pace", "_voices", "samples"}
    assert not doomed & unrecoverable, f"--clean would delete {sorted(doomed & unrecoverable)}"


# --- the publish gate -------------------------------------------------------------------------
#
# scripts/first_listen.py rendered to `f"{date.today()}-panel"` at the TOP LEVEL of episodes/, and
# the only filter the catalogue applies is `is_recast`, so running it put a dated directory in the
# root and the August 3 title into index.json and feed.xml as a real episode for every subscriber.
#
# The script is namespaced under `_firstlisten/` now. That works because both builders glob at
# depth 1, which is the property worth pinning: it is what makes a nested id unpublishable, and it
# is invisible in the script itself.


def test_both_catalogue_builders_glob_only_at_depth_one():
    """A nested episode id cannot reach the feed. Read off the source, so a widened glob fails."""
    import inspect

    for fn in (manifest.build_manifest, feed.rebuild_feed):
        src = inspect.getsource(fn)
        globs = re.findall(r'glob\(\s*["\']([^"\']+)["\']', src)
        assert globs, f"{fn.__name__} no longer globs; re-derive this guard"
        for pattern in globs:
            assert pattern == "*/episode.json", (
                f"{fn.__name__} globs {pattern!r}. If that is intentional, a nested id like "
                f"_firstlisten/<date>-panel is now publishable and scripts/first_listen.py "
                f"needs a different guard."
            )


def test_a_nested_episode_is_not_catalogued(tmp_path):
    """The behaviour, not just the source: prove a namespaced render stays out of both artifacts."""
    real = tmp_path / "2026-08-04"
    real.mkdir()
    (real / "episode.json").write_text(
        '{"id": "2026-08-04", "title": "real", "segments": [], "duration_seconds": 1,'
        ' "audio_path": "episode.mp3", "source_items": []}'
    )
    nested = tmp_path / "_firstlisten" / "2026-08-21-panel"
    nested.mkdir(parents=True)
    (nested / "episode.json").write_text(
        '{"id": "_firstlisten/2026-08-21-panel", "title": "MUST NOT SHIP", "segments": [],'
        ' "duration_seconds": 1, "audio_path": "episode.mp3", "source_items": []}'
    )

    manifest.build_manifest(tmp_path)
    listed = (tmp_path / "index.json").read_text()
    assert "MUST NOT SHIP" not in listed
    assert "real" in listed
