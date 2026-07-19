"""Isolamento tool nativi per provider (anthropic/minimax/glm).

Soluzione centralizzata (2026-07-19): sostituisce le funzioni ad-hoc duplicate
per singola modalità (_strip_foreign_branded_tools, strip_foreign_branded_tools_for_glm)
che coprivano solo le 3 modalità pure e lasciavano scoperte le modalità miste
(mix-am/mix-ag/mix-gm) — bug reale: MiniMax MCP restava visibile a GLM in
mix-ag e ad Anthropic server-tool restava visibile a MiniMax/GLM in mix-gm.

Design a choke-point: filter_tools_for_backend() va chiamata dentro le
funzioni forward_anthropic/forward_anthropic_direct/forward_minimax/forward_glm
(un solo punto per backend, non ai call-site delle pipeline). Ogni richiesta
verso un upstream passa SEMPRE da una di queste 4 funzioni, quindi ogni
pipeline — presente o futura — eredita l'isolamento senza doverlo richiamare
esplicitamente a ogni nuovo mix.
"""
import json

import debug_catalog


def is_anthropic_server_tool(t: dict) -> bool:
    """Server-tool Anthropic (web_search_20250305, computer_use, bash,
    code_execution, ...): eseguiti server-side su api.anthropic.com,
    riconoscibili perché privi di input_schema (i tool client-eseguiti/MCP
    ce l'hanno sempre). Nessun altro backend sa eseguirli — se ricevuti,
    MiniMax/GLM rispondono 400 (bug 2013, 2026-07-04)."""
    return isinstance(t, dict) and "input_schema" not in t


def is_minimax_branded_tool(t: dict) -> bool:
    """Tool nativo MiniMax (MCP mcp__MiniMax__web_search/understand_image)."""
    return isinstance(t, dict) and "minimax" in (t.get("name") or "").lower()


def is_glm_branded_tool(t: dict) -> bool:
    """Tool nativo z.ai/GLM (es. MCP webSearchPrime), riconosciuto per nome
    indipendentemente dall'alias del server MCP scelto dall'utente."""
    if not isinstance(t, dict):
        return False
    name = (t.get("name") or "").lower()
    return "websearchprime" in name or name.startswith("mcp__zai__")


_BRAND_CHECK = {
    "anthropic": is_anthropic_server_tool,
    "minimax": is_minimax_branded_tool,
    "glm": is_glm_branded_tool,
}


def filter_tools_for_backend(body: bytes, backend: str) -> bytes:
    """Rimuove dall'array `tools` i tool brandizzati di provider DIVERSI da
    `backend` ('anthropic'|'minimax'|'glm'). I tool locali di Claude Code
    (Bash/Read/Write/Edit/...) e qualunque MCP non riconosciuto (context7,
    github, ...) non sono mai toccati: non sono brandizzati di un provider
    AI specifico. `backend` sconosciuto o body non-JSON → no-op sicuro."""
    if backend not in _BRAND_CHECK:
        return body
    try:
        data = json.loads(body)
    except Exception:
        return body
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        return body
    foreign = [check for name, check in _BRAND_CHECK.items() if name != backend]
    kept = [t for t in tools if not any(check(t) for check in foreign)]
    if len(kept) == len(tools):
        return body
    stripped_names = [t.get("name", "?") for t in tools if t not in kept]
    debug_catalog.record_event(
        severity="block", category=backend, kind="tool_isolation_strip",
        snippet=f"stripped={stripped_names[:10]} kept={len(kept)}/{len(tools)}",
    )
    if kept:
        data["tools"] = kept
    else:
        data.pop("tools", None)
        data.pop("tool_choice", None)
    return json.dumps(data).encode()
