"""Regressione dei 502 «Timeout on reading data from socket» (2026-07-28).

La sessione condivisa del proxy usa `sock_read=120`. Su una risposta NON-streaming
l'upstream non manda un solo byte finche' la generazione non e' conclusa: quei 120 s
smettono di proteggere dagli stall e diventano un tetto sulla DURATA della
generazione. Risultato: 59 casi fra il 26 e il 28/07, tutti su `minimax passthrough`.

`upstream_timeout_for` decide per-richiesta: override generoso quando non c'e'
streaming, timeout di sessione ereditato quando c'e' (li' i chunk arrivano di
continuo e il valore stretto serve davvero).
"""

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from router_utils import upstream_timeout_for
from router_constants import NON_STREAM_SOCK_READ_SEC

# Valore della sessione condivisa (ai-router-proxy.py), quello che causava i 502.
SESSION_SOCK_READ_SEC = 120


def test_streaming_eredita_timeout_sessione():
    """Con stream=True nessun override: i chunk continui rendono utile il 120 s."""
    body = json.dumps({"model": "x", "stream": True}).encode()
    assert upstream_timeout_for(body) is None


def test_non_streaming_riceve_sock_read_generoso():
    """Senza chiave stream la generazione deve poter durare oltre i 120 s."""
    body = json.dumps({"model": "y"}).encode()
    result = upstream_timeout_for(body)
    assert result is not None
    assert result.sock_read == NON_STREAM_SOCK_READ_SEC
    assert result.connect == 30
    assert result.sock_connect == 15


def test_stream_false_esplicito_riceve_override():
    """stream=False è non-streaming a tutti gli effetti."""
    body = json.dumps({"model": "z", "stream": False}).encode()
    result = upstream_timeout_for(body)
    assert result is not None
    assert result.sock_read == NON_STREAM_SOCK_READ_SEC


def test_body_none_eredita():
    """Senza body non si decide nulla: si eredita il timeout di sessione."""
    assert upstream_timeout_for(None) is None


def test_body_non_json_eredita():
    """Body non interpretabile: prudenza, nessun override."""
    assert upstream_timeout_for(b"non-json-{{{") is None


def test_body_json_non_dict_eredita():
    """JSON valido ma non oggetto: nessun campo stream da leggere."""
    assert upstream_timeout_for(b"[1,2,3]") is None


def test_stream_non_booleano_riceve_override():
    """Solo il True booleano indica streaming: la stringa "true" non conta."""
    body = json.dumps({"stream": "true"}).encode()
    assert upstream_timeout_for(body) is not None


def test_default_e_piu_generoso_del_sock_read_di_sessione():
    """Se questo scende a <= 120 il fix e' annullato e i 502 tornano."""
    assert NON_STREAM_SOCK_READ_SEC > SESSION_SOCK_READ_SEC
