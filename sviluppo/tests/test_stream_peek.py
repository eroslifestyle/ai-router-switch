"""Il peek dell'inizio di uno stream SSE: rileva i 200-vuoti senza bufferizzare.

Il vincolo che questi test difendono: leggere TUTTO lo stream per ispezionarlo lo
trasforma in una risposta bufferizzata (regressione TTFB del 2026-07-22, 45 s
contro ~1 s). Quindi si sbircia solo fino al primo blocco di contenuto, e i byte
sbirciati devono arrivare al client identici e nell'ordine giusto.
"""
import asyncio
import sys

import pytest

sys.path.insert(0, 'src')
import stream_peek  # noqa: E402

pytestmark = pytest.mark.asyncio


class _FakeContent:
    """Come lo StreamReader di aiohttp: i byte si leggono una volta sola."""

    def __init__(self, pezzi, ritardo=0.0):
        self._pezzi = list(pezzi)
        self._ritardo = ritardo

    def at_eof(self):
        return not self._pezzi

    async def iter_any(self):
        while self._pezzi:
            if self._ritardo:
                await asyncio.sleep(self._ritardo)
            yield self._pezzi.pop(0)


class _FakeResp:
    def __init__(self, pezzi, ritardo=0.0, headers=None):
        self.status = 200
        self.headers = headers or {"content-type": "text/event-stream"}
        self.closed = False
        self.content = _FakeContent(pezzi, ritardo)
        self.letture = 0

    def release(self):
        self.closed = True

    async def read(self):
        self.letture += 1
        return b"".join(self.content._pezzi)


SANO = [b'event: message_start\ndata: {"type":"message_start"}\n\n',
        b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n',
        b'event: content_block_delta\ndata: {"text":"ciao"}\n\n',
        b'event: message_stop\ndata: {}\n\n']
VUOTO = [b'event: message_start\ndata: {"type":"message_start"}\n\n',
         b'event: message_delta\ndata: {"stop_reason":"end_turn"}\n\n',
         b'event: message_stop\ndata: {}\n\n']


async def test_stream_sano_si_ferma_al_primo_contenuto():
    resp = _FakeResp(SANO)
    chunks, ha_contenuto, finito = await stream_peek.peek_sse_until_content(resp)
    assert ha_contenuto is True
    assert finito is False, "non deve leggere fino in fondo: quello e' bufferizzare"
    assert len(chunks) == 2, "si ferma appena vede content_block_start"


async def test_stream_vuoto_riconosciuto():
    resp = _FakeResp(VUOTO)
    chunks, ha_contenuto, finito = await stream_peek.peek_sse_until_content(resp)
    assert (ha_contenuto, finito) == (False, True)


async def test_il_client_rivede_tutti_i_byte_in_ordine():
    """Il pezzo che, se sbagliato, corrompe ogni risposta: i chunk sbirciati
    vanno riemessi PRIMA del resto, una volta sola."""
    resp = _FakeResp(SANO)
    chunks, _, _ = await stream_peek.peek_sse_until_content(resp)
    visti = b""
    async for c in stream_peek.PeekedStream(resp, chunks).content.iter_any():
        visti += c
    assert visti == b"".join(SANO)


async def test_tetto_byte_non_decide_nulla():
    """Superato il tetto si smette di sbirciare, senza dichiarare vuoto uno
    stream che magari stava per produrre: finito resta False."""
    resp = _FakeResp(SANO)
    chunks, ha_contenuto, finito = await stream_peek.peek_sse_until_content(resp, cap_bytes=1)
    assert (ha_contenuto, finito) == (False, False)
    assert len(chunks) == 1


async def test_tetto_tempo_non_blocca():
    """Uno stream lento non deve tenere il client appeso oltre il cap."""
    resp = _FakeResp(SANO, ritardo=0.2)
    inizio = asyncio.get_event_loop().time()
    chunks, ha_contenuto, finito = await stream_peek.peek_sse_until_content(resp, cap_sec=0.05)
    durata = asyncio.get_event_loop().time() - inizio
    assert durata < 0.5, f"il cap non ha morso: {durata:.2f}s"
    assert (ha_contenuto, finito) == (False, False)


async def test_superficie_usata_dal_relay():
    """Il relay tocca status/headers/closed/release/read/content.at_eof."""
    resp = _FakeResp(SANO)
    chunks, _, _ = await stream_peek.peek_sse_until_content(resp)
    ps = stream_peek.PeekedStream(resp, chunks)
    assert ps.status == 200
    assert "text/event-stream" in ps.headers["content-type"]
    assert ps.closed is False
    assert ps.content.at_eof() is False, "ci sono chunk sbirciati da riemettere"
    ps.release()
    assert ps.closed is True
    assert await ps.read() == b"".join(SANO), "read() deve includere i byte sbirciati"


async def test_stream_che_non_emette_nulla():
    """Zero eventi: e' vuoto anche questo, e va ritentato."""
    resp = _FakeResp([])
    chunks, ha_contenuto, finito = await stream_peek.peek_sse_until_content(resp)
    assert (chunks, ha_contenuto, finito) == ([], False, True)
