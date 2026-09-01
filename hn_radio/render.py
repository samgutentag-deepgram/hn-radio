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

import urllib.parse
from typing import List

from . import config
from ._http import post_json_for_bytes
from .models import ScriptSegment


def _speak_url(voice_id: str) -> str:
    # Flux voices -> /v2/speak; Aura-2 (previous gen) -> /v1/speak. Same media params on both.
    # The host comes from config so a run can be pointed at a different Deepgram endpoint.
    base = config.speak_endpoint(voice_id)
    query = urllib.parse.urlencode({
        "model": voice_id,
        "encoding": config.AUDIO_ENCODING,
        "container": config.AUDIO_CONTAINER,
        "sample_rate": config.SAMPLE_RATE,
    })
    return f"{base}?{query}"


def render_segment(text: str, voice_id: str, api_key: str) -> bytes:
    """Render one segment to raw linear16 PCM bytes."""
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
    try:
        audio = post_json_for_bytes(
            _speak_url(voice_id), {"text": text}, headers,
            timeout=60, retries=config.http_retries(), backoff=config.HTTP_BACKOFF_SECONDS,
        )
    except Exception as e:
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
        if on_progress:
            on_progress(i + 1, total)
    return pcm
