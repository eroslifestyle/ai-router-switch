"""slim_local_body non deve sollevare UnboundLocalError su 'kept'.

Regressione 2026-10-04: quando il body ha tool MA nessuno fuori allowlist
(tutti in allowlist dopo lo strip MCP) e lo slim dei system-reminder supera i
1000 char, il record_event leggeva `len(kept)` mentre `kept` era assegnato solo
dentro `if removed_names:` -> UnboundLocalError -> 502 su ogni richiesta
mix-al/local/gpt con tool.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import local_backend  # noqa: E402


def test_slim_tool_in_allowlist_con_slim_grande_non_crasha():
    reminder = ("<system-reminder>\nAvailable agent types for the Agent tool:\n"
                + ("- agent-x: una descrizione abbastanza lunga da contare\n" * 400)
                + "</system-reminder>")
    body = json.dumps({
        "model": "coder-next-ablit",
        "tools": [{"name": "Bash"}, {"name": "Read"}],   # entrambi in allowlist -> nessuno rimosso
        "messages": [{"role": "user",
                      "content": [{"type": "text", "text": reminder + "\nciao"}]}],
    }).encode()

    out = local_backend.slim_local_body(body, "mix-al")  # NON deve sollevare
    d = json.loads(out)
    assert [t["name"] for t in d["tools"]] == ["Bash", "Read"], "tool in allowlist persi"
    assert "Available agent types" not in json.dumps(d), "system-reminder non slimmato"
