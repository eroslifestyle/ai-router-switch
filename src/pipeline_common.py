"""pipeline_common.py — Costruzione UNIFICATA del body esecutore per TUTTE le modalità mix.

Motivazione (2026-07-20): ogni pipeline (anthropic/minimax/glm) reimplementava a modo
suo il passaggio THINK→ACT, con bug divergenti:
- mix-am (primitives.build_act_body): distruggeva il system originale.
- minimax (_build_minimax_act_body): distruggeva il system originale.
- mix-ag / mix-gm: il piano THINK non arrivava MAI all'esecutore (usato solo nel VERIFY).

Conseguenza: su skill multi-step (es. /wiki all a 6 passaggi) l'esecutore perdeva le
istruzioni della skill → 2-3 tool call e poi concludeva a metà (premature termination,
6.2% dei failure noti — SHIELDA/MAST arxiv 2508.07935).

Questo modulo fornisce UN SOLO punto di costruzione del body esecutore, usato da tutte
le pipeline, che GARANTISCE:
1. system originale PRESERVATO (istruzioni skill, CLAUDE.md, regole) — mai sovrascritto.
2. piano THINK sempre APPESO (non sostituisce nulla).
3. COMPLETION GUARD globale: criteri di completamento legati al goal ORIGINALE, non al
   sub-task — mitigazione validata contro premature completion.
"""

import json
import os
import threading


# Marker riconosciuti che indicano una skill multi-step in corso nel system/messages.
# Se presenti, il completion guard diventa più stringente.
_MULTISTEP_MARKERS = (
    "passaggi obbligatori", "passaggio 1", "6 passaggi", "step obbligatori",
    "wiki all", "checkpoint", "in sequenza", "obbligatori in sequenza",
    "tutti i passaggi", "multi-step", "todo board",
)

# Guida di orchestrazione + completion guard. Appesa (mai sostitutiva) al system.
_EXECUTOR_GUIDE_TEMPLATE = (
    "\n\n--- ORCHESTRAZIONE (aggiunta dal router, NON dall'utente) ---\n"
    "Un orchestratore ha prodotto questo PIANO-GUIDA per la richiesta corrente. "
    "Usalo come guida, ma la fonte di verità resta la richiesta originale dell'utente "
    "e le istruzioni nel system qui sopra (incluse eventuali skill multi-step).\n"
    "\n"
    "REGOLE DI COMPLETAMENTO (vincolanti):\n"
    "1. Completa TUTTI i passaggi/obiettivi della richiesta ORIGINALE, non solo i primi.\n"
    "2. NON concludere, NON salutare, NON dire 'sono pronto/cosa ti serve' finché "
    "l'INTERO task non è completato e verificato.\n"
    "3. Se la richiesta è una skill con passaggi numerati, esegui OGNI passaggio in "
    "ordine; dopo ciascuno prosegui col successivo senza fermarti.\n"
    "4. Verifica il completamento contro il GOAL globale, non contro il singolo tool "
    "call appena eseguito.\n"
    "5. Se un tool fallisce, gestisci l'errore e prosegui — non abbandonare il task.\n"
    "6. Se nei messaggi compare il marker [IMMAGINE ALLEGATA DALL'UTENTE ...]: "
    "l'utente HA allegato immagini. Tu non le ricevi: la loro descrizione è nel "
    "PIANO-GUIDA qui sotto. NON dire mai 'nessuna immagine allegata', NON chiedere "
    "di riallegarle — lavora sulla descrizione.\n"
    "{multistep_extra}"
    "\nPIANO-GUIDA:\n{plan}"
)

_MULTISTEP_EXTRA = (
    "7. ATTENZIONE: questa è una SKILL MULTI-STEP. È un errore grave fermarsi prima "
    "di aver eseguito tutti i passaggi dichiarati. Tieni traccia di quali passaggi hai "
    "completato e quali mancano.\n"
)


def _system_to_str(system_val) -> str:
    """Normalizza il campo system (str | list di blocchi | None) in stringa."""
    if isinstance(system_val, list):
        parts = []
        for item in system_val:
            if isinstance(item, dict):
                parts.append(item.get("text", "") or json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n\n".join(parts)
    if isinstance(system_val, str):
        return system_val
    return ""


def _detect_multistep(orig: dict) -> bool:
    """True se il body sembra contenere una skill multi-step (system o ultimo user msg)."""
    haystack = _system_to_str(orig.get("system", "")).lower()
    msgs = orig.get("messages") or []
    if msgs:
        last = msgs[-1]
        c = last.get("content", "")
        haystack += " " + (c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)).lower()
    return any(marker in haystack for marker in _MULTISTEP_MARKERS)


def _strip_images_from_messages(messages: list) -> list:
    """Rimuove blocchi image dai content blocks. Testo associato preservato.

    L'esecutore (MiniMax) non riceve immagini per evitare limiti di context e bug
    noti. Il piano THINK contiene già la descrizione/sintesi delle immagini, e
    l'eventuale testo utente associato ai blocchi image rimane nei content.
    Se un messaggio era SOLO immagini, viene inserito un placeholder testuale.
    """
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        # Ogni image diventa un marker testuale ESPLICITO: la rimozione silenziosa
        # faceva negare all'esecutore l'esistenza dell'allegato ("nessuna immagine
        # allegata") anche quando l'utente l'aveva inviata.
        new_blocks = [
            {"type": "text", "text": "[IMMAGINE ALLEGATA DALL'UTENTE — descrizione "
                                     "nel PIANO-GUIDA del system]"}
            if (isinstance(b, dict) and b.get("type") == "image") else b
            for b in content
        ]
        out.append({**m, "content": new_blocks})
    return out


def build_executor_body(orig: dict, plan: str, executor: str = "",
                        *, extra_note: str = "") -> dict:
    """Costruisce il body per l'ESECUTORE preservando system+messages+tools.

    Args:
        orig: body originale del client (con system, messages, tools).
        plan: piano-guida prodotto dal THINK (stringa; può essere "" se assente).
        executor: model id da forzare per l'esecutore (es. 'MiniMax-M2.7'). "" = invariato.
        extra_note: nota aggiuntiva (es. correzione dal verifier) appesa dopo il piano.

    Returns:
        dict pronto per json.dumps → forward_*.

    Garanzie:
    - orig['system'] MAI perso: guida+completion-guard APPESE.
    - orig['messages'] STRIPPATO delle immagini (esecutore non le riceve).
    - orig['tools'] preservati integralmente.
    """
    body = dict(orig)  # conserva messages + tools

    is_multistep = _detect_multistep(orig)
    plan_text = (plan or "").strip() or "(nessun piano esplicito: segui la richiesta originale)"
    guide = _EXECUTOR_GUIDE_TEMPLATE.format(
        multistep_extra=(_MULTISTEP_EXTRA if is_multistep else ""),
        plan=plan_text,
    )
    if extra_note:
        guide += f"\n\n[NOTA]\n{extra_note}\n[/NOTA]"

    orig_system = orig.get("system", "")
    if isinstance(orig_system, list):
        # Formato blocchi Anthropic: appendi un blocco text (non rompere il caching prefix)
        body["system"] = list(orig_system) + [{"type": "text", "text": guide}]
    elif isinstance(orig_system, str) and orig_system:
        body["system"] = orig_system + guide
    else:
        body["system"] = "Sei l'esecutore. Esegui la richiesta dell'utente." + guide

    # Strip immagini: l'esecutore non le riceve, il THINK le ha già analizzate.
    body["messages"] = _strip_images_from_messages(orig.get("messages") or [])

    if executor:
        body["model"] = executor
    body["stream"] = bool(orig.get("stream"))
    return body


def build_executor_body_bytes(orig: dict, plan: str, executor: str = "",
                              *, extra_note: str = "") -> bytes:
    """Come build_executor_body ma serializzato in bytes."""
    return json.dumps(build_executor_body(orig, plan, executor, extra_note=extra_note)).encode()


# ── THINK sintetico + VERIFY gate (fix 2026-07-21, solo modalità mixed) ────────
# Il THINK col body intero (300+ msg / 800KB) andava in timeout a ogni turno;
# il VERIFY su ogni turno (con retry ACT) raddoppiava costi e latenza.
# Qui: digest bounded per il THINK e gate a campione per il VERIFY,
# condivisi da tutte le pipeline mixed (mai duplicare per-pipeline).

THINK_TAIL_MSGS = int(os.environ.get("AIROUTER_THINK_TAIL_MSGS", "6"))
THINK_SUMMARY_BUDGET = int(os.environ.get("AIROUTER_THINK_SUMMARY_BUDGET", "12000"))
THINK_TAIL_MSG_MAX_CHARS = 2000
THINK_MAX_IMAGES = 4
VERIFY_SAMPLE_EVERY = int(os.environ.get("AIROUTER_VERIFY_SAMPLE_EVERY", "5"))
VERIFY_SHORT_OUTPUT_CHARS = 50

_verify_lock = threading.Lock()
_verify_turn_count: dict = {}


def _digest_block_text(blk) -> str:
    """Testo compatto di un content block per la trascrizione THINK."""
    if not isinstance(blk, dict):
        return ""
    t = blk.get("type", "")
    if t == "text":
        return blk.get("text", "")
    if t == "tool_use":
        return f"[tool_use: {blk.get('name', '?')}]"
    if t == "tool_result":
        inner = blk.get("content", "")
        if isinstance(inner, list):
            inner = " ".join(b.get("text", "") for b in inner if isinstance(b, dict))
        return f"[tool_result] {str(inner)[:500]}"
    if t == "image":
        return "[immagine allegata]"
    return ""


def _digest_msg_text(msg: dict) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(filter(None, (_digest_block_text(b) for b in content)))
    return str(content)


def build_think_digest(orig: dict) -> tuple:
    """Digest bounded per il THINK: MAI il body intero.

    Returns:
        (testo, image_blocks): summary dei messaggi vecchi (build_shrink_summary)
        + trascrizione troncata degli ultimi THINK_TAIL_MSGS + blocchi image
        dell'ULTIMO messaggio (il THINK legge le immagini, l'ACT no).
    """
    msgs = orig.get("messages") or []
    tail = msgs[-THINK_TAIL_MSGS:]
    older = msgs[:-THINK_TAIL_MSGS] if len(msgs) > THINK_TAIL_MSGS else []
    parts = []
    if older:
        try:
            from trim_smart import build_shrink_summary
            summary = build_shrink_summary(older, THINK_SUMMARY_BUDGET)
            # Hard-cap: build_shrink_summary non rispetta il budget se molti msg
            # sono tool/lunghi (inclusi integralmente). Tieni inizio+fine.
            if len(summary) > THINK_SUMMARY_BUDGET:
                half = THINK_SUMMARY_BUDGET // 2
                summary = (summary[:half] + "\n... [cronologia intermedia omessa] ...\n"
                           + summary[-half:])
            parts.append(summary)
        except Exception:
            parts.append(f"(cronologia precedente: {len(older)} messaggi omessi)")
    lines = [
        f"[{m.get('role', '?')}] {_digest_msg_text(m)[:THINK_TAIL_MSG_MAX_CHARS]}"
        for m in tail if isinstance(m, dict)
    ]
    parts.append("=== ULTIMI MESSAGGI ===\n" + "\n".join(lines))
    images = []
    if tail and isinstance(tail[-1], dict):
        content = tail[-1].get("content")
        if isinstance(content, list):
            images = [b for b in content
                      if isinstance(b, dict) and b.get("type") == "image"][:THINK_MAX_IMAGES]
    return "\n\n".join(parts), images


def should_verify(chat_fp: str, act_raw: bytes) -> tuple:
    """Gate VERIFY a campione: True solo se output ACT sospetto o 1 turno su N.

    MAI usato per retry automatico dell'ACT (solo warning al client).
    Sospetto = risposta non parsabile / type=error / testo < 50c senza tool_use.
    """
    try:
        j = json.loads(act_raw)
    except Exception:
        return True, "unparseable"
    if j.get("type") == "error":
        return True, "error-response"
    blocks = [b for b in (j.get("content") or []) if isinstance(b, dict)]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    has_tool_use = any(b.get("type") == "tool_use" for b in blocks)
    if not has_tool_use and len(text.strip()) < VERIFY_SHORT_OUTPUT_CHARS:
        return True, "short-output"
    with _verify_lock:
        n = _verify_turn_count.get(chat_fp, 0) + 1
        _verify_turn_count[chat_fp] = n
    if VERIFY_SAMPLE_EVERY > 0 and n % VERIFY_SAMPLE_EVERY == 0:
        return True, "sample"
    return False, ""
