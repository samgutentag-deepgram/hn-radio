"""Pin every test to the default Deepgram host unless it asks for otherwise.

Without this the suite reads the developer's `.env`, so a machine pointed at a different
endpoint gets a different base URL than CI or a teammate. Tests would pass or fail depending on
whose laptop they ran on, which is the exact failure this fixture exists to prevent. It showed
up for real: repointing `.env` broke 29 tests that had nothing to do with the
change.

A test that wants a different host opts in explicitly with
`monkeypatch.setenv("DEEPGRAM_API_HOST", ...)`, which still works because this fixture only
clears the value, it does not lock it.
"""

import os

import pytest

from hn_radio import config


# Settings whose `.env` value must never reach the suite. Each describes how one developer's
# laptop is set up right now, and each changes what the pipeline produces, so leaving any of them
# readable makes tests pass or fail per machine. HN_RADIO_MUSIC joined the list when the deploy
# switch landed: setting it to 0 to work on scripts without waiting for beds would otherwise
# quietly turn the music tests into no-ops. HN_RADIO_BASE_URL joined it when
# `SITE_BASE_URL` became `site_base_url()` and started reading `.env` like everything else here --
# a developer who points their local feed at their Fly app would otherwise bake that host into
# every URL the feed tests build.
_MASKED_FROM_DOT_ENV = {"DEEPGRAM_API_HOST", "HN_RADIO_MUSIC", "HN_RADIO_BASE_URL"}


@pytest.fixture(autouse=True)
def _production_host_by_default(monkeypatch):
    for name in sorted(_MASKED_FROM_DOT_ENV):
        monkeypatch.delenv(name, raising=False)
    # config falls back to reading .env directly, so neutralize that path too. Only the names
    # above are masked; the real reader still serves keys and everything else.
    real = config._read_env_var

    def _masked(name: str):
        if name in _MASKED_FROM_DOT_ENV:
            # Honor an explicit setenv from a test that wants another host; only the
            # .env file fallback is suppressed. Returning None unconditionally here
            # would make the host selector impossible to test at all.
            return os.environ.get(name)
        return real(name)

    monkeypatch.setattr(config, "_read_env_var", _masked)


@pytest.fixture(autouse=True)
def _fresh_render_limits():
    """Start every test with an empty render quota and a free render slot.

    The suite shares one process and `backend.limits` holds its state there on purpose, so without
    this a test that posts to a render endpoint three times decides whether an unrelated test later
    sees a 429. Reset before AND after: before so this test is clean, after so a test that leaves
    the slot held (deliberately, to prove the single-flight guard) cannot wedge everything below it.
    """
    from backend import limits
    limits.reset()
    yield
    limits.reset()
