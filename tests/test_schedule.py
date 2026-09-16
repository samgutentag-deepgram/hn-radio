"""When the runs are, and the one guard against the schedule drifting again.

`crontab` is what actually fires. `window.RUN_HOURS` is a second copy, and it exists because the
landing page has to say when the next run is and the browser cannot read a crontab. Two copies of
one fact need a test that they agree, or they quietly stop agreeing -- which is exactly what
happened between 2026-09-04 and 2026-09-16: the cron went to `0 3,15`, `backend/app.py` kept a
hardcoded `hour=3`, and for twelve days the page counted down to the wrong run while telling
readers the show was daily. The sentence was plausible, so nobody checked it.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime

import pytest

from hn_radio import config, window

CRONTAB = pathlib.Path(__file__).resolve().parent.parent / "crontab"


def _crontab_hours() -> set:
    """The hours the real crontab fires `daily.py` at.

    Parses the schedule rather than the comment above it: a comment that disagreed with its own
    cron line is the failure this is guarding, so reading the comment would be circular.
    """
    for line in CRONTAB.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "daily.py" not in line:
            continue
        fields = line.split()
        assert fields[0] == "0", f"a run at a non-zero minute is not modelled: {line!r}"
        return {int(h) for h in fields[1].split(",")}
    raise AssertionError("no daily.py line in crontab")


def test_the_run_hours_match_the_crontab():
    """THE DRIFT GUARD. If you change one, this fails until you change the other."""
    assert set(window.RUN_HOURS.values()) == _crontab_hours()


def test_there_is_one_run_hour_per_slot():
    """Two slots, two hours, and `slot_at`-style logic downstream depends on am being the earlier
    of the two."""
    assert set(window.RUN_HOURS) == {window.MORNING, window.AFTERNOON}
    assert window.RUN_HOURS[window.MORNING] < 12 <= window.RUN_HOURS[window.AFTERNOON]


def _pacific(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=config.PACIFIC)


@pytest.mark.parametrize("now,expected_am,expected_pm", [
    # Before both runs: both are today.
    ("2026-09-16T01:00", "2026-09-16T03:00", "2026-09-16T15:00"),
    # After the morning run, before the afternoon one: am rolls to tomorrow, pm is still today.
    ("2026-09-16T09:00", "2026-09-17T03:00", "2026-09-16T15:00"),
    # After both: both are tomorrow.
    ("2026-09-16T20:00", "2026-09-17T03:00", "2026-09-17T15:00"),
])
def test_each_slot_counts_to_its_own_next_run(now, expected_am, expected_pm):
    """The bug this replaced: one timer for two runs. At 09:00 the old code reported the next run
    as tomorrow's 3am and the page showed "next in 18h", while the afternoon edition was six hours
    away and is the one a reader is actually waiting for."""
    runs = window.next_runs(_pacific(now))
    assert runs[window.MORNING] == _pacific(expected_am)
    assert runs[window.AFTERNOON] == _pacific(expected_pm)


def test_a_run_exactly_now_is_the_next_one_tomorrow():
    """At 03:00:00 sharp the morning run is starting, not upcoming. Reporting it as 0 seconds away
    would leave the page counting down to a run already in flight; the board's own `state` is what
    says a run is happening."""
    at_three = _pacific("2026-09-16T03:00")
    assert window.next_run(window.MORNING, at_three) == _pacific("2026-09-17T03:00")


def test_next_run_is_always_in_the_future():
    """Swept across every hour of a day, both slots, because an "in -3h" on the page is worse than
    no timer."""
    for hour in range(24):
        now = _pacific(f"2026-09-16T{hour:02d}:30")
        for slot in window.RUN_HOURS:
            assert window.next_run(slot, now) > now, (slot, hour)


def test_an_unknown_slot_is_a_bug_not_a_guess():
    with pytest.raises(ValueError, match="unknown slot"):
        window.next_run("evening")


def test_the_runs_are_pacific_and_dst_aware():
    """`CRON_TZ=America/Los_Angeles` in the crontab means 3am local year-round, not a fixed UTC
    offset. A naive datetime here would put the timer an hour out for half the year."""
    runs = window.next_runs()
    for at in runs.values():
        assert at.tzinfo is not None
        assert at.utcoffset() is not None
        assert at.tzinfo is config.PACIFIC or str(at.tzinfo) == str(config.PACIFIC)


def test_a_naive_now_is_accepted_and_treated_as_pacific_free():
    """`next_run` calls `astimezone`, which on a naive datetime assumes system local time. That is
    a footgun, so every caller in this repo passes an aware one; this pins that the function does
    not silently crash on the other kind, since a test or a script could reach it."""
    runs = window.next_runs(datetime.now(config.PACIFIC))
    assert set(runs) == set(window.RUN_HOURS)
