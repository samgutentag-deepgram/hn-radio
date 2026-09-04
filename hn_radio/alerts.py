"""Tell a human when the nightly show did not ship.

The status board is a PULL surface: it tells you something is wrong once you decide to look at it.
For a job that runs at 3am while nobody is awake, that is the same as not being told. This module is
the push half.

Two channels, each one secret away and each optional:

  webhook   `HN_RADIO_ALERT_WEBHOOK`. Payload `{"text": "..."}`, which Slack and Discord incoming
            webhooks accept as-is.
  Pushover  `HN_RADIO_PUSHOVER_TOKEN` (the application token) and `HN_RADIO_PUSHOVER_USER` (the
            user or group key). Both are required; one without the other is reported as
            misconfigured rather than silently skipped. Posts to the documented
            https://api.pushover.net/1/messages.json endpoint with a fixed title.

Every configured channel gets every alert. No SDK, no account library, no new dependency: `urllib`
is already how this repo talks to Deepgram and Hacker News.

With nothing configured every call here is a no-op that logs locally. That is the correct default
for a repo strangers clone: an alerting integration that fails loudly on a fresh checkout would be
worse than no alerting at all.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 10
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_TITLE = "HN Radio"


def webhook_url() -> str:
    return (os.environ.get("HN_RADIO_ALERT_WEBHOOK") or "").strip()


def pushover_keys() -> tuple[str, str]:
    """(application token, user key). Either may be empty; `notify` decides what that means."""
    return ((os.environ.get("HN_RADIO_PUSHOVER_TOKEN") or "").strip(),
            (os.environ.get("HN_RADIO_PUSHOVER_USER") or "").strip())


def notify(text: str, *, log=print) -> bool:
    """Best-effort alert to every configured channel. True if at least one delivered. NEVER raises.

    Never raising is the whole contract. Every caller is already on a failure path -- something has
    gone wrong and we are trying to say so -- and an exception from the alerting code would replace a
    reportable failure with an unreportable one, which is strictly worse than staying quiet.

    The text is logged either way, so the traceback that prompted this is never only in a channel
    that may not exist.
    """
    log(f"[alert] {text}")
    delivered = False
    url = webhook_url()
    token, user = pushover_keys()
    if not url and not (token or user):
        log("[alert] HN_RADIO_ALERT_WEBHOOK and HN_RADIO_PUSHOVER_TOKEN/USER are not set, so "
            "nothing was sent")
        return False
    if url:
        delivered |= _post("webhook", url, json.dumps({"text": text}).encode("utf-8"),
                           "application/json", log)
    if token or user:
        if token and user:
            body = urllib.parse.urlencode({"token": token, "user": user, "message": text,
                                           "title": PUSHOVER_TITLE}).encode("utf-8")
            delivered |= _post("pushover", PUSHOVER_URL, body,
                               "application/x-www-form-urlencoded", log)
        else:
            # Half a configuration is the likeliest mistake and the one that would otherwise look
            # identical to "not configured". Say which half is missing.
            missing = "HN_RADIO_PUSHOVER_USER" if token else "HN_RADIO_PUSHOVER_TOKEN"
            log(f"[alert] pushover: {missing} is not set, so nothing was sent there")
    return delivered


def _post(channel: str, url: str, body: bytes, content_type: str, log) -> bool:
    try:
        # Building the Request is INSIDE the try, not before it. `Request(...)` validates the URL in
        # its constructor and raises ValueError on something like "not-a-url", so a mistyped Fly
        # secret would otherwise escape this function and break the caller that was trying to report
        # a different failure. A test asserts exactly that, and it caught this.
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": content_type})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            ok = 200 <= resp.status < 300
        log(f"[alert] {channel}: {'delivered' if ok else 'refused it'}")
        return ok
    except (urllib.error.URLError, OSError, ValueError) as e:
        # ValueError covers a malformed URL from a mistyped secret, which is the likeliest failure
        # here and must not take the cron down on top of whatever it was reporting.
        log(f"[alert] {channel}: could not deliver: {e}")
        return False
