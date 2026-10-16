"""Tests for the upstream allowlist guard in router_constants.

Covers _guarded_upstream: default ok, attacker host -> default, http non-locale
-> default, http locale ammesso, host in AIROUTER_UPSTREAM_ALLOW ammesso,
stringa vuota/spazzatura -> default. Nessun caso deve sollevare eccezioni.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from router_constants import _guarded_upstream

DEFAULT = "https://api.anthropic.com"
ENV = "AIROUTER_ANTHROPIC_UPSTREAM"
ALLOWED = {"api.anthropic.com"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ogni test parte senza le due env var coinvolte."""
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("AIROUTER_UPSTREAM_ALLOW", raising=False)


def test_default_when_env_unset():
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == DEFAULT


def test_allowed_host_accepted(monkeypatch):
    monkeypatch.setenv(ENV, "https://api.anthropic.com/v1")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == "https://api.anthropic.com/v1"


def test_attacker_host_falls_back(monkeypatch):
    monkeypatch.setenv(ENV, "https://evil.example.com")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == DEFAULT


def test_lookalike_host_falls_back(monkeypatch):
    """api.anthropic.com.evil.com non deve passare per suffisso."""
    monkeypatch.setenv(ENV, "https://api.anthropic.com.evil.com")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == DEFAULT


def test_http_non_local_falls_back(monkeypatch):
    monkeypatch.setenv(ENV, "http://api.anthropic.com")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == DEFAULT


def test_http_localhost_accepted(monkeypatch):
    monkeypatch.setenv(ENV, "http://127.0.0.1:9999")
    assert _guarded_upstream(ENV, DEFAULT, {"127.0.0.1"}) == "http://127.0.0.1:9999"


def test_extra_allow_env_accepted(monkeypatch):
    monkeypatch.setenv(ENV, "https://staging.internal")
    monkeypatch.setenv("AIROUTER_UPSTREAM_ALLOW", "staging.internal,other.internal")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == "https://staging.internal"


def test_extra_allow_still_requires_https(monkeypatch):
    monkeypatch.setenv(ENV, "http://staging.internal")
    monkeypatch.setenv("AIROUTER_UPSTREAM_ALLOW", "staging.internal")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == DEFAULT


def test_empty_string_falls_back(monkeypatch):
    monkeypatch.setenv(ENV, "")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == DEFAULT


def test_garbage_falls_back(monkeypatch):
    for junk in ("not a url", "://", "http://", "https://"):
        monkeypatch.setenv(ENV, junk)
        assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == DEFAULT


def test_no_exception_and_uppercase_host(monkeypatch):
    """Host in maiuscolo normalizzato; nessuna eccezione su input limite."""
    monkeypatch.setenv(ENV, "https://API.Anthropic.COM")
    assert _guarded_upstream(ENV, DEFAULT, ALLOWED) == "https://API.Anthropic.COM"


def test_module_constants_guarded(monkeypatch):
    """Le costanti pubbliche restano i default ufficiali a env pulita."""
    import router_constants as c

    assert c.ANTHROPIC_UPSTREAM == "https://api.anthropic.com"
    assert c.MINIMAX_UPSTREAM == "https://api.minimaxi.chat/anthropic"