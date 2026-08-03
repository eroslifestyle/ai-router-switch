"""Risposta sintetica che imita la superficie di aiohttp.ClientResponse.

Serve ai backend per restituire un errore SENZA una vera risposta upstream
(chiave assente, rate-limit esaurito, tentativi esauriti).

Non si usa aiohttp.web.Response perche' il relay chiama .release() e
.content.iter_any() sull'oggetto, che web.Response non ha — l'AttributeError
maschera il messaggio d'errore vero dietro un 502 generico.

Due vincoli criteri che SEMBRANO dettagli e non lo sono:
1. release() deve essere SINCRONO, non async. Il relay lo chiama senza await
   (upstream.release()): se fosse una coroutine non verrebbe mai attesa e
   produrrebbe solo un RuntimeWarning silenzioso. La ClientResponse vera ce
   l'ha sincrono.
2. headers deve essere una CIMultiDict, non un dict. Il relay interroga gli
   header con capitalizzazioni diverse (Content-Encoding vs content-type):
   con un dict normale una delle due lookup fallisce sempre, e in particolare
   la rilevazione SSE (content-type) non scatterebbe mai.
"""

from __future__ import annotations

import json
from multidict import CIMultiDict


class SyntheticResponse:
    """Risposta sintetica che mima aiohttp.ClientResponse per il relay."""

    def __init__(
        self,
        status: int,
        body: bytes,
        headers: dict | None = None,
        url: str = "",
    ) -> None:
        self.status = int(status)
        self._body = body if isinstance(body, bytes) else str(body).encode()
        self.url = url
        self.closed = False

        self.headers = CIMultiDict(
            {
                "Content-Type": "application/json",
                "x-ai-router": "synthetic",
            }
        )
        if headers:
            self.headers.update(headers)

    async def read(self) -> bytes:
        """Ritorna il body della risposta."""
        return self._body

    async def json(self) -> dict:
        """Decodifica il body come JSON."""
        return json.loads(self._body)

    async def text(self, encoding: str = "utf-8") -> str:
        """Decodifica il body come testo."""
        return self._body.decode(encoding, errors="replace")

    def release(self) -> None:
        """SINCRONO: rilascia la risposta.

        NON async: il relay lo chiama senza await. Se fosse una coroutine
        non verrebbe mai attesa, producendo solo un RuntimeWarning silenzioso.
        """
        self.closed = True

    @property
    def content_type(self) -> str:
        """Tipo MIME degli header, senza parametri dopo ';'."""
        ct = self.headers.get("Content-Type", "")
        return ct.split(";")[0].strip()

    @property
    def content(self) -> "_ContentProxy":
        """Proxy per l'interfaccia di lettura del body streaming."""
        return _ContentProxy(self._body)


class _ContentProxy:
    """Proxy minimale per aiohttp.response.content."""

    __slots__ = ("_body",)

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_any(self):
        """Yielda tutto il body in un colpo solo, o niente se vuoto."""
        if self._body:
            yield self._body


def synthetic_error(
    status: int,
    error_type: str,
    message: str,
    headers: dict | None = None,
    url: str = "",
) -> SyntheticResponse:
    """Costruisce una risposta d'errore nel formato Anthropic.

    Il formato {"type": "error", "error": {"type": ..., "message": ...}}
    e' quello che i client Anthropic-compatibili sanno interpretare,
    quindi va usato per TUTTI gli errori sintetici.

    Args:
        status: codice HTTP (es. 400, 401, 429, 500).
        error_type: tipo d'errore (es. "authentication_error",
            "rate_limit_error", "invalid_request_error").
        message: messaggio leggibile per il client.
        headers: header aggiuntivi da unire ai default.
        url: URL della richiesta originale (per diagnostica).
    """
    body = {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }
    return SyntheticResponse(
        status=status,
        body=json.dumps(body),
        headers=headers,
        url=url,
    )


def synthetic_rate_limit(
    message: str,
    retry_after: str | None = None,
    url: str = "",
) -> SyntheticResponse:
    """Scorciatoia per errore 429 Rate Limit.

    Il punto critico e' preservare Retry-After: un 429 convertito in 502
    fa perdere al client l'informazione su quanto aspettare, e un 502 non
    e' ritentabile con la stessa semantica.

    Args:
        message: messaggio da mostrare al client.
        retry_after: secondi (o timestamp HTTP) prima di ritentare.
            Se None, Retry-After non viene aggiunto.
        url: URL della richiesta originale (per diagnostica).
    """
    headers: dict[str, str] = {"x-should-retry": "true"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)

    return synthetic_error(
        status=429,
        error_type="rate_limit_error",
        message=message,
        headers=headers,
        url=url,
    )
