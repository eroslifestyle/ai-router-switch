"""Test del sanitizzatore messages verso GLM (fix 400 [1210] difensivo).

Senza rete: verifica in isolamento sanitize_glm_messages (glm_backend).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from glm_backend import (sanitize_glm_messages, clamp_glm_max_tokens,
                         normalize_glm_system, glm_max_tokens_limit, GLM_MIN_MAX_TOKENS)


def _msgs_of(result: bytes):
    return json.loads(result)["messages"]


class TestSanitizeGlmMessages:
    def test_thinking_and_signature_removed(self):
        body = json.dumps({"messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "t", "signature": "abc"},
                {"type": "text", "text": "OK"}]},
        ]}).encode()
        msgs = _msgs_of(sanitize_glm_messages(body))
        blocks = msgs[1]["content"]
        assert all(b.get("type") != "thinking" for b in blocks)
        assert all("signature" not in b for b in blocks)
        assert {"type": "text", "text": "OK"} in blocks

    def test_redacted_thinking_removed(self):
        body = json.dumps({"messages": [
            {"role": "assistant", "content": [
                {"type": "redacted_thinking", "data": "AA"},
                {"type": "text", "text": "OK"}]},
        ]}).encode()
        msgs = _msgs_of(sanitize_glm_messages(body))
        assert all(b.get("type") != "redacted_thinking" for b in msgs[0]["content"])

    def test_empty_message_removed(self):
        body = json.dumps({"messages": [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "t", "signature": "s"}]},
            {"role": "user", "content": [{"type": "text", "text": "go"}]},
        ]}).encode()
        msgs = _msgs_of(sanitize_glm_messages(body))
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_consecutive_same_role_merged(self):
        body = json.dumps({"messages": [
            {"role": "user", "content": [{"type": "text", "text": "a"}]},
            {"role": "user", "content": [{"type": "text", "text": "b"}]},
        ]}).encode()
        msgs = _msgs_of(sanitize_glm_messages(body))
        assert len(msgs) == 1
        assert [b["text"] for b in msgs[0]["content"]] == ["a", "b"]

    def test_clean_payload_unchanged(self):
        body = json.dumps({"model": "glm-4.7", "max_tokens": 64, "messages": [
            {"role": "user", "content": [{"type": "text", "text": "a"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
        ]}).encode()
        assert sanitize_glm_messages(body) == body  # byte-identico: zero regressione

    def test_malformed_input_no_exception(self):
        assert sanitize_glm_messages(b"not json") == b"not json"
        assert sanitize_glm_messages(b"[1,2,3]") == b"[1,2,3]"
        assert sanitize_glm_messages(b"{}") == b"{}"
        weird = json.dumps({"messages": [
            "stringa-non-dict",
            {"content": [{"foo": "bar"}, "plain", {"type": "thinking", "signature": "x"}]},
            {"role": "user", "content": "stringa"},
            {"role": None},
            42,
        ]}).encode()
        out = sanitize_glm_messages(weird)  # non deve sollevare
        json.loads(out)  # resta JSON valido


# ── Normalizzazioni difensive 400 [1210] (2026-09-20) ──────────────────────────

def _body(**kw):
    d = {"model": "glm-4.7", "max_tokens": 1024, "messages": [{"role": "user", "content": "ciao"}]}
    d.update(kw)
    return json.dumps(d).encode()


def test_max_tokens_float_forzato_a_int():
    out = json.loads(clamp_glm_max_tokens(_body(max_tokens=32768.0), model="glm-4.7"))
    assert out["max_tokens"] == 32768


def test_max_tokens_stringa_forzata_a_int():
    out = json.loads(clamp_glm_max_tokens(_body(max_tokens="64000"), model="glm-4.7"))
    # forzata a int; se supera il tetto effettivo viene anche clamped
    assert out["max_tokens"] == min(64000, glm_max_tokens_limit("glm-4.7"))
    assert isinstance(out["max_tokens"], int)


def test_max_tokens_none_default_al_limite():
    out = json.loads(clamp_glm_max_tokens(_body(max_tokens=None), model="glm-4.7"))
    assert out["max_tokens"] == glm_max_tokens_limit("glm-4.7")


def test_max_tokens_zero_e_negativo_innalzati_al_minimo():
    for v in (0, -5):
        out = json.loads(clamp_glm_max_tokens(_body(max_tokens=v), model="glm-4.7"))
        assert out["max_tokens"] == GLM_MIN_MAX_TOKENS


def test_max_tokens_bool_trattato_come_assente():
    out = json.loads(clamp_glm_max_tokens(_body(max_tokens=True), model="glm-4.7"))
    assert out["max_tokens"] == glm_max_tokens_limit("glm-4.7")


def test_max_tokens_valido_invariato():
    b = _body(max_tokens=8192)
    assert clamp_glm_max_tokens(b, model="glm-4.7") == b  # byte-identico


def test_system_dict_convertito_in_lista():
    out = json.loads(normalize_glm_system(_body(system={"role": "system", "content": "x"})))
    assert isinstance(out["system"], list)
    assert out["system"][0]["type"] == "text"


def test_system_stringa_e_lista_invariati():
    for v in ("semplice", [{"type": "text", "text": "b"}, {"type": "text", "text": "c"}]):
        b = _body(system=v)
        assert normalize_glm_system(b) == b  # byte-identico
