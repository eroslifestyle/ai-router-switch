"""Test per is_glm_1210, dump_glm_1210_body e il retry 1210 in forward_glm.

Il 1210 è un errore intermittente di z.ai (Invalid API parameter). Test su:
  a) is_glm_1210: riconoscimento di [1210] in JSON grezzo e gzip
  b) dump: salva il body completo, gestione rotazione file
  c) forward_glm: primo 400 1210 → dump + retry → 200 OK
  d) forward_glm: 400 1211 (altro codice) → niente retry, una sola chiamata
"""
import asyncio
import gzip
import json
import pathlib
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

import synthetic_response


class _FakeRequest:
    """Request finta minimale per forward_glm."""
    method = "POST"
    path_qs = "/v1/messages"
    headers = {}


class _FakeResp:
    """Fake aiohttp ClientResponse per i test."""
    def __init__(self, status=200, body=b"", content_type="application/json"):
        from multidict import CIMultiDict
        self.status = status
        self._body = body
        self.closed = False
        self.headers = CIMultiDict({"Content-Type": content_type})

    async def read(self):
        return self._body

    def release(self):
        self.closed = True


# ─────────────────────────────────────────────────────────────────────────────
# Test diretti su is_glm_1210
# ─────────────────────────────────────────────────────────────────────────────


def test_is_glm_1210_with_quoted_code():
    """Riconosce [1210] in JSON con chiavi doppie."""
    from glm_backend import is_glm_1210
    body = b'{"error":{"code":"1210","message":"..."}}'
    assert is_glm_1210(body) is True


def test_is_glm_1210_with_bracket_code():
    """Riconosce [1210] nel messaggio."""
    from glm_backend import is_glm_1210
    body = b'{"error":"[1210][Invalid API parameter]..."}'
    assert is_glm_1210(body) is True


def test_is_glm_1210_gzip():
    """Decomprime gzip e riconosce [1210]."""
    from glm_backend import is_glm_1210
    plain = b'{"error":"[1210]"}'
    compressed = gzip.compress(plain)
    assert is_glm_1210(compressed) is True


def test_is_glm_1210_different_code():
    """Ritorna False per codice diverso."""
    from glm_backend import is_glm_1210
    body = b'{"error":{"code":"1211"}}'
    assert is_glm_1210(body) is False


def test_is_glm_1210_empty():
    """Ritorna False su body vuoto."""
    from glm_backend import is_glm_1210
    assert is_glm_1210(b"") is False


# ─────────────────────────────────────────────────────────────────────────────
# Test su dump_glm_1210_body
# ─────────────────────────────────────────────────────────────────────────────


def test_dump_glm_1210_body_creates_file(tmp_path, monkeypatch):
    """dump_glm_1210_body crea un file nella cartella."""
    from glm_backend import dump_glm_1210_body
    import paths

    # Monkeypatch paths.logs_dir() per usare la tmp_path
    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)

    body = b'{"messages":[...]}'
    error = b'{"error":"[1210]"}'

    result = dump_glm_1210_body(body, error, "glm-5.3", log_fn=None)

    assert result is not None
    assert result.exists()
    assert result.parent.name == "glm-1210-bodies"

    # Verifica il contenuto
    with open(result) as f:
        data = json.load(f)
    assert data["model"] == "glm-5.3"
    assert data["body"] == '{"messages":[...]}'
    assert "[1210]" in data["error"]


def test_dump_glm_1210_body_rotation(tmp_path, monkeypatch):
    """dump_glm_1210_body mantiene solo i 20 file più recenti."""
    from glm_backend import dump_glm_1210_body, GLM_1210_DUMP_MAX
    import paths

    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)

    body = b'body'
    error = b'error'

    # Crea 25 file
    for i in range(25):
        dump_glm_1210_body(body, error, f"glm-{i}", log_fn=None)

    cartella = tmp_path / "glm-1210-bodies"
    files = list(cartella.glob("*.json"))
    assert len(files) == GLM_1210_DUMP_MAX, f"Expected {GLM_1210_DUMP_MAX}, got {len(files)}"


# ─────────────────────────────────────────────────────────────────────────────
# Test di forward_glm con retry 1210
# ─────────────────────────────────────────────────────────────────────────────


class _Session1210ThenOk:
    """Sessione fake: primo tentativo 400 1210, secondo 200 OK."""
    def __init__(self):
        self.call_count = 0

    async def request(self, **kw):
        self.call_count += 1
        if self.call_count == 1:
            return _FakeResp(400, b'{"error":"[1210]"}')
        else:
            return _FakeResp(200, b'{"content":"ok"}')


class _Session1211_NoRetry:
    """Sessione fake: 400 1211 (NON si ritenta)."""
    def __init__(self):
        self.call_count = 0

    async def request(self, **kw):
        self.call_count += 1
        return _FakeResp(400, b'{"error":"[1211]"}')


@pytest.mark.asyncio
async def test_forward_glm_1210_retry_and_success(monkeypatch, tmp_path):
    """Primo tentativo 400 [1210] → dump + retry → secondo 200 OK."""
    import glm_backend
    import paths

    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)

    async def _key():
        return "k"

    async def _no_sleep(_):
        return None

    events_recorded = []
    def _record_event(**kw):
        events_recorded.append(kw)

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)
    monkeypatch.setattr(glm_backend.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(glm_backend.debug_catalog, "record_event", _record_event)

    sess = _Session1210ThenOk()
    result = await glm_backend.forward_glm(
        _FakeRequest(), b'{"test":"body"}', sess, "glm-5.3"
    )

    # Verifica: 2 tentativi, secondo OK
    assert sess.call_count == 2
    assert result.status == 200

    # Verifica: file dump creato
    cartella = tmp_path / "glm-1210-bodies"
    dump_files = list(cartella.glob("*.json"))
    assert len(dump_files) == 1
    with open(dump_files[0]) as f:
        dump_data = json.load(f)
    assert dump_data["model"] == "glm-5.3"
    assert "[1210]" in dump_data["error"]

    # Verifica: evento glm_1210_retry registrato
    retry_events = [e for e in events_recorded if e.get("kind") == "glm_1210_retry"]
    assert len(retry_events) == 1


@pytest.mark.asyncio
async def test_forward_glm_1211_no_retry(monkeypatch, tmp_path):
    """Errore 400 1211 (non 1210) → NON si ritenta, una sola chiamata."""
    import glm_backend
    import paths

    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)

    async def _key():
        return "k"

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)

    sess = _Session1211_NoRetry()
    result = await glm_backend.forward_glm(
        _FakeRequest(), b'{}', sess, "glm-5.3"
    )

    # Verifica: UNA sola chiamata (niente retry)
    assert sess.call_count == 1
    assert result.status == 400

    # Verifica: nessun file dump
    cartella = tmp_path / "glm-1210-bodies"
    if cartella.exists():
        dump_files = list(cartella.glob("*.json"))
        assert len(dump_files) == 0


@pytest.mark.asyncio
async def test_forward_glm_1210_both_attempts_fail(monkeypatch, tmp_path):
    """Entrambi i tentativi 400 [1210] → ritorna il 400 dal secondo tentativo."""
    import glm_backend
    import paths

    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)

    async def _key():
        return "k"

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(glm_backend, "get_glm_key", _key)
    monkeypatch.setattr(glm_backend.asyncio, "sleep", _no_sleep)

    class _SessionBoth1210:
        call_count = 0
        async def request(self, **kw):
            self.call_count += 1
            return _FakeResp(400, b'{"error":"[1210]"}')

    sess = _SessionBoth1210()
    result = await glm_backend.forward_glm(
        _FakeRequest(), b'{}', sess, "glm-5.3"
    )

    # 2 tentativi, entrambi falliti
    assert sess.call_count == 2
    assert result.status == 400
    # Sul secondo tentativo si ritorna il 400 direttamente senza retry
