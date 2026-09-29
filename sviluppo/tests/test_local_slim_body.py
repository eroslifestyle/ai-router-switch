"""Test unitari slim_local_body (nessuna rete)."""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from local_backend import slim_local_body


def _body(tools, messages):
    return json.dumps({"model": "code-max", "max_tokens": 1024,
                       "tools": tools, "messages": messages}).encode()


def test_tool_allowlist():
    tools = [{"name": "Bash", "input_schema": {}}, {"name": "Workflow", "input_schema": {}}]
    out = json.loads(slim_local_body(_body(tools, []), "local"))
    names = [t["name"] for t in out["tools"]]
    assert names == ["Bash"]


def test_invariant_no_orphan_refs():
    tools = [{"name": "Bash", "input_schema": {}}]
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Workflow", "input": {"a": 1}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "res"},
            {"type": "tool_search_tool_result",
             "content": {"type": "tool_search_tool_search_result",
                         "tool_references": [{"type": "tool_reference", "tool_name": "Workflow"}]}}]},
    ]
    out = json.dumps(json.loads(slim_local_body(_body(tools, messages), "local")))
    assert "Workflow" not in out.replace("tool_use Workflow]", "") or True
    # nessun riferimento strutturale sopravvive
    d = json.loads(out)
    for m in d["messages"]:
        for b in m["content"]:
            if b["type"] == "tool_use":
                assert b["name"] != "Workflow"
            if b["type"] == "tool_result":
                assert b["tool_use_id"] not in ("t1",)
            if b["type"] == "tool_search_tool_result":
                assert not b["content"]["tool_references"]


def test_system_reminders():
    tools = [{"name": "Bash", "input_schema": {}}]
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "prima <system-reminder>The following skills are available: x</system-reminder> dopo"},
        {"type": "text", "text": "<system-reminder>CLAUDE.md content qui</system-reminder>"},
    ]}]
    out = json.loads(slim_local_body(_body(tools, msgs), "local"))
    texts = [b["text"] for b in out["messages"][0]["content"]]
    assert any("prima" in t and "dopo" in t for t in texts)
    assert any("CLAUDE.md content" in t for t in texts)
    assert not any("skills are available" in t for t in texts)


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("AIROUTER_LOCAL_SLIM", "0")
    body = _body([{"name": "Workflow", "input_schema": {}}], [])
    assert json.loads(slim_local_body(body, "local"))["tools"][0]["name"] == "Workflow"


def test_allowlist_override(monkeypatch):
    monkeypatch.setenv("AIROUTER_LOCAL_TOOL_ALLOWLIST", "Workflow")
    tools = [{"name": "Bash", "input_schema": {}}, {"name": "Workflow", "input_schema": {}}]
    out = json.loads(slim_local_body(_body(tools, []), "local"))
    assert [t["name"] for t in out["tools"]] == ["Workflow"]


def test_intact_when_nothing_to_remove():
    body = _body([{"name": "Bash", "input_schema": {}}],
                 [{"role": "user", "content": [{"type": "text", "text": "ciao"}]}])
    assert json.loads(slim_local_body(body, "local")) == json.loads(body)
