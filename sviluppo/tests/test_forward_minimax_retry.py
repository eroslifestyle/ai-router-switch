"""Test per il retry su 429/529 in forward_minimax (cluster MiniMax overloaded).
I test usano mock puri, nessuna rete reale.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))


class _FakeRequest:
    """Request finta minimale per forward_minimax."""
    method = "POST"
    path_qs = "/v1/messages"
    headers = {}
    remote = None


class _FakeResp:
    """Fake aiohttp ClientResponse per i test di forward_minimax."""
    def __init__(self, status=200, body=b"", content_type="application/json"):
        from multidict import CIMultiDict
        self.status = status
        self._body = body
        self.closed = False
        self.headers = CIMultiDict({"Content-Type": content_type})

    async def read(self):
        return self._body

    async def release(self):
        self.closed = True


class _RetryOnce529ThenOk:
    """Sessione fake: primo tentativo 529 (cluster overload), secondo sano."""
    def __init__(self):
        self.call_count = 0

    async def request(self, method, url, **kw):
        self.call_count += 1
        if self.call_count == 1:
            return _FakeResp(529, b'{"error":"cluster overload"}')
        return _FakeResp(200, b'{"id":"msg_ok","output_tokens":42}')


class _Always529:
    """Sessione fake: sempre 529, per verificare che il budget di retry sia rispettato."""
    def __init__(self):
        self.call_count = 0

    async def request(self, method, url, **kw):
        self.call_count += 1
        return _FakeResp(529, b'{"error":"cluster overload"}')


BODY = b'{"model":"claude-3-5-haiku-20241022","messages":[{"role":"user","content":"hi"}],"max_tokens":10}'


def _install_fake_clock(monkeypatch, forward_minimax):
    """Orologio finto condiviso: asyncio.sleep(d) avanza il clock di d invece di
    aspettare davvero, cosi' i cooldown di MinimaxRateLimiter (basati su
    time.monotonic()) si risolvono senza rallentare il test."""
    import time as time_module
    state = {"t": 1000.0}

    def _fake_monotonic():
        return state["t"]

    async def _fake_sleep(dur):
        state["t"] += dur

    monkeypatch.setattr(time_module, "monotonic", _fake_monotonic)
    monkeypatch.setattr(forward_minimax.asyncio, "sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_forward_minimax_529_then_success(monkeypatch):
    """Primo tentativo 529 -> retry -> secondo tentativo sano -> ritorna quello (status 200)."""
    import forward_minimax
    from router_utils import MinimaxRateLimiter

    async def _key():
        return "k"

    monkeypatch.setattr(forward_minimax, "get_minimax_key", _key)
    monkeypatch.setattr(forward_minimax, "MINIMAX_LIMITER", MinimaxRateLimiter())
    _install_fake_clock(monkeypatch, forward_minimax)

    sess = _RetryOnce529ThenOk()
    result = await forward_minimax.forward_minimax(_FakeRequest(), BODY, sess)

    assert sess.call_count == 2
    assert result.status == 200


@pytest.mark.asyncio
async def test_forward_minimax_529_exhausts_budget_returns_429(monkeypatch):
    """Cluster sempre 529: il retry budget si esaurisce e torna un synthetic 429, non un 529 crudo al client."""
    import forward_minimax
    from router_utils import MinimaxRateLimiter

    async def _key():
        return "k"

    monkeypatch.setattr(forward_minimax, "get_minimax_key", _key)
    monkeypatch.setattr(forward_minimax, "MINIMAX_LIMITER", MinimaxRateLimiter())
    _install_fake_clock(monkeypatch, forward_minimax)

    sess = _Always529()
    result = await forward_minimax.forward_minimax(
        _FakeRequest(), BODY, sess, retry_budget_sec=0.001
    )

    assert result.status == 429
    assert sess.call_count >= 1
