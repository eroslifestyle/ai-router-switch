#!/usr/bin/env python3
"""Test per _merge_beta: il beta del client non deve piu' essere cancellato
(root cause dei 400 "Input tag 'tool_addition' ...", 47 casi 2026-09-16/24)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from forward_anthropic import _merge_beta

# 1. beta del client preservato + flag aggiunto
h = {"anthropic-beta": "context-1m,fine-grained-tool-streaming-2025-05-14"}
assert _merge_beta(h, "oauth-2025-04-20") == \
    "context-1m,fine-grained-tool-streaming-2025-05-14,oauth-2025-04-20"
assert h["anthropic-beta"].endswith("oauth-2025-04-20")

# 2. grafia Anthropic-Beta del client: una sola chiave finale, nessun duplicato
h = {"Anthropic-Beta": "context-1m"}
v = _merge_beta(h, "oauth-2025-04-20")
assert v == "context-1m,oauth-2025-04-20"
assert list(h.keys()).count("Anthropic-Beta") == 0
assert sum(1 for k in h if k.lower() == "anthropic-beta") == 1

# 3. dedup se il flag c'era gia' (case-insensitive)
h = {"anthropic-beta": "Oauth-2025-04-20,context-1m"}
assert _merge_beta(h, "oauth-2025-04-20") == "Oauth-2025-04-20,context-1m"

# 4. headers senza beta -> solo il flag
h = {"content-type": "application/json"}
assert _merge_beta(h, "oauth-2025-04-20") == "oauth-2025-04-20"
assert h["anthropic-beta"] == "oauth-2025-04-20"

# 5. ordine preservato (client prima, nostri flag poi, senza riordinare)
h = {"anthropic-beta": "zz-late,aa-early"}
assert _merge_beta(h, "mm-mid", "aa-early") == "zz-late,aa-early,mm-mid"

# 6. grafia sconosciuta (non solo le due note) viene raccolta e rimossa
h = {"ANTHROPIC-BETA": "context-1m"}
assert _merge_beta(h, "oauth-2025-04-20") == "context-1m,oauth-2025-04-20"
assert "ANTHROPIC-BETA" not in h

# 7. spazi attorno ai token strip-ati
h = {"anthropic-beta": " context-1m ,  spaced-flag"}
assert _merge_beta(h, "oauth-2025-04-20") == \
    "context-1m,spaced-flag,oauth-2025-04-20"

# 8. regressione NameError: la riga di log del 400 deve essere costruibile
# anche quando il path non e' /v1/messages (li' _caps non veniva definito).
# Copertura STATICa: valuta l'espressione f-string di log con _caps={} come
# nel ramo else; NON esegue forward_anthropic (async, richiede sessione).
_caps = {}
_beta = h.get("anthropic-beta", "")
raw_err = b"Input tag 'tool_addition' found"
line = (f"[forward_anthropic] 400 body: {raw_err.decode(errors='replace')[:800]} "
        f"| beta={_beta} model={_caps.get('model', '')}")
assert "model=" in line and "beta=" in line

print("OK: 8/8 test passati")
