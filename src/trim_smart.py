"""Smart trimming utilities — content-aware context compression."""
import json
import os

SHRINK_KEEP_HEAD = int(os.environ.get("AIROUTER_SHRINK_KEEP_HEAD", "6"))
SHRINK_KEEP_TAIL = int(os.environ.get("AIROUTER_SHRINK_KEEP_TAIL", "6"))
# Tetto di troncamento per singolo messaggio nel summary. E' anche l'unita' con cui
# si stima il costo di un messaggio quando si applica il budget: vedi _smart_sample_middle.
TRUNCATE_MAX_LEN = 1800


def build_shrink_summary(messages: list, budget: int) -> str:
    """Comprime una lista di messaggi preservando QUALITÀ MASSIMA con budget token.

    Il preambolo istruisce il modello a continuare normalmente senza commentare
    il contesto mancante — blocca "il messaggio è stato troncato" e simili.

    Algoritmo content-aware:
    - TOOL_USE output: PRESERVA INTEGRALMENTE (denso, critico, tipicamente <500 token)
    - Contenuto lungo (user/assistant >2000c): PRESERVA la prima parte + "..."
    - Contenuto breve (<2000c): PRESERVA INTEGRALMENTE
    - MIDDLE: smart-sampling diversificato (prende ogni N-esimo, non solo i primi)
    Questo dà al modello: (a) contesto iniziale, (b) varietà di azioni intermedie,
    (c) contesto recente — senza perdere tool results che contengono output reali."""
    n = len(messages)
    if n == 0:
        # Fix 2026-07-20: anche con 0 messaggi un body compresso vuole testo
        # esplicito, altrimenti il modello dice "messaggio vuoto o troncato".
        return (
            "NOTA: la cronologia precedente e' stata compressa per ragioni di spazio. "
            "Prosegui normalmente."
        )

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

    # FIX BUG-COMPRESSIONE: preamble REATTIVO rimosso. Il modello non deve SAPERE
    # che il contesto è stato compresso — altrimenti risponde "capito, il contesto
    # è stato compresso" inducendo il bug che dovrebbe risolvere. Il summary è
    # già auto-esplicativo: "=== CONTESTO INIZIALE ===" / "=== FASE INTERMEDIA ==="
    # / "=== MESSAGGI RECENTI ===" — il modello capisce la struttura senza che
    # glielo si dica esplicitamente.
    PREAMBLE = ""
    out = PREAMBLE + "\n\n".join(parts)

    # Fase 2: garantisci budget
    # Il budget e' una garanzia hard perche' i chiamanti iterano riducendolo per far
    # entrare il body nel limite del provider: un budget non rispettato impedirebbe la
    # convergenza (misurato 2026-08-02).
    if len(out) <= budget:
        return out

    # Fase 3: riduci sezione intermedia
    # Si accorcia prima la sezione intermedia perche' e' la meno critica delle tre.
    marker = "… [sezione intermedia troncata]"
    for i, part in enumerate(parts):
        if part.startswith("=== FASE INTERMEDIA"):
            excess = len(out) - budget
            cut = excess + 20
            if cut >= len(part) - len(marker):
                parts.remove(part)
            else:
                parts[i] = part[:-cut].rstrip() + "\n" + marker
            break

    out = PREAMBLE + "\n\n".join(parts)
    if len(out) <= budget:
        return out

    # Fase 4: troncamento duro
    # Scatta quando il budget e' piu' piccolo del minimo strutturale di testa+coda.
    hard = "… [contesto troncato]"
    return out[:max(0, budget - len(hard) - 1)] + chr(10) + hard


def _smart_truncate(msg: dict, max_len: int = TRUNCATE_MAX_LEN) -> str:
    """Truncation intelligente: preserva tool_use integrali, tronca resto."""
    content = msg.get("content", "")
    tool_use = msg.get("tool_use", [])
    # niente `role` qui: il prefisso "[ruolo]:" lo mettono i chiamanti (righe 43/51/57)

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
    1. tool_use messages (sempre: contengono output reali, ma ora limitati dal budget)
    2. Messaggi "svolta" (messaggi lunghi di assistant = ragionamento/decisioni)
    3. Campionamento uniforme distribuito nel tempo

    Il budget e' espresso in CARATTERI ed e' ora vincolante anche per i messaggi prioritari."""
    if not messages:
        return []

    sampled = []
    tool_msgs = []
    non_tool = []

    for idx, m in enumerate(messages):
        if m.get("tool_use") or (m.get("role") == "user" and len(str(m.get("content",""))) > 3000):
            tool_msgs.append((idx, m))
        else:
            non_tool.append((idx, m))

    # Budget vincolante anche per i messaggi prioritari
    prio_budget = max(1, (budget * 2) // 3)
    max_prio = max(1, prio_budget // TRUNCATE_MAX_LEN)

    if len(tool_msgs) <= max_prio:
        sampled.extend(tool_msgs)
    else:
        step = len(tool_msgs) / max_prio
        for i in range(max_prio):
            idx = int(i * step)
            sampled.append(tool_msgs[idx])

    num_prio = len(sampled)
    rest_budget = budget - (num_prio * TRUNCATE_MAX_LEN)

    if rest_budget > 0:
        max_items = max(3, rest_budget // TRUNCATE_MAX_LEN)
        if non_tool:
            total = len(non_tool)
            step = max(1, total // max_items)
            for i in range(0, total, step):
                sampled.append(non_tool[i])

    # Preserva l'ordine cronologico originale
    sampled.sort(key=lambda x: x[0])
    return [msg for _, msg in sampled]
