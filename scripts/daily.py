"""Daily automation: generate YESTERDAY's (Pacific) front-page episode with the Claude writer,
then rebuild the feed + site. Run by the scheduled job. Needs DEEPGRAM_API_KEY + ANTHROPIC_API_KEY
(and HN_RADIO_BASE_URL for absolute feed links).

This runs at 3am with nobody watching, so how it FAILS matters as much as what it does. Three things
happen on the way out, and each covers a hole the other two do not:

  1. Any exception is recorded with `status.error`, so the board stops showing a spinner forever and
     starts showing what went wrong. This used to be missing entirely: `status.error` existed and
     nothing in the repo called it, so a run that died on segment 4 of 19 left
     `{"state": "rendering"}` on disk permanently.
  2. The same failure is pushed to `HN_RADIO_ALERT_WEBHOOK`, because a board only tells you once you
     look at it, and at 3am nobody is looking.
  3. Before starting, we check whether the PREVIOUS run left an in-flight state behind. That is the
     only way a HANG is ever reported. A hang raises nothing -- a socket with no timeout, a wedged
     ffmpeg -- so no except clause anywhere can catch it and nothing writes `error`. What it does
     leave is silence, and the next run noticing that silence is what turns it into an alert.

Exit code is non-zero on failure so supercronic logs a failed job rather than a clean one.
"""

import pathlib
import sys
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import alerts, config, pipeline, publish, status
from hn_radio.writers import ClaudeWriter


def _report_a_previous_run_that_never_finished() -> None:
    """If the last run left work in flight, it died without saying so. Say so now.

    Deliberately checked at START rather than by a watchdog. A daily job has no process alive
    between runs, so there is nothing to hold a timer; the next invocation is the first moment
    anything can observe that the last one never reached `done` or `error`. Worst case that is a
    24-hour delay, which is a real limitation and still infinitely better than never.
    """
    previous = status.read()
    if not status.in_flight(previous):
        return
    quiet = status.silent_for(previous)
    quiet_note = f"{int(quiet // 60)} minutes" if quiet is not None else "an unknown length of time"
    episode = previous.get("episode") or "an unknown episode"
    alerts.notify(
        f"HN Radio: the previous run never finished. It was {previous.get('state')} on {episode} "
        f"({previous.get('note') or 'no note'}) and went quiet for {quiet_note}. No exception was "
        f"recorded, so it hung rather than crashed. Today's run is starting now."
    )


def main() -> int:
    _report_a_previous_run_that_never_finished()

    yesterday = (datetime.now(config.PACIFIC) - timedelta(days=1)).date()
    print(f"Generating the {yesterday.isoformat()} (Pacific) front-page episode...")
    try:
        pipeline.run_panel(edition="frontpage", episode_date=yesterday, writer=ClaudeWriter())
        publish.rebuild_site(config.EPISODES_DIR)
    except Exception as e:
        # Record first, alert second. The status write is local and cannot fail in a way that
        # matters; the webhook talks to the network, which is plausibly the very thing that broke.
        traceback.print_exc()
        status.error(f"{type(e).__name__}: {e}")
        alerts.notify(
            f"HN Radio: the {yesterday.isoformat()} episode FAILED. {type(e).__name__}: {e}. "
            f"The feed still serves yesterday's catalog; check /data/cron.log for the traceback."
        )
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
