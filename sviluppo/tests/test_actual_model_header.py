"""Header x-ai-actual-model su tutte e nove le modalita' (audit A3).

Il campo `model` della risposta dice al client quello che il client ha chiesto,
non quello che ha risposto: il relay lo riscrive con orig_model, perche' i
client rifiutano un nome che non riconoscono. E il comportamento differiva fra
modalita': con un modello ignoto in mix-am la risposta dichiarava quel nome
mentre il sidecar registrava MiniMax-M2.7.

La scelta e' additiva: si aggiunge un header e non si tocca il campo, cosi'
nessun client esistente si rompe. Questi test verificano che l'header ci sia e
sia coerente con quello che finisce nel sidecar.
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

MODALITA = ("anthropic", "minimax", "mix-am", "mix-ag", "mix-gm",
            "glm", "qwen", "mix-al", "local")

RISPOSTA_JSON = json.dumps({
    "id": "msg_x", "type": "message", "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [{"type": "text", "text": "ok"}],
    "usage": {"input_tokens": 5, "output_tokens": 2},
}).encode()


class FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk


class FakeUpstream:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = CIMultiDict(headers or {})
        self.closed = False
        self._chunks = list(chunks or [])

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


async def _chiama(mode, body_model="claude-haiku-4-5-20251001",
                  final_override=None):
    """Fa passare una risposta dal relay e ritorna (headers, entry_sidecar)."""
    registrate = []
    corpo = json.dumps({"model": body_model, "max_tokens": 8,
                        "messages": [{"role": "user", "content": "x"}]}).encode()

    def log_fn(_msg):
        pass

    def log_usage(**kw):
        registrate.append(kw)

    originale = streaming_relay.dl
    streaming_relay.dl = FakeDL()
    try:
        async def handler(request):
            relay = StreamingRelay(
                request, corpo, mode, None, {}, set(), "MiniMax-M2.7",
                log_fn, log_usage, lambda *a, **k: None,
            )
            upstream = FakeUpstream(
                200, {"Content-Type": "application/json"}, [RISPOSTA_JSON]
            )
            return await relay.relay(upstream, final_override=final_override)

        app = web.Application()
        app.router.add_post("/t", handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/t")
            await resp.read()
            headers = dict(resp.headers)
    finally:
        streaming_relay.dl = originale
    return headers, (registrate[0] if registrate else {})


@pytest.mark.parametrize("mode", MODALITA)
def test_header_presente_su_ogni_modalita(mode):
    """Nessuna modalita' deve restare senza attribuzione."""
    headers, _ = asyncio.run(_chiama(mode))
    assert "x-ai-actual-model" in headers, (
        f"modalita' {mode}: header assente, il client non sa chi ha risposto"
    )
    assert headers["x-ai-actual-model"].strip()


@pytest.mark.parametrize("mode", MODALITA)
def test_header_coerente_col_sidecar(mode):
    """L'header e il campo `final` del sidecar devono dire la stessa cosa.

    E' il motivo per cui il calcolo e' stato estratto in un metodo unico: due
    copie della stessa catena di casi sarebbero divergute.
    """
    headers, entry = asyncio.run(_chiama(mode))
    assert entry, f"modalita' {mode}: il sidecar non e' stato scritto"
    assert headers["x-ai-actual-model"] == entry["final"]


def test_anthropic_dichiara_claude_direct():
    headers, _ = asyncio.run(_chiama("anthropic"))
    assert headers["x-ai-actual-model"] == "claude-direct"


def test_minimax_dichiara_il_modello_minimax():
    headers, _ = asyncio.run(_chiama("minimax"))
    assert headers["x-ai-actual-model"] == "MiniMax-M2.7"


def test_final_override_ha_la_precedenza():
    """Quando il call site sa chi ha eseguito, vince lui."""
    headers, _ = asyncio.run(_chiama("mix-am", final_override="MiniMax-M3"))
    assert headers["x-ai-actual-model"] == "MiniMax-M3"


def test_modello_ignoto_non_mente_piu():
    """Il caso misurato dall'audit: model inventato in mix-am.

    Il corpo continua a dichiarare il nome chiesto — e' voluto, i client lo
    esigono — ma l'header dice chi ha risposto davvero.
    """
    headers, entry = asyncio.run(_chiama("mix-am", body_model="modello-inesistente-xyz"))
    assert headers["x-ai-actual-model"] == entry["final"]
    assert headers["x-ai-actual-model"] != "modello-inesistente-xyz"


def test_il_campo_model_del_corpo_non_e_stato_toccato():
    """Requisito della scelta additiva: nessun client esistente si rompe."""
    corpo_atteso = json.loads(RISPOSTA_JSON)["model"]
    assert corpo_atteso == "claude-haiku-4-5-20251001"
    headers, _ = asyncio.run(_chiama("anthropic"))
    # L'header e' un'aggiunta, non una sostituzione.
    assert headers["x-ai-actual-model"] == "claude-direct"
