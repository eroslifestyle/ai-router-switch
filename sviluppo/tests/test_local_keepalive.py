"""KeepaliveUpstream: ping SSE durante il prefill silenzioso, poi chunk reali o evento error."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import local_backend  # noqa: E402


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk

    def at_eof(self):
        return True


class _FakeUp:
    def __init__(self, status, chunks=(), body=b""):
        self.status = status
        self.content = _FakeContent(list(chunks))
        self._body = body
        self.closed = False
        self.released = False

    async def read(self):
        return self._body

    def release(self):
        self.released = True


async def _collect(up):
    return [c async for c in up.content.iter_any()]


def test_ping_durante_attesa_poi_chunk_reali(monkeypatch):
    monkeypatch.setattr(local_backend, "SSE_KEEPALIVE_SEC", 0.05)

    async def scenario():
        async def slow():
            await asyncio.sleep(0.18)
            return _FakeUp(200, [b"event: message_start\n", b"data: {}\n\n"])
        up = local_backend.KeepaliveUpstream(asyncio.ensure_future(slow()))
        assert up.status == 200 and "event-stream" in up.headers["content-type"]
        return await _collect(up)

    chunks = asyncio.run(scenario())
    pings = [c for c in chunks if c == local_backend._SSE_PING]
    assert len(pings) >= 2
    assert chunks[-2:] == [b"event: message_start\n", b"data: {}\n\n"]


def test_errore_upstream_diventa_evento_sse_error(monkeypatch):
    monkeypatch.setattr(local_backend, "SSE_KEEPALIVE_SEC", 0.05)

    async def scenario():
        async def fail():
            return _FakeUp(429, body=b'{"error":{"message":"rate"}}')
        up = local_backend.KeepaliveUpstream(asyncio.ensure_future(fail()))
        return await _collect(up)

    chunks = asyncio.run(scenario())
    assert chunks[-1].startswith(b"event: error\ndata: ")
    payload = json.loads(chunks[-1].split(b"data: ", 1)[1])
    assert payload["error"]["type"] == "rate_limit_error"


def test_release_cancella_la_richiesta_pendente():
    async def scenario():
        async def never():
            await asyncio.sleep(10)
        task = asyncio.ensure_future(never())
        up = local_backend.KeepaliveUpstream(task)
        up.release()
        await asyncio.sleep(0)
        return task.cancelled()

    assert asyncio.run(scenario())
