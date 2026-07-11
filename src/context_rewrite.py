"""Rewrite intelligente del body per adattarlo al context del modello target."""

import json, logging, os
from typing import Tuple
from token_counter import estimate_tokens
from model_context_map import get_safe_input_limit

log = logging.getLogger(__name__)

# Costanti
TRIM_STATE_DIR = "/tmp/ai-router-trim"
TOOL_RESULT_AGE_LIMIT = 10  # turni
HEAD_KEEP = 6  # messaggi head
TAIL_KEEP = 6  # messaggi tail

def _smart_truncate_msg(msg: dict, max_len: int = 1800) -> dict:
    """Truncation intelligente: preserva tool_use integrali."""
    result = dict(msg)
    if "content" not in result:
        return result

    if isinstance(result["content"], list):
        new_content = []
        for block in result["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                new_content.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if len(text) > max_len:
                    block = dict(block)
                    block["text"] = text[:max_len] + f"\n[... truncated {len(text)-max_len} chars ...]"
                new_content.append(block)
        result["content"] = new_content
    return result

def rewrite_for_context(body: bytes, model: str, fp: str) -> Tuple[bytes, bool]:
    """
    Ritorna (rewritten_body, was_rewritten).
    Pipeline:
    1. Tool pruning (rimuovi tool_result obsoleti)
    2. Head+tail preservation
    3. Se ancora > 80% context → ritorna (original, False) per escalation
    """
    try:
        os.makedirs(TRIM_STATE_DIR, exist_ok=True)
    except Exception:
        pass

    try:
        data = json.loads(body)
    except Exception:
        return body, False

    msgs = data.get("messages", [])
    if not msgs:
        return body, False

    # Stima token current
    token_est = estimate_tokens(body.decode('utf-8', errors='replace'))
    safe_limit = get_safe_input_limit(model)

    # Se siamo già sotto il limite, niente rewrite
    if token_est <= safe_limit:
        return body, False

    # === STEP 1: Tool pruning ===
    cleaned = []
    tool_result_turns = 0
    for m in msgs:
        if m.get("role") == "tool":
            tool_result_turns += 1
            if tool_result_turns > TOOL_RESULT_AGE_LIMIT:
                continue
        cleaned.append(m)

    # Ricontrolla dopo tool pruning
    msgs = cleaned
    trimmed = dict(data)
    trimmed["messages"] = msgs
    token_est = estimate_tokens(json.dumps(trimmed))

    if token_est <= safe_limit:
        return json.dumps(trimmed).encode(), True

    # === STEP 2: Head+tail ===
    n = len(msgs)
    if n <= HEAD_KEEP + TAIL_KEEP:
        return body, False  # Non si può fare head+tail

    tail_cnt = min(TAIL_KEEP, n // 2)
    trimmed["messages"] = msgs[:-tail_cnt] + msgs[-tail_cnt:]

    trimmed_bytes = json.dumps(trimmed).encode()
    token_est = estimate_tokens(trimmed_bytes.decode('utf-8', errors='replace'))

    log.info(f"[context_rewrite] trim: {len(body)}b→{len(trimmed_bytes)}b ({n}→{len(trimmed['messages'])} msg) model={model} fp={fp}")

    # Salva stato per la prossima iterazione
    try:
        trim_file = f"{TRIM_STATE_DIR}/{fp}.json"
        with open(trim_file, 'wb') as f:
            f.write(trimmed_bytes)
    except Exception:
        pass

    was_rewritten = len(trimmed_bytes) < len(body)
    return trimmed_bytes, was_rewritten
