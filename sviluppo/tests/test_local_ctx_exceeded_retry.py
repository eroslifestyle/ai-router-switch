"""Retry su 400 exceed_context_size_error del backend locale (2026-10-01).

llama.cpp rifiuta i prompt oltre la finestra con 400 il cui JSON (annidato in
una stringa, avvolto da LiteLLM) contiene n_prompt_tokens e n_ctx. Prima del
fix forward_local inoltrava l'errore tale e quale: la chat restava bloccata.
Ora accorcia il body sul limite reale (n_ctx x 0,9 in token stimati, scalati
dal rapporto reale/stima) e ritenta UNA volta, indipendentemente da
LOCAL_MAX_RETRY.
"""
import json
import sys

import pytest

sys.path.insert(0, 'src')
import local_backend  # noqa: E402
from model_context_map import get_safe_input_limit  # noqa: E402
from token_counter import estimate_tokens_body  # noqa: E402

pytestmark = pytest.mark.asyncio

ERRORE_400 = (
    '{"error":{"message":"litellm.ContextWindowExceededError: litellm.BadRequestError: '
    'OpenAIException - \\"error\\":{\\"code\\":400,\\"message\\":\\"the request '
    '(140206 tokens) exceeds the available context size (131072 tokens). This may be '
    'caused by a too long conversation or a combination of prompt and output tokens.\\",'
    '\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":140206,'
    '\\"n_ctx\\":131072}"}}'
).encode()

ERRORE_400_ALTRO = b'{"error":{"type":"invalid_request_error","message":"campo rifiutato"}}'


class _FakeResp:
    def __init__(self, status, body, content_type="application/json"):
        from multidict import CIMultiDict
        self.status = status
        self._body = body
        self.headers = CIMultiDict({"Content-Type": content_type})
        self.content = self  # richiesto da _StopReasonFixed nel path passthrough 200

    async def read(self):
        return self._body

    async def release(self):
        pass


class _FakeRequest:
    method = "POST"
    path_qs = "/v1/messages"
    headers = {}


class _Sessione:
    """Upstream finto: sequenza di risposte (status, body) e corpi inviati."""

    def __init__(self, risposte):
        self.risposte = list(risposte)
        self.corpi = []

    async def post(self, url, data=None, headers=None, timeout=None):
        self.corpi.append(data)
        tupla = self.risposte.pop(0)
        status, body = tupla[0], tupla[1]
        return _FakeResp(status, body, tupla[2] if len(tupla) > 2 else "application/json")


async def _chiama(sess, monkeypatch, log_fn=lambda *a: None):
    async def _key():
        return "k"

    monkeypatch.setattr(local_backend, "get_local_key", _key)
    monkeypatch.setattr(local_backend, "get_local_base", lambda: "http://127.0.0.1:4000")
    corpo = json.dumps({
        "model": "code-max",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": "x " * 20000}],
    }).encode()
    return await local_backend.forward_local(
        _FakeRequest(), corpo, sess, "claude-opus-5",
        log_fn=log_fn, passthrough=True, upstream_model="code-max")


async def test_ctx_exceeded_accorcia_e_ritenta(monkeypatch):
    """n_prompt appena sopra n_ctx: il retry conserva >=50% dei messaggi e sta
    sotto il limite ricalcolato (n_ctx*0.9 in token stimati)."""
    ok = b'{"content":[{"type":"text","text":"ok"}]}'
    sess = _Sessione([(400, ERRORE_400), (200, ok)])
    righe = []
    resp = await _chiama(sess, monkeypatch, lambda m: righe.append(str(m)))
    assert resp.status == 200
    assert len(sess.corpi) == 2
    orig = json.loads(sess.corpi[0])
    secondo = json.loads(sess.corpi[1])
    assert len(secondo["messages"]) >= len(orig["messages"]) * 0.5, \
        f"messaggi: {len(orig['messages'])} -> {len(secondo['messages'])}"
    stima_nuova = estimate_tokens_body(sess.corpi[1], "code-max")
    limite = int(131072 * 0.9 * estimate_tokens_body(sess.corpi[0], "code-max") / 140206)
    assert stima_nuova <= limite, f"{stima_nuova} > {limite}"
    assert len(sess.corpi[1]) < len(sess.corpi[0]), "il secondo body deve essere corto"
    assert any("ctx-exceeded" in r for r in righe), righe


async def test_ctx_exceeded_doppio_400_inoltra_errore(monkeypatch):
    sess = _Sessione([(400, ERRORE_400), (400, ERRORE_400)])
    resp = await _chiama(sess, monkeypatch)
    assert resp.status == 400
    assert b"exceed_context_size_error" in resp.body
    assert len(sess.corpi) == 2, "esattamente 2 chiamate, niente retry del retry"


async def test_400_non_ctx_in_passthrough_arriva_intiero(monkeypatch):
    """Un 400 qualsiasi in passthrough arriva al client con corpo e status
    corretti (la risposta upstream e' gia' stata letta per il parse)."""
    sess = _Sessione([(400, ERRORE_400_ALTRO)])
    resp = await _chiama(sess, monkeypatch)
    assert resp.status == 400
    assert b"campo rifiutato" in resp.body
    assert resp.content_type == "application/json"
    assert len(sess.corpi) == 1


async def test_ctx_retry_funziona_anche_con_local_max_retry_zero(monkeypatch):
    ok = b'{"content":[{"type":"text","text":"ok"}]}'
    sess = _Sessione([(400, ERRORE_400), (200, ok)])
    monkeypatch.setattr(local_backend, "LOCAL_MAX_RETRY", 0)
    resp = await _chiama(sess, monkeypatch)
    assert resp.status == 200
    assert len(sess.corpi) == 2


def test_limite_sicuro_code_max_sotto_finestra():
    assert get_safe_input_limit("code-max") < 131_072


async def test_502_ritenta_esattamente_local_max_retry_plus_uno(monkeypatch):
    """Con upstream sempre 502: esattamente LOCAL_MAX_RETRY+1 chiamate
    (l'attempt extra di max_attempts serve SOLO al ctx-retry)."""
    N = 2
    sess = _Sessione([(502, b'boom')] * (N + 2))
    monkeypatch.setattr(local_backend, "LOCAL_MAX_RETRY", N)
    resp = await _chiama(sess, monkeypatch)
    assert len(sess.corpi) == N + 1


async def test_400_charset_utf8_arriva_intiero(monkeypatch):
    """content_type con "; charset=..." non deve far esplodere web.Response."""
    sess = _Sessione([(400, ERRORE_400_ALTRO, "application/json; charset=utf-8")])
    resp = await _chiama(sess, monkeypatch)
    assert resp.status == 400
    assert b"campo rifiutato" in resp.body
    assert resp.headers["Content-Type"] == "application/json; charset=utf-8"


def test_parse_ctx_exceeded():
    assert local_backend.parse_ctx_exceeded(ERRORE_400.decode()) == (140206, 131072)
    assert local_backend.parse_ctx_exceeded("errore qualunque") is None
