"""The Deepgram API host is selectable, and the public cloud API is the default.

The selector exists so a run can be pointed at a different Deepgram endpoint without editing
code. What is worth pinning is the DEFAULT: "the documented cloud API unless told otherwise" is
the property that stops a deploy drifting somewhere else by accident, and it is what these tests
hold in place.

There was a `_reloaded()` helper here that re-imported config to re-read the environment. Deleted
2026-08-22: it never had a caller, and it was worse than dead weight because tests in this file
monkeypatch config attributes and a reload would throw those away. See config.py's note on why
api_host() is a function and test_feed.py on why reload was removed.
"""


from hn_radio import config, render


OTHER_HOST = "api.example-deepgram-endpoint.com"


def test_api_host_defaults_to_the_cloud_api(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_HOST", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    assert config.api_host() == "api.deepgram.com"


def test_api_host_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_HOST", OTHER_HOST)
    assert config.api_host() == OTHER_HOST


def test_api_host_strips_a_scheme_if_someone_pastes_a_url(monkeypatch):
    # Easy mistake, and it would otherwise produce https://https://host/v2/speak.
    monkeypatch.setenv("DEEPGRAM_API_HOST", f"https://{OTHER_HOST}/")
    assert config.api_host() == OTHER_HOST


def test_speak_url_follows_the_configured_host(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_HOST", OTHER_HOST)
    flux = render._speak_url("flux-cole-en")
    aura = render._speak_url("aura-2-thalia-en")
    assert flux.startswith(f"https://{OTHER_HOST}/v2/speak?")
    assert aura.startswith(f"https://{OTHER_HOST}/v1/speak?")
    assert "model=flux-cole-en" in flux


def test_speak_url_still_defaults_to_the_cloud_api(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_HOST", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    assert render._speak_url("flux-cole-en").startswith("https://api.deepgram.com/v2/speak?")


def test_the_catalog_does_not_follow_the_host(monkeypatch):
    """The host is where calls go; it is not a claim about which voices exist.

    These were coupled once, and the coupling was the bug: pointing the renderer at a different
    endpoint silently swapped the whole cast, so the cast page listed one set of voices and the
    audio rendered another. Nothing failed, because both id sets authenticated. Now the catalog
    is a constant and the host is a URL, and neither can move the other.
    """
    monkeypatch.setenv("DEEPGRAM_API_HOST", OTHER_HOST)
    assert config.active_voice_catalog() is config.VOICE_CATALOG
    assert config.host_voice() == "flux-alexis-en"


def test_the_cast_is_castable_from_the_catalog(monkeypatch):
    """Every voice the show references must exist in the catalog or it renders 400."""
    monkeypatch.delenv("DEEPGRAM_API_HOST", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    assert config.active_voice_catalog() is config.VOICE_CATALOG
    # Alexis since 2026-08-20. This said Haley while `cast.ROLE_VOICES` had been preferring the
    # Alexis ids for weeks, so the legacy v1 path and the panel path cast different hosts.
    assert config.host_voice() == "flux-alexis-en"
    referenced = {config.host_voice(), *config.commenter_voices(), *config.guest_voices()}
    assert referenced <= set(config.VOICE_CATALOG), sorted(referenced - set(config.VOICE_CATALOG))


def test_retries_are_the_configured_budget(monkeypatch):
    """A function rather than a constant, because the long-running scripts raise it by hand.

    The default must stay low: a normal episode would rather fail fast on a real outage than
    hang through a dozen attempts per segment.
    """
    monkeypatch.delenv("DEEPGRAM_API_HOST", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    assert config.http_retries() == config.HTTP_RETRIES == 3
