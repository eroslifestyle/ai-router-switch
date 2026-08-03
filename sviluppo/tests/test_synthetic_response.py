"""Test per synthetic_response — FIX DELLA REGRESSIONE CRITICA.

I backend GLM e Qwen ritornavano una `aiohttp.web.Response` sui percorsi d'errore
(chiave assente, rate-limit del limiter esaurito, tentativi esauriti). Con
passthrough=True quel risultato finisce in `StreamingRelay.relay()`, che chiama
`.release()` e `.content.iter_any()` sull'oggetto: una web.Response non li ha,
e l'AttributeError trasformava il messaggio vero in un 502 opaco
("'Response' object has no attribute 'release'", visto dal vivo sul router il 2026-08-03).
Secondo difetto: un 429 che persisteva su entrambi i tentativi veniva convertito
in 502, facendo perdere al client Retry-After e x-should-retry.
"""
import asyncio
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from synthetic_response import SyntheticResponse, synthetic_error, synthetic_rate_limit

pytestmark = pytest.mark.asyncio


class _FakeRequest:
    """Request finta minimale per testare forward_glm e forward_qwen."""
    method = "POST"
    path_qs = "/v1/messages"
    headers = {}


async def _no_key():
    """Ritorna stringa vuota per simulare chiave assente."""
    return ""


# -----------------------------------------------------------------------------
# Gruppo 1 — contratto della SyntheticResponse
# -----------------------------------------------------------------------------


async def test_release_e_sincrono():
    """Fissa che release() sia sincrono e chiuda l'oggetto."""
    r = SyntheticResponse(200, b"test")
    assert not asyncio.iscoroutinefunction(r.release)
    result = r.release()
    assert result is None
    assert r.closed is True


async def test_headers_case_insensitive():
    """Fissa che gli header siano interrogabili con qualsiasi capitalizzazione."""
    r = SyntheticResponse(
        200,
        b"data",
        headers={"Content-Type": "text/event-stream"}
    )
    assert r.headers.get("content-type") == "text/event-stream"
    assert r.headers.get("Content-Type") == "text/event-stream"


async def test_iter_any_yielda_il_body_intero():
    """Fissa che iter_any() restituisca tutto il body come chunks."""
    body = b"hello world"
    r = SyntheticResponse(200, body)
    chunks = [c async for c in r.content.iter_any()]
    assert b"".join(chunks) == body


async def test_body_vuoto_non_yielda_nulla():
    """Fissa che body vuoto non produca chunk da iter_any()."""
    r = SyntheticResponse(429, b"")
    chunks = [c async for c in r.content.iter_any()]
    assert chunks == []


async def test_read_e_json():
    """Fissa che read() ritorni bytes e json() il dict parsato."""
    payload = json.dumps({"ok": True}).encode()
    r = SyntheticResponse(200, payload)
    assert await r.read() == payload
    assert await r.json() == {"ok": True}


async def test_synthetic_error_formato_anthropic():
    """Fissa che synthetic_error() produca JSON nel formato error Anthropic."""
    resp = synthetic_error(502, "glm_unavailable", "GLM key missing")
    body = json.loads(await resp.read())
    assert resp.status == 502
    assert body["type"] == "error"
    assert body["error"]["type"] == "glm_unavailable"
    assert body["error"]["message"] == "GLM key missing"


async def test_synthetic_rate_limit_preserva_retry_after():
    """Fissa che synthetic_rate_limit() mantenga Retry-After e x-should-retry."""
    resp_with = synthetic_rate_limit("x", retry_after="30")
    assert resp_with.status == 429
    assert resp_with.headers.get("Retry-After") == "30"
    assert resp_with.headers.get("x-should-retry") == "true"

    resp_without = synthetic_rate_limit("x")
    assert "Retry-After" not in resp_without.headers


# -----------------------------------------------------------------------------
# Gruppo 2 — forward_glm e forward_qwen sui percorsi d'errore
# -----------------------------------------------------------------------------


async def test_glm_chiave_assente_passthrough_e_relayabile(monkeypatch):
    """Fissa che con chiave assente e passthrough=True il relay riceva SyntheticResponse."""
    import aiohttp
    import glm_backend

    monkeypatch.setattr(glm_backend, "get_glm_key", _no_key)
    r = await glm_backend.forward_glm(_FakeRequest(), b"{}", None, "glm-4.7", passthrough=True)

    assert not asyncio.iscoroutinefunction(r.release)
    assert hasattr(r, "content")
    assert r.status == 502
    assert b"GLM key missing" in await r.read()
    assert not isinstance(r, aiohttp.web.Response)


async def test_glm_chiave_assente_senza_passthrough_e_web_response(monkeypatch):
    """Fissa che con chiave assente e passthrough=False sia una web.Response."""
    import aiohttp
    import glm_backend

    monkeypatch.setattr(glm_backend, "get_glm_key", _no_key)
    r = await glm_backend.forward_glm(_FakeRequest(), b"{}", None, "glm-4.7", passthrough=False)

    assert isinstance(r, aiohttp.web.Response)
    assert r.status == 502


async def test_qwen_chiave_assente_passthrough_e_relayabile(monkeypatch):
    """Fissa che con chiave assente e passthrough=True il relay riceva SyntheticResponse."""
    import aiohttp
    import qwen_backend

    monkeypatch.setattr(qwen_backend, "get_qwen_key", _no_key)
    r = await qwen_backend.forward_qwen(_FakeRequest(), b"{}", None, "qwen3-coder-plus", passthrough=True)

    assert not asyncio.iscoroutinefunction(r.release)
    assert hasattr(r, "content")
    assert r.status == 502
    assert b"qwen key missing" in await r.read()
    assert not isinstance(r, aiohttp.web.Response)


async def test_qwen_chiave_assente_senza_passthrough_e_web_response(monkeypatch):
    """Fissa che con chiave assente e passthrough=False sia una web.Response."""
    import aiohttp
    import qwen_backend

    monkeypatch.setattr(qwen_backend, "get_qwen_key", _no_key)
    r = await qwen_backend.forward_qwen(_FakeRequest(), b"{}", None, "qwen3-coder-plus", passthrough=False)

    assert isinstance(r, aiohttp.web.Response)
    assert r.status == 502


# -----------------------------------------------------------------------------
# Gruppo 3 — il 429 persistente non diventa piu' un 502
# -----------------------------------------------------------------------------


async def test_429_persistente_resta_429_con_retry_after():
    """Fissa che synthetic_rate_limit() restituisca sempre 429, non 502.

    Il percorso completo (due tentativi entrambi 429) e' gestito dal codice
    di forward_glm/forward_qwen; questo test fissa il contratto dell'oggetto
    che quel percorso ora restituisce.
    """
    resp = synthetic_rate_limit("model", retry_after="7")
    assert resp.status == 429
    assert resp.headers.get("Retry-After") == "7"
    assert resp.headers.get("x-should-retry") == "true"
    body = json.loads(await resp.read())
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert body["error"]["message"] == "model"


class _Fake429:
    """Fake 429 per test retry-after.

    Tre vincoli rispettati per integrarsi col relay:
    - headers e' CIMultiDict (case-insensitive per retry-after)
    - release() e' sincrono (il relay non fa await)
    - .closed esiste (aiohttp lo controlla)
    """
    def __init__(self, retry_after="11"):
        from multidict import CIMultiDict
        self.status = 429
        self.headers = CIMultiDict({"retry-after": retry_after,
                                    "Content-Type": "application/json"})
        self.closed = False

    async def read(self):
        return b'{"error":"rate limited"}'

    def release(self):
        self.closed = True


class _Session429:
    """Sessione finta: risponde 429 a OGNI tentativo, così entrambi falliscono."""
    def __init__(self):
        self.chiamate = 0

    async def request(self, **kw):
        self.chiamate += 1
        return _Fake429()


async def test_429_persistente_non_degrada_mai_a_502(monkeypatch):
    """Fissa la regressione: un 429 upstream resta 429 con Retry-After, mai 502.

    Due rami interni possono chiudere il caso — il secondo tentativo che trova
    ancora 429, oppure il cooldown del limiter che fa scattare RateLimitExhausted
    gia' in acquire. Prima del fix ENTRAMBI finivano su una aiohttp.web.Response
    che il relay non sa gestire, e l'esito che arrivava al client era un 502
    opaco. Il test fissa l'esito, non la strada.
    """
    import aiohttp
    import asyncio
    import glm_backend
    import qwen_backend

    async def _key():
        return "k"

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)
    monkeypatch.setattr(qwen_backend, "get_qwen_key", _key)

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(glm_backend.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(qwen_backend.asyncio, "sleep", _no_sleep)

    casi = ((glm_backend.forward_glm, "glm-4.7", glm_backend.GLM_LIMITER),
            (qwen_backend.forward_qwen, "qwen3-coder-plus", qwen_backend.QWEN_LIMITER))
    try:
        for fn, nome, limiter in casi:
            sess = _Session429()
            r = await fn(_FakeRequest(), b'{}', sess, nome, passthrough=True)
            assert sess.chiamate >= 1, f"{nome}: l'upstream non e' stato interrogato"
            assert r.status == 429, f"{nome}: degradato a {r.status}, doveva restare 429"
            assert r.headers.get("Retry-After"), f"{nome}: Retry-After perso"
            assert r.headers.get("x-should-retry") == "true"
            # il cuore della regressione: dev'essere relayabile
            assert not asyncio.iscoroutinefunction(r.release)
            assert hasattr(r.content, "iter_any")
            assert not isinstance(r, aiohttp.web.Response)
    finally:
        # i limiter sono singleton di modulo: senza reset il cooldown appena
        # innescato resterebbe attivo per gli altri test della stessa sessione.
        for _, _, limiter in casi:
            limiter.on_success()
