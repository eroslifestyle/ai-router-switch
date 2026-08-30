#!/usr/bin/env python3
"""PreToolUse hook: nega Agent()/Task() senza `model` esplicito.

Il router (ai-router-switch) instrada in base al NOME del modello richiesto:
Haiku = esecuzione (ACT), Opus/Sonnet/Fable = pianificazione (THINK). Un
subagent lanciato senza `model` eredita il modello del chiamante, quindi la
richiesta arriva al router con lo stesso ruolo di chi l'ha spawnato: la
delega non delega nulla, aggira semplicemente un giro di rete.

Registrare in .claude/settings.json come PreToolUse su "Task".
"""
import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # input non valido: non bloccare l'utente per un bug del hook

    if payload.get("tool_name") != "Task":
        return 0

    tool_input = payload.get("tool_input") or {}
    model = (tool_input.get("model") or "").strip()

    if model:
        return 0

    reason = (
        "Agent()/Task() senza `model` esplicito: il router instrada in base al nome "
        "del modello richiesto (Haiku=esecuzione, Opus/Sonnet/Fable=pianificazione). "
        "Aggiungi model=\"claude-haiku-4-5-20251001\" per delegare l'esecuzione, oppure "
        "un modello THINK esplicito se il subagent deve pianificare/verificare."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
