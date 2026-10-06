import sys
sys.path.insert(0, 'src')

import json

import qwen_backend
from qwen_backend import normalize_qwen_thinking, add_qwen_system_cache, strip_tool_mutation_blocks

ACT = "qwen3.8-flash"
THINK = qwen_backend.QWEN_THINK  # "qwen3.8-max"


def _body(**kw):
    return json.dumps(kw).encode()


def test_act_enabled_budget_diventa_disabled():
    body = _body(thinking={"type": "enabled", "budget_tokens": 2000}, max_tokens=64)
    out = json.loads(normalize_qwen_thinking(body, ACT))
    assert out["thinking"] == {"type": "disabled"}
    assert out["max_tokens"] == 64


def test_think_enabled_budget_diventa_adaptive():
    body = _body(thinking={"type": "enabled", "budget_tokens": 2000})
    out = json.loads(normalize_qwen_thinking(body, THINK))
    assert out["thinking"] == {"type": "adaptive"}


def test_act_senza_thinking_aggiunge_disabled_resto_identico():
    original = {"model": "m", "max_tokens": 64,
                "messages": [{"role": "user", "content": "ciao"}]}
    out = json.loads(normalize_qwen_thinking(json.dumps(original).encode(), ACT))
    assert out["thinking"] == {"type": "disabled"}
    assert {k: v for k, v in out.items() if k != "thinking"} == original


def test_think_senza_thinking_invariato():
    body = _body(messages=[])
    assert normalize_qwen_thinking(body, THINK) == body


def test_cache_system_stringa_diventa_lista_ephemeral():
    out = json.loads(add_qwen_system_cache(_body(system="prompt")))
    assert out["system"] == [{"type": "text", "text": "prompt",
                              "cache_control": {"type": "ephemeral"}}]


def test_cache_system_lista_marca_solo_ultimo():
    system = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    out = json.loads(add_qwen_system_cache(_body(system=system)))
    assert "cache_control" not in out["system"][0]
    assert out["system"][1]["cache_control"] == {"type": "ephemeral"}


def test_cache_senza_system_invariato():
    body = _body(messages=[])
    assert add_qwen_system_cache(body) == body


def test_strip_tool_mutation_rimuove_da_user_e_system():
    body = _body(model="m", max_tokens=32,
                 messages=[{"role": "system", "content": [
                     {"type": "text", "text": "nuovo tool"},
                     {"type": "tool_addition", "name": "Read"}]},
                     {"role": "user", "content": [
                     {"type": "tool_removal", "name": "Grep"},
                     {"type": "text", "text": "ciao"}]}])
    out = json.loads(strip_tool_mutation_blocks(body))
    assert out["messages"][0]["content"] == [{"type": "text", "text": "nuovo tool"}]
    assert out["messages"][1]["content"] == [{"type": "text", "text": "ciao"}]
    assert out["model"] == "m" and out["max_tokens"] == 32


def test_strip_tool_mutation_senza_blocchi_byte_identici():
    body = _body(messages=[{"role": "user", "content": [
        {"type": "text", "text": "ciao"}]}])
    assert strip_tool_mutation_blocks(body) == body
