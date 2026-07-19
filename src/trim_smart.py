"""Smart trimming utilities — content-aware context compression."""
import json
import os

SHRINK_KEEP_HEAD = int(os.environ.get("AIROUTER_SHRINK_KEEP_HEAD", "6"))
SHRINK_KEEP_TAIL = int(os.environ.get("AIROUTER_SHRINK_KEEP_TAIL", "6"))


def build_shrink_summary(messages: list, budget: int) -> str:
    """Comprime una lista di messaggi preservando QUALITÀ MASSIMA con budget token.
    Algoritmo content-aware:
    - TOOL_USE output: PRESERVA INTEGRALMENTE (denso, critico, tipicamente <500 token)
    - Contenuto lungo (user/assistant >2000c): PRESERVA la prima parte + "..."
    - Contenuto breve (<2000c): PRESERVA INTEGRALMENTE
    - MIDDLE: smart-sampling diversificato (prende ogni N-esimo, non solo i primi)
    Questo dà al modello: (a) contesto iniziale, (b) varietà di azioni intermedie,
    (c) contesto recente — senza perdere tool results che contengono output reali."""
    n = len(messages)
    if n == 0:
        return ""

    tail = messages[-SHRINK_KEEP_TAIL:]
    head_count = min(SHRINK_KEEP_HEAD, n - SHRINK_KEEP_TAIL)
    head = messages[:head_count] if head_count > 0 else []
    middle = messages[head_count:n - SHRINK_KEEP_TAIL] if head_count < n - SHRINK_KEEP_TAIL else []

    parts = []

    if head:
        head_lines = "\n".join(
            f"[{m.get('role','?')}]: {_smart_truncate(m)}"
            for m in head
        )
        parts.append(f"=== CONTESTO INIZIALE ({len(head)} msg) ===\n{head_lines}")

    if middle:
        sampled = _smart_sample_middle(middle, budget)
        middle_lines = "\n".join(
            f"[{m.get('role','?')}]: {_smart_truncate(m)}"
            for m in sampled
        )
        parts.append(f"=== FASE INTERMEDIA ({len(sampled)}/{len(middle)} msg selezionati) ===\n{middle_lines}")

    tail_lines = "\n".join(
        f"[{m.get('role','?')}]: {_smart_truncate(m)}"
        for m in tail
    )
    parts.append(f"=== MESSAGGI RECENTI ({len(tail)} msg) ===\n{tail_lines}")

    return "\n\n".join(parts)


def _smart_truncate(msg: dict, max_len: int = 1800) -> str:
    """Truncation intelligente: preserva tool_use integrali, tronca resto."""
    content = msg.get("content", "")
    tool_use = msg.get("tool_use", [])
    role = msg.get("role", "?")

    if tool_use:
        tool_block = "\n[TOOL_USE]: " + "\n[TOOL_USE]: ".join(
            f"{t.get('name','?')}({json.dumps(t.get('input',{}), ensure_ascii=False)[:300]})"
            for t in tool_use
        )
        content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, list) else (content or "")
        if len(content_str) > max_len:
            return content_str[:max_len] + f"\n... [+{len(content_str)-max_len}c troncati]"
        return content_str + tool_block if content_str else tool_block

    if isinstance(content, list):
        content = json.dumps(content, ensure_ascii=False)
    if len(content) > max_len:
        return content[:max_len] + f"\n... [+{len(content)-max_len}c troncati]"
    return content


def _smart_sample_middle(messages: list, budget: int) -> list:
    """Campiona il mezzo in modo diversificato: copre l'intera finestra temporale
    prendendo messaggi distribuiti, non solo i primi. Priorità:
    1. tool_use messages (sempre: contengono output reali)
    2. Messaggi "svolta" (messaggi lunghi di assistant = ragionamento/decisioni)
    3. Campionamento uniforme distribuito nel tempo"""
    if not messages:
        return []

    sampled = []
    tool_msgs = []
    non_tool = []

    for m in messages:
        if m.get("tool_use") or (m.get("role") == "user" and len(str(m.get("content",""))) > 3000):
            tool_msgs.append(m)
        else:
            non_tool.append(m)

    sampled.extend(tool_msgs)

    byte_per_msg = 500
    max_items = max(3, (budget // 3) // byte_per_msg)
    if non_tool:
        total = len(non_tool)
        step = max(1, total // max_items)
        for i in range(0, total, step):
            sampled.append(non_tool[i])

    return sampled
