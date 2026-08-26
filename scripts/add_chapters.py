"""Backfill chapters + a chaptered MP3 + a show-notes summary onto existing episodes.

No audio re-render: derives chapters from each episode's script, transcodes the existing WAV to a
chaptered MP3, and (if the episode has no summary yet) asks Claude for a 1-2 sentence show-notes
summary. Then rebuilds the feed + manifest. Run:  uv run python scripts/add_chapters.py
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from hn_radio import chapters, config, music, pacing, publish, stitch
from hn_radio.jsonio import write_json
from hn_radio.models import Episode, ScriptSegment, is_recast


def summarize(segments) -> str:
    import anthropic

    transcript = "\n".join(f"{s.speaker_key}: {s.text}" for s in segments)[:6000]
    client = anthropic.Anthropic(api_key=config.get_anthropic_key())
    resp = client.messages.create(
        model="claude-opus-5", max_tokens=1000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content":
                   "In 1-2 warm, wry sentences, summarize this podcast episode for its show notes. "
                   "Plain text, no preamble.\n\n" + transcript}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "").strip()


def main() -> int:
    """Backfill chapters, a chaptered MP3 and a transcript onto every episode that can take one.

    Each episode is handled independently. An earlier version let the first failure abort the run,
    so one episode missing its source WAV meant the two later episodes that *could* have been fixed
    were never attempted, and the feed was never rebuilt either. A per-episode failure is now
    reported and skipped, the rest still process, and the exit code reflects whether anything failed.
    """
    done, skipped = [], []
    for ej in sorted(config.EPISODES_DIR.glob("*/episode.json")):
        data = json.loads(ej.read_text())
        if is_recast(data["id"]):
            continue
        d = ej.parent
        try:
            segs = [ScriptSegment(**s) for s in json.loads((d / "script.json").read_text())]

            if not data.get("summary"):
                print(f"  {data['id']}: summarizing...")
                data["summary"] = summarize(segs)
                write_json(ej, data)

            ep = Episode(id=data["id"], title=data["title"], generated_at=data.get("generated_at", ""),
                         segments=segs, audio_path=data.get("audio_path", ""),
                         source_items=data.get("source_items", []),
                         duration_seconds=data.get("duration_seconds", 0),
                         edition=data.get("edition", ""), summary=data.get("summary", ""))
            wav = d / "episode.wav"
            if not wav.exists():
                # The deployed image excludes episodes/**/episode.wav but keeps the per-segment PCM
                # cache, so an episode seeded from the image has no source audio on the volume. The
                # cache is that audio, so rebuild losslessly from it rather than needing an upload.
                # Driven by script.json, never by globbing: a re-render can leave orphaned .pcm files
                # with higher indices behind, and those are not part of this episode.
                #
                # Paced through the same `pacing.apply` the render path uses, then musicked
                # through the same `music.apply`, in the same order, exactly as `pipeline._finalize`
                # does it. The cache holds RAW renderer output, so a rebuild that stopped after
                # pacing (or skipped music entirely) would come out shorter and un-musicked while
                # the chapter marks below are computed from this script under the current policy:
                # the audio and its own chapters would disagree by seconds. This is the second time
                # that trap has fired - once for pacing, now for music - so start_seconds is taken
                # from whatever `music.apply` reports, never from `stitch.segment_start_times`
                # directly, and `gaps`/`pieces` are always the post-music values handed to `stitch`.
                ordered = sorted(segs, key=lambda x: x.order)
                orders = [s.order for s in ordered]
                raw = stitch.load_cached_segments(d / "segments", orders)
                story_ids = pacing.story_ids_from(data.get("source_items"))
                paced, gaps = pacing.apply(ordered, raw, story_ids=story_ids)
                pieces, gaps, starts = music.apply(ordered, paced, gaps, story_ids=story_ids)
                dur = stitch.stitch(pieces, wav, gaps)
                for s, start in zip(ordered, starts):
                    s.start_seconds = start
                # A re-paced, re-musicked rebuild is a different length from what episode.json
                # recorded, and every offset in it moved. Persist both: `to_mp3_with_chapters`
                # below is handed the duration, and the page's seek buttons read start_seconds.
                ep.duration_seconds = dur
                data["duration_seconds"] = dur
                write_json(ej, data)
                write_json(d / "script.json", [s.to_dict() for s in ordered])
                print(f"  {data['id']}: rebuilt episode.wav from {len(orders)} cached segments "
                      f"({dur}s, {pacing.SHOW_POLICY.name} pacing, with music)")

            chap = chapters.build_chapters(ep)
            chapters.write_chapters_json(chap, d)
            chapters.to_mp3_with_chapters(wav, chap, ep.duration_seconds, d / "episode.mp3")
            (d / "transcript.vtt").write_text(publish.build_vtt(ep))
            print(f"  {data['id']}: {len(chap)} chapters, MP3 + transcript written")
            done.append(data["id"])
        except Exception as e:
            print(f"  {data['id']}: SKIPPED, {e}")
            skipped.append(data["id"])

    # Always rebuild, even after a skip: the episodes that did succeed changed size, and the feed's
    # enclosure lengths have to match or clients see a truncated download.
    # rebuild_site, not just feed + manifest: this used to skip voices.json, and it was never
    # clear whether that was deliberate. Regenerating all three is cheap and cannot be wrong.
    publish.rebuild_site(config.EPISODES_DIR)
    print(f"site rebuilt ({len(done)} episodes rebuilt, {len(skipped)} skipped)")
    if skipped:
        print(f"skipped: {', '.join(skipped)}")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
