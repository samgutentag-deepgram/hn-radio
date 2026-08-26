import wave

from hn_radio import config, stitch


def test_silence_length_matches_duration():
    one_second = stitch.silence(1.0)
    assert len(one_second) == config.SAMPLE_RATE * config.SAMPLE_WIDTH


def test_concat_inserts_gaps_between_but_not_around():
    a = b"\x01\x02" * 100
    b = b"\x03\x04" * 100
    gap = 0.1
    out = stitch.concat_pcm([a, b], gap_seconds=gap)
    expected = len(a) + len(b) + len(stitch.silence(gap))
    assert len(out) == expected


def test_stitch_writes_wav_with_honest_duration(tmp_path):
    # ~0.5s + gap + ~0.5s of PCM
    half = stitch.silence(0.5)
    out = tmp_path / "ep.wav"
    duration = stitch.stitch([half, half], out, gap_seconds=0.2)

    with wave.open(str(out)) as w:
        assert w.getnchannels() == config.CHANNELS
        assert w.getframerate() == config.SAMPLE_RATE
        assert w.getsampwidth() == config.SAMPLE_WIDTH
        read_dur = w.getnframes() / w.getframerate()

    assert abs(read_dur - 1.2) < 0.01      # 0.5 + 0.2 gap + 0.5
    assert abs(duration - read_dur) < 0.01  # returned duration matches the file


def test_rebuilding_from_the_cache_reproduces_the_original_wav(tmp_path):
    """Reconstruction must be lossless, since it replaces the source audio for re-transcoding.

    Repointed 2026-08-22 from `stitch.rebuild_from_segments`, a callerless wrapper now deleted,
    onto the two functions the real rebuild path uses. These three guards are the ONLY coverage
    anywhere of `load_cached_segments`, which is live in `scripts/add_chapters.py`, so they were
    kept rather than deleted with their old entry point.

    The deployed image excludes episodes/**/episode.wav but keeps the per-segment PCM cache, so an
    episode seeded from the image has no WAV on the volume. The cache IS that audio, so the WAV is
    rebuildable rather than lost.
    """
    from hn_radio import stitch

    seg_dir = tmp_path / "segments"
    seg_dir.mkdir()
    pcm = []
    for order in range(4):
        data = bytes([order + 1, 0]) * 500
        (seg_dir / f"{order}.pcm").write_bytes(data)
        pcm.append(data)

    direct = tmp_path / "direct.wav"
    stitch.stitch(pcm, direct)

    rebuilt = tmp_path / "rebuilt.wav"
    stitch.stitch(stitch.load_cached_segments(seg_dir, [0, 1, 2, 3]), rebuilt)

    assert rebuilt.read_bytes() == direct.read_bytes(), \
        "rebuilding must be byte-identical to stitching the same segments directly"


def test_rebuild_ignores_orphaned_segment_files(tmp_path):
    """It must follow the script's orders, not whatever .pcm files happen to be lying around.

    A re-render leaves stale segments behind: one episode on disk has 23 .pcm files for a
    19-segment script. Globbing the directory would splice those strangers into the audio.
    """
    from hn_radio import stitch

    seg_dir = tmp_path / "segments"
    seg_dir.mkdir()
    for order in range(6):
        (seg_dir / f"{order}.pcm").write_bytes(bytes([order + 1, 0]) * 200)

    out = tmp_path / "out.wav"
    # script only references 3 of the 6
    stitch.stitch(stitch.load_cached_segments(seg_dir, [0, 1, 2]), out)

    expected = tmp_path / "expected.wav"
    stitch.stitch([(seg_dir / f"{i}.pcm").read_bytes() for i in (0, 1, 2)], expected)
    assert out.read_bytes() == expected.read_bytes(), "orphaned segments 3-5 must not be included"


def test_rebuild_reports_which_segments_are_missing(tmp_path):
    """Half a rebuilt episode would be worse than a clear failure."""
    import pytest

    from hn_radio import stitch

    seg_dir = tmp_path / "segments"
    seg_dir.mkdir()
    (seg_dir / "0.pcm").write_bytes(b"\x01\x00" * 100)

    with pytest.raises(FileNotFoundError, match="cached segment"):
        stitch.load_cached_segments(seg_dir, [0, 1, 2])


def test_rebuild_with_no_cache_at_all_is_an_error(tmp_path):
    import pytest

    from hn_radio import stitch

    (tmp_path / "segments").mkdir()
    with pytest.raises(FileNotFoundError):
        stitch.load_cached_segments(tmp_path / "segments", [])
