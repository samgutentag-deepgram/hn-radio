"""What stretch of Hacker News an episode covers, and when it airs.

One small value object, because the show stopped being "one Pacific calendar day, read the next
morning" when the cron went to two runs a day. Before that, a `date` carried everything: the
stories were the ones submitted that day, the air date was the day after, and the episode id was
the date. A twice-daily show needs a window that ends when the run starts and reaches back a fixed
number of hours, an air date that is the run's own date, and an id that tells the two runs apart.

Two constructors, one per shape, and every consumer reads the same fields:

  `calendar_day(day)`   the original shape. [midnight, next midnight) Pacific, airs the day after,
                        id is the bare date. Kept for backfills, `--date`, and the archive.
  `ending_at(at, hours)` the scheduled shape. The `hours` before `at`, airs on `at`'s date, id is
                        the date plus `-am` or `-pm` for which run it was.

`slot` is None for the calendar shape, and that is how the fixed intro and outro know which framing
to speak: a calendar episode is "yesterday's front page"; a rolling one is "overnight" or "today".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Union

from . import config

MORNING, AFTERNOON = "am", "pm"


@dataclass(frozen=True)
class EpisodeWindow:
    start: datetime   # Pacific-aware, inclusive
    end: datetime     # Pacific-aware, exclusive
    air_date: date    # the day the intro speaks
    slot: Optional[str] = None  # None | "am" | "pm"

    @classmethod
    def calendar_day(cls, day: date) -> "EpisodeWindow":
        """The whole Pacific day `day`, aired the next morning. The pre-2026-09 shape."""
        start = datetime.combine(day, time.min, tzinfo=config.PACIFIC)
        return cls(start=start, end=start + timedelta(days=1), air_date=day + timedelta(days=1),
                   slot=None)

    @classmethod
    def ending_at(cls, at: datetime, hours: float = config.LOOKBACK_HOURS) -> "EpisodeWindow":
        """The `hours` before `at`, aired on `at`'s Pacific date. `at` must be tz-aware."""
        if at.tzinfo is None:
            raise ValueError("EpisodeWindow.ending_at needs a timezone-aware datetime")
        end = at.astimezone(config.PACIFIC)
        slot = MORNING if end.hour < 12 else AFTERNOON
        return cls(start=end - timedelta(hours=hours), end=end, air_date=end.date(), slot=slot)

    @property
    def content_date(self) -> date:
        """The date the stories belong to, for prompts and titles.

        The calendar shape covers exactly one day and that day is it. The rolling shape straddles
        two, so the air date is the honest answer: it is the day the listener hears it.
        """
        return self.air_date if self.slot else self.start.date()

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    def episode_id(self, edition: str) -> str:
        """`YYYY-MM-DD`, then `-am`/`-pm` for a scheduled run, then `-<edition>` unless frontpage.

        The calendar shape keeps the bare date so every episode already on disk keeps its name,
        and the slot goes before the edition so the ids of one day sort in air order.
        """
        base = self.air_date.isoformat() if self.slot else self.start.date().isoformat()
        if self.slot:
            base = f"{base}-{self.slot}"
        return base if edition == "frontpage" else f"{base}-{edition}"


WindowLike = Union[EpisodeWindow, date]


def coerce(when: WindowLike) -> EpisodeWindow:
    """Accept either shape at a boundary. A bare `date` is the calendar-day episode for that date.

    Every caller that predates the rolling window hands over a `date`, and there are many of them
    (the CLI, the backfill, the experiment scripts, and a row of tests). Coercing at the edge lets
    the pipeline read one type internally without a flag day across all of them.
    """
    if isinstance(when, EpisodeWindow):
        return when
    if isinstance(when, datetime):  # a datetime IS a date, so check it first
        return EpisodeWindow.calendar_day(when.date())
    if isinstance(when, date):
        return EpisodeWindow.calendar_day(when)
    raise TypeError(f"expected a date or EpisodeWindow, got {type(when).__name__}")
