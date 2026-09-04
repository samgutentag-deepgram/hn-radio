# Deploying HN Radio to Fly.io

HN Radio is a single **FastAPI service** (uvicorn) that serves the app plus the episode data, holds
the Deepgram key server-side for one-click recast, and runs its own in-container cron that generates
an episode twice a day, at 3am and 3pm Pacific. Deploying means building the Docker image and running it on Fly.

## What's in the repo for this
- `Dockerfile` - `python:3.12-slim` with ffmpeg, tzdata, supercronic and the uv binary, running
  `docker-entrypoint.sh`. Dependencies come from `uv sync --frozen --no-dev`, so a stale lock fails
  the build instead of quietly re-resolving, and pytest never ships.
- `fly.toml` - Fly config: `internal_port = 8000`, a `hn_radio_data` volume mounted at `/data`, and
  `auto_stop_machines = "off"` with `min_machines_running = 1`.
- `crontab` - one entry, `CRON_TZ=America/Los_Angeles`, running `scripts/daily.py` at 3am and 3pm Pacific.
- `docker-entrypoint.sh` - seeds the volume from the image on first boot, then starts supercronic in
  the background and uvicorn in the foreground.
- `backend/`, `web/`, `hn_radio/`, `scripts/`, `episodes/`, `assets/` - copied into the image. The
  Dockerfile's `COPY` lines are the authority on that list; `.dockerignore` only says what must
  never enter the context.

**The machine never scales to zero, on purpose.** Scale-to-zero and an in-container cron are
incompatible: a machine asleep at 3am Pacific does not generate an episode, and nothing wakes it,
because nobody is requesting anything at 3am (3pm is busier, but the point stands). The cost of a `shared-cpu-1x` staying up is the price
of the show existing.

## One-time setup
```bash
brew install flyctl        # or: curl -L https://fly.io/install.sh | sh
fly auth login
```

## Deploy
The app URL is `https://<app>.fly.dev`, and the RSS feed needs that absolute URL baked in, so pick
the name first.

1. **Pick a unique app name** in `fly.toml` (`app = "hn-radio-<you>"`), and set `HN_RADIO_BASE_URL`
   in its `[env]` block to match. That value must include the `/episodes` path segment: episodes are
   served under `/episodes`, and `config.site_app_url()` derives the human-facing origin by stripping
   that suffix back off.

2. **Generate at least one episode** if you have not already:
   ```bash
   uv run python -m hn_radio --edition makers      # or: make episode
   ```

3. **Bake the real URL into the feed + site** (so a podcast player gets absolute links):
   ```bash
   HN_RADIO_BASE_URL=https://<app>.fly.dev/episodes uv run python scripts/build_site.py
   ```
   The app also rebuilds these three artifacts on every boot from `fly.toml`'s `HN_RADIO_BASE_URL`,
   so this step is about having a correct feed locally, not about the deployed one being wrong.

4. **Launch + set the secrets** (never in the image) + deploy:
   ```bash
   fly launch --no-deploy        # detects Dockerfile + fly.toml; decline databases
   fly secrets set DEEPGRAM_API_KEY=$(grep '^DEEPGRAM_API_KEY=' .env | cut -d= -f2-)
   fly secrets set ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2-)
   fly deploy
   fly apps open
   ```
   `ANTHROPIC_API_KEY` is **not** optional on a machine that runs the cron. `scripts/daily.py`
   imports `ClaudeWriter` at module scope, so without the key the scheduled job dies rather than
   falling back, and the only symptom is a feed that stops moving.

Optionally also set an alert channel, to be told when a scheduled run fails or both takes are
rejected by verification instead of having to look. Either or both:

```bash
fly secrets set HN_RADIO_ALERT_WEBHOOK=https://hooks.slack.com/services/...      # Slack or Discord
fly secrets set HN_RADIO_PUSHOVER_TOKEN=<application token> HN_RADIO_PUSHOVER_USER=<user key>
```

Pushover needs both halves; one without the other is logged as misconfigured and sends nothing.
With nothing set, alerting is a logged no-op. Setting a secret restarts the machine, so no deploy
is needed.

On later updates: `fly deploy`. Episodes generated on the volume are not in the image and are not
affected by a deploy.

## Subscribe in a real podcast player (the RSS demo)
After deploy, the feed is at:
```
https://<app>.fly.dev/episodes/feed.xml
```
Add that URL in Apple Podcasts / Overcast / Pocket Casts ("Add a show by URL"). It carries one item
per episode, enclosing the chaptered `episode.mp3` as `audio/mpeg`. Because `HN_RADIO_BASE_URL` is
set in `fly.toml`, those enclosure URLs are absolute and players can fetch the audio.

The MP3 is resampled to 44.1 kHz rather than left at the 24 kHz Flux returns. That is not a quality
choice: libmp3lame at 24 kHz emits MPEG-2 Layer III, which is legal MP3 and outside what Apple
Podcasts and friends expect, and the symptom is an episode that lists in a player and then refuses
to play.

## Rotating the Deepgram key
`.env` is local only. Updating it changes nothing about the deployed app, and the failure is
quiet: local renders start using the new key while the scheduled cron on Fly keeps using the old
one. If the two keys have different model access, local and production silently render from
different voice catalogs.

Rotate both, in the same sitting:
```bash
fly secrets set DEEPGRAM_API_KEY=$(grep '^DEEPGRAM_API_KEY=' .env | cut -d= -f2-) -a dg-devrel-hn-radio
fly secrets list -a dg-devrel-hn-radio   # confirm the DIGEST changed
```
Setting a secret restarts the machine, so no `fly deploy` is needed. Verify the digest rather than
trusting the command, same as with deploys.

## Notes
- **Episodes live on the volume, not in the image.** `HN_RADIO_EPISODES_DIR=/data/episodes` points
  the app and the cron at the mount, so generated episodes survive a restart and a deploy. The image
  still carries whatever `episodes/` held at build time, and the entrypoint copies that in once on a
  first boot, when the empty volume would otherwise shadow it.
- **Play counts live on the volume and are not backed up.** `episodes/plays.jsonl` and its rotated
  archives sit beside the episodes on `/data`. Destroying the volume destroys the history: there is
  no export, no replica, and nothing reconstructs it, because the events are only ever written at
  the moment they happen. If the numbers ever start mattering, `fly ssh console -C 'cat
  /data/episodes/plays.jsonl'` is the whole backup procedure.
- **Only the chaptered MP3 is kept per episode.** `pipeline._finalize` deletes the WAV and the
  per-segment PCM once the MP3 is written, and `.dockerignore` excludes both from the image. A
  five-minute episode is about 5.5 MB on the volume this way, against 40 MB when all three were
  kept, which is what filled the original 1 GB volume at 35 episodes. The volume is 5 GB now;
  `scripts/daily.py` refuses to render with under `config.MIN_FREE_DISK_BYTES` free and alerts
  instead, before any TTS is bought.
- **Keys are Fly secrets**, read at runtime by `hn_radio.config`; no secrets in the image.
- **Music can be turned off without a deploy.** `fly secrets set HN_RADIO_MUSIC=0` restarts the
  machine and the next render picks it up. Setting it in `fly.toml`'s `[env]` works too but needs a
  full `fly deploy` to change, which defeats the point of the switch.
- **Renders are rate limited** by `backend/limits.py`: one at a time process-wide, three per hour
  per caller, both refused with a 429 and a `Retry-After`. That is availability before cost, because
  two concurrent builds on a `shared-cpu-1x` was an out-of-memory kill of the page. Tune with
  `HN_RADIO_RENDER_LIMIT` and `HN_RADIO_RENDER_WINDOW`.
- Handy: `fly status`, `fly logs`, `fly dashboard`. The cron's own output is appended to
  `/data/cron.log`.

## Planned improvement
A dynamic `/feed.xml` route on the backend that builds absolute URLs from the request host would
remove the need for `HN_RADIO_BASE_URL` entirely (the feed would be correct on any domain
automatically), plus richer iTunes channel tags (author, artwork, category) for a fully polished
podcast listing.
