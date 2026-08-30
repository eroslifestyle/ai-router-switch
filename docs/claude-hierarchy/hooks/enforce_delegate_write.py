#!/usr/bin/env python3
"""PreToolUse hook: in modalita' miste, il THINK non scrive codice di progetto direttamente.

Nelle modalita' dove THINK e ACT sono provider diversi (es. mix-am: Claude pensa,
MiniMax esegue), un Edit/Write diretto del THINK bypassa l'esecutore e consuma
la quota costosa per generare il diff. Sotto una soglia minima (micro-edit) resta
permesso: il giro di un subagent costerebbe piu' del beneficio.

Registrare in .claude/settings.json come PreToolUse su "Edit", "Write", "MultiEdit".
"""
import json
import os
import sys
from pathlib import Path

DELEGATING_MODES = frozenset({
    "mix-am", "mix-am-2",
    "mix-ag", "mix-ag-2",
    "mix-gm", "mix-gm-2",
    "mix-al",
    "ultra",
})

MICRO_EDIT_MAX_LINES = int(os.environ.get("AIROUTER_HOOK_MICRO_EDIT_LINES", "15"))

CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".rb", ".php", ".sh", ".bash",
})


def _config_home() -> Path:
    """Duplica src/paths.py:config_home() — processo separato, no import cross-repo."""
    env_home = os.environ.get("AIROUTER_HOME")
    if env_home:
        return Path(env_home).expanduser()
    legacy = Path.home() / ".claude"
    if legacy.is_dir():
        return legacy
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) / "ai-router-switch" if base else Path.home() / "AppData" / "Roaming" / "ai-router-switch"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ai-router-switch"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) / "ai-router-switch" if xdg else Path.home() / ".config" / "ai-router-switch"


def _current_mode() -> str:
    mode_file = _config_home() / "ai-router-mode"
    try:
        return mode_file.read_text().strip()
    except OSError:
        return ""


def _line_count(tool_name: str, tool_input: dict) -> int:
    if tool_name == "Write":
        return tool_input.get("content", "").count("\n") + 1
    if tool_name == "Edit":
        return tool_input.get("new_string", "").count("\n") + 1
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        return sum(e.get("new_string", "").count("\n") + 1 for e in edits)
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = payload.get("tool_name")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return 0

    mode = _current_mode()
    if mode not in DELEGATING_MODES:
        return 0

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")
    if Path(file_path).suffix not in CODE_EXTENSIONS:
        return 0

    if _line_count(tool_name, tool_input) <= MICRO_EDIT_MAX_LINES:
        return 0

    reason = (
        f"Modalita' '{mode}' attiva: il THINK non scrive codice di progetto "
        f"oltre {MICRO_EDIT_MAX_LINES} righe. Delega a un subagent con model esplicito "
        "sull'esecutore (Haiku instrada sull'ACT della modalita'), poi verifica il diff."
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
