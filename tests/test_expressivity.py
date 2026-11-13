"""Flux expressivity: sent on every Flux call, overridable per voice, dropped when refused, and
part of every render cache key."""

import pytest

from hn_radio import config, pipeline, render
from hn_radio._http import HttpError


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(render, "_expressivity_rejected", set())
    monkeypatch.setattr(config, "SPEAK_EXPRESSIVITY", 1)
    monkeypatch.setattr(config, "SPEAK_EXPRESSIVITY_OVERRIDES", {})


def test_flux_calls_carry_the_show_expressivity():
    assert "expressivity=1" in render._speak_url("flux-cole-en")


def test_aura_never_gets_expressivity():
    assert "expressivity" not in render._speak_url("aura-2-thalia-en")


def test_a_per_voice_override_pulls_one_voice_back(monkeypatch):
    monkeypatch.setattr(config, "SPEAK_EXPRESSIVITY_OVERRIDES", {"flux-heather-en": 0, "flux-kit-en": None})
    assert "expressivity=0" in render._speak_url("flux-heather-en")
    assert "expressivity" not in render._speak_url("flux-kit-en")
    assert "expressivity=1" in render._speak_url("flux-cole-en")


@pytest.mark.parametrize("bad", [3, -3, 1.5, True])
def test_values_off_the_documented_grid_are_refused(monkeypatch, bad):
    monkeypatch.setattr(config, "SPEAK_EXPRESSIVITY", bad)
    with pytest.raises(ValueError):
        config.expressivity_for("flux-cole-en")


def test_a_refused_expressivity_retries_without_it_and_sticks(monkeypatch, capsys):
    urls = []

    def fake_post(url, payload, headers, **kw):
        urls.append(url)
        if "expressivity=" in url:
            raise HttpError(f"POST {url} -> HTTP 400 Bad Request: "
                            '{"err_code":"EXPRESSIVITY_OUT_OF_RANGE"}')
        return b"\x01\x00" * 10

    monkeypatch.setattr(render, "post_json_for_bytes", fake_post)
    assert render.render_segment("hello", "flux-cole-en", "k")
    assert len(urls) == 2 and "expressivity" not in urls[1]
    # Logged once, and the next line goes straight to the default with no wasted request.
    render.render_segment("again", "flux-cole-en", "k")
    assert len(urls) == 3
    assert capsys.readouterr().out.count("rejected expressivity") == 1
    assert render.effective_expressivity("flux-cole-en") is None


def test_a_server_error_is_not_blamed_on_expressivity(monkeypatch):
    """The error text starts with the request URL, which contains `expressivity=1`."""
    def fake_post(url, payload, headers, **kw):
        raise HttpError(f"POST {url} -> HTTP 500 Internal Server Error: boom")

    monkeypatch.setattr(render, "post_json_for_bytes", fake_post)
    with pytest.raises(RuntimeError):
        render.render_segment("hello", "flux-cole-en", "k")
    assert render.effective_expressivity("flux-cole-en") == 1


def test_cache_keys_change_with_expressivity(monkeypatch):
    at_one = render.take_key("flux-cole-en", "hello")
    monkeypatch.setattr(config, "SPEAK_EXPRESSIVITY", None)
    assert render.take_key("flux-cole-en", "hello") != at_one


def test_an_archive_take_is_not_reused_at_a_new_setting():
    class Seg:
        text, voice_id = "hello", "flux-cole-en"

    old = {"text": "hello", "voice_id": "flux-cole-en"}   # rendered before expressivity existed
    assert pipeline._cache_line(old) != pipeline._wanted_line(Seg())
    assert pipeline._cache_line({**old, "expressivity": 1}) == pipeline._wanted_line(Seg())
