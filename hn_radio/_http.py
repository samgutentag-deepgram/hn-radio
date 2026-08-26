"""Tiny stdlib HTTP helpers with retry/backoff. Keeps the zero-dependency promise.

Used by ingest (GET JSON from the HN API) and render (POST JSON, receive audio bytes).
Retries are deliberate: HN and TTS calls are the two network surfaces, and a produced
pipeline should survive a transient blip rather than abort a whole episode.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional


class HttpError(RuntimeError):
    """Raised when a request fails after all retries, with context for debugging."""


def _request(
    url: str,
    *,
    method: str,
    data: Optional[bytes],
    headers: Optional[dict],
    timeout: float,
    retries: int,
    backoff: float,
) -> bytes:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode(errors="replace")[:400]
            except Exception:
                pass
            last_err = HttpError(f"{method} {url} -> HTTP {e.code} {e.reason}: {body}")
            # 4xx (except 429) will not fix themselves; stop early.
            if 400 <= e.code < 500 and e.code != 429:
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = HttpError(f"{method} {url} -> {type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise last_err if last_err else HttpError(f"{method} {url} failed")


def get_json(url: str, *, timeout: float, retries: int, backoff: float):
    raw = _request(url, method="GET", data=None, headers=None,
                   timeout=timeout, retries=retries, backoff=backoff)
    return json.loads(raw.decode())


def get_text(url: str, *, headers: Optional[dict] = None, timeout: float,
             retries: int, backoff: float) -> str:
    raw = _request(url, method="GET", data=None, headers=headers,
                   timeout=timeout, retries=retries, backoff=backoff)
    return raw.decode(errors="replace")


def post_json_for_bytes(
    url: str, payload: dict, headers: dict, *, timeout: float, retries: int, backoff: float
) -> bytes:
    body = json.dumps(payload).encode()
    return _request(url, method="POST", data=body, headers=headers,
                    timeout=timeout, retries=retries, backoff=backoff)
