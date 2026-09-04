"""Scheduled automation: generate the front-page episode for the last `config.LOOKBACK_HOURS` with
the Claude writer, verify it, then rebuild the feed + site. Run by the cron twice a day, 3am and 3pm
Pacific. Needs DEEPGRAM_API_KEY + ANTHROPIC_API_KEY (and HN_RADIO_BASE_URL for absolute feed links).

This runs with nobody watching, so how it FAILS matters as much as what it does. Four things happen
on the way out, and each covers a hole the others do not:

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
  4. A take that RENDERS but is not a show is caught and re-run. The 2026-09-03 episode shipped at
     173 seconds: the Claude writer failed, `PanelWriter` covered as designed, and the fallback read
     a markdown image tag and an S3 URL aloud. Every stage succeeded and nothing asked whether the
     result was an episode. `hn_radio/verify.py` now asks, twice (unspeakable text before the
     render, length after the stitch), and raises before publish. This script answers by trying
     again with a fresh writer, `ATTEMPTS` times in all. If every attempt fails, nothing is
     published: the feed keeps serving the previous episode and the alert says why. A short show
     that reads URLs at listeners is worse than a missed slot.

Exit code is non-zero on failure so supercronic logs a failed job rather than a clean one.
"""

import argparse
import pathlib
import sys
import traceback
from datetime import date, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import alerts, config, pipeline, publish, status
from hn_radio.verify import VerificationError
from hn_radio.window import EpisodeWindow
from hn_radio.writers import ClaudeWriter

# Takes per slot, including the first. Two, not more: each attempt is a full Claude write plus a
# full Flux render, and a writer that failed verification twice in a row is far more likely to be
# broken (a bad prompt, an API incident, a source page full of markup) than unlucky. Past two the
# right move is a human reading the log, which is what the alert is for.
ATTEMPTS = 2


def _report_a_previous_run_that_never_finished() -> None:
    """If the last run left work in flight, it died without saying so. Say so now.

    Deliberately checked at START rather than by a watchdog. A scheduled job has no process alive
    between runs, so there is nothing to hold a timer; the next invocation is the first moment
    anything can observe that the last one never reached `done` or `error`. Worst case that is a
    12-hour delay (it was 24 when the show ran once a day), which is a real limitation and still
    infinitely better than never.
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
        f"recorded, so it hung rather than crashed. This run is starting now."
    )


def generate(window: EpisodeWindow, *, attempts: int = ATTEMPTS, log=print):
    """Produce and publish the episode for `window`, re-running a take that fails verification.

    Returns the Episode. Raises `VerificationError` (the last one) if every attempt fails, and lets
    any other exception through untouched: a crash is not a bad take, and re-running a crash just
    spends money on the same crash.

    A fresh `ClaudeWriter` per attempt on purpose. The writer holds the title and summary from its
    last call, and a retry that reused the instance could carry the failed take's title onto the
    good one. It also makes each attempt an independent sample, which is the entire theory of
    retrying a stochastic writer.
    """
    episode_id = window.episode_id("frontpage")
    last: VerificationError | None = None
    for attempt in range(1, attempts + 1):
        log(f"Generating {episode_id} (attempt {attempt}/{attempts}, "
            f"{window.hours:.0f}h ending {window.end.isoformat(timespec='minutes')})...")
        try:
            return pipeline.run_panel(edition="frontpage", window=window, writer=ClaudeWriter(),
                                      min_seconds=config.MIN_EPISODE_SECONDS, log=log)
        except VerificationError as e:
            last = e
            log(f"[verify] attempt {attempt} rejected: {e}")
            if attempt < attempts:
                log("[verify] re-running with a fresh writer")
    assert last is not None  # attempts >= 1, so the loop either returned or set this
    raise last


def _window_from_argv(argv) -> EpisodeWindow:
    """No arguments: the scheduled shape, the last `LOOKBACK_HOURS` ending now.

    `--date YYYY-MM-DD`: re-make that day's calendar episode in place, with the same writer, the
    same verification and the same re-run the cron gets. This is how a bad episode already on the
    feed is replaced: the id is the bare date, so the render lands on top of the old one and the
    rebuilt feed carries the new audio under the same guid. `scripts/backfill.py` is NOT this: it
    uses `PanelWriter` and has no gate, which is the show that needed replacing in the first place.
    """
    parser = argparse.ArgumentParser(prog="daily.py", description=__doc__.splitlines()[0])
    parser.add_argument("--date", help="re-make the calendar-day episode for YYYY-MM-DD instead "
                                       "of the scheduled rolling window")
    args = parser.parse_args(argv)
    if args.date:
        return EpisodeWindow.calendar_day(date.fromisoformat(args.date))
    return EpisodeWindow.ending_at(datetime.now(config.PACIFIC))


def main(argv=()) -> int:
    """`argv` is the command line WITHOUT the program name. Defaults to none, not `sys.argv`, so a
    test or a caller that imports this gets the scheduled window and never argparse's view of the
    host process's arguments."""
    _report_a_previous_run_that_never_finished()

    window = _window_from_argv(argv)
    episode_id = window.episode_id("frontpage")
    try:
        generate(window)
        publish.rebuild_site(config.EPISODES_DIR)
    except VerificationError as e:
        # Every take rendered and every take was rejected. Nothing was published, so the feed is
        # still serving the previous episode; say that, and say what was wrong with the takes.
        status.error(f"verification failed after {ATTEMPTS} attempts: {e}")
        alerts.notify(
            f"HN Radio: the {episode_id} episode was NOT published. {ATTEMPTS} takes failed "
            f"verification, last reason: {e}. The feed still serves the previous episode; check "
            f"/data/cron.log for each take's script problems."
        )
        return 1
    except Exception as e:
        # Record first, alert second. The status write is local and cannot fail in a way that
        # matters; the webhook talks to the network, which is plausibly the very thing that broke.
        traceback.print_exc()
        status.error(f"{type(e).__name__}: {e}")
        alerts.notify(
            f"HN Radio: the {episode_id} episode FAILED. {type(e).__name__}: {e}. "
            f"The feed still serves the previous episode; check /data/cron.log for the traceback."
        )
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
