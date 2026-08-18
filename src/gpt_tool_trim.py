"""Alleggerisce le richieste della modalità `gpt` rimuovendo le definizioni dei tool MCP.

Perché (misurato 2026-08-18): una sessione VSCode piena manda 304 tool, di cui 278
MCP, per ~67.500 token di soli SCHEMI — a ogni turno, prima di una singola parola
di conversazione. Le modalità cloud lo reggono perché il fardello viene cachato
(`mix-am`: 173k cache_read per richiesta) o perché il provider prefilla in fretta.
La modalità `gpt` è locale e ha `cache_read = 0`: code-max prefilla a ~440 tok/s,
quindi 87k token di contesto costano ~200 s contro un `LOCAL_TIMEOUT_SEC` di 240 s.
Il primo turno arrivava al timeout, e la chat restava appesa senza risposta.

Lo strip vive QUI e non nei settings di Claude Code per una ragione precisa: i
settings sono statici (per utente o per progetto) e non sanno quale modalità è
attiva. Disabilitare gli MCP lato client li spegnerebbe ovunque, penalizzando
`anthropic` e `mix-am` — che i tool li usano e li cachano benissimo. Il router
invece conosce la modalità, quindi il taglio è chirurgico: si applica SOLO a
`gpt`, in qualsiasi progetto, senza toccare nessun'altra modalità.

Cosa resta: i tool BUILT-IN (Read, Write, Edit, Bash, Glob, Grep...). Senza quelli
il modello non potrebbe fare nulla di utile. Cade solo l'accesso ai server MCP
(memoria, playwright, github, omnia...), che il modello locale userebbe comunque
male e che costano il 92% del payload.

Disattivabile con AIROUTER_GPT_STRIP_MCP_TOOLS=0.
"""

import json
import os

from debug_catalog import record_event
from router_utils import MCP_TOOL_PREFIX
from tool_isolation import (sanitize_defer_loading, sanitize_tool_choice,
                            preserva_cache_control)

STRIP_MCP_TOOLS = os.environ.get('AIROUTER_GPT_STRIP_MCP_TOOLS', '1') not in ('0', 'false', 'no')


def strip_mcp_tools(body: bytes) -> bytes:
    """Rimuove dalle richieste `gpt` i tool MCP, preservando i built-in."""
    if not STRIP_MCP_TOOLS:
        return body

    try:
        data = json.loads(body)
    except Exception:
        return body

    tools = data.get('tools')
    if not isinstance(tools, list) or not tools:
        return body

    kept = []
    for t in tools:
        if not isinstance(t, dict):
            kept.append(t)
            continue
        name = t.get('name')
        if isinstance(name, str) and name.startswith(MCP_TOOL_PREFIX):
            continue
        kept.append(t)

    if len(kept) == len(tools):
        return body

    record_event(
        # "info" e non "block": lo strip è il comportamento previsto della
        # modalità, non un'anomalia (stessa scelta di qwen_tool_trim).
        severity='info',
        category='local',
        kind='gpt_mcp_tool_strip',
        snippet=f'stripped={len(tools)-len(kept)} kept={len(kept)}/{len(tools)}',
    )

    if kept:
        preserva_cache_control(tools, kept)
        data['tools'] = kept
    else:
        data.pop('tools', None)
        data.pop('tool_choice', None)

    sanitize_tool_choice(data)
    sanitize_defer_loading(data)
    return json.dumps(data).encode()
