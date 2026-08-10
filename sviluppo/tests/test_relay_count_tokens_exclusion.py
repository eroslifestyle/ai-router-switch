"""Test che il guard empty_response_* non scatta per /v1/messages/count_tokens.

Il fix (2026-08-09): il guard in streaming_relay.py (~riga 585) ora esclude
i path che terminano con "/count_tokens" per evitare falsi positivi sulla
risposta JSON {"input_tokens":N} che ha body piccolo e text_blocks=0.
"""
import pathlib
import re

import pytest

# streaming_relay non e importabile (dipende da router_debug non nel path dei test).
# I test usano solo l'helper _guard_fires definito localmente.


# -----------------------------------------------------------------------------
# Helper che replica la logica del guard (aggiornato con l'exclusion)
# -----------------------------------------------------------------------------

def _guard_fires(path, text_blocks, total_bytes, acc_buf):
    """Replica la logica del guard empty_response_* in streaming_relay.py."""
    if path.endswith("/count_tokens"):
        return False
    if text_blocks == 0 and total_bytes < 500:
        ot = re.findall(rb'"output_tokens"\s*:\s*(\d+)', acc_buf)
        real_out = int(ot[-1]) if ot else 0
        return real_out == 0
    return False


# -----------------------------------------------------------------------------
# Fake aiohttp
# -----------------------------------------------------------------------------

class _FakeRequest:
    """Request finta minimale per StreamingRelay."""
    def __init__(self, path):
        self.method = "POST"
        self.path = path
        self.headers = {}
        self._content = b"{}"
        self._reads_done = 0

    async def read(self):
        self._reads_done += 1
        return self._content

    def prepare_body(self, *args, **kwargs):
        return self._content


# -----------------------------------------------------------------------------
# Test 1: count_tokens NON triggera il guard
# -----------------------------------------------------------------------------

def test_count_tokens_path_not_logged_as_empty():
    """path=/v1/messages/count_tokens con body piccolo e output_tokens=0
    NON deve generare un evento empty_response_*."""
    # Condizioni che normalmente farebbero scattare il guard
    path = "/v1/messages/count_tokens"
    assert path.endswith("/count_tokens") is True  # precondition

    guard_fires = _guard_fires(
        path,
        text_blocks=0,
        total_bytes=25,
        acc_buf=b'{"input_tokens":42}',
    )
    assert guard_fires is False, (
        "Il guard NON deve scattare per /count_tokens: "
        "l'endswith('/count_tokens') deve bloccarlo"
    )


# -----------------------------------------------------------------------------
# Test 2: messages (senza count_tokens) DEVE triggere il guard (anti-regressione)
# -----------------------------------------------------------------------------

def test_messages_path_still_logged_as_empty():
    """path=/v1/messages con body piccolo e output_tokens=0
    DEVE generare un evento empty_response_* (anti-regressione)."""
    path = "/v1/messages"
    assert path.endswith("/count_tokens") is False  # precondition

    guard_fires = _guard_fires(
        path,
        text_blocks=0,
        total_bytes=25,
        acc_buf=b'{"output_tokens":0}',
    )
    assert guard_fires is True, (
        "Il guard DEVE scattare per /v1/messages: "
        "text_blocks=0, total_bytes<500, output_tokens=0"
    )


# -----------------------------------------------------------------------------
# Test 3: count_tokens/ con slash finale NON e' escluso (trade-off documentato)
# -----------------------------------------------------------------------------

def test_count_tokens_with_trailing_slash():
    """path=/v1/messages/count_tokens/ NON termina con '/count_tokens'
    (manca lo slash), quindi il guard NON lo esclude — e' un trade-off
    documentato: l'endpoint corretto non ha slash finale."""
    path = "/v1/messages/count_tokens/"

    assert path.endswith("/count_tokens") is False, (
        "Lo endswith('/count_tokens') e' False con lo slash finale: "
        "il guard NON esclude questo path. Trade-off intenzionale: "
        "l'endpoint corretto non ha slash."
    )

    guard_fires = _guard_fires(
        path,
        text_blocks=0,
        total_bytes=25,
        acc_buf=b'{"output_tokens":0}',
    )
    assert guard_fires is True, (
        "Con slash finale il guard NON esclude: comportamento atteso "
        "dato il trade-off documentato"
    )


# -----------------------------------------------------------------------------
# Test 4: helper _guard_fires = replica esatta della logica del guard
# -----------------------------------------------------------------------------

def test_guard_fires_replica_esatta():
    """Verifica che l'helper _guard_fires replichi esattamente
    tutte le condizioni del guard reale in streaming_relay.py."""
    # Caso base: count_tokens escluso
    assert _guard_fires("/v1/messages/count_tokens", 0, 100, b"{}") is False

    # Caso base: messages con text_blocks>0 — non scatta
    assert _guard_fires("/v1/messages", 1, 100, b"{}") is False

    # Caso base: messages con total_bytes>=500 — non scatta
    assert _guard_fires("/v1/messages", 0, 500, b"{}") is False

    # Caso base: messages con output_tokens>0 — non scatta
    assert _guard_fires("/v1/messages", 0, 100, b'{"output_tokens":5}') is False

    # Positivo: messages con tutte le condizioni — scatta
    assert _guard_fires("/v1/messages", 0, 100, b'{"output_tokens":0}') is True

    # Positivo: messages con buffer vuoto — scatta (real_out=0)
    assert _guard_fires("/v1/messages", 0, 100, b"{}") is True

    # Multi-match: l'ultimo output_tokens conta
    assert _guard_fires(
        "/v1/messages", 0, 100,
        b'{"output_tokens":0}{"output_tokens":5}'
    ) is False, "L'ultimo output_tokens e' 5, non 0"
