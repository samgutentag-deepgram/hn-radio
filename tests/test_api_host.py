"""The Deepgram API host is selectable, and production is the default.

The original reason is now history and is kept as history: the pre-GA Flux voices (the
ga_candidate and _studio ids) lived only on console staging until GA, and the key on hand 401d
against production, so a hardcoded host made it impossible to preview the cast. GA landed
2026-08-12 and a working production key landed 2026-08-22, so local runs and the Fly deploy now
both reach api.deepgram.com. The selector stays because "production unless told otherwise" is the
property worth pinning: it is what stops the deploy drifting onto staging by accident.

There was a `_reloaded()` helper here that re-imported config to re-read the environment. Deleted
2026-08-22: it never had a caller, and it was worse than dead weight because four tests in this
file monkeypatch config attributes and a reload would throw those away. See config.py's note on
why api_host() is a function and test_feed.py on why reload was removed.
"""


from hn_radio import config, render




def test_api_host_defaults_to_production(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_HOST", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    assert config.api_host() == "api.deepgram.com"


def test_api_host_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_HOST", "api.staging.deepgram.com")
    assert config.api_host() == "api.staging.deepgram.com"


def test_api_host_strips_a_scheme_if_someone_pastes_a_url(monkeypatch):
    # Easy mistake, and it would otherwise produce https://https://host/v2/speak.
    monkeypatch.setenv("DEEPGRAM_API_HOST", "https://api.staging.deepgram.com/")
    assert config.api_host() == "api.staging.deepgram.com"


def test_speak_url_follows_the_configured_host(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_HOST", "api.staging.deepgram.com")
    flux = render._speak_url("flux-cole-en")
    aura = render._speak_url("aura-2-thalia-en")
    assert flux.startswith("https://api.staging.deepgram.com/v2/speak?")
    assert aura.startswith("https://api.staging.deepgram.com/v1/speak?")
    assert "model=flux-cole-en" in flux


def test_speak_url_still_defaults_to_production(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_HOST", raising=False)
    monkeypatch.setattr(config, "_read_env_var", lambda name: None)
    assert render._speak_url("flux-cole-en").startswith("https://api.deepgram.com/v2/speak?")


def test_http_retries_is_one_budget_for_every_host(monkeypatch):
    """Retries used to be host-dependent: staging got more, because a cold pre-GA model would 500
    on the first call. With one host that branch is gone, and the budget is the constant unless a
    long-running script raises it deliberately."""
    monkeypatch.setenv("DEEPGRAM_API_HOST", config.DEFAULT_API_HOST)
    production = config.http_retries()
    monkeypatch.setenv("DEEPGRAM_API_HOST", "api.example.invalid")
    assert config.http_retries() == production == config.HTTP_RETRIES


# ELEVEN TESTS WERE DELETED BELOW THIS LINE on 2026-09-16, and they are named here because a
# deleted guard should be findable:
#
#   test_staging_only_voices_are_flagged_as_such
#   test_jun_uses_the_working_id_not_the_demo_sites_stale_one
#   test_staging_gets_more_retries_than_production
#   test_staging_cast_uses_only_the_pre_ga_voices
#   test_production_cast_is_unchanged_and_still_documented_only
#   test_the_staging_cast_is_the_full_featured_eight
#   test_no_production_voice_leaks_into_the_staging_cast
#   test_staging_cast_revoices_every_desk_and_keeps_names_matching
#   test_jun_counts_as_staging_only_despite_a_plain_looking_id
#
# Every one of them was about the pre-GA voice cast: `cast.staging_cast()`, the eight
# `ga_candidate`/`_studio` ids, `config.STAGING_CAST_VOICES`, `config.STAGING_ONLY_VOICES` and
# `config.is_staging_only`. All of that was deleted in the work recovered off the machine on
# 2026-09-05, for the reason the header above already gives: Flux went GA on 2026-08-12, the
# pre-GA ids were dropped from the catalog at GA, and they now 400 in production. There is no
# staging cast to test.
#
# The host SELECTOR is what survived and it is tested above, because "production unless told
# otherwise" is still the property that stops a deploy drifting onto staging.
#
# One of them was doing real work beyond the staging split and has been
# rewritten rather than dropped: `test_staging_gets_more_retries_than_production` was pinning that
# the retry budget is read per call rather than captured, which is still true and is now
# `test_http_retries_is_one_budget_for_every_host`.
