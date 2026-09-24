"""Test di `airouter-info errori` su fixture JSONL in /tmp (assert puri, no framework)."""
import json
import os
import subprocess
import sys
import tempfile
import time

TOOL = os.path.join(os.path.dirname(__file__), "..", "tools", "airouter_info.py")
ORA = time.time()

REC = [
    # classe dominante, con request_shape e unknown_block_types (firma 400)
    {"ts": ORA - 100, "kind": "400-invalid-request", "mode": "glm", "status": 400,
     "upstream_model": "glm-5.3", "beta": "claude-code-20250219",
     "request_shape": {"model": "haiku", "max_tokens": 32000, "system_chars": 1200,
                        "messages_count": 40, "tools_count": 12, "tools_bytes": 60000,
                        "tool_names_sample": ["mcp-video", "context7"],
                        "unknown_block_types": ["tool_search_tool_result"]}},
    {"ts": ORA - 50, "kind": "400-invalid-request", "mode": "glm", "status": 400,
     "upstream_model": "glm-5.3", "beta": "claude-code-20250219",
     "request_shape": {"model": "haiku", "max_tokens": 8000, "system_chars": 900,
                        "messages_count": 10, "tools_count": 4, "tools_bytes": 2000,
                        "tool_names_sample": ["mcp-video"],
                        "unknown_block_types": ["tool_search_tool_result"]}},
    # record VECCHIO senza request_shape: "assente" non e' "0"
    {"ts": ORA - 86400 * 3, "kind": "upstream-429", "mode": "glm", "status": 429,
     "upstream_model": "glm-4.7"},
    # errore generico
    {"ts": ORA - 30, "kind": "stream-abort", "mode": "anthropic", "status": 500,
     "upstream_model": "claude-haiku", "beta": ""},
]


def main() -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for r in REC:
            fh.write(json.dumps(r) + "\n")
        fh.write("{questa riga NON e' json\n")
        path = fh.name
    env = dict(os.environ, AIROUTER_INFO_DEBUG_EVENTS=path)
    p = subprocess.run([sys.executable, TOOL, "errori"], capture_output=True,
                       text=True, timeout=30, env=env)
    print(p.stdout)
    out = p.stdout
    assert p.returncode == 0, f"exit {p.returncode}: {p.stderr}"
    # classi attese
    assert "400-invalid-request" in out and "stream-abort" in out and "upstream-429" in out
    # unknown block type segnalato in ALLARMI
    assert "tool_search_tool_result" in out and "ALLARMI" in out
    # beta vuota su 400 anthropic: il record stream-abort e' 500, quindi NON deve scattare
    assert "anthropic-beta VUOTO" not in out
    # record senza shape contati
    assert "SENZA request_shape" in out
    # riga malformata scartata e contata, non crash
    assert "1 righe non parsabili" in out or "1 riga non parsabili" in out
    # filtri
    p2 = subprocess.run([sys.executable, TOOL, "errori", "--kind", "429"],
                        capture_output=True, text=True, env=env, timeout=30)
    assert p2.returncode == 0
    assert "upstream-429" in p2.stdout and "400-invalid-request" not in p2.stdout
    os.unlink(path)
    print("test_errori: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
