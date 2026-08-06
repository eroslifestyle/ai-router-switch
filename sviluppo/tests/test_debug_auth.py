"""Test per il guard di autenticazione delle rotte /debug/.

Le rotte /debug/ espongono il contenuto delle richieste gestite dal router.
In particolare, /debug/trace restituisce il corpo integrale dell'ultima
richiesta inoltrata, che include il system prompt e la conversazione.

Fintanto che il router ascolta sull'indirizzo di loopback (127.0.0.1 o ::1)
queste rotte non sono raggiungibili dalla rete esterna e restano libere.
Il guard deve entrare in funzione solo quando AIROUTER_LISTEN_HOST viene
aperto a un indirizzo non-loopback, richiedendo il token di debug.
"""

import sys

import pytest

sys.path.insert(0, "src")
from router_debug import (DEBUG_TOKEN_HEADER, _debug_auth_ok, _is_loopback_host, debug_auth_middleware)

# Il progetto usa asyncio_mode strict: solo i test async vanno marcati,
# uno per uno. Un pytestmark globale marcherebbe anche i sincroni, con un
# warning per ciascuno.


class FakeRequest:
    """Request fittizio per test senza dipendenze esterne."""
    def __init__(self, path="/debug/trace", headers=None, query=None):
        self.path = path
        self.headers = headers or {}
        self.query = query or {}


async def _handler_ok(request):
    """Handler asincrono fittizio che il middleware deve raggiungere."""
    return "PASSATO"


def test_loopback_lascia_passare_senza_token(monkeypatch):
    """Verifica che su loopback il guard lasci passare senza token.

    Quando il router ascolta su 127.0.0.1 le rotte debug sono gia
    protette dalla rete e non richiedono autenticazione.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "127.0.0.1")
    monkeypatch.delenv("AIROUTER_DEBUG_TOKEN", raising=False)
    result = _debug_auth_ok(FakeRequest())
    atteso = True
    assert result is atteso, f"Atteso: {atteso}, Ottenuto: {result}"


def test_ogni_forma_di_loopback_riconosciuta():
    """Verifica che tutte le forme di indirizzo loopback siano riconosciute.

    Include ipv4, ipv6, forme con zeri omessi, con parentesi, con spazi.
    """
    loopback_veri = [
        "127.0.0.1",
        "127.0.0.53",
        "127.5.5.5",
        "localhost",
        "LOCALHOST",
        "::1",
        "[::1]",
        "::ffff:127.0.0.1",
        "",
        "  127.0.0.1  ",
    ]
    for host in loopback_veri:
        result = _is_loopback_host(host)
        atteso = True
        assert result is atteso, f"Atteso: {atteso} per '{host}', Ottenuto: {result}"

    loopback_falsi = [
        "0.0.0.0",
        "::",
        "10.0.0.5",
        "192.168.1.10",
        "0.0.0.0 ",
        "example.com",
        "127abc",
    ]
    for host in loopback_falsi:
        result = _is_loopback_host(host)
        atteso = False
        assert result is atteso, f"Atteso: {atteso} per '{host}', Ottenuto: {result}"


def test_host_esposto_senza_token_configurato_nega(monkeypatch):
    """Verifica che su host esposto senza token il guard neghi.

    Quando il router e esposto su 0.0.0.0 e non e stato configurato
    alcun token, le rotte debug sono spente per default per sicurezza,
    non aperte a chiunque.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.delenv("AIROUTER_DEBUG_TOKEN", raising=False)
    result = _debug_auth_ok(FakeRequest())
    atteso = False
    assert result is atteso, f"Atteso: {atteso}, Ottenuto: {result}"


def test_host_esposto_token_giusto_nell_header(monkeypatch):
    """Verifica che su host esposto il token nell'header spezifico sia accettato.

    Il token puo essere presentato nell'header X-Airouter-Debug-Token.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("AIROUTER_DEBUG_TOKEN", "segreto-di-prova")
    result = _debug_auth_ok(FakeRequest(headers={DEBUG_TOKEN_HEADER: "segreto-di-prova"}))
    atteso = True
    assert result is atteso, f"Atteso: {atteso}, Ottenuto: {result}"


def test_host_esposto_token_via_bearer_e_via_query(monkeypatch):
    """Verifica che il token sia accettato anche via Bearer e query string.

    Authorization: Bearer X e token=X sono forme alternative riconosciute.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("AIROUTER_DEBUG_TOKEN", "segreto-di-prova")

    result_bearer = _debug_auth_ok(FakeRequest(headers={"Authorization": "Bearer segreto-di-prova"}))
    atteso = True
    assert result_bearer is atteso, f"Atteso: {atteso} per Bearer, Ottenuto: {result_bearer}"

    result_query = _debug_auth_ok(FakeRequest(query={"token": "segreto-di-prova"}))
    atteso = True
    assert result_query is atteso, f"Atteso: {atteso} per query, Ottenuto: {result_query}"


def test_host_esposto_token_sbagliato_o_assente_nega(monkeypatch):
    """Verifica che token sbagliato o assente neghi l'accesso.

    Nessuna forma di token errato deve garantire accesso alle rotte debug.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("AIROUTER_DEBUG_TOKEN", "segreto-di-prova")

    result_nulla = _debug_auth_ok(FakeRequest())
    atteso = False
    assert result_nulla is atteso, f"Atteso: {atteso} per request vuota, Ottenuto: {result_nulla}"

    result_token_sbagliato = _debug_auth_ok(FakeRequest(headers={DEBUG_TOKEN_HEADER: "sbagliato"}))
    atteso = False
    assert result_token_sbagliato is atteso, f"Atteso: {atteso} per token errato, Ottenuto: {result_token_sbagliato}"

    result_bearer_sbagliato = _debug_auth_ok(FakeRequest(headers={"Authorization": "Bearer sbagliato"}))
    atteso = False
    assert result_bearer_sbagliato is atteso, f"Atteso: {atteso} per Bearer errato, Ottenuto: {result_bearer_sbagliato}"

    result_query_sbagliato = _debug_auth_ok(FakeRequest(query={"token": "sbagliato"}))
    atteso = False
    assert result_query_sbagliato is atteso, f"Atteso: {atteso} per query errata, Ottenuto: {result_query_sbagliato}"

    result_no_bearer = _debug_auth_ok(FakeRequest(headers={"Authorization": "segreto-di-prova"}))
    atteso = False
    assert result_no_bearer is atteso, f"Atteso: {atteso} per Authorization senza Bearer, Ottenuto: {result_no_bearer}"


@pytest.mark.asyncio
async def test_middleware_blocca_le_rotte_debug_quando_esposto(monkeypatch):
    """Verifica che il middleware blocchi tutte le rotte /debug/ su host esposto.

    Quando il router ascolta su 0.0.0.0 e non c'e token, ogni richiesta
    ai path /debug* deve ricevere 404 senza contenuto.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.delenv("AIROUTER_DEBUG_TOKEN", raising=False)

    path_debug = [
        "/debug/errors",
        "/debug/last",
        "/debug/stats",
        "/debug/trace",
        "/debug/health",
        "/debug/catalog",
        "/debug/catalog/abc123",
        "/debug",
    ]

    for path in path_debug:
        resp = await debug_auth_middleware(FakeRequest(path=path), _handler_ok)
        atteso = 404
        assert resp.status == atteso, f"Atteso: {atteso} per '{path}', Ottenuto: {resp.status}"


@pytest.mark.asyncio
async def test_middleware_lascia_passare_le_rotte_non_debug(monkeypatch):
    """Verifica che il middleware non blocchi rotte non-/debug.

    Il prefisso deve essere esatto: /debugger e /debugging/x non sono
    rotte di debug e non vanno bloccate. Inoltre /__router_health
    resta libera perche e la rotta di monitoraggio delle dieci porte.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.delenv("AIROUTER_DEBUG_TOKEN", raising=False)

    path_non_debug = [
        "/__router_health",
        "/v1/messages",
        "/health",
        "/debugger",
        "/debugging/x",
        "/",
    ]

    for path in path_non_debug:
        resp = await debug_auth_middleware(FakeRequest(path=path), _handler_ok)
        atteso = "PASSATO"
        assert resp == atteso, f"Atteso: {atteso} per '{path}', Ottenuto: {resp}"


@pytest.mark.asyncio
async def test_middleware_lascia_passare_su_loopback(monkeypatch):
    """Verifica che su loopback il middleware lasci passare le rotte debug.

    In locale nulla cambia rispetto a prima: le rotte debug restano
    accessibili senza token perche gia protette a livello di rete.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "127.0.0.1")
    monkeypatch.delenv("AIROUTER_DEBUG_TOKEN", raising=False)

    resp = await debug_auth_middleware(FakeRequest(path="/debug/trace"), _handler_ok)
    atteso = "PASSATO"
    assert resp == atteso, f"Atteso: {atteso}, Ottenuto: {resp}"


@pytest.mark.asyncio
async def test_middleware_passa_col_token_giusto(monkeypatch):
    """Verifica che il middleware lasci passare con token corretto.

    Quando il token di debug e configurato e presentato correttamente,
    il middleware deve permettere l'accesso alle rotte /debug/.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.setenv("AIROUTER_DEBUG_TOKEN", "segreto-di-prova")

    resp = await debug_auth_middleware(
        FakeRequest(path="/debug/trace", headers={DEBUG_TOKEN_HEADER: "segreto-di-prova"}),
        _handler_ok,
    )
    atteso = "PASSATO"
    assert resp == atteso, f"Atteso: {atteso}, Ottenuto: {resp}"


@pytest.mark.asyncio
async def test_middleware_blocca_admin_quando_esposto(monkeypatch):
    """
    POST /admin/mode/<modo> riscrive il file di modalita globale del router.
    Esposto in rete senza guard, chiunque potrebbe dirottare la modalita
    di tutte le chat verso un provider a sua scelta.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.delenv("AIROUTER_DEBUG_TOKEN", raising=False)

    for p in ("/admin/mode/minimax", "/admin/mode/anthropic", "/admin", "/admin/qualsiasi/cosa"):
        risultato = await debug_auth_middleware(FakeRequest(path=p), _handler_ok)
        assert risultato.status == 404, f"Atteso: 404 per '{p}', Ottenuto: {risultato.status}"

    monkeypatch.setenv("AIROUTER_DEBUG_TOKEN", "segreto-di-prova")
    risultato = await debug_auth_middleware(
        FakeRequest(path="/admin/mode/minimax", headers={DEBUG_TOKEN_HEADER: "segreto-di-prova"}),
        _handler_ok
    )
    assert risultato == "PASSATO", f"Atteso: PASSATO per '/admin/mode/minimax', Ottenuto: {risultato}"


@pytest.mark.asyncio
async def test_middleware_non_confonde_i_prefissi(monkeypatch):
    """
    Il prefisso deve essere esatto, path che cominciano con le stesse lettere
    ma sono altre rotte non vanno intercettati.
    """
    monkeypatch.setenv("AIROUTER_LISTEN_HOST", "0.0.0.0")
    monkeypatch.delenv("AIROUTER_DEBUG_TOKEN", raising=False)

    for p in ("/debugger", "/debugging/x", "/administrator", "/admins", "/adminX", "/v1/messages", "/__router_health"):
        risultato = await debug_auth_middleware(FakeRequest(path=p), _handler_ok)
        assert risultato == "PASSATO", f"Atteso: PASSATO per '{p}', Ottenuto: {risultato}"
