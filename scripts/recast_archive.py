"""Recast the whole local archive into the two-person show, in place, resumably.

Every episode on disk predates the two-person format (2026-08-20). They carry `ai` / `maker` /
`security` / `drama` desks plus separate hashed guest voices on the quoted comments, so five to
seven distinct voices each. This walks them in date order and reduces each one to Alexis plus one
rotating co-host, so the back catalogue sounds like the show has always worked this way.

IN PLACE, not `<id>-recast`, and that is the whole point rather than a shortcut. `manifest.
build_manifest` and `feed.rebuild_feed` both skip any id `models.is_recast` matches, so ten
`-recast` directories would be ten shows that do not appear in index.json, do not appear in the
feed, and leave the ten five-voice episodes sitting in the archive as the visible ones. A
consistent archive means the canonical ids change casting.

THE WORDS DO NOT CHANGE; THE SEGMENTATION MAY. A blog post quotes lines out of these episodes, so
no text may be invented, dropped, reordered or reworded, and no writer runs and no Anthropic call
is made. What is allowed is joining adjacent segments, because that changes how the words are
RENDERED and not what they are -- see the cold-open merge below. `_verify` enforces the real
invariant directly: the whole script read end to end is the same words in the same order, with
only the whitespace that joins two former segments differing.

`apply_roles` is called and `rewrite_role_names` deliberately is NOT. The rename pass exists
precisely to edit spoken names inside `text`, so it is the one helper `recast.recast` uses that
this cannot. What that costs is real and is not hidden: an old episode still says "over to Priya
at the AI desk" in a script where one guest host reads every non-host line, so the co-host
announces a correspondent who is no longer there and then answers as them. That is the trade the
words-unchanged requirement buys, and it is stated in the run log per episode.

FOUR THINGS THIS PRESERVES that a naive re-render would quietly lose:

  - `speaker_key` on commenter lines. Untouched here, because `apply_roles` only writes
    `voice_id`. It is the record of which real HN account wrote the words; `voice_id` is only who
    read them out. Losing it would turn a real quote into an anonymous one.
  - `source_hn_id`. Same reason: it is the attribution link back to the thread.
THE COLD OPEN is the one place the segmentation deliberately changes. See
`_merge_cold_open_headlines`. Only 2026-08-18 and 2026-08-19 in this archive actually have a split
cold open; 2026-08-01 through 2026-08-17 have none at all -- their segment 1 already carries a
`source_hn_id`, so `writers.cold_open_index` returns None for them and there is nothing to merge.
The merge is therefore a no-op on 17 of 19, which is correct rather than a missed case.

  - `generated_at`. `_finalize` stamps `_now_iso()` into every Episode it builds, and
    `feed.rebuild_feed` reads that field for `<pubDate>`. Left alone, recasting the archive would
    republish ten back episodes as if they all aired today. Restored from the backup afterwards,
    then the site is rebuilt from the corrected files.
MUSIC, which is a real decision and not a default falling through. `_finalize` takes
`with_music=None` from `render_recast`, meaning "ask `config.music_enabled()`", which is on. Every
one of the 19 hosted episodes was rendered DRY -- segment 0 starts at 0.0s in all of them, so
nothing was ever laid ahead of the first word -- so honouring the default adds a theme, a sting at
each story change, a bed under the cold open and an outro to shows that never had them, worth
roughly fifteen to twenty-five seconds each.

That is what this script does, deliberately. The goal is an archive that sounds like the show as
it exists NOW, and the show as it exists now has music: the nightly cron renders with it, and
Sam's own 2026-08-20 test render has it. An archive recast into the two-person format but left
dry would match the new format on voices and the old one on everything else, which is the
inconsistency the whole task exists to remove. `--preserve-original-music` renders each episode
in whatever state it was originally in instead, detected from that first `start_seconds`; it
exists because the alternative reading of "only the voices change" is a defensible one and this
is the flag that makes the choice visible rather than accidental.

Because music makes every duration grow, a file-duration delta on its own no longer tells you
whether the words changed. So the report carries SPEECH seconds too -- the summed per-segment PCM,
before and after, with no pacing and no cues in it -- which isolates the only thing that should
have moved: how fast a different voice reads the same sentence. Rejected alternative: adding a
`with_music` parameter to `render_recast`. It is the right long-term shape, but it is a library
change with its own tests to earn, and this script is the only caller that needs it.

RESUMABILITY, which is the hard part. This run is 206 segments and any one call can fail on a
cold model or a transient 500, so a naive pass does not survive. Three layers, and they cover different failures:

  1. A content-addressed PCM cache under `.render-cache/recast/`, keyed by
     `sha256(voice \x00 text)`, installed by wrapping `render.render_segment`. This is the layer
     that matters. A 500 that exhausts its retries costs ONE segment, and a re-run pays only for
     what is genuinely missing -- including after a crash in `_finalize`, where the episode's own
     `segments/` directory has already been overwritten with new audio while its `script.json`
     still describes the old casting, so `render_recast`'s own reuse check correctly misses on
     every line and would otherwise re-pay for the entire episode.
  2. A SEED of that cache from the pristine backup, for every line whose voice the mapping does
     not actually change. 2026-08-18 and 2026-08-20 were already anchored on flux-alexis-en and
     2026-08-05's comment theater was already on flux-cole-en, so 27 of the 206 lines are free.
     `render_recast` finds those itself, from `episodes/<id>/segments/*.pcm`; seeding the content
     cache as well is what keeps that true after a crash has overwritten the directory it reads.
  3. A PREFETCH pass that fills every remaining cache miss for an episode BEFORE `render_recast`
     is called, over a small thread pool, so `render_recast`'s own serial loop then runs entirely
     out of cache. This is why it exists rather than being an optimisation: rendered serially the
     first attempt was averaging about 90 seconds a segment -- the retry ladder is 12 attempts at
     a linear 0.6s backoff against a host that 500s half the time, so the mean segment is many
     round trips, not one -- which put 179 renders at roughly four and a half hours. It is also
     the only place a per-segment progress line can be emitted at all: `render_recast` logs once
     per episode, and this script must not edit it.

     Four workers, not more. The point is to hide latency that is mostly waiting, and the failure
     this run has to survive is a flaky host; hammering it harder is a way to turn a 500 rate into
     a 429 rate. Rejected: threading `render_recast` itself. Its loop is ordered and builds the
     PCM list positionally, and it is library code with its own tests, so the concurrency belongs
     in the caller that needed it.

Same shape as `scripts/local_episode.py`, which solved this first and is the reference. The
script-level half of its cache is not needed here and must not be reintroduced: no writer runs,
no Anthropic call is made, and the script is read off disk.

SAFETY. Every episode's pristine `script.json`, `episode.json` and `segments/` are copied to
`.recast-backup/<id>/` before anything is overwritten, and the backup is written ONCE: a second
run reads its inputs from there, so re-running is idempotent rather than compounding. If an
episode fails anywhere, its directory is restored from that backup, so a half-stitched episode is
never left on disk. The rendered PCM survives in the content cache either way.

Usage:
    uv run python scripts/recast_archive.py --dry-run     # rotation + checks, zero spend
    uv run python scripts/recast_archive.py               # the whole archive, resumable
    uv run python scripts/recast_archive.py --only 2026-08-01 --only 2026-08-02
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import cast as cast_mod  # noqa: E402
from hn_radio import config, pipeline, recast, render  # noqa: E402
from hn_radio.models import ScriptSegment  # noqa: E402

CACHE_DIR = config.PROJECT_ROOT / ".render-cache" / "recast"
BACKUP_DIR = config.PROJECT_ROOT / ".recast-backup"

# Canonical daily episodes only. Same pattern `cast._recent_episode_ids` uses, for the same
# reason: `-recast` directories and the `_ads` / `_music` / `_voices` experiment dirs are not
# episodes, and the samples directory is not either.
EPISODE_ID = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# What gets copied aside before an in-place recast overwrites it. The derived files (episode.wav,
# episode.mp3, chapters.json, transcript.vtt) are deliberately NOT backed up: all four are pure
# functions of script.json plus the segment PCM, so the backup can regenerate them, and copying
# them would triple a 330MB archive to protect nothing.
BACKED_UP = ("script.json", "episode.json", "chapters.json", "transcript.vtt")

# Ids this script must never recast, whatever is on disk. 2026-08-20 is a local test render that
# is not part of the hosted archive, and the nightly cron renders that date natively in the new
# format at 03:00 Pacific, so recasting it here would be overwriting the real show with a
# reconstruction of a scratch file. It stays in `_episode_ids()` because the rotation is seeded in
# date order and the last episode's presence cannot change any earlier episode's co-host, so
# leaving it visible costs nothing and hiding it would be a second rule to keep in sync.
NEVER_RECAST = frozenset({"2026-08-20"})

PREFETCH_WORKERS = 4

# Counters, module level so the failure path can still report the spend. Guarded because the
# prefetch pass increments them from several threads; `+=` on a dict value is a read and a write,
# and an undercounted spend report is a lie about money.
STATS = {"calls": 0, "cache_hits": 0, "seeded": 0}
STATS_LOCK = threading.Lock()


def _episode_ids() -> List[str]:
    return sorted(p.name for p in config.EPISODES_DIR.glob("*")
                  if p.is_dir() and EPISODE_ID.match(p.name))


def _backup(ep_id: str, log) -> pathlib.Path:
    """Copy an episode's pristine inputs aside, once. Returns the backup directory.

    Once, not every run: after a completed recast the live script.json describes the NEW casting,
    so re-backing up would overwrite the only record of the original voices and make the run
    un-repeatable. The marker is the directory's existence.
    """
    dest = BACKUP_DIR / ep_id
    if dest.exists():
        return dest
    src = config.EPISODES_DIR / ep_id
    tmp = BACKUP_DIR / f".{ep_id}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "segments").mkdir(parents=True, exist_ok=True)
    for name in BACKED_UP:
        # chapters.json and transcript.vtt are absent on episodes rendered before those stages
        # existed. Skipping a missing one is right: `_verify` then simply has nothing to compare
        # that file against, whereas raising here would refuse to recast an old episode over a
        # derived artifact that the recast is about to regenerate anyway. script.json and
        # episode.json are not optional, and `_recast_one` fails on its own if either is absent.
        if (src / name).exists():
            shutil.copy2(src / name, tmp / name)
    if (src / "segments").is_dir():
        for pcm in (src / "segments").glob("*.pcm"):
            shutil.copy2(pcm, tmp / "segments" / pcm.name)
    tmp.rename(dest)  # atomic: a half-copied backup is never mistaken for a complete one
    log(f"      [backup] {dest}")
    return dest


def _restore(ep_id: str, log) -> None:
    """Put an episode's inputs back from its backup, after a failure part-way through.

    Only the inputs. The derived audio is left as whatever the failed run got to, because the
    next attempt overwrites all of it anyway and a restored script.json is what makes that
    attempt read the original casting again.
    """
    src, dst = BACKUP_DIR / ep_id, config.EPISODES_DIR / ep_id
    if not src.exists():
        log(f"      [restore] no backup for {ep_id}; left as-is")
        return
    for name in BACKED_UP:
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)
    (dst / "segments").mkdir(parents=True, exist_ok=True)
    for pcm in (src / "segments").glob("*.pcm"):
        shutil.copy2(pcm, dst / "segments" / pcm.name)
    log(f"      [restore] {ep_id} put back from {src}")


SEG_CACHE = CACHE_DIR / "segments"


def _cache_path(voice_id: str, text: str) -> pathlib.Path:
    """Where a (voice, text) pair's PCM lives. Content-addressed, so nothing about the episode or
    the segment order is in the key: a retry after a 500 is free, and so is a line two episodes
    happen to share."""
    return SEG_CACHE / (hashlib.sha256(f"{voice_id}\x00{text}".encode()).hexdigest()[:32] + ".pcm")


def _install_render_cache(log) -> None:
    """Wrap `render.render_segment` so it reads and writes the content cache.

    Patches the module attribute rather than passing anything down, because both call sites --
    `pipeline.render_recast` and `render.render_all` -- look it up on the module at call time.
    """
    SEG_CACHE.mkdir(parents=True, exist_ok=True)
    real = render.render_segment

    def cached_render_segment(text: str, voice_id: str, api_key: str) -> bytes:
        f = _cache_path(voice_id, text)
        if f.exists():
            with STATS_LOCK:
                STATS["cache_hits"] += 1
            return f.read_bytes()
        pcm = real(text, voice_id, api_key)
        # Write BEFORE returning, so an exception anywhere upstream still keeps the spend. Via a
        # temp file and a rename, because the prefetch pass runs several of these at once and a
        # half-written .pcm that a later run reads as a cache hit is silently truncated audio.
        tmp = f.with_suffix(".pcm.partial")
        tmp.write_bytes(pcm)
        tmp.rename(f)
        with STATS_LOCK:
            STATS["calls"] += 1
        return pcm

    render.render_segment = cached_render_segment
    log(f"[cache] segment PCM cached under {SEG_CACHE} "
        f"({len(list(SEG_CACHE.glob('*.pcm')))} already on disk)")


def _seed_cache(ep_id: str, pristine: List[dict], segments, log) -> int:
    """Copy the original PCM into the content cache for every line whose voice did NOT change.

    `render_recast` already reuses those from `episodes/<id>/segments/`, so on a clean first pass
    this changes nothing. It matters on the SECOND pass after a crash inside `_finalize`: that
    function's first act is to overwrite `segments/*.pcm` with the new audio, so the directory
    `render_recast` reads its "unchanged" audio out of no longer holds the original, and the
    reuse check misses on lines that were never going to change. Seeding here means those lines
    are free forever, from the backup, which is the one copy nothing overwrites.

    Matched on the (voice, text) PAIR, deliberately not by position. Position was correct only
    while the segment list kept its original length; the cold-open merge shortens it, so a
    positional `zip` would pair every post-cold-open line against the wrong original and seed the
    cache with audio for the wrong words. The pair is what the cache key is anyway, so matching on
    it is both correct and the same question the lookup asks.
    """
    seeded = 0
    needed = {(seg.voice_id, seg.text) for seg in segments}
    for old in pristine:
        pair = (old.get("voice_id"), old.get("text"))
        if pair not in needed:
            continue
        src = BACKUP_DIR / ep_id / "segments" / f"{old['order']}.pcm"
        dest = _cache_path(*pair)
        if src.exists() and not dest.exists():
            shutil.copy2(src, dest)
            seeded += 1
    if seeded:
        with STATS_LOCK:
            STATS["seeded"] += seeded
        log(f"      [seed] {seeded} line(s) keep their original voice; PCM reused, not re-rendered")
    return seeded


def _prefetch(ep_id: str, segments, log, workers: int = PREFETCH_WORKERS) -> None:
    """Fill every cache miss for this episode concurrently, before anything serial runs.

    Deduplicated on the (voice, text) key rather than on the segment, because a repeated line
    would otherwise be paid for twice and race two writers onto one cache path.

    Failures are collected, not raised, and reported as a group. One dead segment should not stop
    the other twenty from being paid for and banked; `render_recast` then hits the same line
    serially, raises there, and the caller restores the episode from its backup. That path is
    slower to fail by one call and much better to resume from.
    """
    api_key = config.get_api_key()
    pending: Dict[pathlib.Path, Tuple[int, str, str]] = {}
    for seg in segments:
        f = _cache_path(seg.voice_id, seg.text)
        if not f.exists() and f not in pending:
            pending[f] = (seg.order, seg.voice_id, seg.text)
    n_total, n = len(segments), len(pending)
    if not n:
        log(f"      [prefetch] all {n_total} segments already cached; nothing to render")
        return
    log(f"      [prefetch] {n} of {n_total} segments to render on {workers} workers")

    done, failures = 0, []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(render.render_segment, text, voice, api_key): (order, voice)
                   for (order, voice, text) in pending.values()}
        for fut in as_completed(futures):
            order, voice = futures[fut]
            done += 1
            try:
                pcm = fut.result()
                secs = len(pcm) / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)
                log(f"      [{ep_id}] {done}/{n} order {order:2} {voice:22} "
                    f"{secs:5.1f}s audio  (+{time.monotonic() - started:.0f}s elapsed)")
            except Exception as e:
                failures.append((order, voice, e))
                log(f"      [{ep_id}] {done}/{n} order {order:2} {voice:22} FAILED: {e}")
    if failures:
        log(f"      [prefetch] {len(failures)} segment(s) still unrendered; "
            f"render_recast will retry them serially and fail loudly if they stay dead")


def _pause_count(text: str) -> int:
    """`writers.cold_open_pause_count`, by its real name, so the spacing pass and this script
    cannot disagree about how many boundaries a merged read has."""
    from hn_radio.writers import cold_open_pause_count
    return cold_open_pause_count(text)


def _merge_cold_open_headlines(segments) -> Optional[dict]:
    """Join the leading run of untagged host HEADLINE lines into one segment. Returns what moved.

    THE GREETING STAYS ITS OWN SEGMENT. The current show's top is two segments -- the pipeline's
    fixed `_INTRO`, then the merged headline preview -- and the archive has to match that shape,
    not collapse into a single three-sentence opener the live show does not have. So the run
    starts at index 1. That is also exactly what `writers.cold_open_index` assumes ("Index 0 is
    the pipeline's fixed intro, never the cold open"), and it is why `writers._merge_cold_open`
    cannot be reused as-is: that function runs on the WRITER's segments, before the pipeline
    prepends the greeting, so its `segments[0]` is already the first headline. Called on a
    finished script it would swallow the greeting.

    WHY MERGE AT ALL. Each segment is one TTS call, so a split cold open takes its pauses from the
    silence `stitch` inserts between renders rather than from punctuation Flux is reading. Measured
    on 2026-08-18: starts at 8.649 / 12.109 / 16.535, and the greeting-to-first-headline boundary
    is ~2.9s of dead air. Merging makes the whole preview one continuous read, and it is what
    finally lets `pipeline._space_cold_open` do anything at all: that pass needs
    `cold_open_pause_count` to be non-zero, and a one-sentence segment counts zero, so on a split
    cold open it silently no-ops.

    The run stops at the first segment carrying a `source_hn_id`, so a throw into a story is never
    absorbed -- those open chapter marks (`chapters.build_chapters` keys off a story id's first
    occurrence) and swallowing one would move the mark. It also stops at any non-anchor line,
    because a cold open is the host alone.

    `source_hn_id` on the merged segment is whatever the head already had, which on every episode
    in this archive is None, and the loop guarantees it: a segment with an id ends the run, so no
    id can be absorbed and none can be invented. That matters beyond tidiness --
    `music._sting_boundaries` puts a musical sting at a story id's first appearance, so an id here
    would drop a sting inside the cold open.

    Words are preserved exactly; only the joining whitespace is new (a single space, so each
    sentence's own full stop carries the pause). `order` is renumbered because the segment count
    drops, and every consumer keyed on `order` follows from the renumbered list: the per-segment
    PCM archive is written fresh by `_finalize`, and `render_recast`'s reuse check compares against
    the ORIGINAL script's orders, so after a merge it correctly misses. That miss costs nothing
    because the content-addressed cache is keyed on (voice, text), not on order -- which is the
    second reason that cache exists.
    """
    run = 1
    while (run < len(segments) and not segments[run].source_hn_id
           and segments[run].desk == "anchor" and segments[run].role != "commenter"):
        run += 1
    if run < 3:  # index 1 alone is not a split cold open, it is a cold open
        return None
    head = segments[1]
    parts = [seg.text for seg in segments[1:run]]
    head.text = " ".join(p.strip() for p in parts)
    del segments[2:run]
    for i, seg in enumerate(segments):
        seg.order = i
    return {"merged": run - 1, "parts": len(parts), "text": head.text}


def _had_music(segments: List[dict]) -> bool:
    """Whether this episode was originally rendered with music.

    Read off the first segment's start offset, which is the only honest record left. Without
    music `music.apply` returns the paced audio untouched and segment 0 starts at 0.0; with it,
    the intro cue is laid ahead of the first word and segment 0 starts after it (7.16s under the
    current constants, 3.16s in 2026-08-07, which was rendered when INTRO_SECONDS was shorter).
    Deliberately a threshold rather than an equality test, so a past or future change to
    INTRO_SECONDS does not silently re-classify an episode as dry.
    """
    return bool(segments and (segments[0].get("start_seconds") or 0.0) > 1.0)


def _rotation(ep_ids: List[str], log) -> Dict[str, Dict[str, str]]:
    """Cast every episode through the show's own rotation, in date order.

    `recent_voices` is threaded by hand instead of letting `episode_cast` call
    `recent_cohost_voices`, and that is required rather than tidier. That function looks for a
    segment tagged `desk == "cohost"`, and this recast writes voices WITHOUT rewriting desks --
    the archive keeps its `ai` / `maker` / `drama` tags, because a desk rename is a script change
    and the script is frozen. So a recast episode contributes nothing to the window it is read
    from, every episode would see an empty history, and the hash rotation alone would decide.
    That still avoids most repeats but guarantees none, and adjacent duplicates are exactly what
    the window exists to stop.

    Ordered oldest first and accumulated newest first, matching `recent_cohost_voices`'s contract,
    so the seeding is identical to what the nightly show would have produced had it always worked
    this way.
    """
    plan: Dict[str, Dict[str, str]] = {}
    recent: List[str] = []
    for ep_id in ep_ids:
        # Truncated to the window, because that is what `recent_cohost_voices(limit=...)` hands
        # `episode_cast` on the live show. With 19 episodes and a window of 14 the untruncated
        # list would start suppressing voices the real rotation had already released, so the
        # archive would not be reproducible by the code that is supposed to have produced it.
        window = recent[:cast_mod.COHOST_RECENCY_WINDOW]
        ep_cast, subs = cast_mod.episode_cast(recent_voices=window, before=ep_id)
        cohost = ep_cast.cohost
        # `validate_mapping` is the one gate that refuses a retired voice, a non-Flux voice, or
        # the same voice in both chairs. Called here, before any spend, so a bad rotation fails
        # the whole run in a dry-run rather than at episode nine.
        plan[ep_id] = recast.validate_mapping({"anchor": ep_cast.anchor.voice_id,
                                               "cohost": cohost.voice_id})
        recent.insert(0, cohost.voice_id)
        log(f"  {ep_id}  Showrunner {ep_cast.anchor.name:9} ({ep_cast.anchor.voice_id})"
            f"   Guest host {cohost.name:9} ({cohost.voice_id})"
            + (f"  subs={subs}" if subs else ""))
    return plan


def _speech_seconds(seg_dir: pathlib.Path, orders) -> float:
    """Total rendered speech in a segment cache, with no pacing gaps and no music cues in it.

    This is the honest "did the words change" signal. File duration is not, once music is in
    play: every episode gains fifteen to twenty-five seconds of cues, which swamps the thing being
    checked. Summed PCM moves for exactly one reason -- a different voice reading the same
    sentence at a different speed -- so a delta here of more than a few percent is worth looking
    at and a delta in the file duration usually is not.
    """
    total = 0
    for o in orders:
        f = seg_dir / f"{o}.pcm"
        if f.exists():
            total += f.stat().st_size
    return total / (config.SAMPLE_RATE * config.SAMPLE_WIDTH)


def _verify(ep_id: str, pristine: List[dict], expected: List[dict], meta: dict,
            mapping: Dict[str, str], merge: Optional[dict]) -> List[str]:
    """Check the recast episode against its pristine backup. Returns a list of problems.

    Read from DISK, not from the in-memory Episode `render_recast` returned, because the published
    artifact is the thing anyone else will read and an assertion about the object in hand would
    pass even if `publish` wrote something different.

    Every check here is one thing this task can break silently, and "silently" is the operative
    word: nothing in the pipeline errors if a title is regenerated, a summary is dropped, or a
    quote loses its username. The audio still stitches and the page still renders.
    """
    problems = []
    ep_dir = config.EPISODES_DIR / ep_id
    now = json.loads((ep_dir / "script.json").read_text())

    # THE WORD CHECK, and it is the one that actually matters. Segmentation may change (the cold
    # open merge joins three headline segments into one), so byte-identical per segment is no
    # longer the invariant. What must hold is that the whole script, read end to end, is the same
    # words in the same order: only the whitespace that joins two former segments is new. This
    # catches an invented, dropped, reordered or reworded line in one comparison, whatever the
    # segmentation did, which is exactly why it is checked separately from the positional pass.
    words_before = " ".join(s["text"] for s in pristine).split()
    words_after = " ".join(s["text"] for s in now).split()
    if words_before != words_after:
        n = next((i for i, (a, b) in enumerate(zip(words_before, words_after)) if a != b),
                 min(len(words_before), len(words_after)))
        problems.append(f"WORDS CHANGED at word {n}: {words_before[n:n + 6]} -> "
                        f"{words_after[n:n + 6]} ({len(words_before)} -> {len(words_after)} words)")

    if len(now) != len(expected):
        problems.append(f"segment count {len(expected)} -> {len(now)} "
                        f"(expected {len(expected)} after the cold-open merge)")
        return problems
    for old, new in zip(expected, now):
        if old["text"] != new["text"]:
            problems.append(f"order {old['order']}: text changed")
        if old["speaker_key"] != new["speaker_key"]:
            problems.append(f"order {old['order']}: speaker_key "
                            f"{old['speaker_key']!r} -> {new['speaker_key']!r}")
        if old.get("source_hn_id") != new.get("source_hn_id"):
            problems.append(f"order {old['order']}: source_hn_id changed")
        if old.get("desk") != new.get("desk"):
            problems.append(f"order {old['order']}: desk changed")
    voices = {s["voice_id"] for s in now}
    if voices != set(mapping.values()):
        problems.append(f"voices on disk {sorted(voices)} != mapping {sorted(set(mapping.values()))}")

    # Episode metadata. `title` and `summary` are the show notes, and the known failure mode is
    # that a path which does not pass them through falls back to a generated `_panel_title` and an
    # empty summary, which looks like a plausible episode rather than like a bug.
    live_meta = json.loads((ep_dir / "episode.json").read_text())
    for field in ("title", "summary", "generated_at", "edition"):
        if (meta.get(field) or "") != (live_meta.get(field) or ""):
            problems.append(f"{field}: {meta.get(field)!r} -> {live_meta.get(field)!r}")
    if [i.get("hn_id") for i in meta.get("source_items", [])] != \
       [i.get("hn_id") for i in live_meta.get("source_items", [])]:
        problems.append("source_items changed")

    # The transcript is REGENERATED and must be, because pacing and music move every start time.
    # What is checked is that it exists and still has one cue per script segment: a transcript
    # that silently lost lines is worse than one with shifted timings, and the speaker labels
    # legitimately change where a desk line moved to the Guest host.
    vtt = ep_dir / "transcript.vtt"
    if not vtt.exists():
        problems.append("transcript.vtt missing")
    else:
        cues = vtt.read_text().count("-->")
        if cues != len(now):
            problems.append(f"transcript.vtt has {cues} cues for {len(now)} segments")

    # Chapter TITLES come from the stories, so they must not move. Their timings must, for the
    # same reason the transcript's do.
    chap = ep_dir / "chapters.json"
    old_chap = BACKUP_DIR / ep_id / "chapters.json"
    if not chap.exists():
        problems.append("chapters.json missing")
    elif old_chap.exists():
        def titles(p):
            d = json.loads(p.read_text())
            rows = d if isinstance(d, list) else d.get("chapters", [])
            return [c.get("title") for c in rows]
        was, is_now = titles(old_chap), titles(chap)
        # Exact equality is the WRONG check here, and the difference is a fix rather than a
        # regression. The archived chapters.json files were written before `build_chapters` kept
        # a `seen_stories` SET: a script that refers back to an earlier story carries that
        # story's `source_hn_id`, and the old code minted a second chapter with the same title
        # every time it did, so 14 of these 19 episodes shipped one duplicate chapter. Regenerating
        # them drops it. So the rule is: every title must survive, in order, and the only permitted
        # change is that a title which appeared twice now appears once. A title actually going
        # missing, or a new one appearing, still fails.
        seen, deduped = set(), []
        for t in was:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        if is_now != deduped:
            problems.append(f"chapter titles changed beyond duplicate removal: {was} -> {is_now}")

    # The cold open, after the fact. `pipeline._space_cold_open` only does anything when
    # `writers.cold_open_index` recognises the shape AND the read has at least one internal
    # sentence boundary, and it fails SILENTLY on both counts -- it returns the audio untouched.
    # So a merge that produced a segment the spacing pass does not recognise would look like a
    # success and sound like the defect it was meant to fix. Checked here rather than trusted.
    if merge:
        from hn_radio.writers import cold_open_index
        objs = [ScriptSegment(**{k: v for k, v in s.items()}) for s in now]
        idx = cold_open_index(objs)
        if idx != 1:
            problems.append(f"cold_open_index is {idx}, expected 1 after the merge; "
                            f"the spacing pass will not fire on the merged read")
        elif _pause_count(now[1]["text"]) < 1:
            problems.append("merged cold open has no internal sentence boundary; "
                            "the spacing pass will no-op")

    for name in ("episode.wav", "episode.mp3"):
        if not (ep_dir / name).exists():
            problems.append(f"{name} missing")
    return problems


def _recast_one(ep_id: str, mapping: Dict[str, str], log, workers: int = PREFETCH_WORKERS,
                preserve_music: bool = False) -> dict:
    """Recast one episode in place. Returns a row for the report, or raises."""
    backup = _backup(ep_id, log)
    pristine = json.loads((backup / "script.json").read_text())
    meta = json.loads((backup / "episode.json").read_text())
    before_duration = float(meta.get("duration_seconds") or 0.0)

    # Always from the PRISTINE copy, never from the live file. That is what makes a re-run
    # idempotent: `role_of` needs the episode's ORIGINAL anchor voice to tell a quote the host
    # read from a quote that had its own guest voice, and a completed run has already replaced it.
    segments = [ScriptSegment(**d) for d in pristine]

    # The cold open first, because it changes the segment LIST, and everything after it -- the
    # role mapping, the cache seed, the prefetch, the verification baseline -- has to be computed
    # against the shape that is actually going to be rendered.
    merge = _merge_cold_open_headlines(segments)
    if merge:
        log(f"      [cold open] {merge['parts']} headline segments merged into one continuous "
            f"read ({len(merge['text'])} chars); {_pause_count(merge['text'])} internal pause(s) "
            f"for the spacing pass")
    else:
        log("      [cold open] nothing to merge (no leading run of untagged host headlines)")

    # Captured AFTER the merge and BEFORE `apply_roles`, which is the only window where this is
    # both the intended shape and still carries the original voices. It is the baseline `_verify`
    # compares the published script against, derived here rather than re-derived there, so the
    # check cannot agree with a bug by repeating it.
    expected = [{"order": s.order, "text": s.text, "speaker_key": s.speaker_key,
                 "source_hn_id": s.source_hn_id, "desk": s.desk, "role": s.role}
                for s in segments]

    for role, pairs in recast.role_takeovers(segments).items():
        if role in mapping:
            log(f"      {recast.ROLE_LABELS[role]} takes over " + ", ".join(
                f"{recast.SLOT_LABELS.get(slot, slot)} ({config.voice_name(v) or v})"
                for slot, v in pairs))

    # apply_roles ONLY. rewrite_role_names would edit spoken names inside `text`, and the script
    # is frozen; see the module docstring for what that leaves in the copy.
    recast.apply_roles(segments, mapping)

    # Both cheap layers first, then the expensive one, so `render_recast` below runs entirely out
    # of cache and its serial loop costs nothing. `normalize_segments` runs inside it and is
    # idempotent on an already-normalized script (verified against all ten: zero text changes),
    # so the text these keys are computed from is the text it will ask for.
    _seed_cache(ep_id, pristine, segments, log)
    _prefetch(ep_id, segments, log, workers=workers)

    orders = [d["order"] for d in pristine]
    speech_before = _speech_seconds(BACKUP_DIR / ep_id / "segments", orders)

    music_on = _had_music(pristine) if preserve_music else config.music_enabled()
    real_music_enabled = config.music_enabled
    config.music_enabled = lambda: music_on
    try:
        ep = pipeline.render_recast(
            segments,
            original_id=ep_id,
            episode_id=ep_id,        # in place; see the module docstring
            title=meta.get("title", ep_id),
            source_items=meta.get("source_items", []),
            cast=cast_mod.active_cast(),  # names only: every voice_id is already pinned above
            edition=meta.get("edition", ""),
            summary=meta.get("summary", ""),
            log=log,
        )
    finally:
        config.music_enabled = real_music_enabled

    # `_finalize` stamps a fresh `generated_at`, which is the feed's `<pubDate>`. Put the original
    # back and rebuild the site from the corrected file, or the archive republishes as ten shows
    # that all aired today.
    live = config.EPISODES_DIR / ep_id / "episode.json"
    doc = json.loads(live.read_text())
    doc["generated_at"] = meta.get("generated_at", doc["generated_at"])
    live.write_text(json.dumps(doc, indent=2) + "\n")
    from hn_radio import publish
    publish.rebuild_site(config.EPISODES_DIR)

    problems = _verify(ep_id, pristine, expected, meta, mapping, merge)
    return {
        "id": ep_id, "cohost": mapping["cohost"], "music": music_on, "n": len(expected),
        "merged": merge["parts"] if merge else 0, "n_before": len(pristine),
        "before": before_duration, "after": ep.duration_seconds,
        "speech_before": speech_before,
        "speech_after": _speech_seconds(config.EPISODES_DIR / ep_id / "segments", orders),
        "voices": len({s.voice_id for s in ep.segments}), "problems": problems,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="recast_archive", description=__doc__.split("\n")[0])
    ap.add_argument("--only", action="append", default=[],
                    help="recast just this episode id (repeatable). The rotation is still "
                         "computed over the WHOLE archive, so a partial run casts the same "
                         "co-host it would have in a full one.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the rotation and the per-episode plan, spend nothing")
    ap.add_argument("--preserve-original-music", action="store_true",
                    help="render each episode with the music state it originally had, instead of "
                         "the show's current tuning. See the module docstring.")
    ap.add_argument("--workers", type=int, default=PREFETCH_WORKERS,
                    help=f"concurrent renders in the prefetch pass (default {PREFETCH_WORKERS}; "
                         f"raising it leans harder on the API for no extra throughput)")
    args = ap.parse_args(argv)

    log = print
    # Hundreds of calls in a row: wait out a transient failure rather than restarting the pass.
    config.http_retries = lambda: 12

    all_ids = _episode_ids()
    if not all_ids:
        log("No canonical episodes on disk.")
        return 1
    log(f"[plan] casting {len(all_ids)} episodes through the show's own rotation "
        f"(window {cast_mod.COHOST_RECENCY_WINDOW}), oldest first:")
    plan = _rotation(all_ids, log)

    targets = args.only or all_ids
    unknown = [t for t in targets if t not in plan]
    if unknown:
        log(f"Not episodes on disk: {unknown}")
        return 2
    # After --only, so naming an excluded id explicitly is still refused rather than silently
    # honoured. A guard that a flag can talk its way past is not a guard.
    blocked = [t for t in targets if t in NEVER_RECAST]
    if blocked:
        log(f"[skip] never recast: {blocked} (see NEVER_RECAST)")
        targets = [t for t in targets if t not in NEVER_RECAST]

    if args.dry_run:
        for ep_id in targets:
            pristine = json.loads((config.EPISODES_DIR / ep_id / "script.json").read_text())
            music_on = (_had_music(pristine) if args.preserve_original_music
                        else config.music_enabled())
            log(f"  {ep_id}  {len(pristine):3} segments  originally="
                f"{'scored' if _had_music(pristine) else 'dry':6} will render="
                f"{'scored' if music_on else 'dry':6} -> {plan[ep_id]}")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _install_render_cache(log)

    rows, failed = [], []
    for i, ep_id in enumerate(targets, 1):
        log(f"\n=== [{i}/{len(targets)}] {ep_id} -> {plan[ep_id]}")
        try:
            rows.append(_recast_one(ep_id, plan[ep_id], log, workers=args.workers,
                                    preserve_music=args.preserve_original_music))
        except Exception as e:  # one bad episode must not cost the other nine
            log(f"    FAILED {ep_id}: {type(e).__name__}: {e}")
            _restore(ep_id, log)
            failed.append((ep_id, f"{type(e).__name__}: {e}"))

    log("\n" + "=" * 100)
    log(f"{'episode':11} {'guest host':22} {'seg':>7} {'cold':>4} {'v':>2} {'file before':>11} "
        f"{'file after':>10} {'speech before':>13} {'speech after':>12}  checks")
    for r in rows:
        flag = "PROBLEMS: " + "; ".join(r["problems"]) if r["problems"] else "ok"
        log(f"{r['id']:11} {r['cohost']:22} {r['n_before']:3}->{r['n']:<3} "
            f"{r['merged'] or '-':>4} {r['voices']:2} "
            f"{r['before']:10.1f}s {r['after']:9.1f}s "
            f"{r['speech_before']:12.1f}s {r['speech_after']:11.1f}s "
            f"({r['speech_after'] - r['speech_before']:+.1f}s)  {flag}")
    log(f"\nsegments rendered (paid): {STATS['calls']}   served from cache: {STATS['cache_hits']}"
        f"   seeded from the original PCM: {STATS['seeded']}")
    log("(one 'rendered' is one segment, not one HTTP request: `_http._request` retries a 500 up "
        "to 12 times inside a single render, so the wire count is higher.)")
    if failed:
        log("failed: " + "; ".join(f"{e} ({why})" for e, why in failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
