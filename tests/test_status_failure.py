"""When the nightly run dies, somebody has to find out. These pin how.

For a while nothing found out. `status.error()` was defined and had **zero callers** anywhere
in the repo, and `scripts/daily.py` had no exception handling at all, so a run that died on segment 4
of 19 left `{"state": "rendering"}` on disk permanently: the board showed a spinner forever and the
traceback existed only in `/data/cron.log`, which nothing surfaces and nothing alerts on.

Two failure shapes, and they need different machinery. That split is what this file is really about:

  CRASH -- raises, so an `except` can catch it, write `error`, and push an alert.
  HANG  -- raises NOTHING. A socket without a timeout, a wedged ffmpeg, a machine that lost its
           network. No exception ever arrives, so no `except` clause can help and `error` is never
           called. All it leaves behind is silence, so silence is the only thing that can be
           detected: `is_stalled` infers it on read, and the next run reports it on start.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hn_radio import alerts, status


@pytest.fixture(autouse=True)
def _tmp_status(monkeypatch, tmp_path):
    monkeypatch.setattr(status.config, "EPISODES_DIR", tmp_path)
    monkeypatch.delenv("HN_RADIO_ALERT_WEBHOOK", raising=False)
    monkeypatch.delenv("HN_RADIO_PUSHOVER_TOKEN", raising=False)
    monkeypatch.delenv("HN_RADIO_PUSHOVER_USER", raising=False)
    monkeypatch.delenv("HN_RADIO_STALL_SECONDS", raising=False)
    return tmp_path


def _stamp(seconds_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


# --- the crash path -----------------------------------------------------------------------------

def test_error_takes_the_board_out_of_in_flight():
    status.begin("2026-08-12", "frontpage")
    status.stage("rendering", "rendering segment 4/19", 4, 19)
    assert status.in_flight(status.read())
    status.error("RuntimeError: Deepgram refused")
    data = status.read()
    assert data["state"] == "error"
    assert "Deepgram refused" in data["last_error"]
    assert not status.in_flight(data), "an errored run must not still read as working"


def test_daily_records_and_alerts_and_exits_nonzero_on_a_crash(monkeypatch, tmp_path):
    """The whole point of the wrapper: a failed run says so three ways."""
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)

    def boom(*a, **k):
        raise RuntimeError("Deepgram refused segment 4")

    sent = []
    monkeypatch.setattr(daily.pipeline, "run_panel", boom)
    monkeypatch.setattr(daily.alerts, "notify", lambda text, **k: sent.append(text) or True)

    assert daily.main() == 1, "a failed nightly run must exit non-zero so cron logs a failure"
    data = status.read()
    assert data["state"] == "error"
    assert "Deepgram refused segment 4" in data["last_error"]
    assert sent and "FAILED" in sent[0]


def test_a_successful_run_neither_alerts_nor_errors(monkeypatch, tmp_path):
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    sent = []
    monkeypatch.setattr(daily.pipeline, "run_panel", lambda *a, **k: None)
    monkeypatch.setattr(daily.publish, "rebuild_site", lambda *a, **k: {})
    monkeypatch.setattr(daily.alerts, "notify", lambda text, **k: sent.append(text) or True)
    assert daily.main() == 0
    assert sent == [], "a clean run must not page anyone"


# --- the hang path ------------------------------------------------------------------------------

def test_a_fresh_in_flight_run_is_not_stalled():
    status.begin("2026-08-12", "frontpage")
    status.stage("rendering", "rendering segment 4/19", 4, 19)
    assert not status.is_stalled(status.read())


def test_a_silent_in_flight_run_is_stalled():
    assert status.is_stalled({"state": "rendering", "updated_at": _stamp(status.stall_seconds() + 60)})


def test_idle_and_error_are_never_stalled_however_old():
    """Only work that claims to be in progress can stall. Finished work is just old."""
    for state in ("idle", "error"):
        assert not status.is_stalled({"state": state, "updated_at": _stamp(86400)})


def test_a_record_with_no_timestamp_counts_as_stalled():
    """Unknown is not the same as fine, and treating it as fine is how a hang hides."""
    assert status.is_stalled({"state": "rendering"})
    assert status.is_stalled({"state": "rendering", "updated_at": "not a date"})


def test_in_flight_covers_a_stage_name_nobody_has_invented_yet():
    """Defined as "not idle and not error" on purpose, so a new stage is covered by default."""
    assert status.in_flight({"state": "transcoding-for-some-future-reason"})


def test_the_stall_threshold_is_tunable_but_a_typo_cannot_disable_it(monkeypatch):
    monkeypatch.setenv("HN_RADIO_STALL_SECONDS", "60")
    assert status.stall_seconds() == 60
    for bad in ("0", "-1", "soon", ""):
        monkeypatch.setenv("HN_RADIO_STALL_SECONDS", bad)
        assert status.stall_seconds() == status.DEFAULT_STALL_SECONDS


def test_the_next_run_reports_a_previous_run_that_hung(monkeypatch, tmp_path):
    """The only mechanism that ever reports a hang actively.

    A daily job has no process alive between runs, so nothing can hold a watchdog timer. The next
    invocation noticing that the last one never reached `done` or `error` is the first moment a hang
    becomes observable at all.
    """
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    (tmp_path / "status.json").write_text(json.dumps({
        "state": "rendering", "episode": "2026-08-11", "note": "rendering segment 4/19",
        "updated_at": _stamp(9 * 3600),
    }))
    sent = []
    monkeypatch.setattr(daily.alerts, "notify", lambda text, **k: sent.append(text) or True)
    monkeypatch.setattr(daily.pipeline, "run_panel", lambda *a, **k: None)
    monkeypatch.setattr(daily.publish, "rebuild_site", lambda *a, **k: {})

    assert daily.main() == 0, "reporting yesterday's hang must not fail today's run"
    assert len(sent) == 1
    assert "never finished" in sent[0] and "2026-08-11" in sent[0]
    assert "hung rather than crashed" in sent[0]


def test_a_clean_previous_run_is_not_reported(monkeypatch, tmp_path):
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    (tmp_path / "status.json").write_text(json.dumps({"state": "idle", "updated_at": _stamp(9 * 3600)}))
    sent = []
    monkeypatch.setattr(daily.alerts, "notify", lambda text, **k: sent.append(text) or True)
    monkeypatch.setattr(daily.pipeline, "run_panel", lambda *a, **k: None)
    monkeypatch.setattr(daily.publish, "rebuild_site", lambda *a, **k: {})
    assert daily.main() == 0
    assert sent == []


# --- what the board is told ---------------------------------------------------------------------

def test_api_status_derives_stalled_rather_than_storing_it(monkeypatch, tmp_path):
    """Derived on READ, because elapsed time cannot be frozen into a record by a process that hung."""
    from backend.app import app
    monkeypatch.setattr(status.config, "EPISODES_DIR", tmp_path)
    (tmp_path / "status.json").write_text(json.dumps({
        "state": "rendering", "episode": "2026-08-11", "note": "rendering segment 4/19",
        "updated_at": _stamp(status.stall_seconds() + 600),
    }))
    body = TestClient(app).get("/api/status").json()
    assert body["stalled"] is True
    assert body["silent_seconds"] >= status.stall_seconds()
    assert body["stall_after_seconds"] == status.stall_seconds()
    on_disk = json.loads((tmp_path / "status.json").read_text())
    assert "stalled" not in on_disk, "the judgement must not be written back into the record"


def test_api_status_reports_a_healthy_run_as_not_stalled(monkeypatch, tmp_path):
    from backend.app import app
    monkeypatch.setattr(status.config, "EPISODES_DIR", tmp_path)
    status.begin("2026-08-12", "frontpage")
    status.stage("rendering", "rendering segment 4/19", 4, 19)
    body = TestClient(app).get("/api/status").json()
    assert body["stalled"] is False


# --- the alert channel --------------------------------------------------------------------------

def test_notify_is_a_logged_noop_with_no_webhook_configured(monkeypatch):
    """A repo strangers clone must not fail because they have no alerting endpoint."""
    lines = []
    assert alerts.notify("the show did not ship", log=lines.append) is False
    assert any("the show did not ship" in ln for ln in lines), "the message must still be logged"
    assert any("not set" in ln for ln in lines)


def test_notify_never_raises_even_on_a_mistyped_webhook(monkeypatch):
    """Every caller is already on a failure path. An exception here hides the real failure."""
    for bad in ("not-a-url", "http://127.0.0.1:1/nope", "ftp://wat"):
        monkeypatch.setenv("HN_RADIO_ALERT_WEBHOOK", bad)
        assert alerts.notify("x", log=lambda *a: None) is False


def test_notify_posts_json_text_to_the_webhook(monkeypatch):
    """The `{"text": ...}` shape is what Slack and Discord accept, so setup is one secret."""
    seen = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        seen["type"] = req.headers.get("Content-type")
        return _Resp()

    monkeypatch.setenv("HN_RADIO_ALERT_WEBHOOK", "https://hooks.example.test/abc")
    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    assert alerts.notify("the show did not ship", log=lambda *a: None) is True
    assert seen["url"] == "https://hooks.example.test/abc"
    assert seen["body"] == {"text": "the show did not ship"}
    assert seen["type"] == "application/json"


def _capture_posts(monkeypatch):
    posts = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        posts.append({"url": req.full_url, "body": req.data.decode(),
                      "type": req.headers.get("Content-type")})
        return _Resp()
    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    return posts


def test_notify_posts_a_form_to_pushover_with_both_keys(monkeypatch):
    """Pushover's documented shape: form-encoded token, user, message, title to messages.json."""
    from urllib.parse import parse_qs
    posts = _capture_posts(monkeypatch)
    monkeypatch.setenv("HN_RADIO_PUSHOVER_TOKEN", "app-token")
    monkeypatch.setenv("HN_RADIO_PUSHOVER_USER", "user-key")
    assert alerts.notify("the show did not ship", log=lambda *a: None) is True
    assert len(posts) == 1 and posts[0]["url"] == alerts.PUSHOVER_URL
    assert posts[0]["type"] == "application/x-www-form-urlencoded"
    form = parse_qs(posts[0]["body"])
    assert form == {"token": ["app-token"], "user": ["user-key"],
                    "message": ["the show did not ship"], "title": [alerts.PUSHOVER_TITLE]}


def test_every_configured_channel_gets_the_alert(monkeypatch):
    posts = _capture_posts(monkeypatch)
    monkeypatch.setenv("HN_RADIO_ALERT_WEBHOOK", "https://hooks.example.test/abc")
    monkeypatch.setenv("HN_RADIO_PUSHOVER_TOKEN", "app-token")
    monkeypatch.setenv("HN_RADIO_PUSHOVER_USER", "user-key")
    assert alerts.notify("x", log=lambda *a: None) is True
    assert {p["url"] for p in posts} == {"https://hooks.example.test/abc", alerts.PUSHOVER_URL}


def test_half_a_pushover_config_sends_nothing_and_names_the_missing_half(monkeypatch):
    """The likeliest mistake, and the one that would otherwise look exactly like "not configured"."""
    posts = _capture_posts(monkeypatch)
    monkeypatch.setenv("HN_RADIO_PUSHOVER_TOKEN", "app-token")
    lines = []
    assert alerts.notify("x", log=lines.append) is False
    assert posts == []
    assert any("HN_RADIO_PUSHOVER_USER is not set" in ln for ln in lines)


def _load_daily():
    """Import scripts/daily.py, which is a script rather than a package module."""
    root = str(status.config.PROJECT_ROOT / "scripts")
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("daily")


# --- the bad-take path (verification + re-run) --------------------------------------------------
#
# A third failure shape, found by replaying a fallback episode: every stage SUCCEEDS and the result is still not a show.
# The Claude writer failed, PanelWriter covered, and a 173-second episode that read a markdown image
# tag aloud went out on the public feed. `hn_radio/verify.py` raises on it; this is what daily.py
# does with the raise.

def _rolling_window():
    from hn_radio.window import EpisodeWindow
    return EpisodeWindow.ending_at(datetime(2026, 9, 5, 3, tzinfo=status.config.PACIFIC))


def test_a_rejected_take_is_re_run_with_a_fresh_writer_and_the_good_one_ships(monkeypatch, tmp_path):
    from hn_radio.verify import VerificationError
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    calls = []

    def run_panel(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise VerificationError(["episode is 173s, under the 300s minimum"])
        return "episode"
    monkeypatch.setattr(daily.pipeline, "run_panel", run_panel)

    assert daily.generate(_rolling_window(), log=lambda *a, **k: None) == "episode"
    assert len(calls) == 2
    assert calls[0]["writer"] is not calls[1]["writer"], "a retry must not reuse the failed writer"
    assert all(kw["min_seconds"] == daily.config.MIN_EPISODE_SECONDS for kw in calls)
    assert all(kw["window"].episode_id("frontpage") == "2026-09-05-am" for kw in calls)


def test_when_every_take_is_rejected_nothing_ships_and_the_alert_says_so(monkeypatch, tmp_path):
    from hn_radio.verify import VerificationError
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    n = {"calls": 0}

    def always_short(**kw):
        n["calls"] += 1
        raise VerificationError([f"take {n['calls']} has url in spoken text"])
    monkeypatch.setattr(daily.pipeline, "run_panel", always_short)
    rebuilt = []
    monkeypatch.setattr(daily.publish, "rebuild_site", lambda *a, **k: rebuilt.append(1))
    sent = []
    monkeypatch.setattr(daily.alerts, "notify", lambda text, **k: sent.append(text) or True)

    assert daily.main() == 1
    assert n["calls"] == daily.ATTEMPTS == 2
    assert rebuilt == [], "nothing was published, so there is nothing to rebuild"
    assert status.read()["state"] == "error"
    assert "verification failed after 2 attempts" in status.read()["last_error"]
    assert len(sent) == 1 and "NOT published" in sent[0] and "take 2" in sent[0]


def test_a_crash_is_not_retried(monkeypatch, tmp_path):
    """Re-running a crash spends a Claude call and a Flux render on the same crash."""
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    n = {"calls": 0}

    def boom(**kw):
        n["calls"] += 1
        raise RuntimeError("Deepgram refused")
    monkeypatch.setattr(daily.pipeline, "run_panel", boom)
    monkeypatch.setattr(daily.alerts, "notify", lambda text, **k: True)
    assert daily.main() == 1
    assert n["calls"] == 1


# --- the full-disk path -------------------------------------------------------------------------
#
# Found the expensive way: 35 episodes filled the 1 GB volume, all 26 Flux calls for the next take
# succeeded, and the first write after them raised ENOSPC. The check now runs before any spend.

def test_a_nearly_full_disk_alerts_before_any_render(monkeypatch, tmp_path):
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    monkeypatch.setattr(daily.config, "MIN_FREE_DISK_BYTES", 10 ** 18, raising=True)
    rendered = []
    monkeypatch.setattr(daily.pipeline, "run_panel", lambda **kw: rendered.append(1))
    sent = []
    monkeypatch.setattr(daily.alerts, "notify", lambda text, **k: sent.append(text) or True)

    assert daily.main() == 1
    assert rendered == [], "no TTS is bought on a disk that cannot hold the result"
    assert status.read()["state"] == "error"
    assert "disk full before render" in status.read()["last_error"]
    assert len(sent) == 1 and "NOT attempted" in sent[0] and "No TTS was spent" in sent[0]


def test_require_free_disk_measures_the_nearest_existing_ancestor(tmp_path):
    daily = _load_daily()
    missing = tmp_path / "not" / "yet" / "episodes"
    assert daily._require_free_disk(missing, floor=0) == shutil.disk_usage(tmp_path).free


def test_the_scheduled_run_asks_for_a_rolling_window_ending_now(monkeypatch, tmp_path):
    """3am asks for the 18 hours before 3am; the id carries the slot; the floor is on."""
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    seen = {}
    monkeypatch.setattr(daily.pipeline, "run_panel", lambda **kw: seen.update(kw) or "ep")
    monkeypatch.setattr(daily.publish, "rebuild_site", lambda *a, **k: {})

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 5, 15, 0, 7, tzinfo=status.config.PACIFIC)
    monkeypatch.setattr(daily, "datetime", _Clock)

    assert daily.main() == 0
    w = seen["window"]
    assert w.episode_id("frontpage") == "2026-09-05-pm"
    assert (w.end - w.start).total_seconds() == daily.config.LOOKBACK_HOURS * 3600
    assert seen["min_seconds"] == daily.config.MIN_EPISODE_SECONDS
    assert seen["edition"] == "frontpage"


def test_dash_dash_date_remakes_that_calendar_episode_in_place_with_the_same_gate(monkeypatch, tmp_path):
    """How a bad episode already on the feed gets replaced: same id, same writer, same floor."""
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    seen = {}
    monkeypatch.setattr(daily.pipeline, "run_panel", lambda **kw: seen.update(kw) or "ep")
    monkeypatch.setattr(daily.publish, "rebuild_site", lambda *a, **k: {})
    assert daily.main(["--date", "2026-09-03"]) == 0
    w = seen["window"]
    assert w.slot is None and w.episode_id("frontpage") == "2026-09-03", "lands on top of the old one"
    assert seen["min_seconds"] == daily.config.MIN_EPISODE_SECONDS
    assert type(seen["writer"]).__name__ == "ClaudeWriter"


def test_no_arguments_is_still_the_scheduled_rolling_window(monkeypatch, tmp_path):
    daily = _load_daily()
    monkeypatch.setattr(daily.status.config, "EPISODES_DIR", tmp_path)
    seen = {}
    monkeypatch.setattr(daily.pipeline, "run_panel", lambda **kw: seen.update(kw) or "ep")
    monkeypatch.setattr(daily.publish, "rebuild_site", lambda *a, **k: {})
    assert daily.main([]) == 0
    assert seen["window"].slot in ("am", "pm")
