"""Central configuration: credentials, endpoints, and the knobs each stage reads.

Loads the Deepgram key from the project .env (no third-party dotenv library:
a tiny parser keeps the zero-dependency promise).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# "A day" for the show is a Pacific calendar day (DST-aware, so "Tuesday" is the real Pacific
# Tuesday year-round). Used to window story selection and to compute "yesterday" for the daily run.
PACIFIC = ZoneInfo("America/Los_Angeles")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Overridable so the deployed app + daily cron can read/write a mounted Fly volume.
EPISODES_DIR = Path(os.environ.get("HN_RADIO_EPISODES_DIR", str(PROJECT_ROOT / "episodes")))

# --- HN ingest ---
HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
N_STORIES = 3          # stories covered per episode. Three, so each gets a real exchange
                       # instead of a single take; see the 2026-08-08 script spec.
N_COMMENTS = 2         # comments performed from the top thread
# How far back a scheduled run reaches for stories, in hours. The cron runs twice a day, twelve
# hours apart, so eighteen gives each show a six-hour overlap with the one before it. The overlap
# is deliberate: a story that broke at 2am should be eligible for the morning show AND still be
# eligible for the afternoon one if the morning show did not pick it. Stories the previous episode
# DID cover are excluded in `pipeline.run_panel`, so the overlap never reads the same lead twice.
LOOKBACK_HOURS = 18

# --- Verification ---
# The shortest stitched episode the scheduled run will publish. Every episode where the Claude
# writer failed and `PanelWriter` covered has come in under 180s; every Claude episode over 320s.
# Five minutes sits in the gap with room on both sides. `hn_radio/verify.py` explains the gate.
MIN_EPISODE_SECONDS = 300
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 0.6
HTTP_TIMEOUT_SECONDS = 20

# --- Flux TTS render ---
# Batch REST: POST https://api.deepgram.com/v2/speak?model=flux-{voice}-en
# Raw linear16 (container=none) so stitching is pure byte concatenation and we
# write one honest WAV header ourselves.
DEFAULT_API_HOST = "api.deepgram.com"
def api_host() -> str:
    """The Deepgram API host. The public cloud API unless DEEPGRAM_API_HOST says otherwise.

    An escape hatch for pointing the renderer at a different Deepgram endpoint. The Fly deploy
    sets no host, so a deployed run always talks to the documented cloud API.
    """
    host = _read_env_var("DEEPGRAM_API_HOST") or DEFAULT_API_HOST
    # Tolerate a pasted URL rather than building https://https://host/v2/speak.
    host = host.removeprefix("https://").removeprefix("http://").strip("/")
    return host


def speak_endpoint(voice_id: str) -> str:
    """Flux voices go to /v2/speak, Aura-2 (previous gen) to /v1/speak. Same media params."""
    version = "v2" if voice_family(voice_id) == "flux" else "v1"
    return f"https://{api_host()}/{version}/speak"


# Voices retired by decision rather than by the catalog. Marcus and Renee both still render:
# Marcus is in the GA catalog and Renee was dropped from it, but neither belongs in this show.
# Sam pulled both by ear -- they were not sounding good in the generated podcast.
# Kept as a named set so the reason survives, and so a future catalog regeneration cannot quietly
# reinstate them: the generator writes VOICE_CATALOG from the docs, and the docs still list Marcus.
#
# Priya joined them later, same kind of call: Sam pulled her by ear. She is still in the
# published docs, so like Marcus she needs this filter or the next regeneration puts her straight
# back. One line to reverse if he changes his mind, which is the whole reason this is a set of ids
# and not a series of edits scattered across the catalog and the pools.
#
# This filter reaches VOICE_CATALOG (applied to the literal further down) and therefore ALL_VOICES,
# but it does NOT reach the hand-written pools below. COMMENTER_VOICES, GUEST_VOICES and
# FLUX_SUGGESTED_EXPRESSIVITY are separate literals, so a retired id has to come out of each of
# them by hand; tests/test_voice_catalog.py is what catches a miss.
# The retirement splits two ways, and the split matters to more than bookkeeping now that the
# cast page shows the whole published catalog. Marcus and Priya are still IN the published docs and
# the team shipped an official orb for both, so they are part of "the 36" a reader can see on
# deepgram.com even though this show will not cast them. Renee is gone from the docs outright and
# has no orb, so she is not part of that 36 and must never be offered anywhere.
#
# These two names were previously only in tests/test_voice_catalog.py, which pinned the same split.
# They moved here because PUBLISHED_VOICES below needs the distinction at runtime,
# and a definition the code cannot reach is a definition that drifts.
REMOVED_AT_GA = ("flux-renee-en",)
RETIRED_BY_DECISION = ("flux-marcus-en", "flux-priya-en")
RETIRED_VOICES = REMOVED_AT_GA + RETIRED_BY_DECISION

# --- Music ---
_OFF_VALUES = {"0", "false", "no", "off"}


def music_enabled() -> bool:
    """Whether renders lay music. On unless HN_RADIO_MUSIC says otherwise.

    This exists to separate two decisions that were welded together. `_finalize` has taken a
    `with_music` argument since the music work landed, but no production caller passed it, so the
    only way to render a dry episode was to edit the source. That made every deploy of unrelated
    work -- uv, the render guards, failure visibility -- wait on the beds and intro beats being
    finished, because deploying the code meant deploying the music to the 3am show.

    A function, not a module constant, and deliberately so. Reading here means the same thing
    works from a Fly secret, from `.env`, and from a monkeypatched test. `SITE_BASE_URL` used to
    be the counter-example in this file -- `os.environ.get` at import, so fixed before a caller
    could change it and blind to `.env` -- and it is `site_base_url()` for those reasons now.

    Unrecognized values stay ON. The failure modes are not symmetric: a bed nobody wanted is
    obvious to the first listener, while a typo'd `HN_RADIO_MUSIC=flase` silently shipping bare
    speech sounds like an ordinary podcast and could run for days unnoticed.
    """
    raw = _read_env_var("HN_RADIO_MUSIC")
    if raw is None:
        return True
    return raw.strip().lower() not in _OFF_VALUES


AUDIO_ENCODING = "linear16"
AUDIO_CONTAINER = "none"
SAMPLE_RATE = 24000    # Hz, mono, 16-bit
SAMPLE_WIDTH = 2       # bytes per sample (16-bit)
CHANNELS = 1

# --- Stitch ---
GAP_SECONDS = 0.45     # silence inserted between segments

# --- Publish ---
# RSS enclosures need absolute URLs. Set HN_RADIO_BASE_URL to the real origin at
# deploy time (e.g. the Fly.io app URL); see fly.toml.
# The site serves the episodes/ dir as web root, so an episode lives at {BASE}/{id}/.
#
# The default MUST be a host that actually serves the audio. It used to be
# https://hn-radio.example.com, which does not resolve: the app rewrites feed.xml on startup,
# so a plain `make start` produced a feed whose episodes appeared in a podcast player and then
# refused to play. Defaulting to the dev server means an imported local feed just works.
# The base always INCLUDES /episodes, matching how the app mounts the catalog.
DEFAULT_SITE_BASE_URL = "http://localhost:8000/episodes"


def site_base_url() -> str:
    """The absolute origin published URLs are built from, INCLUDING the /episodes segment.

    A function. This was
    `SITE_BASE_URL = os.environ.get("HN_RADIO_BASE_URL", ...)` at module scope, which is the trap
    `music_enabled` was written as a function to avoid and which its docstring named by hand.
    Two things were wrong with the constant and they are separate:

    * READ AT IMPORT. Fixed before any caller could change it, so a test, a notebook, or the app
      setting a value in-process could not affect it. The workaround was `importlib.reload(config)`,
      which hands back a DIFFERENT module object than the one the rest of the process is holding;
      `tests/test_feed.py` had to do exactly that to test the default at all.
    * NO `.env` FALLBACK. `os.environ.get` does not read the project `.env`, so this was the one
      setting `sample.env` advertises that `sample.env` could not actually set. Every other one --
      both API keys, DEEPGRAM_API_HOST, HN_RADIO_MUSIC -- goes through `_read_env_var`.

    Trailing slashes are stripped here rather than at each of the four call sites that append a
    path to this value. An operator writing `.../episodes/` used to produce `.../episodes//id/`.
    """
    return (_read_env_var("HN_RADIO_BASE_URL") or DEFAULT_SITE_BASE_URL).rstrip("/")


def site_app_url() -> str:
    """The origin the browser app is served from, derived from `site_base_url()`.

    `site_base_url()` points at the generated-artifact root (`.../episodes`), because RSS
    enclosures live there. The APP is one level up: `backend/app.py` mounts `web/` at `/` and
    `episodes/` at `/episodes`. The feed needs both -- enclosures under the artifact root, and
    every human-facing link under the app root -- so this derives the second from the first rather
    than adding a second environment variable that could disagree with it.

    Defensive about the suffix: HN_RADIO_BASE_URL is operator-set and may not end in /episodes.
    """
    base = site_base_url()
    return base[: -len("/episodes")] if base.endswith("/episodes") else base
SITE_TITLE = "HN Radio"
SITE_DESCRIPTION = "The Hacker News front page, read to you morning and afternoon. Made with Deepgram Flux TTS."
# Podcast channel metadata (placeholder for the demo)
SITE_AUTHOR = "Deepgram DevRel"
SITE_OWNER_EMAIL = "devrel@deepgram.com"
SITE_CATEGORY = "Technology"

# --- Voices (Flux TTS launch catalog; model string = flux-{voice}-en) ---
# Alexis, and only Alexis. This said flux-haley-en for a while, while `cast.ROLE_VOICES`
# prefers the Alexis ids, so the show had been casting Alexis and this constant had been naming
# Haley for weeks: the v1 legacy path (`voices.assign_voice`) read this and the panel path read
# the cast, and the two disagreed with nothing to notice it. One host, one id, both paths.
HOST_VOICE = "flux-alexis-en"
# Contrasting voices so a multi-person thread is easy to follow by ear.
COMMENTER_VOICES = [
    "flux-cole-en",
    "flux-jack-en",
    "flux-bruce-en",
    "flux-rufus-en",
    "flux-drew-en",
]

# Voices used to perform HN commenters in comment theater. Every catalog voice that CANNOT hold a
# desk, which is 28 of 35.
#
# It was three (Drew, Bruce, Heather) before that, and three was the wrong number for what this pool
# is for. With N_COMMENTS = 2 there were only three possible pairings in the entire show, and two of
# those three put two American adult men the docs describe almost identically next to each other. A
# guest who sounds like that every night is not a member of the public, he is a fourth host.
#
# It also made the recurring-character hook a lie. `guest_voice_for` hashes the HN username so a
# regular keeps their voice between episodes, but across three seats roughly every third commenter
# collides, and a hash that collides that often is a rotation wearing a hash's clothes. Across 28 it
# is a real identity: @dang sounds like @dang, and not like the last four people.
#
# DESK VOICES USED TO BE EXCLUDED, AND THAT REQUIREMENT IS GONE. It was real while it lasted:
# `taken_guests` in writers.py starts empty rather than seeded with the episode's cast, so a desk
# voice in here could perform a comment in the same episode it anchored -- the "one voice, two
# characters" failure cast.py raises over, except silent.
#
# It cannot happen any more, because the show does not cast a guest voice at all: the host and the
# co-host read the comments themselves. tests/test_voice_catalog.py inverted the assertion to match
# and now pins that the OVERLAP IS EXPECTED. This comment used to claim the opposite, and said the
# test pinned a separation the test had stopped pinning.
#
# Curated by ear, not generated: every voice below rendered a preview via scripts/voice_preview.py,
# and cutting one that reads wrong is expected. The tests guard the invariants, not the membership.
GUEST_VOICES = [
    "flux-bree-en",    # Bree, American F Mature, Friendly, sweet, kind
    "flux-brittany-en",# Brittany, American F Mature, Confident, kind, soft
    "flux-brooke-en",  # Brooke, American F Young, Friendly, intelligent, fast
    "flux-bruce-en",   # Bruce, American M Adult, Friendly, kind, natural
    "flux-cliff-en",   # Cliff, American M Mature, Deep, confident, calm
    "flux-colin-en",   # Colin, British M Adult, Warm, friendly, trustworthy
    "flux-conor-en",   # Conor, British M Mature, Confident, deep, friendly
    "flux-donovan-en", # Donovan, American M Adult, Professional, calm, thoughtful
    "flux-drew-en",    # Drew, American M Adult, Confident, relaxed, soft
    "flux-elise-en",   # Elise, American F Adult, Clear, professional, calm
    "flux-gemma-en",   # Gemma, British F Young, Friendly, kind, approachable
    "flux-hannah-en",  # Hannah, American F Young, Clear, confident, thoughtful
    "flux-heather-en", # Heather, American F Young, Clear, engaging, energetic
    "flux-kai-en",     # Kai, Singaporean M Young Adult, Clear, calm, professional
    "flux-kelsey-en",  # Kelsey, American F Young Adult, Clear, professional, caring
    "flux-kit-en",     # Kit, British M Young Adult, Friendly, energetic, thoughtful
    "flux-maeve-en",   # Maeve, Irish F Adult, Friendly, energetic, confident
    "flux-marcelo-en", # Marcelo, Filipino M Young Adult, Clear, calm, professional
    "flux-meena-en",   # Meena, Indian F Adult, Empathetic, professional, calm
    "flux-meghan-en",  # Meghan, American F Adult, Friendly, nice, energetic
    "flux-miles-en",   # Miles, American M Adult, Clear, calm, professional
    "flux-naveen-en",  # Naveen, Indian M Adult, Clear, professional, knowledgeable
    "flux-paige-en",   # Paige, American F Young Adult, Clear, professional, calm
    "flux-rufus-en",   # Rufus, British M Adult, Friendly, confident, intelligent
    "flux-sean-en",    # Sean, British M Mature, Friendly, kind, caring
    "flux-sharon-en",  # Sharon, Australian F Young, Formal, calm, relaxed
    "flux-sienna-en",  # Sienna, American F Young Adult, Clear, professional, calm
    "flux-tanner-en",  # Tanner, British M Adult, Professional, calm, confident
]

def voice_name(voice_id: str) -> Optional[str]:
    """The display name for a voice id: every voice this project can encounter, not just the
    castable ones.

    Deliberately the WIDEST lookup in the file. Two callers need a name for a voice that cannot
    be cast right now. The substitution message names a character who is absent ("Alexis is out
    today"), and `recast.rewrite_role_names` reads the OLD voice off an archived script, which
    may be a retired voice or a previous-generation Aura id. A narrower lookup returns None
    there, and a None makes the rewrite skip in silence: the show introduces one name and a
    different voice speaks.
    """
    entry = PUBLISHED_VOICES.get(voice_id) or ALL_VOICES.get(voice_id)
    return entry[0] if entry else None


def active_voice_catalog() -> dict:
    """The voices castable right now: the GA catalog minus anything retired by ear.

    A function rather than a constant because the tests substitute a catalog through it, which
    is how every "what happens when a voice is missing" case is exercised without a live call.
    """
    return VOICE_CATALOG


def host_voice() -> str:
    return HOST_VOICE


def commenter_voices() -> list:
    return list(COMMENTER_VOICES)


def guest_voices() -> list:
    return list(GUEST_VOICES)


def desk_voice(role: str, default: str) -> str:
    """Voice for a desk role."""
    return default


def http_retries() -> int:
    """Retry budget per render call.

    A function rather than a bare constant because the long-running scripts raise it: an
    archive re-render makes hundreds of calls in a row and would rather wait than restart.
    A normal episode stays at the constant, so a real outage fails fast instead of hanging.
    """
    return HTTP_RETRIES

# The Flux TTS voice catalog, verbatim from the canonical docs:
#   https://developers.deepgram.com/docs/flux-tts/voices
# Model string is flux-{voice}-{language}. Per those docs the SAME catalog is served on both
# /v2/speak transports, the streaming WebSocket and batch REST, so nothing here needs to change
# if a streaming render path is added later.
#
# The EA note that used to sit here said the catalog might change before GA. It did. GA shipped
# (deepgram-docs #1090, #1097) and the published catalog went from twelve voices to thirty-six,
# with Renee dropped outright. The remaining twenty-five were added afterward, so this is now
# thirty-five of thirty-six: the whole published catalog except Renee, who is in RETIRED_VOICES.
# Regenerate from fern/pages/text-to-speech/flux-tts/voices.mdx, not by hand.
#
# Only voices on that page belong here. A probe found ~19 additional flux-* ids that DO render on
# this project's key (they appear in the talk.deepgram.com demo bundle), but they are not in the
# published launch catalog, so shipping them in a public demo would be showing off voices no
# customer can rely on. They were added and then removed for exactly that reason.
#
# Notes are accent, gender, age band, then the first three character words from the docs table.
#
# GENERATED from voices.mdx, not hand-typed, which is why it is all 36 rather than the 11 it sat
# at before GA. Regenerate it the same way; do not add rows by hand.
#
# It is a faithful copy of the docs ON PURPOSE, retired voices included, and RETIRED_VOICES filters
# it immediately below. Deleting a retired voice from this literal instead would make the table stop
# matching its source, and the next regeneration would silently put the voice back.
VOICE_CATALOG = {
    "flux-alexis-en":   ("Alexis", "American F Adult, Clear, professional, calm"),
    "flux-bree-en":     ("Bree", "American F Mature, Friendly, sweet, kind"),
    "flux-brittany-en": ("Brittany", "American F Mature, Confident, kind, soft"),
    "flux-brooke-en":   ("Brooke", "American F Young, Friendly, intelligent, fast"),
    "flux-bruce-en":    ("Bruce", "American M Adult, Friendly, kind, natural"),
    "flux-cliff-en":    ("Cliff", "American M Mature, Deep, confident, calm"),
    "flux-cole-en":     ("Cole", "American M Young, Friendly, clear, interesting"),
    "flux-colin-en":    ("Colin", "British M Adult, Warm, friendly, trustworthy"),
    "flux-conor-en":    ("Conor", "British M Mature, Confident, deep, friendly"),
    "flux-donovan-en":  ("Donovan", "American M Adult, Professional, calm, thoughtful"),
    "flux-drew-en":     ("Drew", "American M Adult, Confident, relaxed, soft"),
    "flux-elise-en":    ("Elise", "American F Adult, Clear, professional, calm"),
    "flux-gemma-en":    ("Gemma", "British F Young, Friendly, kind, approachable"),
    "flux-haley-en":    ("Haley", "American F Young Adult, Clear, professional, caring"),
    "flux-hannah-en":   ("Hannah", "American F Young, Clear, confident, thoughtful"),
    "flux-heather-en":  ("Heather", "American F Young, Clear, engaging, energetic"),
    "flux-jack-en":     ("Jack", "British M Adult, Confident, thoughtful, friendly"),
    "flux-kai-en":      ("Kai", "Singaporean M Young Adult, Clear, calm, professional"),
    "flux-kelsey-en":   ("Kelsey", "American F Young Adult, Clear, professional, caring"),
    "flux-kit-en":      ("Kit", "British M Young Adult, Friendly, energetic, thoughtful"),
    "flux-maeve-en":    ("Maeve", "Irish F Adult, Friendly, energetic, confident"),
    "flux-marcelo-en":  ("Marcelo", "Filipino M Young Adult, Clear, calm, professional"),
    "flux-marcus-en":   ("Marcus", "American M Adult, Friendly, helpful, smooth"),
    "flux-meena-en":    ("Meena", "Indian F Adult, Empathetic, professional, calm"),
    "flux-meghan-en":   ("Meghan", "American F Adult, Friendly, nice, energetic"),
    "flux-miles-en":    ("Miles", "American M Adult, Clear, calm, professional"),
    "flux-naveen-en":   ("Naveen", "Indian M Adult, Clear, professional, knowledgeable"),
    "flux-paige-en":    ("Paige", "American F Young Adult, Clear, professional, calm"),
    "flux-priya-en":    ("Priya", "Indian F Adult, Confident, empathetic, professional"),
    "flux-rufus-en":    ("Rufus", "British M Adult, Friendly, confident, intelligent"),
    "flux-sean-en":     ("Sean", "British M Mature, Friendly, kind, caring"),
    "flux-sharon-en":   ("Sharon", "Australian F Young, Formal, calm, relaxed"),
    "flux-sienna-en":   ("Sienna", "American F Young Adult, Clear, professional, calm"),
    "flux-tanner-en":   ("Tanner", "British M Adult, Professional, calm, confident"),
    "flux-wade-en":     ("Wade", "American M Adult, Warm, confident, clear"),
    "flux-wes-en":      ("Wes", "American M Adult, Thoughtful, friendly, warm"),
}

# Deepgram's suggested expressivity per voice, from the Flux TTS demo at talk.deepgram.com.
# Range -5..5. Trimmed to the catalog voices; test_voice_catalog pins that it stays trimmed.
#
# CORRECTED against the live docs. Batch /v2/speak takes both knobs now: `speed`, from
# 0.85 to 1.15 in 0.05 steps, which returns SPEED_NOT_SUPPORTED on model and language pairs that
# do not have it, and `expressivity`, still marked beta, from -2 (calm) to 2 (animated). Sources:
# developers.deepgram.com/docs/flux-tts/batch and developers.deepgram.com/docs/tts-voice-controls.
#
# The old note, kept so the reason we believed it survives. Probed exhaustively: batch
# rejected every spelling of expressivity as an unknown query parameter, and rejected the
# documented `speed` parameter with a different and more telling error ("Requested model does not
# support the 'speed' parameter"). That probe predates Flux TTS GA, which is when both parameters
# reached the batch path, so the conclusion aged out even though the observations were real. The
# streaming spelling `perturbations` is still not a documented batch parameter, so that half of
# the finding holds.
#
# CHECK THE SCALES BEFORE WIRING THIS UP. The values below run -5..5, because that is the scale the
# demo publishes. The batch `expressivity` parameter runs -2..2. The two do not map onto each
# other, so a 2.0 in this table does not mean a 2 to the API, and passing these numbers straight
# through sends something other than what the demo does. Any use of them needs a deliberate
# conversion, not a pass-through.
#
# Kept for the same reason as before: the docs state both transports serve this same catalog, so
# these values describe the voices themselves and stay usable the day a render path wants them,
# and a high value hints at how animated a voice is when casting by ear. The app sends neither
# parameter today, and this correction does not change that.
FLUX_SUGGESTED_EXPRESSIVITY = {
    "flux-bruce-en": 2.0, "flux-cole-en": 2.0, "flux-drew-en": 2.0, "flux-heather-en": 1.0,
    "flux-jack-en": 1.0,
    "flux-rufus-en": 2.0, "flux-sharon-en": 1.0,
    # haley and alexis are absent from the demo bundle, so no published suggestion exists.
}

# Aura-2 voices: the PREVIOUS generation (before Flux), served on /v1/speak. A curated subset,
# used for the old-vs-new comparison in recast ("here are the old voices; recast with Flux").
_PUBLISHED_CATALOG_SOURCE = VOICE_CATALOG  # the docs table, before the filter below
VOICE_CATALOG = {k: v for k, v in VOICE_CATALOG.items() if k not in RETIRED_VOICES}

# THE 36. Everything the published Flux catalog carries today: the 34 this show will cast, plus the
# two it retired by ear. This is the list the cast page grids and the list every official orb SVG
# matches, exactly, with nothing left over on either side -- which is how we know it is the right
# list rather than a convenient one.
#
# NOT the same as ALL_VOICES (which adds the previous-generation Aura voices) and NOT the same as
# VOICE_CATALOG (which is what the show can seat). A page that means "show me the catalog" wants
# this; a renderer choosing a voice wants VOICE_CATALOG. Conflating them is how Renee ended up in
# a production commenter pool with nothing guarding her.
PUBLISHED_VOICES = {
    **VOICE_CATALOG,
    **{vid: _PUBLISHED_CATALOG_SOURCE[vid] for vid in RETIRED_BY_DECISION
       if vid in _PUBLISHED_CATALOG_SOURCE},
}


def is_retired(voice_id: str) -> bool:
    """Whether a PUBLISHED_VOICES entry is one the show has retired.

    The cast page labels these in words rather than dropping them, so a reader sees the whole
    catalog and is told which two this show does not use. Colour is never the signal.
    """
    return voice_id in RETIRED_VOICES

AURA_CATALOG = {
    "aura-2-thalia-en":    ("Thalia", "American F, clear and energetic"),
    "aura-2-andromeda-en": ("Andromeda", "American F, casual and expressive"),
    "aura-2-helena-en":    ("Helena", "American F, caring and natural"),
    "aura-2-athena-en":    ("Athena", "American F, calm and professional"),
    "aura-2-apollo-en":    ("Apollo", "American M, confident and casual"),
    "aura-2-arcas-en":     ("Arcas", "American M, natural and smooth"),
}

# Every valid voice (Flux = new, Aura = previous), for recast validation.
ALL_VOICES = {**VOICE_CATALOG, **AURA_CATALOG}


def voice_family(voice_id: str) -> str:
    """'flux' (new, /v2/speak) or 'aura' (previous gen, /v1/speak)."""
    return "aura" if voice_id.startswith("aura") else "flux"


# Deepgram's public voice showcase (linked from the recast page).
DEEPGRAM_VOICES_URL = "https://deepgram.com/product/text-to-speech"


def _read_env_var(name: str) -> Optional[str]:
    """Read a var from the environment, falling back to the project .env file."""
    value = os.environ.get(name)
    if value:
        return value.strip()
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == name:
                return val.strip().strip('"').strip("'")
    return None


def get_api_key() -> str:
    """Return the Deepgram API key from the environment or the project .env."""
    key = _read_env_var("DEEPGRAM_API_KEY")
    if key:
        return key
    raise RuntimeError(
        "DEEPGRAM_API_KEY not found. Set it in the environment or in hn-radio/.env "
        "(copy sample.env). Any Deepgram project key works; Flux TTS on the cloud API has "
        "been generally available since 2026-08-12 and needs no Early Access flag."
    )


def get_anthropic_key() -> str:
    """Return the Anthropic API key (for the Claude script writer)."""
    key = _read_env_var("ANTHROPIC_API_KEY")
    if key:
        return key
    raise RuntimeError(
        "ANTHROPIC_API_KEY not found. Set it in the environment or in hn-radio/.env "
        "to use the Claude writer (--writer claude)."
    )


def has_anthropic_key() -> bool:
    return bool(_read_env_var("ANTHROPIC_API_KEY"))


def desk_name(role: str, default: str) -> str:
    """Spoken character name for a desk."""
    return default
