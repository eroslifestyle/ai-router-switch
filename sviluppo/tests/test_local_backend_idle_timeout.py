"""forward_local deve usare un idle-read timeout, non solo un tetto totale.

Root cause (audit 2026-09-02, sviluppo/audit/2026-09-02-audit-velocita-reale/
REPORT.md §4.1): con timeout=ClientTimeout(total=LOCAL_TIMEOUT_SEC) una
connessione completamente silenziosa (nessun byte, nessun output utile) restava
appesa fino al tetto pieno (240s, misurato: 63 casi su code-max con outcome
"empty" a status 200 dopo 241s). Un ClientTimeout con sock_read piu' basso del
total abortisce lo stallo molto prima, SENZA toccare il tetto totale che
protegge dal deadlock GPU documentato nel commento sopra LOCAL_TIMEOUT_SEC
(il client Claude Code abbandona a 300s: il router deve scadere prima, o lo
slot GPU resta occupato da una richiesta che nessuno sta piu' aspettando).

Questo test verifica che entrambe le soglie vengano passate a session.post(),
e che il tetto totale (invariato) resti sotto i 300s del client.
"""
import json
import sys

import pytest
from aiohttp import ClientTimeout

sys.path.insert(0, 'src')
import local_backend  # noqa: E402

pytestmark = pytest.mark.asyncio


class _FakeResp:
    def __init__(self, status=200, body=b'{"content":[{"type":"text","text":"ok"}]}'):
        from multidict import CIMultiDict
        self.status = status
        self._body = body
        self.headers = CIMultiDict({"Content-Type": "application/json"})

    async def read(self):
        return self._body

    async def release(self):
        pass


class _FakeRequest:
    method = "POST"
    path_qs = "/v1/messages"
    headers = {}


class _SessioneCatturaTimeout:
    def __init__(self):
        self.timeout_ricevuto = None

    async def post(self, url, data=None, headers=None, timeout=None):
        self.timeout_ricevuto = timeout
        return _FakeResp()


async def test_session_post_riceve_sia_idle_che_totale(monkeypatch):
    monkeypatch.setattr(local_backend.secrets_provider, "get_secret",
                         lambda *a, **kw: "fake-key")
    monkeypatch.setattr(local_backend, "get_local_key",
                         lambda: _async_return("fake-key"))
    monkeypatch.setattr(local_backend, "get_local_base", lambda: "http://127.0.0.1:8083")

    sessione = _SessioneCatturaTimeout()
    body = json.dumps({"model": "code-max", "messages": []}).encode()

    await local_backend.forward_local(
        _FakeRequest(), body, sessione, "claude-opus-5",
        log_fn=lambda *a, **kw: None, passthrough=True, upstream_model="code-max",
    )

    t = sessione.timeout_ricevuto
    assert isinstance(t, ClientTimeout)
    assert t.total == local_backend.LOCAL_TIMEOUT_SEC
    assert t.sock_read == local_backend.LOCAL_IDLE_READ_SEC


async def test_idle_read_e_piu_stretto_del_totale():
    """Se l'idle timeout non fosse piu' stretto del totale, non abortirebbe mai
    prima: sarebbe un no-op che non risolve nulla."""
    assert local_backend.LOCAL_IDLE_READ_SEC < local_backend.LOCAL_TIMEOUT_SEC


async def test_totale_resta_sotto_i_300s_del_client():
    """Vincolo di sistema invariato (commento sopra LOCAL_TIMEOUT_SEC): il
    router deve scadere PRIMA dei 300s di default del client Claude Code,
    altrimenti lo slot GPU resta occupato da una richiesta abbandonata."""
    assert local_backend.LOCAL_TIMEOUT_SEC < 300


def _async_return(value):
    async def _inner(*a, **kw):
        return value
    return _inner()
