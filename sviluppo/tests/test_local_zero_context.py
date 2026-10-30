"""Mode local pura: la richiesta parte da contesto ZERO.

Richiesta utente 2026-10-30: niente tool (nemmeno i core), niente system prompt,
niente system-reminder nei messages. Disattivabile con AIROUTER_LOCAL_ZERO_CONTEXT=0.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import local_backend  # noqa: E402


def _body_con_contesto() -> bytes:
    return json.dumps({
        "model": "coder-next-ablit",
        "tools": [{"name": "Bash"}],
        "tool_choice": {"type": "auto"},
        "system": [{"type": "text", "text": "X"}],
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>CLAUDE.md roba</system-reminder>"},
                {"type": "text", "text": "ciao"},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "out"},
            ]},
        ],
    }).encode()


def test_local_rimuove_tools_system_e_reminder(monkeypatch):
    monkeypatch.delenv("AIROUTER_LOCAL_ZERO_CONTEXT", raising=False)
    out = local_backend.slim_local_body(_body_con_contesto(), "local")
    d = json.loads(out)
    assert "tools" not in d
    assert "tool_choice" not in d
    assert "system" not in d
    assert "system-reminder" not in json.dumps(d)
    assert "ciao" in json.dumps(d)
    tipi = [b.get("type") for m in d["messages"] if isinstance(m["content"], list)
            for b in m["content"]]
    assert "tool_use" not in tipi and "tool_result" not in tipi


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
