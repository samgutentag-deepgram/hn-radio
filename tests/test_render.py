from hn_radio import config, render


def test_voice_family():
    assert config.voice_family("flux-haley-en") == "flux"
    assert config.voice_family("aura-2-thalia-en") == "aura"


def test_speak_url_picks_endpoint_by_family():
    flux = render._speak_url("flux-haley-en")
    aura = render._speak_url("aura-2-thalia-en")
    assert flux.startswith("https://api.deepgram.com/v2/speak?")
    assert aura.startswith("https://api.deepgram.com/v1/speak?")
    assert "model=flux-haley-en" in flux
    assert "model=aura-2-thalia-en" in aura
    assert "encoding=linear16" in flux and "encoding=linear16" in aura


def test_all_voices_includes_both_families():
    assert "flux-haley-en" in config.ALL_VOICES
    assert "aura-2-thalia-en" in config.ALL_VOICES
