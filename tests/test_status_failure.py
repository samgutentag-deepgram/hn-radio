"""When the nightly run dies, somebody has to find out. These pin how.

Before 2026-08-12 nothing found out. `status.error()` was defined and had **zero callers** anywhere
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
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hn_radio import alerts, status


@pytest.fixture(autouse=True)
def _tmp_status(monkeypatch, tmp_path):
    monkeypatch.setattr(status.config, "EPISODES_DIR", tmp_path)
    monkeypatch.delenv("HN_RADIO_ALERT_WEBHOOK", raising=False)
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


def _load_daily():
    """Import scripts/daily.py, which is a script rather than a package module."""
    root = str(status.config.PROJECT_ROOT / "scripts")
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("daily")
