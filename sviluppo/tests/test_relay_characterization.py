"""
Test suite per StreamingRelay.
Utilizza un upstream fittizio per verificare il comportamento del relay.
"""

import asyncio
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, '.')
from streaming_relay import StreamingRelay
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient


# ----------------------------------------------------------------------
# Classe fittizia che simula l'upstream
# ----------------------------------------------------------------------
class FakeContent:
    """Contenuto asincrono con metodo iter_any()."""

    def __init__(self, chunks):
        #chunks: lista di bytes da emettere in ordine
        self._chunks = chunks

    async def iter_any(self):
        """Generatore asincrono che restituisce i chunk."""
        for chunk in self._chunks:
            yield chunk


class FakeUpstream:
    """
    Upstream fittizio con attributi .status, .headers,
    metodi asincroni .read() e .release() e proprietà .content.
    """

    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        # CIMultiDict e non dict: aiohttp consegna header CASE-INSENSITIVE, e il
        # relay rileva l'SSE con headers.get("content-type"). Con un dict normale
        # un "Content-Type" maiuscolo non verrebbe trovato, is_sse resterebbe
        # False, il content-length verrebbe propagato e il client fallirebbe con
        # ContentLengthError. Il fake deve avere la stessa semantica del reale.
        from multidict import CIMultiDict
        self.headers = CIMultiDict(headers if headers is not None else {})
        # Il relay controlla `upstream.closed` prima di rilasciare la connessione:
        # senza questo attributo il fake solleva AttributeError a metà stream e il
        # client vede un TransferEncodingError, non l'errore vero.
        self.closed = False
        # chunks: sequenza di bytes che verrà restituita da read() e iter_any()
        self._chunks = list(chunks) if chunks else []
        self._released = False

    async def read(self):
        """Restituisce tutti i chunk uniti in un unico bytes."""
        return b''.join(self._chunks)

    # SINCRONO come ClientResponse.release() di aiohttp: il relay lo chiama
    # senza await, quindi una versione async resterebbe una coroutine mai attesa.
    def release(self):
        """Simula il rilascio della risorsa."""
        self._released = True

    @property
    def content(self):
        """Proprietà che ritorna un oggetto con iter_any()."""
        return FakeContent(self._chunks)


# ----------------------------------------------------------------------
# Helper che esegue una chiamata POST verso '/test' del relay
# ----------------------------------------------------------------------
async def _esegui(upstream, extra=None):
    """
    Crea un'applicazione aiohttp con un gestore che costruisce
    StreamingRelay e invoca relay.relay().
    Utilizza TestServer e TestClient per la comunicazione.
    Restituisce (status, headers_dict, body_bytes).
    """

    async def handler(request):
        """Gestore della rotta POST /test."""
        up = request.app['upstream']
        extra_headers = request.app.get('extra')
        # Creazione del relay con i parametri richiesti
        relay = StreamingRelay(
            request,
            b'{}',               # body
            'mix-gm',            # mode
            None,                # orig
            {},                  # request_orig_model
            set(),               # hop_headers
            'MiniMax-M2.7',      # minimax_model
            lambda *a, **k: None,  # log_fn
            lambda *a, **k: None,  # log_router_usage_fn
            lambda *a, **k: None   # trim_context_fn
        )
        # Esegue il relay con l'upstream fittizio
        response = await relay.relay(up, extra_headers=extra_headers)
        return response

    # Costruzione dell'applicazione e aggiunta della rotta
    app = web.Application()
    app['upstream'] = upstream
    app['extra'] = extra
    app.router.add_post('/test', handler)

    # Utilizzo di AppRunner per avviare il server su porta effimera
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        # TestClient avvia automaticamente il server
        async with TestClient(TestServer(app, host='localhost', port=0)) as client:
            resp = await client.post('/test')
            status = resp.status
            # Conversione dei header in dict (maiuscole con trattino)
            # CIMultiDict e non dict: gli header HTTP sono case-insensitive e il
            # relay li scrive minuscoli ("x-accel-buffering"), mentre gli assert
            # li cercano capitalizzati. Con un dict la ricerca fallirebbe a vuoto.
            from multidict import CIMultiDict
            headers = CIMultiDict(resp.headers)
            body = await resp.read()
    finally:
        await runner.cleanup()

    return status, headers, body


# ----------------------------------------------------------------------
# Test 1: header SSE corretti
# ----------------------------------------------------------------------
def test_header_sse():
    """Verifica che il relay applichi i corretti header per SSE."""
    up = FakeUpstream(
        status=200,
        headers={
            'Content-Type': 'text/event-stream',
            'Content-Length': '999'
        },
        chunks=[b'data: test\n\n']
    )
    status, headers, body = asyncio.run(_esegui(up))
    assert status == 200, f"Status atteso 200, ottenuto {status}"
    assert headers.get('X-Accel-Buffering') == 'no', \
        "Header X-Accel-Buffering assente o diverso da 'no'"
    assert 'no-cache' in headers.get('Cache-Control', ''), \
        "Cache-Control non contiene 'no-cache'"
    assert 'Content-Length' not in headers, \
        "Content-Length non dovrebbe essere presente nella risposta SSE"
    print('  test_header_sse: OK')


# ----------------------------------------------------------------------
# Test 2: gestione chunk
# ----------------------------------------------------------------------
def test_chunk():
    """Verifica che i chunk upstream vengano correttamente inoltrati."""
    up = FakeUpstream(
        status=200,
        headers={'Content-Type': 'text/event-stream'},
        chunks=[b'data: uno', b'data: due', b'data: tre']
    )
    status, headers, body = asyncio.run(_esegui(up))
    assert status == 200, f"Status atteso 200, ottenuto {status}"
    body_str = body.decode('utf-8')
    # Verifica presenza e ordine dei tre frammenti
    assert 'uno' in body_str and 'due' in body_str and 'tre' in body_str, \
        "I frammenti non sono tutti presenti nel corpo"
    assert body_str.find('uno') < body_str.find('due') < body_str.find('tre'), \
        "L'ordine dei frammenti non è corretto"
    print('  test_chunk: OK')


# ----------------------------------------------------------------------
# Test 3: errore upstream
# ----------------------------------------------------------------------
def test_errore():
    """Verifica che gli errori upstream vengano propagati correttamente."""
    up = FakeUpstream(
        status=400,
        headers={'Content-Type': 'application/json'},
        chunks=[b'{"error":"boom"}']
    )
    status, headers, body = asyncio.run(_esegui(up))
    assert status == 400, f"Status atteso 400, ottenuto {status}"
    assert b'boom' in body, "Il corpo della risposta non contiene il messaggio di errore"
    print('  test_errore: OK')


# ----------------------------------------------------------------------
# Test 4: header extra
# ----------------------------------------------------------------------
def test_extra_headers():
    """Verifica che gli header aggiuntivi vengano inclusi nella risposta."""
    up = FakeUpstream(
        status=200,
        headers={'Content-Type': 'text/event-stream'},
        chunks=[b'data: extra\n\n']
    )
    extra = {'x-ai-verified': 'test-value'}
    status, headers, body = asyncio.run(_esegui(up, extra))
    assert status == 200, f"Status atteso 200, ottenuto {status}"
    # I header vengono normalizzati con la prima lettera maiuscola e il resto minuscolo
    # Es: X-Ai-Verified
    assert 'X-Ai-Verified' in headers or 'x-ai-verified' in headers, \
        "Header extra non presente nella risposta"
    assert headers.get('X-Ai-Verified') == 'test-value' or headers.get('x-ai-verified') == 'test-value', \
        "Valore dell'header extra non corretto"
    print('  test_extra_headers: OK')


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    """Esegue tutti i test e riporta il risultato."""
    test_header_sse()
    test_chunk()
    test_errore()
    test_extra_headers()
    print('TUTTI I TEST PASSATI')


if __name__ == '__main__':
    main()


# ----------------------------------------------------------------------
# Test 5: usage estratto da entrambi i dialetti SSE (Anthropic e z.ai)
# ----------------------------------------------------------------------
async def _esegui_usage(chunks):
    """Come _esegui, ma restituisce il dict usage passato al logger del sidecar."""
    catturato = {}

    def _sink(*a, **k):
        u = k.get("usage") or next((x for x in a if isinstance(x, dict) and "input_tokens" in x), None)
        if u:
            catturato.update(u)

    async def handler(request):
        relay = StreamingRelay(
            request, b'{}', 'glm', None, {}, set(), 'MiniMax-M2.7',
            lambda *a, **k: None, _sink, lambda *a, **k: None)
        return await relay.relay(request.app['upstream'])

    app = web.Application()
    app['upstream'] = FakeUpstream(
        headers={'Content-Type': 'text/event-stream'}, chunks=chunks)
    app.router.add_post('/test', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        async with TestClient(TestServer(app, host='localhost', port=0)) as client:
            await (await client.post('/test')).read()
    finally:
        await runner.cleanup()
    return catturato


def _sse(*eventi):
    import json as _j
    return [f"data: {_j.dumps(e)}\n\n".encode() for e in eventi]


def test_usage_dialetto_anthropic():
    """Anthropic: input e cache_read arrivano nel message_start."""
    u = asyncio.run(_esegui_usage(_sse(
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 120, "cache_read_input_tokens": 4000,
            "cache_creation_input_tokens": 50}}},
        {"type": "message_delta", "usage": {"output_tokens": 7},
         "delta": {"stop_reason": "end_turn"}})))
    assert u["input_tokens"] == 120
    assert u["cache_read_input_tokens"] == 4000
    assert u["output_tokens"] == 7


def test_usage_dialetto_zai_nel_delta():
    """z.ai (GLM): message_start vuoto, i valori veri stanno nel message_delta.

    Regressione 2026-08-16: leggendo solo il message_start, ogni richiesta GLM in
    streaming finiva nel sidecar con input=0 e cache_read=0 — la cache risultava
    morta su 7.316 richieste mentre lo stream dichiarava 32.000 token letti."""
    u = asyncio.run(_esegui_usage(_sse(
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 0, "output_tokens": 0}}},
        {"type": "message_delta", "usage": {
            "input_tokens": 26, "output_tokens": 3,
            "cache_read_input_tokens": 32000},
         "delta": {"stop_reason": "end_turn"}})))
    assert u["input_tokens"] == 26, "input del delta ignorato"
    assert u["cache_read_input_tokens"] == 32000, "cache_read del delta ignorato"
