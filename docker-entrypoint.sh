#!/bin/sh
# HN Radio container entrypoint. Seeds the episodes volume on first boot, then runs the daily
# cron (supercronic) in the background and the web server in the foreground.
set -e

EP="${HN_RADIO_EPISODES_DIR:-/data/episodes}"
# On first boot the empty volume shadows the image's baked episodes; copy them in once.
if [ -d /app/episodes ] && [ ! -e "$EP/index.json" ]; then
  echo "[entrypoint] seeding episodes into $EP from the image..."
  mkdir -p "$EP"
  cp -a /app/episodes/. "$EP/"
fi

echo "[entrypoint] starting daily cron (supercronic) + web server..."
supercronic /app/crontab &
exec uvicorn backend.app:app --host 0.0.0.0 --port 8000
