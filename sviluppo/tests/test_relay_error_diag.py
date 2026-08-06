import sys
import pytest
sys.path.insert(0, 'src')
sys.path.insert(0, '.')
from streaming_relay import StreamingRelay
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
from multidict import CIMultiDict

pytestmark = pytest.mark.asyncio

class FakeContentBoom:
    def __init__(self, with_at_eof: bool):
        self._with_at_eof = with_at_eof
        if with_at_eof:
            self.at_eof = lambda: False

    async def iter_any(self):
        yield b'data: primo\n\n'
        raise RuntimeError('boom-client-gone')

class FakeUpstreamBoom:
    def __init__(self, with_at_eof: bool):
        self.status = 200
        self.closed = False
        self.headers = CIMultiDict({'Content-Type': 'text/event-stream'})
        self._content = FakeContentBoom(with_at_eof)

    async def read(self):
        return b''

    def release(self):
        pass

    @property
    def content(self):
        return self._content

async def _run(with_at_eof: bool):
    logs = []

    async def handler(request):
        up = FakeUpstreamBoom(with_at_eof)
        relay = StreamingRelay(
            request,
            b'{}',
            'mix-am',
            None,
            {},
            set(),
            None,
            'MiniMax-M2.7',
            lambda msg, *a, **k: logs.append(str(msg)),
            lambda *a, **k: None,
            lambda *a, **k: None
        )
        await relay.relay(up)
        return web.Response(text='done')

    app = web.Application()
    app.router.add_post('/test', handler)
    async with TestClient(TestServer(app, host='localhost', port=0)) as client:
        try:
            await client.post('/test')
        except Exception:
            # transport error on client side is expected
            pass
    return logs

async def test_diag_non_rompe_il_relay_senza_at_eof():
    logs = await _run(with_at_eof=False)
    matching = [line for line in logs if 'relay loop ERROR after' in line and 'boom-client-gone' in line]
    assert len(matching) == 1, "Expected exactly one log line with 'relay loop ERROR after' and 'boom-client-gone'. Logs:\n" + "\n".join(logs)
    line = matching[0]
    assert '[diag=NA]' in line, "Expected '[diag=NA]' in line. Line: " + line + "\nLogs:\n" + "\n".join(logs)

async def test_diag_riporta_i_campi_quando_disponibili():
    logs = await _run(with_at_eof=True)
    matching = [line for line in logs if 'relay loop ERROR after' in line]
    assert len(matching) >= 1, "Expected at least one 'relay loop ERROR after' line. Logs:\n" + "\n".join(logs)
    line = matching[0]
    for field in ('client_closing=', 'upstream_eof=', 'exc=RuntimeError', 'sse=True'):
        assert field in line, f"Expected '{field}' in line. Line: {line}\nLogs:\n" + "\n".join(logs)

async def test_log_riporta_i_chunk_gia_inviati():
    logs = await _run(with_at_eof=True)
    matching = [line for line in logs if 'relay loop ERROR after' in line]
    assert len(matching) >= 1, "Expected at least one 'relay loop ERROR after' line. Logs:\n" + "\n".join(logs)
    line = matching[0]
    assert 'after 1 chunks' in line, "Expected 'after 1 chunks' in line. Line: " + line + "\nLogs:\n" + "\n".join(logs)
