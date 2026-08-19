"""Test per _glm_is_empty e il retry su empty-response in forward_glm.

Il rilevamento (GLM 200 ma body vuoto) è nel path passthrough per i modelli glm-5.x.
I test usano mock puri, nessuna rete reale.
"""
import asyncio
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

import synthetic_response

# pytestmark = pytest.mark.asyncio  # applicato solo ai test async


class _FakeRequest:
    """Request finta minimale per forward_glm."""
    method = "POST"
    path_qs = "/v1/messages"
    headers = {}


# -----------------------------------------------------------------------------
# Test diretti su _glm_is_empty
# -----------------------------------------------------------------------------


def test_glm_is_empty_zero_output_tokens():
    """Body con output_tokens:0 in un delta → vuoto."""
    from glm_backend import _glm_is_empty
    body = b'{"output_tokens":0}'
    assert _glm_is_empty(body) is True


def test_glm_is_empty_nonzero_output_tokens():
    """Body con output_tokens:42 (l'ultimo match è 42) → non vuoto."""
    from glm_backend import _glm_is_empty
    body = b'{"output_tokens":0}{"output_tokens":42}'
    assert _glm_is_empty(body) is False


def test_glm_is_empty_no_match():
    """Body senza campo output_tokens → comportamento conservativo (False)."""
    from glm_backend import _glm_is_empty
    body = b'{"model":"glm-5.3","finish_reason":"stop"}'
    assert _glm_is_empty(body) is False


def test_glm_is_empty_empty_bytes():
    """Body vuoto → True."""
    from glm_backend import _glm_is_empty
    assert _glm_is_empty(b"") is True


# -----------------------------------------------------------------------------
# Helper: fake response
# -----------------------------------------------------------------------------

class _FakeResp:
    """Fake aiohttp ClientResponse per i test di forward_glm."""
    def __init__(self, status=200, body=b"", content_type="application/json"):
        from multidict import CIMultiDict
        self.status = status
        self._body = body
        self.closed = False
        self.headers = CIMultiDict({"Content-Type": content_type})

    async def read(self):
        return self._body

    def release(self):
        self.closed = True


class _RetryOnceThenOk:
    """Sessione fake: primo tentativo vuoto, secondo sano."""
    def __init__(self):
        self.call_count = 0

    async def request(self, **kw):
        self.call_count += 1
        if self.call_count == 1:
            return _FakeResp(200, b'{"output_tokens":0}')
        else:
            return _FakeResp(200, b'{"output_tokens":42}{"model":"glm-5.3"}')


class _BothEmpty:
    """Sessione fake: entrambi i tentativi vuoti."""
    def __init__(self):
        self.call_count = 0

    async def request(self, **kw):
        self.call_count += 1
        return _FakeResp(200, b'{"output_tokens":0}')


class _Glm4Passthrough:
    """Sessione fake per glm-4.7: NON viene letta in passthrough (fast-path)."""
    def __init__(self):
        self.read_called = False

    async def request(self, **kw):
        resp = _FakeResp(200, b'{"model":"glm-4.7","content":"ok"}')
        orig_read = resp.read

        async def tracked_read():
            self.read_called = True
            return await orig_read()

        resp.read = tracked_read
        return resp


# -----------------------------------------------------------------------------
# Test di forward_glm con retry su empty-response
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forward_glm_glm5_empty_then_retry_then_success(monkeypatch):
    """Primo tentativo vuoto → retry → secondo tentativo sano → ritorna quello."""
    import glm_backend
    import synthetic_response  # lazy: sempre fresco da sys.modules

    async def _key():
        return "k"

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)
    monkeypatch.setattr(glm_backend.asyncio, "sleep", _no_sleep)

    sess = _RetryOnceThenOk()
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "glm-5.3", passthrough=True
    )

    assert sess.call_count == 2, "Devono essere stati fatti 2 tentativi"
    assert isinstance(result, synthetic_response.SyntheticResponse)
    assert result.headers.get("x-ai-router") is None
    body = await result.read()
    assert b"output_tokens" in body and b"42" in body


@pytest.mark.asyncio
async def test_forward_glm_glm5_empty_twice_returns_502(monkeypatch):
    """Entrambi i tentativi vuoti → 502 con x-should-retry."""
    import glm_backend

    async def _key():
        return "k"

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)
    monkeypatch.setattr(glm_backend.asyncio, "sleep", _no_sleep)

    sess = _BothEmpty()
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "glm-5.3", passthrough=True
    )

    assert sess.call_count == 2, "Devono essere stati fatti 2 tentativi"
    assert result.status == 502
    assert result.headers.get("x-should-retry") == "true"
    body = await result.read()
    assert b"empty" in body or b"502" in body


@pytest.mark.asyncio
async def test_forward_glm_glm47_passthrough_unchanged(monkeypatch):
    """glm-4.7 (non glm-5.x) bypassa la logica empty-check e ritorna resp raw."""
    import glm_backend

    async def _key():
        return "k"

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)

    sess = _Glm4Passthrough()
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "glm-4.7", passthrough=True
    )

    assert sess.read_called is False, (
        "glm-4.7 passthrough non deve chiamare resp.read() — "
        "deve tornare l'oggetto raw"
    )
    assert isinstance(result, _FakeResp)


@pytest.mark.asyncio
async def test_buffered_upstream_no_x_ai_router_header():
    """buffered_upstream rimuove x-ai-router:synthetic dalla risposta reale."""
    resp = synthetic_response.buffered_upstream(
        status=200,
        body=b'{"model":"glm-5.3"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.headers.get("x-ai-router") is None
    assert resp.status == 200


# -----------------------------------------------------------------------------
# Ramo STREAMING (SSE): peek dell'inizio, mai buffering dell'intero stream
# -----------------------------------------------------------------------------

_SSE_SANO = [b'event: message_start\ndata: {}\n\n',
             b'event: content_block_start\ndata: {}\n\n',
             b'event: content_block_delta\ndata: {"text":"ok"}\n\n',
             b'event: message_stop\ndata: {}\n\n']
_SSE_VUOTO = [b'event: message_start\ndata: {}\n\n',
              b'event: message_stop\ndata: {}\n\n']


class _FakeSseContent:
    """StreamReader finto: i byte si consumano una volta sola, come in aiohttp."""

    def __init__(self, pezzi):
        self._pezzi = list(pezzi)

    def at_eof(self):
        return not self._pezzi

    async def iter_any(self):
        while self._pezzi:
            yield self._pezzi.pop(0)


class _FakeSseResp:
    def __init__(self, pezzi):
        from multidict import CIMultiDict
        self.status = 200
        self.closed = False
        self.headers = CIMultiDict({"Content-Type": "text/event-stream"})
        self.content = _FakeSseContent(pezzi)
        self.read_chiamata = False

    async def read(self):
        self.read_chiamata = True
        return b"".join(self.content._pezzi)

    def release(self):
        self.closed = True


class _SessioneSse:
    def __init__(self, sequenze):
        self.sequenze = list(sequenze)
        self.call_count = 0
        self.ultima = None

    async def request(self, **kw):
        self.ultima = _FakeSseResp(self.sequenze[min(self.call_count, len(self.sequenze) - 1)])
        self.call_count += 1
        return self.ultima


async def _prepara(monkeypatch, glm_backend):
    async def _key():
        return "k"

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)
    monkeypatch.setattr(glm_backend.asyncio, "sleep", _no_sleep)


@pytest.mark.asyncio
async def test_sse_sano_non_viene_bufferizzato(monkeypatch):
    """Il caso che protegge il TTFB: lo stream sano NON deve passare da read()."""
    import glm_backend
    await _prepara(monkeypatch, glm_backend)

    sess = _SessioneSse([_SSE_SANO])
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "claude-sonnet-5", passthrough=True,
        upstream_model="glm-5.3")

    assert sess.call_count == 1
    assert sess.ultima.read_chiamata is False, "read() sull'intero stream = buffering"
    visti = b""
    async for c in result.content.iter_any():
        visti += c
    assert visti == b"".join(_SSE_SANO), "il client deve rivedere tutti i byte, in ordine"


@pytest.mark.asyncio
async def test_sse_vuoto_ritenta_e_poi_502(monkeypatch):
    """Stream che si chiude senza contenuto: si ritenta, poi 502 con x-should-retry."""
    import glm_backend
    await _prepara(monkeypatch, glm_backend)

    sess = _SessioneSse([_SSE_VUOTO, _SSE_VUOTO])
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "claude-sonnet-5", passthrough=True,
        upstream_model="glm-5.3")

    assert sess.call_count == 2, "il primo vuoto deve produrre un retry"
    assert result.status == 502
    assert result.headers.get("x-should-retry") == "true"


@pytest.mark.asyncio
async def test_sse_vuoto_poi_sano_ritorna_il_sano(monkeypatch):
    import glm_backend
    await _prepara(monkeypatch, glm_backend)

    sess = _SessioneSse([_SSE_VUOTO, _SSE_SANO])
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "claude-sonnet-5", passthrough=True,
        upstream_model="glm-5.3")

    assert sess.call_count == 2
    visti = b""
    async for c in result.content.iter_any():
        visti += c
    assert b"content_block_start" in visti


@pytest.mark.asyncio
async def test_sse_di_glm4_non_viene_sbirciato(monkeypatch):
    """Il peek riguarda i glm-5.x: glm-4.7 resta passthrough puro."""
    import glm_backend
    await _prepara(monkeypatch, glm_backend)

    sess = _SessioneSse([_SSE_VUOTO])
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "claude-haiku-4-5-20251001", passthrough=True,
        upstream_model="glm-4.7")

    assert sess.call_count == 1, "nessun retry: il ramo non si applica"
    assert result is sess.ultima, "deve tornare la ClientResponse originale"


@pytest.mark.asyncio
async def test_sse_compresso_non_viene_sbirciato(monkeypatch):
    """Con content-encoding i marcatori non sono leggibili nei byte grezzi:
    meglio non decidere che decidere male."""
    import glm_backend
    await _prepara(monkeypatch, glm_backend)

    class _SessioneGzip(_SessioneSse):
        async def request(self, **kw):
            resp = await super().request(**kw)
            resp.headers["Content-Encoding"] = "gzip"
            return resp

    sess = _SessioneGzip([_SSE_VUOTO])
    result = await glm_backend.forward_glm(
        _FakeRequest(), b"{}", sess, "claude-sonnet-5", passthrough=True,
        upstream_model="glm-5.3")

    assert sess.call_count == 1
    assert result is sess.ultima
