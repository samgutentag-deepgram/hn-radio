"""The orchestrator: wires the stages into one run.

    ingest -> select -> cast -> source -> write -> normalize -> voices ->
    render -> pace -> music -> [cache] -> stitch -> chapters -> publish

Thirteen stages plus one unnumbered disk write. Three functions in sequence are the whole show:
`run_panel` fetches, selects, casts, sources and writes; `render_panel` turns a finished script
into audio; `_finalize` is the shared tail that paces, scores, caches, stitches, chapters and
publishes.

Note the order. The show is CAST BEFORE its sources are fetched. That used to matter because
routing read titles and domains; it no longer reads anything at all (the co-host rotation is a
function of the episode id), so casting could move anywhere. It stays here because the log line
that names today's two regulars is the first useful thing a run prints.

Each stage is a plain function taking data and returning data, so this file reads as the
whole pipeline at a glance and any stage can be swapped or tested in isolation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from . import (config, editions, ingest, music, normalize, pacing, render, sources, status,
               stitch, voices)
from .cast import episode_cast as episode_cast_for
from .editions import DEFAULT_EDITION, EDITION_TITLES
from .models import Episode, ScriptSegment
from .writers import (ClaudeWriter, PanelWriter, ScriptWriter, cold_open_index,
                      cold_open_pause_count)

SELECTION_POOL = 30  # stories pulled before an edition narrows to n


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _panel_title(edition: str, stories, top_story, episode_date: date) -> str:
    edition_name = EDITION_TITLES.get(edition, "Front Page")
    headline = top_story.title if top_story is not None else stories[0].title
    return f"HN Radio {edition_name} - {episode_date.strftime('%b %-d')}: {headline}"


# The show's fixed signature: the same intro + outro on every episode, so it feels coherent over
# time. The writer only handles the middle (the day's stories + comments), and is told in the
# prompt NOT to write a greeting or a sign-off, or the show would open twice.
#
# Both lines name people, which is why they live here and not in the writers. The host introduces
# HERSELF (the show named Deepgram from the start but the host never named herself, which is odd
# for a person talking to you every morning), and she introduces TODAY'S co-host, who is a
# different voice most days -- so the copy has to be built from the episode cast, and only the
# pipeline holds that. Both are single segments and must stay that way: the merged cold open the
# writers now produce follows this line, and `music.BED_SEGMENTS = 2` lays the ambient bed across
# exactly those two.
# THE DATE IS THE AIR DATE, NOT THE EPISODE DATE, and the difference is a bug Sam caught by ear on
# 2026-08-23: the episode that landed that morning opened with "It's Saturday, August 22".
#
# `episode_date` is the CONTENT date and is deliberately yesterday -- `scripts/daily.py:57` asks for
# `now(PACIFIC) - 1 day` because a complete day of front page is the whole point, and
# `run_panel` fetches that day's stories. The intro then spoke that same date as if it were today,
# so every episode ever published has been a day behind on air while being correct about its
# content. Nobody noticed for nineteen episodes because the date sounded plausible.
#
# So the copy now names both, and neither is relative to when you happen to press play: the air
# date is spoken outright, and the stories are framed as yesterday's relative to THAT. A listener
# hearing it a week later still hears an internally consistent episode, which is what an archive
# needs.
_INTRO = ("Hi, this is {host}. You're listening to Hacker News Radio, from Deepgram Flux. It's "
          "{date}, and I have {cohost} with me. Here's what happened on Hacker News yesterday.")
_OUTRO = ("That's yesterday's front page. From me and {cohost}, on Deepgram Flux, we'll talk "
          "to you tomorrow.")


# THE SOLO-EPISODE VARIANTS ARE GONE, deleted 2026-08-22. There used to be a `_cohost_name`
# helper here plus a solo branch in each of these two functions, on the stated grounds that
# `custom.py` builds casts from a listener's picks and could hand over one with no co-host. That
# justification was false twice over, and both halves were checked before this was removed:
#
#   - `custom.py` never reaches these functions. It formats its own `_INTRO`/`_OUTRO` (`:399-401`,
#     used at `:511-514`), so a listener-built cast has never taken this path.
#   - `custom.validate` raises `ConfigError("Pick at least one desk, ...")` on an empty desks map
#     (`custom.py:91-92`), so it cannot produce a cast with no second seat in the first place.
#
# Every caller hands over a cast that seats two: `run_panel` builds one with `episode_cast`, which
# always returns `desks=[cohost]`, `DEFAULT_CAST` seats Wade, and `scripts/frame_experiment.py`
# passes a built cast. An exhaustive resolve over every eligible catalog id found no anchor and
# co-host sharing a name on either host, so the equal-names case the solo branch existed for was
# not reachable either.
#
# The one case that IS constructible is a `cast_from_config` where a listener gives the anchor and
# a desk the same voice. If that ever needs handling, it wants an explicit `raise`, not a silent
# rewrite into a solo show: quietly dropping the second person is how a cast bug ships as a
# stylistic choice.


def air_date_for(episode_date: date) -> date:
    """The day this episode goes out: the day after the front page it covers.

    Derived rather than read off the clock, on purpose. `date.today()` here would make the script
    non-reproducible -- a re-render of an old episode would re-date it to the day of the re-render,
    which is exactly the trap the archive re-render already hit once with `generated_at`. Deriving
    it keeps a backfilled or re-rendered episode saying what it would have said had it aired on
    time, so the archive stays internally consistent.

    Known edge: `python -m hn_radio` with no `--date` uses today as the episode date, so this
    returns tomorrow. That path renders a PARTIAL day of front page and is an operator preview, not
    a published episode; the cron is the thing that airs, and it passes yesterday.
    """
    return episode_date + timedelta(days=1)


def _intro_segments(cast, episode_date: date) -> List[ScriptSegment]:
    text = _INTRO.format(host=cast.anchor.name, cohost=cast.cohost.name,
                         date=air_date_for(episode_date).strftime("%A, %B %-d"))
    return [ScriptSegment(order=0, role="anchor", speaker_key=cast.anchor.name, desk="anchor",
                          text=text)]


def _outro_segments(cast) -> List[ScriptSegment]:
    return [ScriptSegment(order=0, role="anchor", speaker_key=cast.anchor.name, desk="anchor",
                          text=_OUTRO.format(cohost=cast.cohost.name))]


def run_panel(
    edition: str = DEFAULT_EDITION,
    n_stories: int = config.N_STORIES,
    n_comments: int = config.N_COMMENTS,
    episode_date: Optional[date] = None,
    writer: Optional[ScriptWriter] = None,
    with_music: Optional[bool] = None,
    log=print,
) -> Episode:
    """The automated panel pipeline: fetch -> select by edition -> source -> write -> render.

    `with_music=None` defers to `config.music_enabled()`; True or False overrides it for this run.
    """
    episode_date = episode_date or date.today()
    writer = writer or PanelWriter()
    status.begin(episode_date.isoformat(), edition)

    log(f"[1/6] Fetch: {SELECTION_POOL} stories for {episode_date.isoformat()} ({edition})...")
    status.stage("fetching", "pulling the front page")
    pool = ingest.fetch_front_page_for_date(episode_date, SELECTION_POOL)
    # Selection no longer needs a cast. It used to build one just to reach `cast.score_for` for
    # the edition keyword match; those keywords live in `editions` now, and the episode cast no
    # longer reads the stories either, so the two stages are finally independent.
    selected = editions.select_stories(pool, edition, n_stories)
    # `run_panel` builds the cast itself, always. The `cast=` parameter this used to accept was
    # removed 2026-08-22: nine call sites repo-wide, none passed it, so the guarded branch was
    # unreachable. `render_recast` and `render_custom` are called directly and never come through
    # here, which is why nothing needed the seam.
    #
    # `before` does two jobs: it makes "held the second chair recently" relative to the episode
    # being generated (a backfill must not treat later episodes as recent, and a re-render of
    # today must not read its own previous script), and it seeds the co-host rotation, so the
    # same date always casts the same co-host.
    cast, substitutions = episode_cast_for(before=episode_date.isoformat())
    names = ", ".join(d.name for d in [cast.anchor] + list(cast.desks))
    log(f"      cast: {names}"
        + (f" (covering for {', '.join(substitutions.values())})" if substitutions else ""))
    if isinstance(writer, ClaudeWriter) and not writer._caller_set_substitutions:
        writer.substitutions = substitutions
    ingest.populate_kids(selected)  # Algolia (past-date) stories lack kids; fetch for comment theater
    log(f"      selected {len(selected)}: " + "; ".join(f"{s.title[:40]}" for s in selected))

    log("[2/6] Source: fetching and summarizing each linked source...")
    status.stage("sourcing", "reading the linked sources")
    for s in selected:
        sources.enrich_story(s)
        log(f"      [{s.source_kind:7}] {s.title[:50]}")

    log("[3/6] Comments: pulling the busiest selected thread...")
    top = ingest.pick_top_thread(selected)
    comments = ingest.fetch_top_comments(top, n_comments) if top else []
    log(f"      top thread {'#' + str(top.id) if top else '(none)'}, {len(comments)} comments")

    log(f"[4/6] Write: {type(writer).__name__} assembling the panel script...")
    status.stage("writing", "writing the panel script")
    try:
        segments = writer.write(selected, top, comments, cast, edition, episode_date)
    except Exception as e:  # LLM writer refusal / API / parse failure -> keep the show on air
        if isinstance(writer, PanelWriter):
            raise
        log(f"      [warn] {type(writer).__name__} failed ({e}); falling back to PanelWriter")
        segments = PanelWriter().write(selected, top, comments, cast, edition, episode_date)
    log(f"      {len(segments)} segments")

    # Wrap the writer's content in the fixed show intro + outro, then renumber.
    segments = _intro_segments(cast, episode_date) + segments + _outro_segments(cast)
    for i, seg in enumerate(segments):
        seg.order = i

    title = writer.episode_title() or _panel_title(edition, selected, top, episode_date)
    summary = writer.episode_summary() or ""
    # `points` is recorded so later features can rank a past episode's stories against fresh ones.
    # Without it the custom-episode picker scored every cached story as 0 and always lost to live.
    # `author` rides along so the show notes can print a byline. It is deliberately NOT given to
    # the writer: `writers.py:139` and its pinning test record the decision that the desk says
    # nothing rather than mis-attributing a submitter as an author. Metadata, not the desk speaking.
    source_items = [{"hn_id": s.id, "title": s.title, "url": s.url, "points": s.points,
                     "author": s.author}
                    for s in selected]
    # 1-per-day naming: the daily front-page episode is just YYYY-MM-DD; other editions keep a suffix.
    episode_id = episode_date.isoformat() if edition == "frontpage" else f"{episode_date.isoformat()}-{edition}"
    log(f"[5/6] Render + [6/6] Publish: {episode_id}")
    return render_panel(segments, episode_id=episode_id, title=title, source_items=source_items,
                        cast=cast, edition=edition, summary=summary, with_music=with_music, log=log)


def render_panel(
    segments: List[ScriptSegment],
    *,
    episode_id: str,
    title: str,
    source_items: List[dict],
    cast,
    edition: str = "makers",
    summary: str = "",
    with_music: Optional[bool] = None,
    log=print,
) -> Episode:
    """Reusable back-half: voices -> render -> stitch -> publish for a pre-written script.

    Fed by the in-session writer today and the Claude writer once the key lands. The script
    (segments) already exists; this turns it into a rendered, published episode.
    """
    normalize.normalize_segments(segments)  # HN -> Hacker News, etc. (display == audio; idempotent)

    log(f"[voices] assigning {len(segments)} segments to cast voices...")
    voices.assign_voices(segments, cast)

    log("[render] rendering each segment via Deepgram batch...")
    pcm = render.render_all(segments, config.get_api_key(),
                            on_progress=lambda i, n: status.stage("rendering", f"rendering segment {i}/{n}", i, n))
    log(f"         {sum(len(p) for p in pcm) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH):.1f}s of audio")

    return _finalize(segments, pcm, episode_id=episode_id, title=title,
                     source_items=source_items, edition=edition, summary=summary,
                     with_music=with_music, log=log)


def _space_cold_open(segments, pcm, log=print):
    """Return a copy of `pcm` with the cold open's headline pauses set to one fixed length.

    The other half of the cold-open fix. The writers merged the preview into ONE segment so it is
    ONE TTS call, which killed the ~2.9s holes between headlines; what that leaves is Flux running
    the sentences together (0.09 / 0.13 / 0.22s of silence inside the real 2026-08-20 read). This
    evens those boundaries out to `pacing.COLD_OPEN_PAUSE_SECONDS` each. Splitting the segment
    back up is the one repair known NOT to work; see the note above `writers.cold_open_index`.

    Lives here, in the wiring, rather than inside `pacing.apply`, for two reasons. `pacing.apply`
    is a pure function of segments plus audio and is called by the experiment scripts that chose
    these numbers, so a hidden pass in there would silently override the variant under test. And
    the cold open is a SCRIPT concept: which segment it is and how many sentences it has both come
    from `writers`, which is a dependency the pacing module deliberately does not have.

    Never mutates `pcm`. `_finalize` has already written that list to the per-segment cache as the
    episode's archive, and a later rebuild must start from what the renderer actually returned.
    """
    takes = list(pcm)
    i = cold_open_index(segments)
    if i is None or i >= len(takes):
        return takes  # no stories, no preview: custom.py can reach here with an empty pick
    pauses = cold_open_pause_count(segments[i].text)
    if pauses < 1:
        return takes  # a one-sentence cold open has no boundary inside it to space
    spaced = pacing.set_internal_pauses(takes[i], pauses)
    added = (len(spaced) - len(takes[i])) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)
    takes[i] = spaced
    log(f"[pacing] cold open: {pauses} headline pause(s) set to "
        f"{pacing.COLD_OPEN_PAUSE_SECONDS}s (+{added:.2f}s)")
    return takes


def _finalize(segments, pcm, *, episode_id, title, source_items, edition, summary="",
              with_music: Optional[bool] = None, log=print) -> Episode:
    """Shared tail: cache per-segment audio, set start times, stitch, chapter + MP3, publish.

    `with_music=None` means "ask `config.music_enabled()`", which is how the deploy switch
    reaches the pipeline: every production caller leaves it None, so a Fly secret decides. Pass
    True or False to override the environment for one run (`--music` / `--no-music`).
    """
    from . import chapters as chapters_mod

    if with_music is None:
        with_music = config.music_enabled()

    out_dir = config.EPISODES_DIR / episode_id
    story_ids = pacing.story_ids_from(source_items)

    # Cache each segment's RAW PCM, not the paced copy, so a later recast can reuse the voices
    # that did not change. Raw is what makes the cache a real archive: it is exactly what the
    # renderer returned, so a future pacing change can be re-evaluated against old episodes for
    # free (that is how the current policy was chosen). Re-pacing on rebuild is cheap; an
    # un-rendering is impossible.
    #
    # FIRST, before pacing and music, and that ordering is the point. This write used to sit
    # after both of them with nothing wrapped in between, so a single exception in `music.apply`
    # threw away every TTS call in the episode -- the one irreplaceable thing in the run, since
    # pacing and music are pure functions of it and can be redone for free. Writing here means the
    # worst a later crash costs is the stitch, not the spend.
    seg_dir = out_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    for seg, p in zip(segments, pcm):
        (seg_dir / f"{seg.order}.pcm").write_bytes(p)

    # The cold open's own internal spacing, AFTER the archive write and BEFORE pacing, because it
    # changes that segment's duration and everything downstream measures the audio it is handed.
    takes = _space_cold_open(segments, pcm, log=log)

    # Pacing, computed once and used for BOTH the offsets and the audio. The page's seek
    # buttons, the chapter marks, and the VTT are all derived from `start_seconds`, so pacing
    # the audio one way and measuring it another is silently misaligned audio rather than an
    # error. `pacing.apply` returns both halves together so they cannot diverge here.
    paced, gaps = pacing.apply(segments, takes, story_ids=story_ids)

    # Music takes the paced audio and threads cues through it, which is where the list that gets
    # stitched stops being one-piece-per-script-segment: it now carries an intro cue, a sting at
    # every story change, an outro cue, and the cold open merged into one bedded piece. So the
    # offsets can no longer be read off that list, and `music.apply` hands back `starts` (one per
    # ORIGINAL segment) alongside the audio it built. There is deliberately no
    # `stitch.segment_start_times` call here any more: measuring `pieces` would return offsets
    # for cues, and measuring `paced` would describe a file nobody wrote.
    pieces, gaps, starts = music.apply(segments, paced, gaps, story_ids=story_ids,
                                       enabled=with_music, log=log)
    for seg, start in zip(segments, starts):
        seg.start_seconds = start  # per-segment offsets, for the page's seek buttons

    log(f"[stitch] concatenating into one episode file ({pacing.SHOW_POLICY.name} pacing"
        f"{', with music' if with_music else ''})...")
    duration = stitch.stitch(pieces, out_dir / "episode.wav", gaps)

    episode = Episode(
        id=episode_id, title=title, generated_at=_now_iso(), segments=segments,
        audio_path=str(out_dir / "episode.wav"), source_items=source_items,
        duration_seconds=duration, edition=edition, summary=summary,
    )

    log("[chapters] deriving chapters, writing chapters.json + chaptered MP3...")
    status.stage("publishing", "chapters, MP3, transcript, feed")
    chap = chapters_mod.build_chapters(episode)
    chapters_mod.write_chapters_json(chap, out_dir)
    chapters_mod.to_mp3_with_chapters(out_dir / "episode.wav", chap, duration, out_dir / "episode.mp3")

    log("[publish] writing page, JSON, RSS, transcript...")
    from . import publish
    (out_dir / "transcript.vtt").write_text(publish.build_vtt(episode))
    publish.publish(episode, out_dir)
    status.done(episode_id, duration)
    log(f"[done] {episode_id}: {title} ({duration:.0f}s, {len({s.voice_id for s in segments})} voices)")
    return episode


def render_recast(segments, *, original_id, episode_id, title, source_items, cast, edition="", summary="", log=print) -> Episode:
    """Incremental recast: re-render only segments whose voice changed vs the original; reuse the
    cached PCM for the rest (falling back to rendering if a cache is missing, e.g. older episodes)."""
    import json

    status.begin(episode_id, edition)
    normalize.normalize_segments(segments)
    voices.assign_voices(segments, cast)  # respects the voice_ids already set by the mapping
    api_key = config.get_api_key()

    orig_seg_dir = config.EPISODES_DIR / original_id / "segments"
    orig_script = config.EPISODES_DIR / original_id / "script.json"
    orig = {}  # order -> (text, voice_id) from the original, to reuse only truly-unchanged lines
    if orig_script.exists():
        for d in json.loads(orig_script.read_text()):
            orig[d["order"]] = (d.get("text"), d.get("voice_id"))

    pcm, reused, rendered = [], 0, 0
    total = len(segments)
    for i, seg in enumerate(segments):
        cache = orig_seg_dir / f"{seg.order}.pcm"
        # reuse the cached audio only if BOTH the voice and the exact words are unchanged
        if orig.get(seg.order) == (seg.text, seg.voice_id) and cache.exists():
            pcm.append(cache.read_bytes()); reused += 1
        else:
            pcm.append(render.render_segment(seg.text, seg.voice_id, api_key)); rendered += 1
        status.stage("rendering", f"recasting segment {i + 1}/{total}", i + 1, total)
    log(f"[recast] reused {reused} cached segments, re-rendered {rendered}")

    return _finalize(segments, pcm, episode_id=episode_id, title=title,
                     source_items=source_items, edition=edition, summary=summary, log=log)


def render_custom(segments, *, episode_id, title, source_items, cast, provenance,
                  edition="custom", summary="", log=print):
    """Render a custom episode, reusing cached PCM from whichever episode each line came from.

    Differs from render_recast in one way that matters: a custom episode draws lines from several
    past episodes at once, so segment `order` alone cannot identify a cache entry. `provenance`
    maps this episode's order -> (source episode id, that episode's order), and reuse still
    requires the words AND the voice to be unchanged in the source.
    """
    import json

    status.begin(episode_id, edition)
    normalize.normalize_segments(segments)
    voices.assign_voices(segments, cast)  # respects voice_ids already set from the config
    api_key = config.get_api_key()

    scripts = {}  # source episode id -> {order: (text, voice_id)}

    def _source_line(src_id, src_order):
        if src_id not in scripts:
            path = config.EPISODES_DIR / src_id / "script.json"
            table = {}
            if path.exists():
                try:
                    for d in json.loads(path.read_text()):
                        table[d["order"]] = (d.get("text"), d.get("voice_id"))
                except (OSError, json.JSONDecodeError):
                    pass
            scripts[src_id] = table
        return scripts[src_id].get(src_order)

    pcm, reused, rendered = [], 0, 0
    total = len(segments)
    for i, seg in enumerate(segments):
        src = provenance.get(seg.order)
        hit = False
        if src:
            src_id, src_order = src
            cache = config.EPISODES_DIR / src_id / "segments" / f"{src_order}.pcm"
            if _source_line(src_id, src_order) == (seg.text, seg.voice_id) and cache.exists():
                pcm.append(cache.read_bytes())
                reused += 1
                hit = True
        if not hit:
            pcm.append(render.render_segment(seg.text, seg.voice_id, api_key))
            rendered += 1
        status.stage("rendering", f"building segment {i + 1}/{total}", i + 1, total)
    log(f"[custom] reused {reused} cached segments, rendered {rendered}")

    return _finalize(segments, pcm, episode_id=episode_id, title=title,
                     source_items=source_items, edition=edition, summary=summary, log=log)
