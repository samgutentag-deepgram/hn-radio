"""Stage 3b - Render. Turn each script segment into audio via Flux TTS batch.

Batch REST: POST https://api.deepgram.com/v2/speak?model={voice}&encoding=linear16&container=none
Requesting raw linear16 (headerless) means the response body IS the PCM sample buffer, so
stitching is byte concatenation and we avoid the batch container=wav placeholder-header bug
(the ~2 GB data length). Each segment is one stateless call, which is all batch supports.

Why raw HTTP and not the official SDK (a conformance note): the published
`deepgram-sdk` (6.1.1, latest) exposes Speak V2 ONLY as a streaming WebSocket client
(`speak.v2 connect`), with no batch REST method. Deepgram documents Flux batch as a raw
request (a curl), and the official `fastapi-flux` starter itself proxies Flux over raw
protocol rather than the SDK. HN Radio is deliberately batch, so raw HTTP here is the
house-conformant path. The SDK would only apply if we added a streaming / voice-agent feature.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
from typing import List, Optional

from . import config
from ._http import post_json_for_bytes
from .models import ScriptSegment


log = logging.getLogger(__name__)


def _speak_url(voice_id: str, with_speed: bool = True, with_expressivity: bool = True) -> str:
    # Flux voices -> /v2/speak; Aura-2 (previous gen) -> /v1/speak. Same media params on both.
    # The host comes from config so a run can be pointed at a different Deepgram endpoint.
    base = config.speak_endpoint(voice_id)
    params = {
        "model": voice_id,
        "encoding": config.AUDIO_ENCODING,
        "container": config.AUDIO_CONTAINER,
        "sample_rate": config.SAMPLE_RATE,
    }
    if with_speed and config.SPEAK_SPEED is not None:
        params["speed"] = config.SPEAK_SPEED
    expressivity = effective_expressivity(voice_id) if with_expressivity else None
    if expressivity is not None:
        params["expressivity"] = expressivity
    return f"{base}?{urllib.parse.urlencode(params)}"


# What Flux returns when a model/language pair has no `speed` support. Retrying without the
# parameter is the right response: a slightly slow read still ships, a hard failure takes the
# 3am show off the air over a knob that only changes pace.
_SPEED_UNSUPPORTED = ("SPEED_NOT_SUPPORTED", "does not support the 'speed' parameter")

# Same posture for expressivity, which is beta. The docs name no "unsupported" code for it (only
# EXPRESSIVITY_OUT_OF_RANGE and EXPRESSIVITY_INCREMENT_INVALID), so any rejection that names the
# parameter drops it for that voice, for the rest of the process, and says so once.
_EXPRESSIVITY_REJECTED_MARKERS = ("EXPRESSIVITY", "expressivity")
_expressivity_rejected: set = set()


def _rejected_for(msg: str, markers) -> bool:
    """True when a 4xx response BODY names one of `markers`.

    Only the part after ' -> ' is read: the error text starts with the request URL, and that URL
    carries `expressivity=1`, so matching the whole string would blame every failure on it.
    """
    reply = msg.split(" -> ", 1)[-1]
    return reply.startswith("HTTP 4") and any(m in reply for m in markers)


def effective_expressivity(voice_id: str) -> Optional[int]:
    """What this voice is actually sent: the configured value, unless the API refused it.

    This is also what a render cache has to key on, so a take made at one setting is never
    reused for another (see pipeline._cache_line).
    """
    if voice_id in _expressivity_rejected:
        return None
    return config.expressivity_for(voice_id)


def take_key(voice_id: str, text: str) -> str:
    """A content key for one rendered take: voice, words and the expressivity it is sent at.

    For the scripts that keep a content-addressed PCM cache. A take at the API default hashes the
    way it always did, so caches made before expressivity existed stay valid for the default.
    """
    x = effective_expressivity(voice_id)
    tag = f"{voice_id}\x00{text}" if x is None else f"{voice_id}|x{x}\x00{text}"
    return hashlib.sha256(tag.encode()).hexdigest()[:32]


def render_segment(text: str, voice_id: str, api_key: str) -> bytes:
    """Render one segment to raw linear16 PCM bytes."""
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}

    def _post(with_speed: bool, with_expressivity: bool) -> bytes:
        return post_json_for_bytes(
            _speak_url(voice_id, with_speed, with_expressivity), {"text": text}, headers,
            timeout=60, retries=config.http_retries(), backoff=config.HTTP_BACKOFF_SECONDS,
        )

    # Each knob that the API turns down is dropped and the request tried again, at most once per
    # knob. A flat read still ships; a hard failure would take the show off the air.
    with_speed, with_expressivity = True, effective_expressivity(voice_id) is not None
    while True:
        try:
            audio = _post(with_speed, with_expressivity)
            break
        except Exception as e:
            msg = str(e)
            if with_expressivity and _rejected_for(msg, _EXPRESSIVITY_REJECTED_MARKERS):
                if voice_id not in _expressivity_rejected:
                    log.warning("Flux rejected expressivity for %s; rendering it without: %s",
                                voice_id, msg[:200])
                    print(f"[render] Flux rejected expressivity for {voice_id}; "
                          f"rendering it at the default from here on")
                _expressivity_rejected.add(voice_id)
                with_expressivity = False
                continue
            if with_speed and any(m in msg for m in _SPEED_UNSUPPORTED):
                with_speed = False
                continue
            raise RuntimeError(
                f"Flux render failed for voice={voice_id!r} text={text[:60]!r}: {e}"
            ) from e
    # Guard: if the server ever returns a container despite container=none, drop the header.
    if audio[:4] == b"RIFF":
        audio = audio[44:]
    if not audio:
        raise RuntimeError(f"Flux returned empty audio for voice={voice_id!r} text={text[:60]!r}")
    return audio


def render_all(segments: List[ScriptSegment], api_key: str, on_progress=None) -> List[bytes]:
    """Render every segment in order. Returns PCM bytes aligned to `segments`.

    `on_progress(done, total)` is called after each segment (for the status board)."""
    pcm: List[bytes] = []
    total = len(segments)
    for i, seg in enumerate(segments):
        if not seg.voice_id:
            raise RuntimeError(f"Segment {seg.order} has no voice_id; run voices.assign_voices first.")
        pcm.append(render_segment(seg.text, seg.voice_id, api_key))
        seg.expressivity = effective_expressivity(seg.voice_id)
        if on_progress:
            on_progress(i + 1, total)
    return pcm
