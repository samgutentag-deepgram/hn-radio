import shutil
import subprocess
import wave

import pytest

from hn_radio import chapters
from hn_radio.models import Episode, ScriptSegment


def _probe(mp3, field):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", f"stream={field}", "-of", "csv=p=0", str(mp3)],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="needs ffmpeg + ffprobe")
def test_distributed_mp3_is_mpeg1_at_44100(tmp_path):
    """The MP3 we hand to podcast players must be MPEG-1 Layer III at 44.1 kHz.

    Deepgram returns 24 kHz PCM. Encoding straight through (no -ar) yields MPEG-2 Layer III,
    which is legal MP3 but outside what Apple Podcasts and many clients expect. Such episodes
    typically appear in a player's episode list and then refuse to play, because the feed
    metadata parses fine while the audio decoder rejects the low-sample-rate MPEG-2 stream.
    Sample rate does not change file size here, since -b:a fixes the bitrate.
    """
    wav = tmp_path / "e.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000)  # 1s of silence at the pipeline's native rate

    mp3 = chapters.to_mp3_with_chapters(
        wav, [{"startTime": 0.0, "title": "Intro"}], 1.0, tmp_path / "e.mp3")

    assert _probe(mp3, "sample_rate") == "44100"
    assert _probe(mp3, "codec_name") == "mp3"
    # chapters must survive the resample
    got = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "csv=p=0", str(mp3)],
                         capture_output=True, text=True, check=True).stdout
    assert "Intro" in got


def _seg(order, role, start, desk=None, hn=None):
    return ScriptSegment(order=order, role=role, speaker_key="x", desk=desk, text="t",
                         start_seconds=start, source_hn_id=hn)


def test_build_chapters_intro_stories_threads_wrapup():
    segs = [
        _seg(0, "anchor", 0.0, desk="anchor"),
        _seg(1, "anchor", 10.0, desk="anchor", hn=1),
        _seg(2, "desk", 15.0, desk="maker", hn=1),
        _seg(3, "anchor", 40.0, desk="anchor", hn=2),
        _seg(4, "commenter", 60.0, hn=99),
        _seg(5, "anchor", 80.0, desk="anchor"),
    ]
    ep = Episode(id="x", title="t", generated_at="", segments=segs, audio_path="",
                 source_items=[{"hn_id": 1, "title": "Story One", "url": "u1"},
                               {"hn_id": 2, "title": "Story Two", "url": None}],
                 duration_seconds=90.0)
    ch = chapters.build_chapters(ep)
    assert [c["title"] for c in ch] == ["Intro", "Story One", "Story Two", "From the threads", "Wrap-up"]
    assert ch[0]["startTime"] == 0.0 and ch[1]["startTime"] == 10.0
    assert ch[1]["url"].endswith("id=1")
    # strictly increasing
    assert all(ch[i]["startTime"] < ch[i + 1]["startTime"] for i in range(len(ch) - 1))


def test_build_chapters_opens_one_chapter_per_story_despite_a_callback():
    """A callback references an earlier story, and the old "!= previous id" test minted a
    duplicate chapter with the same title every time it happened.

    Shipped for real: episodes/2026-08-08/chapters.json has 8 chapters for 3 stories, two of
    the titles appearing twice, because the branch's prompt now permits exactly this.
    """
    segs = [
        _seg(0, "anchor", 0.0, desk="anchor"),
        _seg(1, "anchor", 10.0, desk="anchor", hn=1),   # story one opens here
        _seg(2, "desk", 15.0, desk="ai", hn=1),
        _seg(3, "anchor", 40.0, desk="anchor", hn=2),   # story two opens here
        _seg(4, "desk", 45.0, desk="ai", hn=2),
        _seg(5, "desk", 55.0, desk="ai", hn=1),         # CALLBACK to story one
        _seg(6, "anchor", 70.0, desk="anchor", hn=3),   # story three opens here
        _seg(7, "desk", 80.0, desk="ai", hn=2),         # CALLBACK to story two
        _seg(8, "anchor", 95.0, desk="anchor"),
    ]
    ep = Episode(id="x", title="t", generated_at="", segments=segs, audio_path="",
                 source_items=[{"hn_id": 1, "title": "Story One", "url": "u1"},
                               {"hn_id": 2, "title": "Story Two", "url": "u2"},
                               {"hn_id": 3, "title": "Story Three", "url": "u3"}],
                 duration_seconds=120.0)
    titles = [c["title"] for c in chapters.build_chapters(ep)]
    assert titles == ["Intro", "Story One", "Story Two", "Story Three", "Wrap-up"]
    assert len(titles) == len(set(titles))
    # and each chapter still opens at the story's FIRST mention, not its callback
    starts = {c["title"]: c["startTime"] for c in chapters.build_chapters(ep)}
    assert starts["Story One"] == 10.0 and starts["Story Two"] == 40.0


def test_missing_source_wav_raises_a_message_that_names_the_cause(tmp_path):
    """A bare ffmpeg exit 254 says nothing. This is the error that actually explains it.

    The deployed image excludes episodes/**/episode.wav (.dockerignore), so an episode seeded from
    the image has no source audio on the volume. That is what made the first production run of
    scripts/add_chapters.py fail, and the swallowed stderr made it look like a bad ffmpeg flag.
    """
    with pytest.raises(FileNotFoundError, match="no source audio"):
        chapters.to_mp3_with_chapters(
            tmp_path / "nope.wav", [{"startTime": 0.0, "title": "Intro"}], 1.0,
            tmp_path / "out.mp3")


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="needs ffmpeg + ffprobe")
def test_ffmpeg_failure_surfaces_stderr(tmp_path):
    """A real ffmpeg error must carry its own diagnostics, not just an exit code and an argv list."""
    bogus = tmp_path / "e.wav"
    bogus.write_bytes(b"this is not a wav file")   # exists, so it gets past the pre-check
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        chapters.to_mp3_with_chapters(
            bogus, [{"startTime": 0.0, "title": "Intro"}], 1.0, tmp_path / "out.mp3")
