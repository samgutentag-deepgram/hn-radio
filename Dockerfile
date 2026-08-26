# HN Radio v3: one FastAPI service that serves the app + does one-click recast, plus an in-container
# daily cron (supercronic) that generates yesterday's episode onto a mounted volume. Keys are
# provided at runtime as Fly secrets (DEEPGRAM_API_KEY, ANTHROPIC_API_KEY); never baked in.
FROM python:3.12-slim
WORKDIR /app

# ffmpeg: chaptered-MP3 transcode. tzdata: DST-aware cron. curl: fetch supercronic.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata curl \
    && rm -rf /var/lib/apt/lists/*

# supercronic: a cron runner designed for containers (runs alongside uvicorn).
ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64
RUN curl -fsSL "$SUPERCRONIC_URL" -o /usr/local/bin/supercronic && chmod +x /usr/local/bin/supercronic

# uv, pinned. Copied into the official python image rather than starting FROM an astral image, so
# the interpreter still comes from python:3.12-slim and `.python-version` -> base image -> uv.lock
# stay one chain that tests/test_python_version.py can actually check.
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

# UV_COMPILE_BYTECODE: pay the .pyc cost once at build time instead of on every cold start.
# UV_LINK_MODE=copy: the cache and the venv are on different layers, where hardlinking warns.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Dependencies before source, so editing hn_radio/ does not re-resolve the world.
#
# --frozen: install exactly what uv.lock says and FAIL if it is stale, rather than silently
#   re-resolving at build time to versions no one ran the suite against.
# --no-dev: pytest and httpx stay out of the image. Everything the 3am cron needs is a runtime
#   dependency in pyproject.toml, including anthropic -- daily.py imports ClaudeWriter at module
#   scope, so misfiling it as dev would take the nightly show off the air.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY hn_radio ./hn_radio
COPY backend ./backend
COPY web ./web
COPY scripts ./scripts
COPY episodes ./episodes
# The show theme. `music.apply` DEGRADES SILENTLY when the track is absent -- deliberately, so a
# missing asset can never take the nightly show off the air -- which means forgetting this line
# ships a music-free podcast with no error anywhere. Caught exactly that way before the first
# deploy of the music work.
COPY assets ./assets
COPY crontab docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
