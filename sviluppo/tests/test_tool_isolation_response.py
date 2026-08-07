""""
Questo modulo verifica la guardia response-side sull'isolamento tool all'interno di StreamingRelay.
La guardia e' osservativa: registra e segnala l'uso di tool non autorizzati senza bloccarli.
Il falso positivo su Bash e' il caso che ha gia' fatto fallire una prima implementazione basata su is_anthropic_server_tool.
"""

import asyncio
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, '.')
import streaming_relay
from streaming_relay import StreamingRelay
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient
from multidict import CIMultiDict


class FakeContent:
    """Contenuto fittizio per simulare upstream.content."""
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk


class FakeUpstream:
    """Simula aiohttp.ClientResponse con i soli metodi usati da StreamingRelay."""
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = CIMultiDict(headers if headers is not None else {})
        self.closed = False
        self._chunks = list(chunks) if chunks else []

    async def read(self):
        return b''.join(self._chunks)

    def release(self):
        # Sincrono come in aiohttp.ClientResponse.release
        pass

    @property
    def content(self):
        return FakeContent(self._chunks)


class FakeDL:
    """Sostituisce streaming_relay.dl per evitare scritture nel catalogo reale."""
    def __init__(self):
        self.captured = []

    def capture(self, **kw):
        self.captured.append(kw)


# Costanti SSE: frammenti di risposta in formato Server-Sent Events
SSE_ZAI = 'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_1","name":"mcp__zai__web_search_prime","input":{}}}\n\n'
SSE_BASH = 'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_2","name":"Bash","input":{}}}\n\n'
SSE_MINIMAX = 'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_3","name":"mcp__MiniMax__web_search","input":{}}}\n\n'


async def _esegui(upstream, mode, final_override):
    """
    Esegue un test end-to-end costruendo StreamingRelay con i parametri richiesti.
    Restituisce (righe_di_log, catture_dl).
    """
    logs = []

    def log_fn(msg):
        # log_fn viene chiamato da StreamingRelay per registrare eventi
        logs.append(msg)

    def log_fn_usage(*a, **k):
        """Stub: il relay lo chiama ma il test non ne verifica gli argomenti."""

    def log_fn_trim(*a, **k):
        """Stub: come sopra."""

    # Sostituisco dl con un FakeDL per catturare le eventuali segnalazioni
    fake_dl = FakeDL()
    original_dl = streaming_relay.dl
    streaming_relay.dl = fake_dl
    try:
        async def handler(request):
            relay = StreamingRelay(
                request,
                b'{}',
                mode,
                None,
                {},
                set(),
                'MiniMax-M2.7',
                log_fn,
                log_fn_usage,
                log_fn_trim
            )
            response = await relay.relay(upstream, final_override=final_override)
            return response

        app = web.Application()
        app.router.add_post('/test', handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post('/test')
            await resp.read()
    finally:
        streaming_relay.dl = original_dl

    return logs, fake_dl.captured


def test_rileva_tool_straniero():
    upstream = FakeUpstream(
        status=200,
        headers={'Content-Type': 'text/event-stream'},
        chunks=[SSE_ZAI.encode()]
    )
    logs, captured = asyncio.run(_esegui(upstream, 'mix-am', 'MiniMax-M2.7'))
    foreign_logs = [line for line in logs if 'FOREIGN-TOOLUSE' in line and 'mcp__zai__web_search_prime' in line]
    assert foreign_logs, f"Atteso un log FOREIGN-TOOLUSE per mcp__zai__web_search_prime, ottenuto: {logs}"
    foreign_captures = [c for c in captured if c.get('kind') == 'foreign_tool_use_response']
    assert foreign_captures, f"Attesa una cattura con kind='foreign_tool_use_response', ottenuta: {captured}"
    print('  test_rileva_tool_straniero: OK')


def test_nessun_falso_positivo_su_tool_locale():
    upstream = FakeUpstream(
        status=200,
        headers={'Content-Type': 'text/event-stream'},
        chunks=[SSE_BASH.encode()]
    )
    logs, captured = asyncio.run(_esegui(upstream, 'mix-am', 'MiniMax-M2.7'))
    foreign_logs = [line for line in logs if 'FOREIGN-TOOLUSE' in line]
    assert not foreign_logs, f"Trovati FOREIGN-TOOLUSE inaspettati: {foreign_logs}"
    foreign_captures = [c for c in captured if c.get('kind') == 'foreign_tool_use_response']
    assert not foreign_captures, f"Trovata cattura foreign_tool_use_response inattesa: {foreign_captures}"
    print('  test_nessun_falso_positivo_su_tool_locale: OK')


def test_tool_nativo_del_backend_non_segnalato():
    upstream = FakeUpstream(
        status=200,
        headers={'Content-Type': 'text/event-stream'},
        chunks=[SSE_MINIMAX.encode()]
    )
    logs, captured = asyncio.run(_esegui(upstream, 'mix-am', 'MiniMax-M2.7'))
    foreign_logs = [line for line in logs if 'FOREIGN-TOOLUSE' in line]
    assert not foreign_logs, f"Trovati FOREIGN-TOOLUSE inaspettati: {foreign_logs}"
    print('  test_tool_nativo_del_backend_non_segnalato: OK')


def main():
    test_rileva_tool_straniero()
    test_nessun_falso_positivo_su_tool_locale()
    test_tool_nativo_del_backend_non_segnalato()
    print('TUTTI I TEST PASSATI')


if __name__ == '__main__':
    main()
