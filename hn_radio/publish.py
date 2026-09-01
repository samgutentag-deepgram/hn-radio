"""Stage 5 - Publish. Write one episode's data files, and rebuild the site-wide artifacts.

This module is a table of contents, not an implementation. It knows the ORDER things happen in
and nothing about the formats:

    feed        RSS 2.0 + the show notes inside it       hn_radio/feed.py
    manifest    the JSON web/ reads (episodes, voices)   hn_radio/manifest.py
    transcript  WebVTT, from the script's start times    hn_radio/transcript.py
    jsonio      how any JSON artifact gets written       hn_radio/jsonio.py

It used to be all three of those plus a full HTML player page, at 568 lines. The page was removed
(nothing linked to it; see `feed.py` for the 404 that hid behind it), and the formats moved out
into modules small enough to read in one sitting. That is the point: someone following
"how do I add voice to my app" should be able to open any one of these and finish it.

Nothing here renders HTML. Rendering an episode for a human is `web/`'s job.
"""

from __future__ import annotations

from pathlib import Path

from . import feed, manifest, transcript
from .jsonio import write_json
from .models import Episode

# The one deliberate re-export: `pipeline` and `add_chapters` both write transcript.vtt through
# it, and "the transcript is part of publishing" stays true even though the formatting moved out.
# The other four formatters were re-exported too, briefly, on the theory that call sites depended
# on them -- a grep says no non-test caller ever did, so they were forwarders that only made every
# format function answer to two names.
build_vtt = transcript.build_vtt


def publish(episode: Episode, out_dir: Path) -> dict:
    """Write the data files for ONE episode, then refresh the site. Returns the paths written.

    Site-wide refresh, not just the feed. This function always reached up to `out_dir.parent` to
    rewrite `feed.xml` for the whole catalogue, so it was already doing a site-wide job -- it just
    did one third of one. `index.json` and `voices.json` are the other two, and with no build step
    in `web/` those two files ARE the frontend's API: an episode absent from them does not exist
    as far as the site is concerned.

    Every caller that wanted the other two thirds asked for them by hand. `backend/app.py`'s
    startup hook, `scripts/daily.py`, `scripts/backfill.py`, `scripts/add_chapters.py` and
    `scripts/build_site.py` each call `rebuild_site` themselves; `hn_radio/__main__.py` did not.
    So `python -m hn_radio` published an episode into every subscribed podcast app and left the
    web app showing yesterday's list, and `scripts/local_episode.py` carried a hand-written
    `rebuild_site` call to paper over it. Doing the whole job here fixes every present caller and
    every future one, instead of adding a sixth place to remember.

    Calling `rebuild_site` twice (here, and again in a caller that has not dropped its own call)
    is wasteful but harmless: all three artifacts are pure functions of what is on disk.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    episode_json = out_dir / "episode.json"
    script_json = out_dir / "script.json"

    # This episode's own files FIRST. `rebuild_site` enumerates `*/episode.json` on disk, so it
    # can only see this episode once that file exists.
    write_json(episode_json, episode.to_dict())
    write_json(script_json, [s.to_dict() for s in episode.segments])

    return {
        "episode_json": str(episode_json),
        "script_json": str(script_json),
        **rebuild_site(out_dir.parent),
    }


def rebuild_site(episodes_dir: Path) -> dict:
    """Regenerate every site-wide artifact from what is on disk. Returns the paths written.

    Exists because five callers -- the app's startup hook, the daily cron, the backfill script,
    the chapter backfill, and build_site.py -- each called `rebuild_feed`, `build_manifest` and
    `build_voices_json` in a row. Four listed all three; one listed only two, and it was not
    obvious whether that was a decision or an omission. One entry point makes "refresh the site"
    a single idea, and adding a fourth artifact later means editing one place instead of five.

    All three are pure functions of the episodes directory: no network, no Deepgram key, no
    rendering. Safe to call on every boot, which is what the app does.

    `episodes_dir` is required, not defaulted to `config.EPISODES_DIR`. Every caller already passes
    it explicitly, and a hidden config read inside a function documented as a pure function of its
    argument is the kind of second path that only shows up when a test writes to the real tree.
    """
    return {
        "feed_xml": str(feed.rebuild_feed(episodes_dir)),
        "index_json": str(manifest.build_manifest(episodes_dir)),
        "voices_json": str(manifest.build_voices_json(episodes_dir)),
    }
