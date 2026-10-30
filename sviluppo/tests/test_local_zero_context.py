"""Mode local pura: contesto minimo stile agente `pi`.

Semantica 2026-10-30: solo tool core (Bash, Read, Edit, Write, Grep, Glob; override
AIROUTER_LOCAL_ZERO_TOOLS), system corto con la directory di lavoro, niente
system-reminder nei messages. Disattivabile con AIROUTER_LOCAL_ZERO_CONTEXT=0.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import local_backend  # noqa: E402


def _body_con_contesto() -> bytes:
    return json.dumps({
        "model": "coder-next-ablit",
        "tools": [{"name": "Bash"}, {"name": "mcp__x__y"}],
        "tool_choice": {"type": "auto"},
        "system": "Sei Claude Code.\nPrimary working directory: /tmp/proj\n",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>CLAUDE.md roba</system-reminder>"},
                {"type": "text", "text": "ciao"},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_use", "id": "t2", "name": "mcp__x__y", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "out"},
            ]},
        ],
    }).encode()


def test_local_tiene_tool_core_e_system_corto(monkeypatch):
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_CONTEXT", raising=False)
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_TOOLS", raising=False)
    out = local_backend.slim_local_body(_body_con_contesto(), "local")
    d = json.loads(out)
    assert [t["name"] for t in d["tools"]] == ["Bash"]
    assert local_backend.LOCAL_ZERO_SYSTEM in d["system"]
    assert "Directory di lavoro: /tmp/proj" in d["system"]
    assert "system-reminder" not in json.dumps(d)
    assert "ciao" in json.dumps(d)


def test_tool_use_core_resta_e_mcp_declassato(monkeypatch):
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_CONTEXT", raising=False)
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_TOOLS", raising=False)
    d = json.loads(local_backend.slim_local_body(_body_con_contesto(), "local"))
    blocchi = d["messages"][1]["content"]
    assert blocchi[0]["type"] == "tool_use" and blocchi[0]["name"] == "Bash"
    assert blocchi[1]["type"] == "text" and "mcp__x__y" in blocchi[1]["text"]
    assert d["messages"][2]["content"][0]["type"] == "tool_result"


def test_messaggio_solo_reminder_diventa_segnaposto(monkeypatch):
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_CONTEXT", raising=False)
    body = json.dumps({
        "model": "coder-next-ablit",
        "messages": [{"role": "user", "content": "<system-reminder>x</system-reminder>"}],
    }).encode()
    d = json.loads(local_backend.slim_local_body(body, "local"))
    assert d["messages"][0]["content"] == "(vuoto)"


def test_mix_al_conserva_system(monkeypatch):
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_CONTEXT", raising=False)
    d = json.loads(local_backend.slim_local_body(_body_con_contesto(), "mix-al"))
    assert "system" in d


def test_flag_zero_disattiva_zero_context(monkeypatch):
    monkeypatch.setenv("AIROUTER_LOCAL_ZERO_CONTEXT", "0")
    d = json.loads(local_backend.slim_local_body(_body_con_contesto(), "local"))
    assert "system" in d


def test_messaggi_role_system_via_e_cwd_estratta(monkeypatch):
    # Forma reale di Claude Code 2.1.310: ambiente e output hook in un messaggio
    # role "system" dentro messages, non nel campo system.
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_CONTEXT", raising=False)
    body = json.dumps({"system": [{"type": "text", "text": "You are Claude."}], "messages": [
        {"role": "user", "content": "ciao"},
        {"role": "system", "content": [{"type": "text", "text":
            "hook PONYTAIL\n - Primary working directory: /tmp/my proj\n - Is a git repository: false"}]},
    ]}).encode()
    d = json.loads(local_backend.slim_local_body(body, "local"))
    assert [m["role"] for m in d["messages"]] == ["user"]
    assert "Directory di lavoro: /tmp/my proj" in d["system"]
    assert "PONYTAIL" not in json.dumps(d)
