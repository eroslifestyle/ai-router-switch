# ~110 lines
"""Shrink testo adattivo per body che superano il context window del provider.

Logica gemella di pipeline_minimax._try_shrink_body (che opera su dict). Qui
interfaccia bytes -> bytes, riusata da forward_anthropic (fallback su 400),
glm_backend e qwen_backend (guardia preventiva). pipeline_minimax resta
intatto: la sua interfaccia (dict, con side-effect su orig) e' diversa e il
suo path e' testato separatamente (12 test in test_pipeline.py). Duplicare
l'orchestrazione del budget e' preferibile a un refactor che tocchi il path
critico minimax (YAGNI sulla deduplicazione, vedi step 7 del task).

Helper di base condivisi: build_shrink_summary + SHRINK_KEEP_TAIL da
trim_smart, _repair_message_sequence da router_utils."""
import json
import os

from router_utils import log, _repair_message_sequence
from trim_smart import build_shrink_summary, SHRINK_KEEP_TAIL

# Budget progressivo del riassunto (in caratteri). Identico a pipeline_minimax:
# scende di meta a ogni passo per convergere rapidamente. Gli ultimi due
# (16k, 8k) sono i piu frequenti su body molto grandi.
_SHRINK_BUDGETS = (560_000, 280_000, 140_000, 70_000, 35_000, 16_000, 8_000)

# Budget finale quando nemmeno 8k + tail-6 basta: tail ridotto a 2 messaggi.
_FINAL_BUDGET = 8_000
_FINAL_TAIL = 2

# Taglio sticky: K (numero di messaggi iniziali compressi nel summary) e' sempre
# un multiplo di _STICKY_STEP, e il budget del summary e' FISSO. Sono le due
# condizioni perche' il prefisso resti identico fra turni consecutivi: se uno
# dei due seguisse la lunghezza della conversazione, cambierebbe a ogni turno e
# il caching non aggancerebbe mai. Lo scalino grande costa un solo miss ogni
# _STICKY_STEP messaggi.
_STICKY_STEP = int(os.environ.get("AIROUTER_STICKY_SHRINK_STEP", "50"))
_STICKY_SUMMARY_BUDGET = int(os.environ.get("AIROUTER_STICKY_SUMMARY_BUDGET", "40000"))


async def shrink_body_to_budget(body: bytes, max_bytes: int,
                                *, shrink_images: bool = False) -> bytes | None:
    """Prova a shrinkare il body per farlo stare in max_bytes.

    Algoritmo (identico a pipeline_minimax._try_shrink_body):
    - Estrae system e messages dal body JSON
    - Per ogni budget decrescente in _SHRINK_BUDGETS:
        summary = build_shrink_summary(msgs, budget)
        tail = ultimi SHRINK_KEEP_TAIL messaggi riparati
        system = system_str + summary
        shrunk = orig con messages=tail, system=system_content, no thinking
        se len(shrunk) <= max_bytes: ritorna shrunk
    - Final fallback: tail=2, budget=8k
    - Se nulla entra, ritorna None

    Args:
        body: body JSON della richiesta (bytes)
        max_bytes: target di byte entro cui far stare il body shrunkato
        shrink_images: se True, ridimensiona immagini base64 prima dello shrink
                      (costoso: richiede PIL; default False perche i chiamanti
                      gestiscono le immagini a parte)

    Returns:
        Body shrunkato come bytes, o None se non rientra in max_bytes.
    """
    try:
        orig = json.loads(body)
    except Exception:
        return None

    msgs = orig.get("messages", []) or []
    if not msgs:
        return None

    if shrink_images:
        try:
            from pipeline_anthropic import _shrink_images_in_messages
            _shrink_images_in_messages(orig)
        except Exception:
            pass

    system_str = _extract_system_string(orig.get("system", ""))

    # PRIMA STRADA: taglio STICKY, che preserva il prompt caching a valle.
    # Lo shrink classico (sotto) riassume TUTTI i messaggi in un summary che
    # finisce nel campo `system` e tiene gli ultimi 6: entrambe le parti
    # cambiano a ogni turno, quindi l'upstream vede sempre un prefisso nuovo.
    # Misurato il 2026-08-16 sul traffico GLM reale: shrink consecutivi
    # 605.760b->435.526b, 607.911b->437.691b, 608.992b->438.770b — tre prefissi
    # diversi in tre turni, e infatti su 3.557 richieste GLM di agosto lo ZERO
    # per cento ha riusato la cache, ripagando 229,4M token (il 98% del suo
    # input). Qui invece si comprimono i primi K messaggi con K quantizzato:
    # finché il body rientra, K non cambia, quindi summary e coda restano
    # identici e l'unica parte nuova è quella in fondo. Un solo miss quando K
    # sale di uno scalino, poi di nuovo cache.
    sticky = _shrink_sticky(orig, msgs, system_str, max_bytes)
    if sticky is not None:
        return sticky

    for budget in _SHRINK_BUDGETS:
        result = _build_shrunk(orig, msgs, system_str, budget, SHRINK_KEEP_TAIL)
        if result is not None and len(result) <= max_bytes:
            log(f"context_shrink: budget {budget}b tail={min(SHRINK_KEEP_TAIL, len(msgs))} "
                f"-> {len(result)}b <= {max_bytes}b")
            return result

    # Final fallback: tail ridotto a 2 messaggi
    result = _build_shrunk(orig, msgs, system_str, _FINAL_BUDGET, _FINAL_TAIL)
    if result is not None and len(result) <= max_bytes:
        log(f"context_shrink: final budget {_FINAL_BUDGET}b tail={_FINAL_TAIL} "
            f"-> {len(result)}b")
        return result

    return None


def _shrink_sticky(orig: dict, msgs: list, system_str: str,
                   max_bytes: int) -> bytes | None:
    """Comprime i primi K messaggi, con K quantizzato a passi di _STICKY_STEP.

    Il valore di K e il budget del summary NON dipendono dalla lunghezza attuale
    della conversazione: finche' il body rientra in max_bytes, K resta quello del
    turno precedente e il prefisso inviato all'upstream e' identico byte per
    byte. E' questa proprieta' — non la compressione in se' — a far funzionare il
    prompt caching.

    Ritorna None se nessun K permette di rientrare (body dominato da pochi
    messaggi enormi): in quel caso decide il chiamante, che ha lo shrink
    classico come fallback.
    """
    n = len(msgs)
    for k in range(_STICKY_STEP, n + _STICKY_STEP, _STICKY_STEP):
        if k >= n:
            break
        try:
            summary = build_shrink_summary(msgs[:k], _STICKY_SUMMARY_BUDGET)
            coda = _repair_message_sequence(msgs[k:])
            if not coda:
                break
            shrunk = dict(orig)
            shrunk["messages"] = coda
            testo = (system_str + "\n\n" + summary) if system_str else summary
            if testo:
                shrunk["system"] = testo
            out = json.dumps(shrunk).encode()
        except Exception as e:
            log(f"context_shrink sticky EXC: {e}")
            return None
        if len(out) <= max_bytes:
            log(f"context_shrink sticky: compressi i primi {k} messaggi di {n}, "
                f"coda={len(coda)} -> {len(out)}b <= {max_bytes}b")
            return out
    return None


def _extract_system_string(system_val) -> str:
    """Estrae il testo dal campo system (stringa o lista di blocchi text)."""
    if isinstance(system_val, list):
        parts = []
        for v in system_val:
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, dict) and v.get("type") == "text":
                parts.append(v.get("text", ""))
        return "\n\n".join(parts)
    return str(system_val) if system_val else ""


def _build_shrunk(orig: dict, msgs: list, system_str: str,
                  budget: int, keep_tail: int) -> bytes | None:
    """Costruisce un body shrunkato con dato budget summary e tail length.

    Ritorna i bytes del body shrunkato, o None se la costruzione fallisce.
    """
    try:
        summary_content = build_shrink_summary(msgs, budget)
        tail_msgs = _repair_message_sequence(msgs[-keep_tail:])
        system_content = (system_str + "\n\n" + summary_content
                          if system_str else summary_content)
        shrunk = dict(orig)
        shrunk["messages"] = tail_msgs
        if system_content:
            shrunk["system"] = system_content
        shrunk.pop("thinking", None)
        return json.dumps(shrunk).encode()
    except Exception as e:
        log(f"context_shrink _build_shrunk EXC: {e}")
        return None
