# HN Radio

The Hacker News front page, turned into a produced twice-daily podcast. Plain text goes in, a
multi-voice news show comes out, every word rendered by Deepgram Flux TTS with no markup and no
per-word tuning.

Live at [dg-devrel-hn-radio.fly.dev](https://dg-devrel-hn-radio.fly.dev). The feed at
[`/episodes/feed.xml`](https://dg-devrel-hn-radio.fly.dev/episodes/feed.xml) is a real RSS feed,
confirmed subscribable in Overcast. A cron job on the Fly volume builds an episode from the last
18 hours of the front page at 3am and 3pm Pacific, and re-runs a take that fails verification.

Each episode is a two-person show. Alexis hosts every episode; the second chair goes to a different
voice from the Flux catalog every episode, with a twenty-episode no-repeat window. The two of them
cover every story together and read the Hacker News comments themselves, naming the commenter first,
inside the story the comment belongs to. It opens with a ten-word headline per story, covers three
stories with a real follow-up question on each, and carries a music theme with a sting at every
story change.

This is a DevRel demo for the Flux TTS launch, and it deliberately exercises the **batch** path
(HTTP `/v2/speak`) rather than streaming.

**New here?** Read [`docs/glossary.md`](docs/glossary.md) first. The vocabulary is precise and
deliberately so: "line" and "section" are different things, so are "role", "character" and "voice",
and three words used to mean two things each until that document settled them.

## Before you start

- **[uv](https://docs.astral.sh/uv/)** (`brew install uv`). Required, not a convenience: every
  Makefile target goes through it and there is no pip fallback path, because reproducing `uv sync`
  with pip means reading `uv.lock` by hand.
- **Python 3.12.** Pinned in `.python-version` and provisioned by uv, so you do not need it on your
  PATH already. Tests fail if the running interpreter, the pin, and the Dockerfile base image ever
  disagree.
- **ffmpeg**, a system binary. Every episode is transcoded to a chaptered MP3, so a run dies
  without it.
- **A Deepgram API key.** Nothing special on it. Flux TTS went generally available on the cloud
  API on 2026-08-12, so a normal project key works. Self-hosted Flux is still Early Access, and
  this project only talks to the cloud API.
- **An Anthropic API key**, only if you want the Claude writer. Optional locally, because the
  `panel` writer is deterministic and offline. Not optional on a machine running the scheduled cron:
  `scripts/daily.py` imports `ClaudeWriter` at module scope.

To hear a voice before casting it, `scripts/voice_preview.py` renders one line in every catalog
voice and builds an audition page at `episodes/_voices/index.html`, grouped by what each voice does
in the show today:

```bash
uv run python scripts/voice_preview.py
uv run python scripts/voice_preview.py --line "Some other sentence."
```

It is resumable and skips voices already on disk, so a re-run is free. Auditioning the whole catalog
is the point rather than a side effect, because the co-host is drawn from the whole catalog: every
voice on that page will host a morning show sooner or later, and this is how one gets vetoed by ear
into `config.RETIRED_VOICES`. It also doubles as a catalog probe. `resolve_role` walks past a voice
missing from the catalog and announces a substitution, but `config.GUEST_VOICES` is read straight
through, so a dead id in it is a hard failure rather than a degradation in the two paths that still
read that pool: the recast picker's guest preset, and a custom edition's comment theater. Anything
that renders a preview is safe to cast.

If `make episode` fails partway through, use `scripts/local_episode.py` instead. It caches the
written script and every rendered segment keyed by `(voice, sha256(text))`, so a re-run pays only
for what is genuinely missing. That matters because `_finalize` writes the per-segment cache only
after every segment has rendered, so one failed call late in an episode currently discards every
successful call before it.

`scripts/make_cover.py` writes `episodes/cover.png`, the image every podcast player shows for the
show. It is the only thing here that imports Pillow, so Pillow sits in the `dev` dependency group:
`uv sync` installs it locally, and `uv sync --no-dev` -- what the image and the (dormant) GitHub
workflow both run --
leaves it out. Its fonts are looked up across a candidate list rather than hardcoded, so it runs on
a Mac, in the container, and on a CI runner; with no TrueType face anywhere it says so and falls
back to Pillow's bitmap default, which renders an ugly cover instead of raising.

`scripts/make_favicon.py` writes the browser icons into `web/`: `favicon.ico` (16/32/48),
`icon-192.png`, `icon-512.png` and `apple-touch-icon.png`. It is deliberately not the cover
downscaled. The cover is a 36-orb honeycomb with a wordmark across it, tuned for the 55px Apple
Podcasts renders it at, and at the 16px a browser tab renders it at both the pattern and the
wordmark collapse into a grey square. So the favicon is a single orb, the same shape `web/orb.js`
paints beside every voice, in the green run, with its palette and geometry imported from
`make_cover.py` rather than copied. Same Pillow story: `dev` group, never in the container.

## Run it

```bash
make install
```

That runs `uv sync`: it provisions Python 3.12, builds `.venv`, and installs exactly what
`uv.lock` pins -- removing anything installed that the lock does not list. Then it copies
`sample.env` to `.env` if you do not have one. Put your key in it:

```
DEEPGRAM_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here   # only for --writer claude
```

Then:

```bash
make start        # http://localhost:8000
```

`make help` lists every entry point. The Makefile is the authoritative list, so check there before
writing a one-off command.

Dependencies are locked, and everything goes through [uv](https://docs.astral.sh/uv/) --
`brew install uv` is the one prerequisite the Makefile checks for. `pyproject.toml` is the
human-edited source and `uv.lock` is generated from it by `make lock`. Edit the `pyproject`, run
`make lock`, commit both. `make upgrade` is the separate, deliberate step that takes newer versions
and re-runs the suite.

`uv.lock` is universal by construction: one file, correct on both macOS arm64 and the linux amd64
image, with platform markers where they differ. The container installs from the same lock with
`uv sync --frozen --no-dev`, so a stale lock fails the build instead of quietly re-resolving, and
pytest never ships. The test suite checks that every locked package carries an exact version, that
the declared dependencies stay unpinned, that the lock targets the same interpreter as
`.python-version` and the Dockerfile, that your installed venv actually matches the lock, and that
the image still passes `--frozen --no-dev`.

## Making an episode

```bash
make episode                                                   # today, Makers edition
uv run python -m hn_radio --edition ai
uv run python -m hn_radio --edition frontpage --writer claude --minutes 6
uv run python -m hn_radio --date 2026-08-03               # a past day, via Algolia
```

Four editions: `makers` (the default, which promotes personal repos, blogs, and Show HN posts
while demoting product launches), `ai`, `security`, and `frontpage` (straight popularity, no skew).
An edition reweights story selection and nothing else. It used to decide which themed desk led the
show as well; the desks are gone, so the topic keyword tables that drive the skew now live in
`editions.TOPICS`, next to the two things that actually read them.

Two writers. `panel` is deterministic and offline, built from extractive summaries of the linked
sources. `claude` is the LLM version, Opus 5 by default, which produces actual banter and is what
the daily cron uses.

Output lands in `episodes/<episode-id>/`: `episode.mp3` (chaptered), `episode.wav`, `chapters.json`,
`episode.json`, `script.json`, `transcript.vtt`, and per-segment PCM under `segments/`. Every run
then rebuilds the three site-wide files beside them, `feed.xml`, `index.json` and `voices.json`,
because an episode no feed and no page can see is not published. There is no generated HTML per
episode; `web/episode.html` renders one from the JSON, which is the only implementation.

Three stories per episode, not four. Each gets a take plus one real follow-up, which is worth more
than a fourth story skimmed.

### The scheduled show, and replacing a bad episode

The cron runs `scripts/daily.py` at 3am and 3pm Pacific. Each run covers the 18 hours before it
started (`config.LOOKBACK_HOURS`), so consecutive shows overlap by six hours on purpose; stories the
previous episode actually covered are dropped from the pool, so the overlap never airs the same lead
twice. Scheduled episodes are named by air date and slot, `2026-09-05-am` and `2026-09-05-pm`; the
bare-date ids from before the change stay as they are.

Every scheduled take is verified before it is published (`hn_radio/verify.py`). Unspeakable text in
the script, meaning URLs, markdown or HTML, fails the take before the Flux render is paid for. A
stitched episode under `config.MIN_EPISODE_SECONDS` (five minutes) fails it after the stitch and
before publish, so a short take never reaches the feed. A rejected take is re-run once with a fresh
writer. If both takes fail, nothing is published, the feed keeps serving the previous episode, and
the alert says why. This exists because the 2026-09-03 episode shipped at 173 seconds: the Claude
writer had failed, `PanelWriter` covered as designed, and the fallback read a markdown image tag and
an S3 URL aloud. Every stage had succeeded and nothing had asked whether the result was a show.

To replace an episode that is already on the feed, run the same script against that day. It uses
the Claude writer, the same floor and the same re-run, and writes over the same id, so the rebuilt
feed carries the new audio under the same guid:

```bash
# locally, into episodes/
uv run python scripts/daily.py --date 2026-09-03

# on the deployed machine, into the Fly volume (about four minutes)
fly ssh console -a dg-devrel-hn-radio -C "sh -c 'cd /app && /app/.venv/bin/python scripts/daily.py --date 2026-09-03'"
```

Two things to know. Podcast apps that already downloaded the old file may keep it, because the guid
did not change; the website and new subscribers get the new audio at once. And the re-made episode
gets a fresh `generated_at`, which the feed reads as `pubDate`, so it sorts to the top until the
next scheduled episode lands. The co-host is the same as the original render, because the rotation
is seeded by the episode id. Do not use `scripts/backfill.py` for this: it uses the deterministic
`panel` writer with no verification, which is the show that needed replacing.

Running `scripts/daily.py` with no arguments makes the scheduled episode for right now, which is
how to get one without waiting for the cron.

## The pipeline

```
ingest -> select -> cast -> source -> write -> normalize -> voices ->
render -> [cache] -> pace -> music -> stitch -> chapters -> publish
```

| Module | What it does |
|--------|--------------|
| `ingest.py` | Top stories and comments from the HN Firebase API. Past dates come from Algolia HN Search instead |
| `editions.py` | Reweights the pool by edition and picks the stories that make the show |
| `cast.py` | Casting, two seats. The host resolves against an ordered list of **characters**, so an unavailable voice becomes an in-fiction line ("Alexis is out today") instead of a silent swap. The co-host has no such list on purpose: its candidates are computed per episode from the live catalog, rotated by a hash of the episode id, and held back for `COHOST_RECENCY_WINDOW` episodes after a turn. One voice cannot hold both seats |
| `sources.py` | Reads what each story actually links to (GitHub README via the API, otherwise the page text) and makes an extractive summary, so the two regulars have something real to say |
| `writers.py` | `PanelWriter` (deterministic) or `ClaudeWriter` (Opus 5). An LLM failure falls back to the deterministic writer so the show still ships |
| `normalize.py` | Abbreviation expansion on the input text (HN becomes Hacker News). Nothing phonetic |
| `voices.py` | Assigns a voice per line from the episode's cast. Performed comments carry a **regular's** voice, pinned by the writer, while keeping the commenter's real username and comment id on the line. `guest_voice_for`, which hashed a username into a separate guest pool so a regular commenter kept their voice between episodes, was deleted on 2026-08-22: the claim here that recast and custom editions still offered a guest voice was false, since `recast.ROLES` is anchor and cohost only. The `GUEST_VOICES` pool itself is still live as the recast picker's guest preset |
| `render.py` | One batch `/v2/speak` call per segment, returning raw PCM |
| `pacing.py` | How much air sits between two lines. A gap per boundary type, chosen from the script's own structure, after normalizing away the silence Flux bakes into each segment's edges |
| `music.py` | The theme: an intro cue, a sting at each story's first mention, a bed over the cold open. Levels are set relative to the episode's own speech, never as a fixed gain. Set `HN_RADIO_MUSIC=0` (or pass `--no-music`) to render speech only |
| `stitch.py` | Concatenates the PCM with the per-boundary gaps and writes one correct WAV header |
| `chapters.py` | Derives chapters from the script, writes `chapters.json`, and bakes ID3 CHAP frames into the MP3 with ffmpeg |
| `publish.py` | A table of contents: what happens when. Knows the order, not the formats |
| `feed.py` | RSS 2.0, plus the show notes that go inside it as the item description |
| `manifest.py` | The JSON `web/` reads: the episode list and the voice catalog. With no build step, these two files *are* the frontend's API |
| `transcript.py` | WebVTT, built from the start times `pacing` and `music` computed |
| `jsonio.py` | The one way a JSON artifact gets written, so `episode.json` reads the same whichever tool touched it last |

Every segment's raw PCM is cached on disk. Recasting an episode into different voices, or building
a custom one out of past episodes, re-renders only the lines whose words or voice actually changed
and byte-copies the rest.

## The web app

`backend/app.py` is one small FastAPI service. It serves the static site from `web/`, serves
`episodes/` as the catalog, and holds the Deepgram key server-side so the browser never sees it.

- `web/index.html` is the episode list, the subscribe row, and a live status board.
- `web/episode.html` is the player: sticky console, waveform, chapter dots, and the full script
  scrolling along with the audio. Its second tab is the recast picker, which offers two roles,
  Showrunner and Guest host, over the Flux catalog; a voice taken by one is marked taken and
  refused to the other.
- `web/build.html` is the desk picker, and it is the one place desks survive. The daily show has
  none; a build-your-own edition seats them deliberately, because picking a desk is how a listener
  casts a voice AND filters the topic at once, so the thing picked has to be a subject. Choose the
  desks, cast a voice on each, pick how far back to draw stories from, and build an episode on
  demand. It runs on three routes:
  `GET /api/build/pool` (candidate stories, each tagged as cheap-to-reuse or needs-rendering),
  `POST /api/build/plan` (prices the build in reuse / re-render / new segments, before you commit),
  and `POST /api/build` (actually renders it).

- `web/stats.html` is the play counter's board, linked from a footer on the landing page.

The rest of the API is `POST /api/recast`, `GET /api/status`, `GET /api/trending`,
`POST /api/plays`, `GET /api/stats`, and `GET /api/health`. (`POST /api/generate` was deleted on
2026-08-22: no caller anywhere, and `make episode` and `scripts/daily.py` already reach the same
pipeline.)

When a scheduled run fails you find out, which was not previously true: `status.error()` existed and
nothing called it, and `scripts/daily.py` had no exception handling, so a run that died mid-render left
`{"state": "rendering"}` on disk and the page showed a spinner forever. Two failure shapes need
different machinery. A **crash** raises, so `daily.py` catches it, records it with `status.error`,
pushes an alert, and exits non-zero. A **hang** raises nothing -- a socket with no timeout, a wedged
ffmpeg -- so nothing can catch it; all it leaves is silence. `status.is_stalled` infers it from that
silence on read, `/api/status` reports `stalled` as derived rather than stored, and the board says
"Run stalled" with a square marker rather than a coloured dot. The next scheduled run also reports a
previous run that never reached `done` or `error`, which is the only active notice a hang ever gets,
because a daily job has no process alive in between to hold a watchdog.
A **bad take** is the third shape: every stage succeeds and the result is still not a show. That one
is caught by the verification pass described under "Making an episode" and re-run once.

Two alert channels, both optional, both read with a bare `os.environ.get` (unlike the keys), so a
Fly secret or a shell export reaches them and a line in `.env` does not. `HN_RADIO_ALERT_WEBHOOK`
posts `{"text": "..."}`, which Slack and Discord incoming webhooks accept as-is.
`HN_RADIO_PUSHOVER_TOKEN` plus `HN_RADIO_PUSHOVER_USER` (the application token and the user key,
both required) posts to Pushover's `messages.json` with the title "HN Radio". Every configured
channel gets every alert. With nothing set, alerting is a logged no-op, which is the right default
for a repo strangers clone; with half a Pushover pair set, the log names the missing half.
`HN_RADIO_STALL_SECONDS` tunes the stall threshold, default 900; it is generous because the Claude
writer and the MP3 transcode are both legitimately silent for a while.

The two endpoints that render -- `/api/recast` and `/api/build` -- are guarded by
`backend/limits.py`. One render runs at a time process-wide, and a caller gets 3 per hour; both
refusals are a 429 with `Retry-After`. This is availability before cost: each render is one Claude
call plus ~20 Flux calls held in memory on the same `shared-cpu-1x` machine that serves the site, so
two concurrent builds were an out-of-memory kill of the page, not just a bigger bill. Cost matters
too, because the deployed app holds the keys server-side on purpose -- that is what makes the build
page one click instead of a signup, and it means public callers spend ours. Tune with
`HN_RADIO_RENDER_LIMIT` and `HN_RADIO_RENDER_WINDOW`. `/api/build/plan` is deliberately not
throttled: it prices a build without spending anything.

**Play counts, and the boundary on what they mean.** `hn_radio/plays.py` appends one line per
event to `episodes/plays.jsonl` -- a timestamp, an episode id, and one of `view`, `play`, or a
`progress` mark at 25/50/75/100. The rollup at `GET /api/stats` is derived from that file on every
read, never stored, so no counter can drift out of sync with the events behind it. It rotates into
`plays-totals.json` past 5MB and the totals survive the roll.

**It counts the site, not the world.** A podcast client that pulled `episode.mp3` out of the feed
never loaded a page and never ran `web/plays.js`, so it is invisible here and always will be.
Catching those would take byte-range hits on the audio out of an access log the Fly proxy does not
retain. `stats.html` says so above its first number, which is the difference between a stats page
and a misleading one.

Nothing identifying is stored: no IP, no user agent, no cookie, no visitor id. "One play per
listener" is worked out in the browser with `sessionStorage`, specifically so the server never needs
an identity to avoid double-counting. Two tabs count twice and tomorrow counts again, which for a
daily show is arguably the more honest number.

`POST /api/plays` validates the episode id against what is on disk before writing -- otherwise it is
an append-anything primitive on the volume, and a junk id is a permanent key in the rollup. Past
that gate the write is best-effort like `status.py`: a read-only volume or a full disk is still a
202, because a listener pressing play must never see an error from a counter. It has its own per-IP
window (`HN_RADIO_PLAYS_LIMIT`, default 60 per `HN_RADIO_PLAYS_WINDOW`, default 60s) and
deliberately **not** the render slot, which is held for the whole five minutes a Flux render takes.

`HN_RADIO_MUSIC=0` renders speech only: no intro cue, no stings, no cold-open bed. It exists so
shipping the code and shipping the music are two decisions instead of one. `_finalize` had taken a
`with_music` argument since the music landed, but nothing outside the process could reach it, so
every deploy of unrelated work waited on the beds being finished. Anything other than
`0`/`false`/`no`/`off` reads as on, deliberately: an unwanted bed is obvious to the first listener,
while a typo'd value silently shipping bare speech sounds like an ordinary podcast and could run for
days unnoticed. `--music` / `--no-music` overrides it for one run.

## Design notes

**Batch, not streaming.** Episodes are produced hours before anyone listens, so there is no latency
budget to spend. This is the one demo that exercises the Flux HTTP batch path end to end.

**No cross-turn voice claims.** Batch REST is stateless and each segment is an independent call.
Cross-turn voice consistency is a streaming-only capability, and this app does not have it and does
not claim it. What it does show is expressiveness, stable voice assignment per speaker, and entity
accuracy on the worst corpus available (HN comments, full of version numbers and tool names).

**Raw HTTP, not the SDK.** The published `deepgram-sdk` exposes Speak V2 only as a streaming
WebSocket client, with no batch REST path at all. Deepgram documents Flux batch as a raw request,
and the official `fastapi-flux` starter proxies it over raw protocol too. Since HN Radio is
deliberately batch, raw HTTP is the conformant choice here. The SDK would only apply if a streaming
feature were added.

**Headerless `linear16`, not `container=wav`.** The batch WAV container returns a placeholder
~2 GB data-length header (it reuses the streaming writer). Requesting `container=none` means the
response body is the PCM buffer, so stitching is byte concatenation and we write one honest header
at the end.

**The cold open is one TTS call.** Splitting the headline preview into a segment per story put the
gaps `stitch` inserts between renders inside the read, which measured about 2.9s of dead air around
3.5s of speech, and gave each headline its own onset and its own sentence-final fall. Re-splitting it
is the one repair known not to work. Evening out the silence Flux leaves *inside* the single render
is the one that does, and `pacing.COLD_OPEN_PAUSE_SECONDS` sets those to a flat 0.55s each.

**44.1 kHz MP3.** Flux returns 24 kHz, and libmp3lame at 24 kHz emits MPEG-2 Layer III. That is
legal MP3 and outside what Apple Podcasts and friends expect, which shows up as an episode that
lists in a player and then refuses to play. Resampling to 44.1 kHz emits MPEG-1 Layer III instead.

**Two regular voices, not seven, and one of them changes.** Listeners reliably track about three
speakers; earlier episodes ran six or seven and it cost comprehension. Three fixed personas fixed
that and bought a different problem: a demo for a thirty-four-voice catalog could only ever exhibit
the host plus three fixed correspondents. So the show seats two, and re-casts the second chair every
episode from the whole catalog. The separate comment voice went with the desks, because the
reason for one was that the regulars were each busy being a subject; two people who cover everything
can read a stranger's words and say whose they are first. Catalog breadth is demonstrated ACROSS
episodes rather than inside one, which is also how a real show introduces a cast.

**A rotation seeded on the episode id, not on a counter.** Re-rendering a failed episode has to be
the same show, so who sits in the second chair is a function of the date rather than of how many
episodes have aired. The no-repeat window is read off the episodes directory, which is already the
record of what went out, so there is no state file to keep in step with reality.

**Casting resolves against the live catalog.** The host's role holds an ordered list of characters,
and whichever voices the configured API host actually serves decides who gets it. When a preferred
character is unavailable the substitute says so on air. That is honest, it needs no dated
conditional to unwind when access changes, and it turns the most persistent failure mode in this
repo -- code assuming a voice exists -- into something the show states out loud.

**No hidden pronunciation overrides.** `normalize.py` expands abbreviations and stops there. No
phonetic respelling, so what the page displays is exactly what the audio says. The repo is meant to
be read.

## Tests

```bash
make test
```

No network and no API key required. They cover the pipeline stages, the API routes, the feed, the
interpreter pin, and the dependency lock.

The count is deliberately not written down here. It moved on most of the commits that built this
repo, so a number in this file is a claim that goes stale silently. It is at least the same number
everywhere now: the suite reports **zero skips on any checkout**. It used to report one, because
`tests/test_music.py` re-checked its level invariant against the cached takes of the 2026-08-08
episode and `episodes/` is gitignored; those takes are committed under `tests/golden/music/` as of
`172922c`. Ask the suite for the number anyway:

```bash
uv run pytest -q --collect-only | tail -1
```

Some are **characterization tests**: `tests/test_publish_characterization.py` pins the exact bytes of
the feed, the manifests and the transcript against golden files in `tests/golden/`. A failure there
is not automatically a regression, it means output moved and you have to say whether that was
intended. To accept a deliberate change:

```bash
HN_RADIO_UPDATE_GOLDEN=1 make test
git diff tests/golden/          # read this before committing
```

An unread golden update is worse than no test, because it launders a change into a green suite.

## Deploy

See [`DEPLOY.md`](DEPLOY.md). Short version: it is a `python:3.12-slim` image with the uv binary
copied in, dependencies installed by `uv sync --frozen --no-dev` from the committed lock, running
uvicorn on Fly, with the Deepgram key as a Fly secret, episodes on a mounted volume, and a
`CRON_TZ`-aware cron entry that runs `scripts/daily.py` at 3am and 3pm Pacific year-round.

## Working on it

**Start here:** [`docs/glossary.md`](docs/glossary.md) for the vocabulary, then this file's
pipeline table for where the code lives.

A few conventions that are load-bearing rather than stylistic:

- **`hn_radio/` is stdlib-only**, apart from a lazy `anthropic` import inside `ClaudeWriter.write`.
  ffmpeg is a subprocess. The web layer adds FastAPI and uvicorn; the pipeline itself does not.
  That lazy import makes `anthropic` look optional and it is not: `scripts/daily.py` imports
  `ClaudeWriter` at module scope and the 3am/3pm cron runs it, so `anthropic` is a declared **runtime**
  dependency. Filing it under the dev group would build a healthy-looking image whose only symptom
  is that the podcast stops appearing overnight.
- **`web/` has no build step.** No bundler, no `package.json`, no framework. Pages load native ES
  modules with `<script type="module">`. That is deliberate: a reader should be able to open one
  file and see how a Flux request becomes audio in a player, without framework noise in the way.
- **Comments explain WHY, not what.** Several read like a paragraph because they record a decision
  and what it cost. If a comment tells you a rule and the rule looks wrong, read the whole comment
  before deleting either.
- **The per-line PCM cache is an archive.** `episodes/<id>/segments/*.pcm` holds exactly what the
  renderer returned. That is why pacing, music and sting placement could each be A/B'd against past
  episodes for **zero API calls**, and why the cache stores raw audio rather than the finished mix.
  If you are about to evaluate anything about timing or music, rebuild from cache; do not re-render.
- **The catalog is data, not an assumption.** `config.VOICE_CATALOG` is the castable set and
  `config.RETIRED_VOICES` is the by-ear veto on top of it. Nothing downstream hardcodes a voice
  id: `resolve_role` walks a preference list against whatever the catalog currently holds and
  reports who it could not seat, so a voice leaving the catalog degrades the show by one
  substitution and an in-fiction line rather than failing the render.

Before opening a PR:

```bash
make test          # no network, no key needed
make check         # asserts .venv is on the pinned interpreter
```

`make check` only checks the interpreter. The dependency lock is checked by the suite:
`tests/test_dependency_lock.py` asserts the lock is exactly pinned, that it targets the same
Python as `.python-version` and the Dockerfile, that your installed venv matches it, and that the
image still installs with `--frozen --no-dev`.

If you changed anything that emits a file a listener or a podcast app consumes, expect a
characterization test to fail. Read the golden diff and say whether the change was intended.

## Where the rest of it lives

[`DEPLOY.md`](DEPLOY.md) is the runbook: Fly, the secrets, the volume and the cron.

[`docs/glossary.md`](docs/glossary.md) is the vocabulary: what a desk, a role, a voice and an
edition mean here, and which of them are written into files on disk rather than chosen at render
time.

The comments are the third document. Several read like a paragraph because they record a decision
and what it cost rather than restating the line below them. If a comment states a rule and the
rule looks wrong, it is worth reading the whole comment before deleting either.
