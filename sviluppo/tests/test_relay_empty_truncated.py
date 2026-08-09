"""
Test per il rilevamento empty/truncated in StreamingRelay.relay() finally.

Verifica che:
- una risposta 200 con output_tokens=0 e text_blocks=0 emetta
  empty_response_<mode> via dl.capture;
- un eccezione nel loop streaming emetta truncated_response_<mode>.

Il relay e' l'unico imbuto che vede il corpo di tutte le risposte 200 delle
modalita' pure (forward_anthropic/forward_minimax restituiscono la
ClientResponse NON letta), quindi il rilevamento vive qui.
"""
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from multidict import CIMultiDict

pytestmark = pytest.mark.asyncio


class _Content:
    def __init__(self, chunks, raise_after=None):
        self._chunks = list(chunks)
        self._raise_after = raise_after

    async def iter_any(self):
        for i, c in enumerate(self._chunks):
            yield c
        if self._raise_after is not None:
            raise self._raise_after


class _Up:
    """ClientResponse minimale per il relay."""

    def __init__(self, status=200, chunks=None, raise_after=None,
                 content_type="text/event-stream"):
        self.status = status
        self.closed = False
        self.headers = CIMultiDict({"Content-Type": content_type})
        self._content = _Content(chunks or [], raise_after)

    async def read(self):
        return b""

    def release(self):
        self.closed = True

    @property
    def content(self):
        return self._content


async def _run_relay(up, mode):
    """Esegue il relay su un finto upstream e raccoglie le chiamate dl.capture."""
    from streaming_relay import StreamingRelay
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient

    captured = []

    class _OrigDL:
        def capture(self, **kw):
            captured.append(kw)

    import router_debug
    real_dl = router_debug.dl
    router_debug.dl = _OrigDL()
    # Il relay fa `from router_debug import dl` all import time, quindi sostituisce
    # sull istanza del modulo gia caricato:
    import streaming_relay as sr_mod
    real_sr_dl = sr_mod.dl
    sr_mod.dl = _OrigDL()

    async def handler(request):
        relay = StreamingRelay(
            request, b"{}", mode, None, {}, set(),
            "MiniMax-M2.7", lambda *a, **k: None,
            lambda *a, **k: None, lambda *a, **k: None,
        )
        return await relay.relay(up)

    app = web.Application()
    app.router.add_post("/t", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        async with TestClient(TestServer(app, host="localhost", port=0)) as c:
            await c.post("/t")
    finally:
        await runner.cleanup()
        router_debug.dl = real_dl
        sr_mod.dl = real_sr_dl
    return captured


async def test_empty_200_emits_event():
    """200 con solo message_start + message_delta (output_tokens=0, nessun text block)."""
    chunks = [
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"output_tokens":0}}}\n\n',
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":0}}\n\n',
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n',
    ]
    up = _Up(status=200, chunks=chunks)
    captured = await _run_relay(up, mode="minimax")
    kinds = [c.get("kind") for c in captured]
    assert "empty_response_minimax" in kinds, f"empty_response_minimax missing in {kinds}"


async def test_nonempty_200_no_event():
    """200 con output_tokens>0 NON deve emettere empty_response."""
    chunks = [
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"output_tokens":0}}}\n\n',
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","content_block":{"type":"text"}}\n\n',
        b'event: message_delta\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}\n\n',
        b'event: message_stop\n'
        b'data: {"type":"message_stop"}\n\n',
    ]
    up = _Up(status=200, chunks=chunks)
    captured = await _run_relay(up, mode="anthropic")
    kinds = [c.get("kind") for c in captured]
    assert "empty_response_anthropic" not in kinds, f"false positive: {kinds}"


async def test_loop_exception_emits_truncated():
    """Un eccezione nel loop streaming deve emettere truncated_response_<mode>."""
    chunks = [
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"output_tokens":0}}}\n\n',
    ]
    up = _Up(status=200, chunks=chunks,
             raise_after=RuntimeError("simulated upstream disconnect"))
    captured = await _run_relay(up, mode="glm")
    kinds = [c.get("kind") for c in captured]
    assert "truncated_response_glm" in kinds, f"truncated_response_glm missing in {kinds}"


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_empty_200_emits_event())
    asyncio.run(test_nonempty_200_no_event())
    asyncio.run(test_loop_exception_emits_truncated())
    print("self-check OK")
