import sys
sys.path.insert(0, 'src')
import json

from glm_backend import strip_thinking_for_model
from anthropic_capabilities import strip_thinking_for_model as _strip_ac

HAIKU = "claude-haiku-4-5-20251001"


def body_with(thinking):
    d = {"model": HAIKU, "max_tokens": 1024, "messages": [{"role": "user", "content": "ciao"}]}
    if thinking is not None:
        d["thinking"] = thinking
    return json.dumps(d).encode()


def test_haiku_con_thinking_rimosso():
    body = body_with({"type": "adaptive"})
    out = strip_thinking_for_model(body, HAIKU)
    d = json.loads(out)
    assert "thinking" not in d, f"Atteso thinking assente, ottenuto: {d.get('thinking')}"
    d_src = json.loads(body)
    d_src.pop("thinking")
    assert d == d_src, "Il resto del body deve restare identico"


def test_opus_con_thinking_invariato():
    body = body_with({"type": "adaptive"}).replace(HAIKU.encode(), b"claude-opus-5")
    assert strip_thinking_for_model(body, "claude-opus-5") == body


def test_haiku_senza_thinking_identico():
    body = body_with(None)
    assert strip_thinking_for_model(body, HAIKU) == body


def test_body_non_json_identico():
    body = b"non e' json"
    assert strip_thinking_for_model(body, HAIKU) == body


def test_env_keep_thinking_invariato(monkeypatch):
    monkeypatch.setenv("AIROUTER_GLM_KEEP_THINKING", "1")
    body = body_with({"type": "adaptive"})
    assert strip_thinking_for_model(body, HAIKU) == body


def test_env_keep_thinking_nuova_invariato(monkeypatch):
    monkeypatch.setenv("AIROUTER_KEEP_THINKING", "1")
    body = body_with({"type": "adaptive"})
    assert strip_thinking_for_model(body, HAIKU) == body


def test_import_da_anthropic_capabilities():
    body = body_with({"type": "adaptive"})
    d = json.loads(_strip_ac(body, HAIKU))
    assert "thinking" not in d, f"Atteso thinking assente, ottenuto: {d.get('thinking')}"
    body_opus = body.replace(HAIKU.encode(), b"claude-opus-5")
    assert _strip_ac(body_opus, "claude-opus-5") == body_opus
