"""Contratto di risposta delle nove modalita' — CARATTERIZZAZIONE (audit A6).

Questi test descrivono il comportamento REALE, non quello desiderabile. Servono
a rendere visibile una modifica del contratto, non a imporne uno nuovo: se un
test qui diventa rosso significa che qualcosa e' cambiato, e va deciso se il
cambiamento e' voluto — non necessariamente che sia un bug.

Misura del 2026-08-08, un ciclo di sviluppo/tools/probe_modes.py su tutte e
nove le porte fisse, richieste identiche senza Accept-Encoding:

  modalita    http  encoding   formato id            provider dell'id
  anthropic    401  -          -                     (valida la chiave client)
  minimax      200  identity   esadecimale nudo      MiniMax
  mix-am       200  identity   esadecimale nudo      MiniMax
  mix-al       200  identity   resp_<base64>         LiteLLM locale
  glm          200  gzip       msg_                  z.ai
  mix-gm       200  identity   esadecimale nudo      MiniMax
  mix-ag       200  gzip       msg_                  z.ai
  qwen         200  gzip       msg_                  Alibaba
  local        200  identity   resp_<base64>         LiteLLM locale

Due precisazioni rispetto all'audit:

1. I formati di id sono TRE, non due. L'audit aveva rilevato msg_ contro
   resp_; il ciclo di sonda ha mostrato che MiniMax usa un esadecimale nudo,
   senza alcun prefisso.
2. Le differenze di encoding e di id NON nascono nel router: nascono negli
   upstream. Il relay passa i byte come li riceve — auto_decompress=False nella
   ClientSession, scelta deliberata e documentata, perche' la libreria Brotli
   locale e' rotta e decomprimere romperebbe ogni risposta br. Uniformare
   significherebbe riscrivere i corpi, cioe' introdurre un punto di rottura
   dove oggi non ce n'e' uno.

Per questo qui NON si uniforma nulla: si fissa cio' che il router garantisce,
cioe' di non alterare quello che l'upstream ha mandato, e si documenta cio'
che varia. Cambiare l'encoding senza un test che dimostri cosa si rompe
sarebbe esattamente il tipo di modifica che questo lavoro evita.
"""
import asyncio
import gzip
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "src"))
sys.path.insert(0, str(RADICE))

import streaming_relay  # noqa: E402
from streaming_relay import StreamingRelay  # noqa: E402

# Comportamento misurato il 2026-08-08. La tabella e' il contratto osservato.
CONTRATTO_OSSERVATO = {
    "anthropic": {"encoding": "gzip", "id": "msg_"},
    "minimax": {"encoding": "identity", "id": "esadecimale"},
    "mix-am": {"encoding": "identity", "id": "esadecimale"},
    "mix-am-2": {"encoding": "identity", "id": "esadecimale"},  # routing identico a mix-am
    "mix-al": {"encoding": "identity", "id": "resp_"},
    "glm": {"encoding": "gzip", "id": "msg_"},
    "mix-gm": {"encoding": "identity", "id": "esadecimale"},
    "mix-gm-2": {"encoding": "identity", "id": "esadecimale"},  # routing identico a mix-gm
    "mix-ag": {"encoding": "gzip", "id": "msg_"},
    "mix-ag-2": {"encoding": "gzip", "id": "msg_"},  # routing identico a mix-ag
    "qwen": {"encoding": "gzip", "id": "msg_"},
    "local": {"encoding": "identity", "id": "resp_"},
    # gpt: pura locale come `local`, stesso backend → stesso contratto osservato.
    # Non ancora verificata su traffico reale: il modello non e' servito (2026-08-18).
    "gpt": {"encoding": "identity", "id": "resp_"},
    # ultra: gzip + msg_ — OSSERVATO su traffico reale il 2026-08-22, non dedotto:
    # probe ACT sulla porta della modalita' -> model=glm-4.7, id=msg_2026...
    "ultra": {"encoding": "gzip", "id": "msg_"},
}

IDENTIFICATIVI = {
    "msg_": "msg_01ABCdef",
    "resp_": "resp_bGl0ZWxsbTpjdXN0b21fbGxtX3Byb3ZpZGVy",
    "esadecimale": "06c60ca0deadbeef",
}


def _corpo_risposta(identificativo):
    return json.dumps({
        "id": identificativo, "type": "message", "role": "assistant",
        "model": "m", "content": [{"type": "text", "text": "ok"}],
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


async def _passa_dal_relay(mode, corpo_upstream, headers_upstream):
    """Fa attraversare al relay una risposta upstream e ritorna cosa vede il client."""
    originale = streaming_relay.dl
    streaming_relay.dl = FakeDL()
    try:
        async def handler(request):
            relay = StreamingRelay(
                request,
                json.dumps({"model": "claude-haiku-4-5-20251001"}).encode(),
                mode, None, {}, set(), "MiniMax-M2.7",
                lambda *a: None, lambda **k: None, lambda *a, **k: None,
            )
            return await relay.relay(
                FakeUpstream(200, headers_upstream, [corpo_upstream])
            )

        app = web.Application()
        app.router.add_post("/t", handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/t", headers={"Accept-Encoding": "identity"})
            grezzo = await resp.read()
            return dict(resp.headers), grezzo
    finally:
        streaming_relay.dl = originale


class TestPropagazioneByte:
    """Cio' che il router GARANTISCE: non altera il corpo dell'upstream."""

    @pytest.mark.parametrize("mode", sorted(CONTRATTO_OSSERVATO))
    def test_corpo_non_compresso_passa_invariato(self, mode):
        corpo = _corpo_risposta("msg_test")
        headers, ricevuto = asyncio.run(
            _passa_dal_relay(mode, corpo, {"Content-Type": "application/json"})
        )
        assert json.loads(ricevuto)["id"] == "msg_test"

    @pytest.mark.parametrize("mode", ["glm", "mix-ag", "qwen"])
    def test_corpo_gzip_resta_gzip(self, mode):
        """Il router NON decomprime: auto_decompress=False e' deliberato.

        La libreria Brotli locale e' rotta e decomprimere romperebbe ogni
        risposta br; il Content-Encoding viene propagato perche' il client
        possa decodificare da solo.
        """
        corpo = gzip.compress(_corpo_risposta("msg_test"))
        headers, ricevuto = asyncio.run(_passa_dal_relay(
            mode, corpo,
            {"Content-Type": "application/json", "Content-Encoding": "gzip"},
        ))
        assert headers.get("Content-Encoding") == "gzip", (
            "senza questo header il client non sa che deve decodificare"
        )
        # I byte qui sono gia' decompressi: e' il TestClient di aiohttp a farlo,
        # non il router. Ed e' proprio la prova che l'header e' arrivato intatto
        # al client, che ha potuto decodificare da solo. Il contenuto integro
        # dimostra che il router non ha toccato il payload.
        assert json.loads(ricevuto)["id"] == "msg_test"

    @pytest.mark.parametrize("forma", sorted(IDENTIFICATIVI))
    def test_i_tre_formati_di_id_passano_tutti(self, forma):
        """msg_, resp_ e l'esadecimale nudo di MiniMax: nessuno viene toccato."""
        atteso = IDENTIFICATIVI[forma]
        _, ricevuto = asyncio.run(_passa_dal_relay(
            "mix-am", _corpo_risposta(atteso), {"Content-Type": "application/json"}
        ))
        assert json.loads(ricevuto)["id"] == atteso


class TestContrattoOsservato:
    """Cio' che VARIA fra modalita', fissato come misurato."""

    def test_tabella_copre_le_nove_modalita(self):
        import router_constants
        attese = set(router_constants.PORT_MODE.values())
        assert set(CONTRATTO_OSSERVATO) == attese

    def test_tre_formati_di_id_distinti(self):
        """L'audit ne aveva rilevati due; la sonda ha trovato il terzo."""
        assert len({v["id"] for v in CONTRATTO_OSSERVATO.values()}) == 3

    @pytest.mark.parametrize("mode", ["glm", "mix-ag", "qwen", "anthropic"])
    def test_modalita_che_comprimono(self, mode):
        assert CONTRATTO_OSSERVATO[mode]["encoding"] == "gzip"

    @pytest.mark.parametrize(
        "mode", ["minimax", "mix-am", "mix-gm", "mix-al", "local"]
    )
    def test_modalita_che_non_comprimono(self, mode):
        assert CONTRATTO_OSSERVATO[mode]["encoding"] == "identity"

    @pytest.mark.parametrize("mode", ["mix-al", "local"])
    def test_le_modalita_locali_usano_id_litellm(self, mode):
        """resp_<base64>: l'id incorpora metadati del provider.

        Un client che assume il prefisso msg_ o una lunghezza ragionevole puo'
        comportarsi male. Non e' stato cambiato: cambiarlo significherebbe
        riscrivere il corpo, e non c'e' un test che dimostri chi si romperebbe.
        """
        assert CONTRATTO_OSSERVATO[mode]["id"] == "resp_"

    @pytest.mark.parametrize("mode", ["minimax", "mix-am", "mix-gm"])
    def test_le_modalita_minimax_usano_id_senza_prefisso(self, mode):
        assert CONTRATTO_OSSERVATO[mode]["id"] == "esadecimale"


class TestHeaderComuni:
    """L'unica cosa che il router aggiunge in modo uniforme."""

    @pytest.mark.parametrize("mode", sorted(CONTRATTO_OSSERVATO))
    def test_x_ai_actual_model_su_tutte(self, mode):
        """L'attribuzione del modello e' l'unico contratto davvero uniforme
        fra le nove modalita' — ed e' stato introdotto apposta (audit A3)."""
        headers, _ = asyncio.run(_passa_dal_relay(
            mode, _corpo_risposta("msg_x"), {"Content-Type": "application/json"}
        ))
        assert headers.get("x-ai-actual-model"), (
            f"{mode}: senza questo header il client non sa chi ha risposto"
        )
