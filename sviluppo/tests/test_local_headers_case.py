"""Gli header del wrapper local restano case-insensitive (F4, 2026-08-16).

`_StopReasonFixed` avvolge la risposta del backend locale per correggere lo
`stop_reason` che LiteLLM 1.95.0 non mappa. La sua property `headers` copiava gli
header in un **dict normale**: aiohttp conserva il case con cui l'header e'
arrivato, e il lookup case-insensitive lo fa la struttura (CIMultiDict), non la
chiave. Con la copia in dict, `headers.get("content-type")` del relay restituisce
None se l'upstream ha mandato `Content-Type` — quindi `is_sse` diventa False, uno
stream SSE viene trattato come JSON, il parser dell'usage non trova nulla e
ripiega sulle stime `total_bytes // 4`.

Effetto misurato prima del fix: in mode `local` il sidecar registrava
`stop_reason=""`, `text_blocks=0` e `outcome=unknown` su **36 righe su 37**, con
conteggi inventati — una sonda con `max_tokens=16` risultava con 698 token di
output.
"""
import sys
from pathlib import Path

from multidict import CIMultiDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from local_backend import _StopReasonFixed


class _RispostaFinta:
    def __init__(self, headers):
        self.headers = headers
        self.content = None


def _wrapper(headers):
    w = _StopReasonFixed.__new__(_StopReasonFixed)
    object.__setattr__(w, "_orig", _RispostaFinta(headers))
    return w


def test_lookup_minuscolo_su_header_maiuscolo():
    """Il caso che il fix esiste per risolvere."""
    w = _wrapper(CIMultiDict([("Content-Type", "text/event-stream")]))
    assert w.headers.get("content-type") == "text/event-stream"


def test_il_relay_riconosce_lo_stream():
    """Riproduce la riga esatta del relay che decide se e' SSE."""
    w = _wrapper(CIMultiDict([("Content-Type", "text/event-stream; charset=utf-8")]))
    is_sse = "text/event-stream" in (w.headers.get("content-type") or "").lower()
    assert is_sse is True


def test_content_length_resta_rimosso():
    """Il wrapper riscrive il corpo, quindi la lunghezza dichiarata non vale piu'."""
    w = _wrapper(CIMultiDict([("Content-Type", "application/json"), ("Content-Length", "42")]))
    h = w.headers
    assert "content-length" not in h and "Content-Length" not in h


def test_non_e_un_dict_normale():
    """Controprova diretta sul tipo: un dict qui reintrodurrebbe il bug."""
    w = _wrapper(CIMultiDict([("Content-Type", "text/event-stream")]))
    assert isinstance(w.headers, CIMultiDict)
    assert not type(w.headers) is dict


def test_header_gia_minuscoli_continuano_a_funzionare():
    """Il fix non deve dipendere dal case che manda l'upstream."""
    w = _wrapper(CIMultiDict([("content-type", "text/event-stream")]))
    assert w.headers.get("Content-Type") == "text/event-stream"


def test_altri_header_preservati():
    w = _wrapper(CIMultiDict([("Content-Type", "text/event-stream"),
                              ("x-litellm-model-name", "openai/qcnext-mxfp4")]))
    assert w.headers.get("X-LiteLLM-Model-Name") == "openai/qcnext-mxfp4"
