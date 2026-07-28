#!/usr/bin/env python3
"""Test end-to-end del gate di contesto, in-process e con upstream FINTO.

Perché serve: il test statico `test_g1_nessun_400_sintetico` si limita a cercare
stringhe nel sorgente del proxy. Non avrebbe mai visto che il rewrite proattivo
era disattivato da un UnboundLocalError — il codice c'era, semplicemente non
faceva nulla. Qui la richiesta attraversa davvero `handle()`.

Non tocca MAI la porta 8787 live: l'app è costruita con `_make_app` e servita da
un TestServer su porta effimera, con gli upstream rediretti su un server finto
locale.
"""

import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

import pytest

# pytest-asyncio gira in modalita strict: senza questo marker i test async del
# modulo verrebbero raccolti ma non eseguiti. Il marker va dichiarato qui, non
# aggiunto da un hook di collection: a quel punto il plugin ha gia deciso.
pytestmark = pytest.mark.asyncio

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

BODY_CHARS = 3_500_000        # ~875k token stimati = 87,5% di un limite da 1M
MAX_FORWARDED_BYTES = 100_000  # oltre significa che il rewrite non ha agito


def _big_body() -> dict:
    return {
        "model": "claude-opus-4-8",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Z" * BODY_CHARS}],
    }


class Harness:
    """Fake upstream + proxy in-process + catalogo isolato."""

    def __init__(self):
        self.received = {}
        self.session = None
        self.client = None
        self.fake_runner = None
        self.proxy = None
        self.catalog_path = None
        self._orig_catalog = None
        self._debug_catalog = None

    async def start(self):
        from aiohttp import web, ClientSession
        from aiohttp.test_utils import TestServer, TestClient

        async def fake(request):
            self.received["bytes"] = len(await request.read())
            self.received["path"] = request.path
            return web.json_response({
                "id": "msg_fake", "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": "ok"}], "model": "fake",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        fake_app = web.Application(client_max_size=1024 * 1024 * 200)
        fake_app.router.add_route("*", "/{tail:.*}", fake)
        self.fake_runner = web.AppRunner(fake_app)
        await self.fake_runner.setup()
        site = web.TCPSite(self.fake_runner, "127.0.0.1", 0)
        await site.start()
        port = self.fake_runner.addresses[0][1]

        # Gli upstream sono costanti risolte all'import di router_constants:
        # vanno rediretti PRIMA di importare il proxy.
        os.environ["AIROUTER_ANTHROPIC_UPSTREAM"] = f"http://127.0.0.1:{port}"
        os.environ["AIROUTER_MINIMAX_UPSTREAM"] = f"http://127.0.0.1:{port}"

        spec = importlib.util.spec_from_file_location(
            "proxy_under_test", SRC / "ai-router-proxy.py")
        self.proxy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.proxy)

        # Catalogo isolato: il test non deve sporcare logs/BUG-CATALOG.jsonl.
        import debug_catalog
        self._debug_catalog = debug_catalog
        self._orig_catalog = debug_catalog.CATALOG_JSONL
        handle, tmp_path = tempfile.mkstemp(prefix="ctx_gate_catalog_", suffix=".jsonl")
        os.close(handle)
        self.catalog_path = pathlib.Path(tmp_path)
        debug_catalog.CATALOG_JSONL = self.catalog_path
        debug_catalog._catalog_cache["data"] = None
        debug_catalog._catalog_cache["ts"] = 0

        self.session = ClientSession()
        app = self.proxy._make_app(self.session, "anthropic")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def post(self, body: dict, sid: str):
        self.received.clear()
        return await self.client.post(
            "/v1/messages", json=body,
            headers={"x-api-key": "test", "X-Claude-Code-Session-Id": sid})

    def catalog_entries(self) -> list:
        entries = []
        try:
            with self.catalog_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except FileNotFoundError:
            pass
        return entries

    async def stop(self):
        if self.client is not None:
            await self.client.close()
        if self.session is not None:
            await self.session.close()
        if self.fake_runner is not None:
            await self.fake_runner.cleanup()
        if self._debug_catalog is not None and self._orig_catalog is not None:
            self._debug_catalog.CATALOG_JSONL = self._orig_catalog
            self._debug_catalog._catalog_cache["data"] = None
            self._debug_catalog._catalog_cache["ts"] = 0
        if self.catalog_path is not None:
            self.catalog_path.unlink(missing_ok=True)


async def test_gate_inoltra_oltre_limite(h: Harness):
    """Il router NON deve emettere un 400 di contesto tutto suo."""
    resp = await h.post(_big_body(), "sid-e2e-1")
    text = await resp.text()

    assert resp.status == 200, f"Status inatteso: {resp.status} — {text[:200]}"
    assert "Usa /compact" not in text, "Il router ha emesso il 400 sintetico di contesto"
    assert h.received, "La richiesta non è stata inoltrata all'upstream"
    print("  test_gate_inoltra_oltre_limite: OK")


async def test_gate_riscrive_in_zona_morta(h: Harness):
    """Copre due bug reali insieme.

    1) La zona morta fra il safe input limit (800k) e la soglia compact (90% di
       1M): il body sta sotto compact ma sopra il limite sicuro, e prima nessuno
       interveniva — il buffer del 20% riservato all'output veniva mangiato.
    2) L'UnboundLocalError causato da un `from model_context_map import
       get_safe_input_limit` locale dentro `handle()`: rendeva la variabile
       locale all'intera funzione, il safe limit finiva a 0 e il rewrite
       proattivo non scattava mai, in silenzio.
    """
    body = _big_body()
    original_len = len(json.dumps(body).encode())

    await h.post(body, "sid-e2e-2")

    forwarded = h.received.get("bytes", original_len)
    assert forwarded < MAX_FORWARDED_BYTES, \
        f"Body non riscritto: inoltrati {forwarded:,} byte"
    assert forwarded < original_len // 10, \
        f"Riduzione insufficiente: {forwarded:,} su {original_len:,} byte"
    print("  test_gate_riscrive_in_zona_morta: OK")


async def test_ctx_gate_telemetria(h: Harness):
    """L'evento ctx_gate deve conservare i dati diagnostici.

    `record_event` accettava `detail` e lo scartava: nel catalogo restavano solo
    firma e contatore, quindi la telemetria del gate era cieca per costruzione.
    """
    await h.post(_big_body(), "sid-e2e-3")

    eventi = [e for e in h.catalog_entries() if e.get("kind") == "ctx_gate"]
    assert eventi, "Nessun evento ctx_gate registrato"

    detail_raw = eventi[-1].get("example_detail")
    assert detail_raw, "L'evento ctx_gate non conserva example_detail"
    detail = json.loads(detail_raw)

    attese = ("est_tokens", "limit", "safe_limit", "pct", "body_bytes",
              "rewritten_bytes", "msgs", "last_user_prefix", "heartbeat")
    mancanti = [k for k in attese if k not in detail]
    assert not mancanti, f"Chiavi mancanti nel detail: {mancanti}"
    assert detail["rewritten_bytes"] < detail["body_bytes"], \
        f"rewritten_bytes {detail['rewritten_bytes']} non minore di body_bytes {detail['body_bytes']}"
    print("  test_ctx_gate_telemetria: OK")


async def _run_all():
    h = Harness()
    await h.start()
    try:
        await test_gate_inoltra_oltre_limite(h)
        await test_gate_riscrive_in_zona_morta(h)
        await test_ctx_gate_telemetria(h)
    finally:
        await h.stop()


def main():
    print("=" * 40)
    asyncio.run(_run_all())
    print("=" * 40)
    print("TUTTI I TEST PASSATI")


if __name__ == "__main__":
    main()
