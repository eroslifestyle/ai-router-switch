import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from self_healing.sensor import classify_outcome, classify_task

def test_outcome_empty():
    assert classify_outcome(200, "end_turn", 0, 0, 0) == "empty"

def test_outcome_ok():
    assert classify_outcome(200, "end_turn", 2, 0, 100) == "ok"

def test_outcome_error_status():
    assert classify_outcome(502, "", 0, 0, 0) == "error"

def test_outcome_error_stop():
    assert classify_outcome(200, "error", 1, 0, 10) == "error"

def test_outcome_tool_only():
    assert classify_outcome(200, "end_turn", 0, 1, 50) == "tool_only"

def test_outcome_truncated():
    assert classify_outcome(200, "max_tokens", 2, 0, 8000) == "truncated"

def test_outcome_max_tokens_empty():
    assert classify_outcome(200, "max_tokens", 0, 0, 8000) == "empty"

def test_outcome_none_safe():
    assert classify_outcome(None, None, None, None, None) == "empty"

def test_task_coding():
    assert classify_task({"messages":[{"role":"user","content":"def foo(): fix the bug and implement refactor"}], "tools":[]}) == "coding"

def test_task_coding_no_tools():
    assert classify_task({"messages":[{"role":"user","content":"def foo class bar import baz refactor implement fix bug"}]}) == "coding"

def test_task_vision():
    assert classify_task({"messages":[{"role":"user","content":[{"type":"image","source":{}}]}]}) == "vision"

def test_task_chat():
    assert classify_task({"messages":[{"role":"user","content":"ciao"}]}) == "chat"

def test_task_bytes_body():
    assert classify_task(b'{"messages":[{"role":"user","content":"hello"}]}') == "chat"

def test_task_reasoning_large():
    large_content = "a " * 5000
    assert classify_task({"messages":[{"role":"user","content": large_content}]}) == "reasoning"
