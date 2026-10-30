"""Tee della risposta local: streaming intatto + capture del body a fine stream.

Fix 2026-10-30: prima il ramo local bufferizzava tutto (up.read()) e il primo
byte arrivava solo a generazione finita. Ora TeeResponse inoltra i chunk subito
e passa a on_done la copia del body quando lo stream termina.
"""

import asyncio
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


class _FakeUpstream:
    def __init__(self, chunks=(b"a", b"b", b"c"), read_body=None):
        self.status = 200
        self.headers = {}
        self.content = _FakeContent(list(chunks))
        self._read_body = read_body

    async def read(self):
        return self._read_body


def test_stream_chunks_in_order_and_capture_after_last():
    captured = []
    tee = local_backend.TeeResponse(_FakeUpstream(), lambda body: captured.append(body))

    async def run():
        received = []
        async for chunk in tee.content.iter_any():
            received.append(chunk)
            if len(received) == 1:
                # Dopo il primo chunk il trace non deve essere ancora partito.
                assert captured == []
        return received

    received = asyncio.run(run())
    assert received == [b"a", b"b", b"c"]
    assert captured == [b"abc"]


def test_read_returns_body_and_notifies_on_done():
    captured = []
    tee = local_backend.TeeResponse(
        _FakeUpstream(read_body=b"err"), lambda body: captured.append(body)
    )
    body = asyncio.run(tee.read())
    assert body == b"err"
    assert captured == [b"err"]


def test_getattr_delegates_to_upstream():
    tee = local_backend.TeeResponse(_FakeUpstream(), lambda body: None)
    assert tee.status == 200
    assert tee.content.at_eof() is True


if __name__ == "__main__":
    test_stream_chunks_in_order_and_capture_after_last()
    test_read_returns_body_and_notifies_on_done()
    test_getattr_delegates_to_upstream()
    print("OK")
