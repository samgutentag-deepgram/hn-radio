"""Tell a human when the nightly show did not ship.

The status board is a PULL surface: it tells you something is wrong once you decide to look at it.
For a job that runs at 3am while nobody is awake, that is the same as not being told. This module is
the push half.

One channel, deliberately: an outgoing webhook at `HN_RADIO_ALERT_WEBHOOK`. The payload shape is
`{"text": "..."}`, which is what Slack and Discord incoming webhooks already accept, so wiring it up
is `fly secrets set HN_RADIO_ALERT_WEBHOOK=...` and nothing else. No SDK, no account, no new
dependency -- `urllib` is already how this repo talks to Deepgram and Hacker News.

With no webhook configured every call here is a no-op that logs locally. That is the correct default
for a repo strangers clone: an alerting integration that fails loudly on a fresh checkout would be
worse than no alerting at all.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 10


def webhook_url() -> str:
    return (os.environ.get("HN_RADIO_ALERT_WEBHOOK") or "").strip()


def notify(text: str, *, log=print) -> bool:
    """Best-effort alert. Returns True if it was delivered, False otherwise. NEVER raises.

    Never raising is the whole contract. Every caller is already on a failure path -- something has
    gone wrong and we are trying to say so -- and an exception from the alerting code would replace a
    reportable failure with an unreportable one, which is strictly worse than staying quiet.

    The text is logged either way, so the traceback that prompted this is never only in a webhook
    that may not exist.
    """
    log(f"[alert] {text}")
    url = webhook_url()
    if not url:
        log("[alert] HN_RADIO_ALERT_WEBHOOK is not set, so nothing was sent")
        return False
    try:
        # Building the Request is INSIDE the try, not before it. `Request(...)` validates the URL in
        # its constructor and raises ValueError on something like "not-a-url", so a mistyped Fly
        # secret would otherwise escape this function and break the caller that was trying to report
        # a different failure. A test asserts exactly that, and it caught this.
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            ok = 200 <= resp.status < 300
        log(f"[alert] {'delivered' if ok else 'webhook refused it'}")
        return ok
    except (urllib.error.URLError, OSError, ValueError) as e:
        # ValueError covers a malformed URL from a mistyped secret, which is the likeliest failure
        # here and must not take the cron down on top of whatever it was reporting.
        log(f"[alert] could not deliver: {e}")
        return False
