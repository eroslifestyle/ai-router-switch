"""
Test end-to-end per verificare che il timeout di lettura per le richieste non-streaming
viene effettivamente applicato alla singola richiesta upstream, mentre lo streaming
eredita il timeout di sessione. Il test imposta un timeout non-streaming minuscolo
(2 secondi) e un upstream finto che ritarda 4 secondi prima di rispondere; se l'override
è applicato la richiesta non-streaming fallisce (502), mentre quella streaming passa
perché eredita il valore ampio della sessione.
"""

import asyncio
import importlib.util
import os
import pathlib
import sys
import pytest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

pytestmark = pytest.mark.asyncio

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

UPSTREAM_DELAY_SEC = 4.0
NON_STREAM_LIMIT_SEC = 2.0


class Harness:
    def __init__(self):
        self.client = None
        self.session = None
        self.fake_runner = None
        self.proxy = None

    async def start(self):
        # fake upstream ritarda per simulare lentezza rete
        async def fake(request):
            await asyncio.sleep(UPSTREAM_DELAY_SEC)
            return web.json_response({
                "id": "msg_x",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "model": "fake",
                "usage": {"input_tokens": 1, "output_tokens": 1}
            })
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", fake)
        self.fake_runner = web.AppRunner(app)
        await self.fake_runner.setup()
        site = web.TCPSite(self.fake_runner, "127.0.0.1", 0)
        await site.start()
        # recupera la porta assegnata dal sistema
        port = self.fake_runner.addresses[0][1]
        # imposta env prima dell'import del proxy
        os.environ["AIROUTER_ANTHROPIC_UPSTREAM"] = f"http://127.0.0.1:{port}"
        os.environ["AIROUTER_MINIMAX_UPSTREAM"] = f"http://127.0.0.1:{port}"
        os.environ["AIROUTER_NON_STREAM_SOCK_READ_SEC"] = str(NON_STREAM_LIMIT_SEC)
        # carica il modulo proxy
        proxy_spec = importlib.util.spec_from_file_location(
            "proxy_timeout_test", SRC / "ai-router-proxy.py"
        )
        proxy_mod = importlib.util.module_from_spec(proxy_spec)
        proxy_spec.loader.exec_module(proxy_mod)
        self.proxy = proxy_mod
        # prepara sessione client e test client
        self.session = aiohttp.ClientSession()
        app_proxy = self.proxy._make_app(self.session, "minimax")
        self.client = TestClient(TestServer(app_proxy))
        await self.client.start_server()

    async def stop(self):
        if self.client:
            await self.client.close()
        if self.session:
            await self.session.close()
        if self.fake_runner:
            await self.fake_runner.cleanup()


async def test_non_streaming_viene_tagliato_dal_suo_timeout(h):
    # richiesta senza stream deve essere interrotta dal timeout custom
    resp = await h.client.post(
        "/v1/messages",
        headers={
            "x-api-key": "test",
            "X-Claude-Code-Session-Id": "sid-to-1"
        },
        json={
            "model": "MiniMax-M2.7",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "ping"}]
        }
    )
    assert resp.status == 502, (
        "Il timeout non-streaming non è stato applicato; "
        "la richiesta avrebbe dovuto essere tagliata."
    )


async def test_streaming_eredita_il_timeout_di_sessione(h):
    # streaming non riceve override, usa timeout di sessione
    resp = await h.client.post(
        "/v1/messages",
        headers={
            "x-api-key": "test",
            "X-Claude-Code-Session-Id": "sid-stream-1"
        },
        json={
            "model": "MiniMax-M2.7",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True
        }
    )
    assert resp.status == 200, (
        "Lo streaming avrebbe dovuto ereditare il timeout "
        "ampio della sessione e non essere interrotto."
    )
