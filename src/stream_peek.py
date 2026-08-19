#!/usr/bin/env python3
"""Sbirciare l'inizio di uno stream SSE senza bufferizzarlo.

Il problema che risolve: GLM risponde a volte 200 con uno stream che si chiude
senza aver mai aperto un blocco di contenuto — al client arriva una risposta
vuota, pagata. Rilevarlo leggendo TUTTA la risposta la trasforma in una risposta
bufferizzata, che e' la regressione di TTFB del 2026-07-22 (45 s contro ~1 s).

Qui si legge solo l'INIZIO: appena compare il primo ``content_block_start`` si
smette e si restituisce uno stream che riemette i byte gia' letti e poi prosegue
dall'originale. In una risposta sana costa i millisecondi del primo blocco; in
una risposta vuota lo stream finisce prima, e allora si puo' ancora ritentare
perche' al client non e' stato inviato nulla.

Due tetti di sicurezza (byte e secondi): se il modello tarda oltre, si smette di
sbirciare e si passa in streaming puro, cioe' al comportamento di prima.
"""
import asyncio
import os

# Il primo blocco di una risposta GLM (thinking o text) arriva subito: questi
# tetti servono solo a non restare mai appesi. Superati, si passa allo stream.
PEEK_CAP_SEC = float(os.environ.get("AIROUTER_SSE_PEEK_CAP_SEC", "20"))
PEEK_CAP_BYTES = int(os.environ.get("AIROUTER_SSE_PEEK_CAP_BYTES", "65536"))

# Marcatori che dicono "il modello ha iniziato a produrre qualcosa".
_MARCATORI_CONTENUTO = (b"content_block_start", b"content_block_delta")


class PeekedStream:
    """Risposta ``ClientResponse``-like che riemette i chunk gia' letti.

    Espone la sola superficie che il relay usa (status, headers, closed,
    release, read, content.iter_any, content.at_eof); tutto il resto delega
    alla risposta originale.
    """

    def __init__(self, upstream, chunks: list[bytes]) -> None:
        self._up = upstream
        self._chunks = [c for c in chunks if c]
        self.status = upstream.status
        self.headers = upstream.headers
        self.url = getattr(upstream, "url", "")

    @property
    def closed(self) -> bool:
        return getattr(self._up, "closed", False)

    def release(self) -> None:
        self._up.release()

    async def read(self) -> bytes:
        """Il letto piu' il resto: serve ai rami non-streaming del relay."""
        resto = await self._up.read()
        return b"".join(self._chunks) + (resto or b"")

    @property
    def content(self) -> "_ContenutoSbirciato":
        return _ContenutoSbirciato(self._chunks, self._up)


class _ContenutoSbirciato:
    """Proxy di ``response.content`` che antepone i chunk gia' letti."""

    __slots__ = ("_chunks", "_up")

    def __init__(self, chunks: list[bytes], upstream) -> None:
        self._chunks = chunks
        self._up = upstream

    def at_eof(self) -> bool:
        if self._chunks:
            return False
        try:
            return self._up.content.at_eof()
        except Exception:
            return False

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk
        self._chunks = []
        async for chunk in self._up.content.iter_any():
            yield chunk


async def peek_sse_until_content(upstream, cap_sec: float = PEEK_CAP_SEC,
                                 cap_bytes: int = PEEK_CAP_BYTES):
    """Legge l'inizio dello stream fino al primo blocco di contenuto.

    Ritorna ``(chunks, ha_contenuto, finito)``:
      - ``chunks``: i byte letti, da riemettere al client;
      - ``ha_contenuto``: e' comparso un content_block_start/delta;
      - ``finito``: lo stream si e' chiuso durante la sbirciata.

    ``finito and not ha_contenuto`` e' la risposta vuota: il chiamante puo'
    ancora ritentare, perche' nulla e' stato inviato al client.
    """
    chunks: list[bytes] = []
    letti = 0
    ha_contenuto = False
    finito = False
    scadenza = asyncio.get_event_loop().time() + cap_sec
    iteratore = upstream.content.iter_any()
    while True:
        rimasto = scadenza - asyncio.get_event_loop().time()
        if rimasto <= 0:
            break
        try:
            chunk = await asyncio.wait_for(iteratore.__anext__(), timeout=rimasto)
        except StopAsyncIteration:
            finito = True
            break
        except (asyncio.TimeoutError, Exception):
            # Qualsiasi errore di lettura: si smette di sbirciare e si lascia
            # decidere al chiamante con quel che si e' visto finora. Non si
            # inghiotte lo stream, che resta consumabile dal relay.
            break
        if not chunk:
            continue
        chunks.append(chunk)
        letti += len(chunk)
        if any(m in chunk for m in _MARCATORI_CONTENUTO):
            ha_contenuto = True
            break
        if letti >= cap_bytes:
            break
    return chunks, ha_contenuto, finito


def demo() -> None:  # pragma: no cover - check runnable, niente framework
    """Check minimo: una risposta sana e una vuota, piu' il riassemblaggio."""
    class _FakeContent:
        """Come lo StreamReader di aiohttp: i byte si leggono UNA volta sola.

        Il primo fake li riemetteva da capo a ogni iter_any(), e il riassemblaggio
        sembrava duplicare i chunk sbirciati — un difetto del fake, non del codice.
        """

        def __init__(self, pezzi):
            self._pezzi = list(pezzi)

        def at_eof(self):
            return not self._pezzi

        async def iter_any(self):
            while self._pezzi:
                yield self._pezzi.pop(0)

    class _FakeResp:
        def __init__(self, pezzi):
            self.status = 200
            self.headers = {"content-type": "text/event-stream"}
            self.closed = False
            self.content = _FakeContent(pezzi)

        def release(self):
            self.closed = True

        async def read(self):
            return b""

    async def _prova():
        sana = _FakeResp([b'event: message_start\n', b'event: content_block_start\n', b'event: ciao\n'])
        chunks, contenuto, finito = await peek_sse_until_content(sana)
        assert contenuto is True and finito is False, (contenuto, finito)
        assert len(chunks) == 2, chunks
        # il relay rivede TUTTI i byte, quelli sbirciati e quelli dopo
        visti = b""
        async for c in PeekedStream(sana, chunks).content.iter_any():
            visti += c
        assert visti == b'event: message_start\nevent: content_block_start\nevent: ciao\n', visti

        vuota = _FakeResp([b'event: message_start\n', b'event: message_stop\n'])
        chunks, contenuto, finito = await peek_sse_until_content(vuota)
        assert contenuto is False and finito is True, (contenuto, finito)

        lenta = _FakeResp([b'event: message_start\n'])
        chunks, contenuto, finito = await peek_sse_until_content(lenta, cap_bytes=1)
        assert contenuto is False, contenuto  # tetto byte raggiunto: non si decide
        print("ok stream_peek")

    asyncio.run(_prova())


if __name__ == "__main__":  # pragma: no cover
    demo()
