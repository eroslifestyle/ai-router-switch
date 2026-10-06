import sys
sys.path.insert(0, 'src')

import json

import qwen_backend
import token_counter
from qwen_backend import (prepare_qwen_body, set_body_model, normalize_qwen_thinking,
                          add_qwen_system_cache, strip_tool_mutation_blocks,
                          clamp_qwen_max_tokens, qwen_max_output_for)
from role_routing import QWEN_THINK

ACT = "qwen3.8-flash"


def _body(**kw):
    return json.dumps(kw).encode()


def _full_body():
    return _body(model="claude-x", max_tokens=999999,
                 thinking={"type": "enabled", "budget_tokens": 1000},
                 system="sei utile",
                 messages=[{"role": "user", "content": [
                     {"type": "tool_addition", "name": "Read"},
                     {"type": "text", "text": "ciao"}]}])


def test_prep_think_tutte_le_trasformazioni():
    out = json.loads(prepare_qwen_body(_full_body(), QWEN_THINK))
    assert out["model"] == QWEN_THINK
    assert out["thinking"] == {"type": "adaptive"}
    assert out["max_tokens"] == qwen_max_output_for(QWEN_THINK)
    assert out["system"] == [{"type": "text", "text": "sei utile",
                              "cache_control": {"type": "ephemeral"}}]
    assert out["messages"][0]["content"] == [{"type": "text", "text": "ciao"}]


def test_prep_act_thinking_disabled():
    out = json.loads(prepare_qwen_body(_full_body(), ACT))
    assert out["thinking"] == {"type": "disabled"}


def test_prep_equivalenza_catchia_vecchia():
    old = _full_body()
    for upstream in (QWEN_THINK, ACT):
        chain = set_body_model(old, upstream)
        chain = normalize_qwen_thinking(chain, upstream)
        chain = add_qwen_system_cache(chain)
        chain = strip_tool_mutation_blocks(chain)
        chain = clamp_qwen_max_tokens(chain)
        assert json.loads(chain) == json.loads(prepare_qwen_body(old, upstream))


def test_prep_body_non_json_invariato():
    body = b"nonjson"
    assert prepare_qwen_body(body, QWEN_THINK) == body


def test_estimate_delega_token_counter():
    body = b'{"messages":[]}'
    assert qwen_backend._estimate_tokens(body, "qwen3-coder-plus") == \
        token_counter.estimate_tokens_body(body, "qwen3-coder-plus")
