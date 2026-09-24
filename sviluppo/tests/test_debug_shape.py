"""Test diagnostica errori profondi: request_shape estesa, cap sent_body, client_disconnect.

Regressione dei 400 'Input tag tool_addition does not match any of the expected
tags' (47 casi 16-24/09/2026), diagnosticati in 9 giorni per mancanza di tipi
dei content block e flag beta nei record.
Assert puri, nessun framework: python3 sviluppo/tests/test_debug_shape.py
"""
import sys
sys.path.insert(0, 'src')

from router_debug import DebugLogger, KNOWN_CONTENT_BLOCK_TYPES, _sent_body_cap
import debug_catalog
from debug_catalog import CLIENT_DISCONNECT_MARKERS
import os, tempfile

# Catalogo ISOLATO per i test: mai toccare logs/BUG-CATALOG.jsonl di produzione.
os.environ["AIROUTER_CATALOG_PATH"] = tempfile.mktemp(suffix=".jsonl")
import importlib
importlib.reload(debug_catalog)
from debug_catalog import record_event, _load_catalog


def test_unknown_block_type_rilevato():
    orig = {
        "model": "claude-haiku",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "ciao"},
                {"type": "tool_addition", "tool": "x"},
            ]},
        ],
    }
    shape = DebugLogger._request_shape(orig)
    assert shape["unknown_block_types"] == ["tool_addition"], shape
    assert shape["content_block_types"].get("tool_addition") == 1, shape
    assert shape["content_block_types"].get("text") == 1, shape
    assert shape["last_message_role"] == "user"
    assert shape["last_block_type"] == "tool_addition"
    print("OK test_unknown_block_type_rilevato")


def test_body_normale_nessuno_sconosciuto():
    orig = {
        "model": "claude-haiku",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "q"},
                {"type": "tool_use", "id": "t1", "name": "f", "input": {}},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "r"},
            ]},
        ],
    }
    shape = DebugLogger._request_shape(orig)
    assert shape["unknown_block_types"] == [], shape
    assert set(shape["content_block_types"]) <= set(KNOWN_CONTENT_BLOCK_TYPES)
    print("OK test_body_normale_nessuno_sconosciuto")


def test_input_non_dict():
    assert DebugLogger._request_shape(None) == {}
    assert DebugLogger._request_shape("stringa") == {}
    print("OK test_input_non_dict")


def test_cap_env_e_clamp():
    old = os.environ.get("AIROUTER_DEBUG_SENT_BODY_BYTES")
    try:
        os.environ["AIROUTER_DEBUG_SENT_BODY_BYTES"] = "65536"
        assert _sent_body_cap() == 65536, _sent_body_cap()
        os.environ["AIROUTER_DEBUG_SENT_BODY_BYTES"] = "100"  # sotto min
        assert _sent_body_cap() == 1024, _sent_body_cap()
        os.environ["AIROUTER_DEBUG_SENT_BODY_BYTES"] = "99999999"  # sopra max
        assert _sent_body_cap() == 262144, _sent_body_cap()
        os.environ["AIROUTER_DEBUG_SENT_BODY_BYTES"] = "non-numero"
        assert _sent_body_cap() == 32768, _sent_body_cap()
        del os.environ["AIROUTER_DEBUG_SENT_BODY_BYTES"]
        assert _sent_body_cap() == 32768, _sent_body_cap()
    finally:
        if old is None:
            os.environ.pop("AIROUTER_DEBUG_SENT_BODY_BYTES", None)
        else:
            os.environ["AIROUTER_DEBUG_SENT_BODY_BYTES"] = old
    print("OK test_cap_env_e_clamp")


def test_client_disconnect_riclassificato():
    sig = record_event(
        severity="error", category="glm", kind="forward_exception",
        snippet="Cannot write to closing transport",
        detail={"client_closing": True})
    catalog = _load_catalog()
    entry = catalog[sig]
    assert entry["kind"] == "client_disconnect", entry
    assert entry["severity"] == "info", entry
    print("OK test_client_disconnect_riclassificato")


def test_errore_upstream_non_riclassificato():
    sig = record_event(
        severity="error", category="glm", kind="forward_exception",
        snippet="upstream 500 internal server error", detail={})
    catalog = _load_catalog()
    entry = catalog[sig]
    assert entry["kind"] == "forward_exception", entry
    assert entry["severity"] == "error", entry
    print("OK test_errore_upstream_non_riclassificato")


if __name__ == "__main__":
    test_unknown_block_type_rilevato()
    test_body_normale_nessuno_sconosciuto()
    test_input_non_dict()
    test_cap_env_e_clamp()
    test_client_disconnect_riclassificato()
    test_errore_upstream_non_riclassificato()
    print("TUTTI I TEST PASSANO (6/6)")
