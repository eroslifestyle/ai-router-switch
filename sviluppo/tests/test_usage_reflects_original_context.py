"""usage.input_tokens al client riflette il body ORIGINALE, non quello riscritto.

Quando il proxy accorcia il body (rewrite di contesto o bottleneck-shrink), la
risposta upstream riporta i token della richiesta ACCORCIATA: Claude Code somma
input+cache dell'ultima risposta per decidere l'autocompact, vede poco, non
compatta mai e la sessione satura oltre la finestra. Il relay aggiunge il delta
a usage.input_tokens del primo evento (message_start in SSE, campo usage in JSON).
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streaming_relay  # noqa: E402
from streaming_relay import StreamingRelay  # noqa: E402


class FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk


class FakeUpstream:
    def __init__(self, headers, chunks):
        self.status = 200
        self.headers = CIMultiDict(headers)
        self.closed = False
        self._chunks = list(chunks)

    async def read(self):
        return b"".join(self._chunks)

    def release(self):
        pass

    @property
    def content(self):
        return FakeContent(self._chunks)


class FakeDL:
    def capture(self, **kw):
        pass


def _msg_start(input_tokens=100):
    return json.dumps({
        "type": "message_start",
        "message": {
            "id": "msg_x", "type": "message", "role": "assistant",
            "model": "claude-haiku-4-5-20251001",
            "usage": {"input_tokens": input_tokens, "output_tokens": 1},
        },
    }).encode()


async def _run(chunks, content_type, delta, orig_model=None):
    """Relay su una risposta sintetica; ritorna (body client, usage del sidecar)."""
    registrate = []
    streaming_relay.dl = FakeDL()
    try:
        async def handler(request):
            relay = StreamingRelay(
                request, b'{"model":"x","messages":[]}', "anthropic", None, {},
                set(), "MiniMax-M2.7", lambda *_: None,
                lambda **kw: registrate.append(kw),
                lambda *a, **k: None,
                usage_delta_tokens=delta,
            )
            up = FakeUpstream({"Content-Type": content_type}, chunks)
            return await relay.relay(up, final_override=orig_model)

        app = web.Application()
        app.router.add_post("/t", handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/t")
            body = await resp.read()
    finally:
        pass
    return body, (registrate[0] if registrate else {})


SSE_HEADERS = {"Content-Type": "text/event-stream"}


def test_sse_message_start_input_tokens_aumentati_del_delta():
    chunk1 = b"event: message_start\ndata: " + _msg_start(100) + b"\n\n"
    chunk2 = b"event: content_block_start\ndata: {\"type\":\"content_block_start\"}\n\n"
    chunk3 = b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
    out, entry = asyncio.run(_run([chunk1, chunk2, chunk3], "text/event-stream", delta=1000))
    assert out == chunk1.replace(b'"input_tokens": 100', b'"input_tokens": 1100') + chunk2 + chunk3
    # il sidecar registra i token PAGATI (upstream), non quelli gonfiati al client
    assert entry["usage"]["input_tokens"] == 100, entry


def test_sse_delta_zero_byte_identico():
    chunk1 = b"event: message_start\ndata: " + _msg_start(100) + b"\n\n"
    chunk2 = b"event: message_stop\ndata: {}\n\n"
    out, _ = asyncio.run(_run([chunk1, chunk2], "text/event-stream", delta=0))
    assert out == chunk1 + chunk2


def test_non_streaming_json_usage_patchato():
    body = json.dumps({
        "id": "msg_x", "type": "message", "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 5, "output_tokens": 2},
    }).encode()
    out, entry = asyncio.run(_run([body], "application/json", delta=777))
    j = json.loads(out)
    assert j["usage"]["input_tokens"] == 5 + 777
    # sidecar: token upstream, non gonfiati
    assert entry["usage"]["input_tokens"] == 5, entry


def test_json_delta_zero_byte_identico():
    body = json.dumps({"usage": {"input_tokens": 5, "output_tokens": 2}}).encode()
    out, _ = asyncio.run(_run([body], "application/json", delta=0))
    assert out == body


def test_glm_input_tokens_nel_message_delta():
    """Dialetto z.ai/GLM: input_tokens vuoto nel start, valorizzato nel delta.

    Il client deve vedere il delta sul message_delta; il message_start a 0 resta
    0. Il sidecar registra il valore upstream (500), non quello gonfiato.
    """
    c1 = b"event: message_start\ndata: " + json.dumps({
        "type": "message_start",
        "message": {"usage": {"input_tokens": 0, "output_tokens": 1}},
    }).encode() + b"\n\n"
    c2 = b"event: message_delta\ndata: " + json.dumps({
        "type": "message_delta", "delta": {},
        "usage": {"input_tokens": 500, "output_tokens": 9},
    }).encode() + b"\n\n"
    out, entry = asyncio.run(_run([c1, c2], "text/event-stream", delta=1000))
    assert b'"input_tokens": 0,' in out
    assert b'"input_tokens": 1500' in out
    assert entry["usage"]["input_tokens"] == 500, entry
