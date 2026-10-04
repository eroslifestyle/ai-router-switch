"""La modalità local deve togliere SEMPRE il campo top-level `thinking`:
nessun modello locale lo supporta (l'abliterato via Ollama risponde 500
"does not support thinking", 2026-10-04). La scelta non dipende dal modello
richiesto da Claude Code (opus/sonnet il thinking lo supportano)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from local_backend import drop_thinking_field  # noqa: E402


def test_rimuove_thinking_top_level():
    body = json.dumps({
        "model": "claude-opus-4-8",
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "messages": [{"role": "user", "content": "ciao"}],
    }).encode()
    out = json.loads(drop_thinking_field(body))
    assert "thinking" not in out
    assert out["messages"] == [{"role": "user", "content": "ciao"}]


def test_body_senza_thinking_invariato():
    body = b'{"model":"claude-haiku-4-5","messages":[{"role":"user","content":"x"}]}'
    assert drop_thinking_field(body) is body


def test_json_non_valido_invariato():
    body = b'non-json'
    assert drop_thinking_field(body) is body
