"""Il backend locale: isolamento tool e diagnostica che nomina il modello vero.

Due difetti chiusi il 2026-08-19:
  - `forward_local` non filtrava i tool: al modello locale arrivavano i
    server-tool di Anthropic e i tool brandizzati degli altri provider, che non
    puo' eseguire (era l'unico backend senza il choke-point di tool_isolation);
  - il parametro `upstream_model` era dichiarato e mai letto, quindi log ed
    eventi del catalogo nominavano `claude-opus-5` invece di `code-max`.
"""
import json
import sys

import pytest

sys.path.insert(0, 'src')
import local_backend  # noqa: E402
import tool_isolation  # noqa: E402

pytestmark = pytest.mark.asyncio


class _FakeResp:
    def __init__(self, status=200, body=b'{"content":[{"type":"text","text":"ok"}]}'):
        from multidict import CIMultiDict
        self.status = status
        self._body = body
        self.closed = False
        self.headers = CIMultiDict({"Content-Type": "application/json"})

    async def read(self):
        return self._body

    async def release(self):
        self.closed = True


class _FakeRequest:
    method = "POST"
    path_qs = "/v1/messages"
    headers = {}


class _Sessione:
    """Cattura il body che il backend invia davvero all'upstream locale."""

    def __init__(self, status=200):
        self.body_inviato = None
        self._status = status

    async def post(self, url, data=None, headers=None, timeout=None):
        self.body_inviato = data
        return _FakeResp(status=self._status)


CORPO_CON_TOOL = json.dumps({
    "model": "claude-opus-5",
    "messages": [{"role": "user", "content": "ciao"}],
    "tools": [
        {"name": "web_search", "type": "web_search_20250305"},        # server-tool Anthropic
        {"name": "mcp__zai__web_search_prime", "input_schema": {}},   # GLM
        {"name": "mcp__MiniMax__web_search", "input_schema": {}},     # MiniMax
        {"name": "Bash", "input_schema": {}},                         # locale, resta
        {"name": "mcp__context7__docs", "input_schema": {}},          # MCP neutro, resta
    ],
}).encode()


async def _chiama(sess, monkeypatch, log_fn, model="claude-opus-5", upstream="code-max"):
    async def _key():
        return "k"

    monkeypatch.setattr(local_backend, "get_local_key", _key)
    monkeypatch.setattr(local_backend, "get_local_base", lambda: "http://127.0.0.1:4000")
    return await local_backend.forward_local(
        _FakeRequest(), CORPO_CON_TOOL, sess, model,
        log_fn=log_fn, passthrough=True, upstream_model=upstream)


async def test_i_tool_stranieri_non_arrivano_al_modello_locale(monkeypatch):
    sess = _Sessione()
    await _chiama(sess, monkeypatch, lambda *a, **k: None)
    inviati = [t["name"] for t in json.loads(sess.body_inviato).get("tools", [])]
    assert inviati == ["Bash", "mcp__context7__docs"], inviati


async def test_i_log_nominano_il_modello_servito(monkeypatch):
    """Il difetto: gli eventi di categoria 'local' dicevano claude-opus-5."""
    righe = []
    sess = _Sessione()
    await _chiama(sess, monkeypatch, lambda m: righe.append(str(m)))
    assert righe, "forward_local deve loggare il tentativo"
    assert any("code-max" in r for r in righe), righe
    assert not any("claude-opus-5" in r for r in righe), righe


async def test_senza_upstream_model_si_ripiega_sul_richiesto(monkeypatch):
    """Chi chiama senza upstream_model non deve perdere la riga di log."""
    righe = []
    sess = _Sessione()
    await _chiama(sess, monkeypatch, lambda m: righe.append(str(m)),
                  model="code-max", upstream="")
    assert any("code-max" in r for r in righe), righe


async def test_body_senza_tool_resta_identico(monkeypatch):
    """No-op sicuro: se non c'e' niente da togliere, il body non si tocca."""
    corpo = json.dumps({"model": "x", "messages": []}).encode()

    async def _key():
        return "k"

    monkeypatch.setattr(local_backend, "get_local_key", _key)
    monkeypatch.setattr(local_backend, "get_local_base", lambda: "http://127.0.0.1:4000")
    sess = _Sessione()
    await local_backend.forward_local(_FakeRequest(), corpo, sess, "claude-opus-5",
                                      log_fn=lambda *a, **k: None, passthrough=True,
                                      upstream_model="code-max")
    assert sess.body_inviato == corpo


@pytest.mark.filterwarnings("ignore")
async def test_backend_local_riconosciuto_da_tool_isolation():
    """Senza la voce in _BRAND_CHECK il filtro era un no-op silenzioso."""
    assert "local" in tool_isolation._BRAND_CHECK
    assert tool_isolation.is_local_branded_tool({"name": "qualunque"}) is False
