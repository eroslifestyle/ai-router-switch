"""MiniMax body transformation utilities — remap model, strip unsupported fields, sanitize server tools.

Dependency injection: il proxy passa log_fn, resolve_fp, log_model_fn, orig_model_store
per evitare closure sul global scope del proxy.
"""
import json
import os

MINIMAX_MODEL = os.environ.get("AIROUTER_MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_UNSUPPORTED_FIELDS = ("context_management", "mcp_servers", "thinking")
MINIMAX_MIN_MAX_TOKENS = int(os.environ.get("AIROUTER_MINIMAX_MIN_MAX_TOKENS", "1024"))


def _system_to_text(system_val) -> str:
    """Converte il campo system (str | list di blocchi | None) in stringa."""
    if isinstance(system_val, list):
        parts = []
        for item in system_val:
            if isinstance(item, dict):
                t = item.get("type", "")
                if t == "text":
                    parts.append(item.get("text", ""))
                elif t == "thinking":
                    inner = item.get("thinking", "")
                    if isinstance(inner, dict):
                        parts.append(inner.get("thinking", ""))
                    else:
                        parts.append(str(inner))
        return "\n".join(parts)
    if isinstance(system_val, str):
        return system_val
    return ""


def _inject_system_as_message(data: dict) -> None:
    """MiniMax non supporta il campo top-level `system` — lo converte in un messaggio role=system.

    Bug: quando il body arriva da Claude Code, `system` è spesso una lista di blocchi
    (formato Anthropic). Senza questa conversione, MiniMax riceve solo i messaggi utente
    senza istruzioni di sistema né piano — quindi non capisce cosa fare e non scrive file.
    """
    system_val = data.get("system", "")
    if not system_val:
        return
    system_text = _system_to_text(system_val)
    if not system_text:
        return
    # Rimuovi il campo top-level system
    data.pop("system", None)
    # Iniettalo come primo messaggio role=system
    msgs = data.get("messages", [])
    if not isinstance(msgs, list):
        msgs = []
        data["messages"] = msgs
    # Inietta PRIMA di qualsiasi altro messaggio (priorità)
    msgs.insert(0, {"role": "system", "content": system_text})


_SERVER_TOOL_BLOCK_TYPES = (
    "server_tool_use",
    "web_search_tool_result",
    "web_fetch_tool_result",
    "code_execution_tool_result",
)


def strip_server_tools_for_minimax(data: dict) -> None:
    """Bug 2026-07-04: MiniMax non conosce i server tool Anthropic (web_search_20250305...).
    Rifiuta sia le definizioni in `tools` (niente input_schema) sia i blocchi
    server_tool_use/web_search_tool_result rimasti nella history → 400 (2013).
    Strip delle definizioni + conversione dei blocchi in testo. Muta `data`."""
    tools = data.get("tools")
    if isinstance(tools, list):
        kept = [t for t in tools if not (isinstance(t, dict) and "input_schema" not in t)]
        if len(kept) != len(tools):
            if kept:
                data["tools"] = kept
            else:
                data.pop("tools", None)
                data.pop("tool_choice", None)
    for m in data.get("messages", []):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for i, blk in enumerate(c):
            if isinstance(blk, dict) and blk.get("type") in _SERVER_TOOL_BLOCK_TYPES:
                payload = {k: v for k, v in blk.items() if k != "type"}
                c[i] = {"type": "text",
                        "text": f"[{blk['type']}] "
                                + json.dumps(payload, ensure_ascii=False, default=str)[:4000]}


def remap_body_for_minimax(raw: bytes, request=None, *,
                           orig_model_store=None, resolve_fp=None,
                           log_model_fn=None, log_fn=None) -> bytes:
    """Riscrive il model Claude -> MiniMax-M3 e rimuove i campi beta non supportati.

    Dependency injection (proxy passes its globals):
    - orig_model_store: dict shared with relay SSE (chat_fp -> orig_model)
    - resolve_fp: callable(request) -> chat fingerprint
    - log_model_fn: callable(orig_model, new_model, chat_id)
    - log_fn: callable(msg) for logging
    """
    _log = log_fn or print
    try:
        data = json.loads(raw)
        orig = data.get("model", "")
        if orig and not orig.startswith("MiniMax"):
            if resolve_fp and request:
                chat_id = resolve_fp(request)
                if log_model_fn:
                    log_model_fn(orig, MINIMAX_MODEL, chat_id)
            else:
                chat_id = "?"
            # Ricorda per riscrittura SSE — consumato dal relay()
            if orig_model_store is not None:
                if len(orig_model_store) > 2000:
                    _keep = orig_model_store.get("__remap__")
                    orig_model_store.clear()
                    if _keep is not None:
                        orig_model_store["__remap__"] = _keep
                orig_model_store[chat_id] = orig
            data["model"] = MINIMAX_MODEL
        for f in MINIMAX_UNSUPPORTED_FIELDS:
            data.pop(f, None)
        strip_server_tools_for_minimax(data)
        # MiniMax non supporta il campo top-level `system` — lo convertiamo in
        # un messaggio role=system nell'array messages. Senza questo, l'esecutore
        # non riceve il piano né le istruzioni di sistema → non scrive file.
        _inject_system_as_message(data)
        try:
            _mt = int(data.get("max_tokens", 0) or 0)
            if 0 < _mt < MINIMAX_MIN_MAX_TOKENS:
                data["max_tokens"] = MINIMAX_MIN_MAX_TOKENS
        except (TypeError, ValueError):
            pass
        return json.dumps(data).encode()
    except Exception:
        _log("remap_body: json parse fail, passthrough")
        return raw
