"""Regressione: request_shape in debug-errors.jsonl.

Il body troncato a 8KB finiva sempre dentro lo system prompt dei subagent:
mai tools/max_tokens visibili, quindi i 400 [1210] del 21-22/09/2026
diagnosticati alla cieca. request_shape e' calcolato da orig PRIMA del
troncamento."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, SRC_DIR)

from router_debug import DebugLogger  # noqa: E402


def test_request_shape_body_valido():
    orig = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 32000,
        "system": [{"type": "text", "text": "abc" * 1000},
                   {"type": "text", "text": "xy"}],
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "Bash"}, {"name": "Read"}, {"name": "Grep"}],
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "tool_choice": {"type": "auto"},
        "stream": True,
    }
    s = DebugLogger._request_shape(orig)
    assert s["model"] == "claude-haiku-4-5-20251001"
    assert s["max_tokens"] == 32000
    assert s["system_chars"] == 3002
    assert s["messages_count"] == 1
    assert s["tools_count"] == 3
    assert s["tool_names_sample"] == ["Bash", "Read", "Grep"]
    assert s["has_thinking"] is True
    assert s["tool_choice_type"] == "auto"
    assert s["top_level_keys"] == sorted(orig.keys())
    # piccolo per costruzione: il cap dei 15 nomi non deve esplodere
    assert len(s["tool_names_sample"]) <= 15


def test_request_shape_body_invalido_non_esplode():
    assert DebugLogger._request_shape(None) == {}
    assert DebugLogger._request_shape("non un dict") == {}
    # system come stringa
    s = DebugLogger._request_shape({"model": "m", "system": "pippo", "messages": []})
    assert s["system_chars"] == 5 and s["tools_count"] == 0


def test_capture_record_contiene_request_shape():
    """capture() end-to-end: il record ha request_shape accanto a sent_body."""
    dl = DebugLogger.get()
    dl.capture(kind="test_shape", status=400, orig={"model": "m", "max_tokens": 8},
               sent_body=b'{"system": "' + b"x" * 9000 + b'"}')
    rec = dl.errors[-1]
    assert rec["request_shape"]["max_tokens"] == 8
    assert "sent_body" in rec  # retrocompat
