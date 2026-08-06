"""Misura il TTFB del proxy in mix-gm con stream:true, contro un upstream SSE lento.

Serve a stabilire per prova, non per lettura del codice, se l'ACT viene bufferizzato:
con un passthrough il primo byte arriva molto prima della fine della generazione.
"""
import asyncio
import importlib.util
import os
import pathlib
import sys
import time

import pytest

# pytest-asyncio gira in modalita strict: senza questo marker il test async del
# modulo verrebbe raccolto ma non eseguito.
pytestmark = pytest.mark.asyncio

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

N_CHUNKS = 6
CHUNK_DELAY_SEC = 0.25
TTFB_MAX_RATIO = 0.5


class Harness:
    def __init__(self):
        # inizializzati qui: stop() viene chiamato anche se start() fallisce a meta'
        self.client = None
        self.session = None
        self.runner = None

    async def start(self):
        from aiohttp import web, ClientSession
        from aiohttp.test_utils import TestServer, TestClient

        # Simula un upstream SSE lento
        async def fake(request):
            resp = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"}
            )
            await resp.prepare(request)
            for i in range(N_CHUNKS):
                await resp.write(
                    b'data: {"type":"content_block_delta","delta":{"text":"c%d"}}\n\n' % i
                )
                await resp.drain()
                await asyncio.sleep(CHUNK_DELAY_SEC)
            await resp.write_eof()
            return resp

        fake_app = web.Application()
        fake_app.router.add_route("*", "/{tail:.*}", fake)

        runner = web.AppRunner(fake_app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]

        # Imposta gli upstream prima dell'import del proxy
        os.environ["AIROUTER_ANTHROPIC_UPSTREAM"] = f"http://127.0.0.1:{port}"
        os.environ["AIROUTER_MINIMAX_UPSTREAM"] = f"http://127.0.0.1:{port}"

        # Importazione del modulo proxy (nome file con trattini)
        spec = importlib.util.spec_from_file_location(
            "proxy_ttfb", SRC / "ai-router-proxy.py"
        )
        proxy_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proxy_mod)

        self.session = ClientSession()
        app = proxy_mod._make_app(self.session, "mix-gm")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

        # Conserva i riferimenti per la chiusura
        self.runner = runner
        self.site = site

    async def stop(self):
        if self.client is not None:
            await self.client.close()
            self.client = None
        if self.session is not None:
            await self.session.close()
            self.session = None
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None


async def test_ttfb_passthrough(h):
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 64,
        "stream": True,
        "messages": [{"role": "user", "content": "ping"}]
    }
    t0 = time.monotonic()
    resp = await h.client.post(
        "/v1/messages",
        json=body,
        headers={"x-api-key": "test", "X-Claude-Code-Session-Id": "sid-ttfb"}
    )
    ttfb = None
    n = 0
    async for chunk in resp.content.iter_any():
        if not chunk:
            continue
        if ttfb is None:
            ttfb = time.monotonic() - t0
        n += 1
    total = time.monotonic() - t0

    assert resp.status == 200, f"Risposta non 200: {resp.status}"
    assert ttfb is not None, "Nessun chunk ricevuto"

    print(
        f"status={resp.status}, ttfb={ttfb:.3f}s, total={total:.3f}s, "
        f"chunks={n}, ratio={ttfb/total:.2f}"
    )

    assert ttfb < total * TTFB_MAX_RATIO, (
        f"TTFB ({ttfb:.3f}s) troppo alto rispetto al totale ({total:.3f}s). "
        "Il relay ha bufferizzato l'intera risposta invece di inoltrarla progressivamente."
    )


async def _run_all():
    h = Harness()
    await h.start()
    try:
        await test_ttfb_passthrough(h)
    finally:
        await h.stop()


def main():
    print("=" * 40)
    asyncio.run(_run_all())
    print("TUTTI I TEST PASSATI")


if __name__ == "__main__":
    main()
