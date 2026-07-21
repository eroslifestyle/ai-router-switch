import json
import logging
from typing import Tuple

from trim_smart import build_shrink_summary, SHRINK_KEEP_TAIL
from router_utils import _repair_message_sequence
from token_counter import estimate_tokens
from model_context_map import get_safe_input_limit

log = logging.getLogger(__name__)


def rewrite_for_context(body: bytes, model: str, fp: str) -> Tuple[bytes, bool]:
    # Fail-safe: un errore nel rewrite non deve MAI bloccare il proxy.
    try:
        return _rewrite_impl(body, model, fp)
    except Exception as e:
        log.warning("rewrite_for_context fail-safe: %s", e)
        return (body, False)


def _rewrite_impl(body: bytes, model: str, fp: str) -> Tuple[bytes, bool]:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return (body, False)

    msgs = data.get("messages", [])
    if not msgs:
        return (body, False)

    token_est = estimate_tokens(body.decode('utf-8', errors='replace'))
    safe_limit = get_safe_input_limit(model)

    if token_est <= safe_limit:
        return (body, False)

    # ATTEMPT 1: tail + summary nel system per preservare contesto recente
    tail_msgs = _repair_message_sequence(msgs[-SHRINK_KEEP_TAIL:])
    budget = safe_limit * 3 // 4
    summary = build_shrink_summary(msgs, budget)

    # Normalizza system (list o str) in stringa
    system_raw = data.get("system", "")
    if isinstance(system_raw, list):
        system_str = "\n\n".join(
            json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item)
            for item in system_raw
        )
    elif isinstance(system_raw, str):
        system_str = system_raw
    else:
        system_str = ""

    system_content = (system_str + "\n\n" + summary) if system_str else summary

    new = dict(data)
    new["messages"] = tail_msgs
    if system_content:
        new["system"] = system_content
    new.pop("thinking", None)

    new_bytes = json.dumps(new).encode()
    if estimate_tokens(new_bytes.decode('utf-8', 'replace')) <= safe_limit:
        return (new_bytes, True)

    # ATTEMPT 2: piu' aggressivo, solo ultimi 2 messaggi senza summary
    tail2 = _repair_message_sequence(msgs[-2:] if len(msgs) >= 2 else msgs)

    new2 = dict(data)
    new2["messages"] = tail2
    if system_str:
        new2["system"] = system_str
    new2.pop("thinking", None)

    new2_bytes = json.dumps(new2).encode()
    if estimate_tokens(new2_bytes.decode('utf-8', 'replace')) <= safe_limit:
        return (new2_bytes, True)

    # Fallback: ritorna il piu' piccolo per dare una chance alla rete di sicurezza a valle
    if len(new2_bytes) < len(body):
        return (new2_bytes, True)

    return (body, False)


# _save_trim_state RIMOSSA (fix 2026-07-21): scriveva il body riscritto (tail-6 +
# summary) in TRIM_STATE_DIR; il TRIM INTERCEPT (rimosso) lo caricava al turno DOPO
# al posto della richiesta vera → il modello riceveva 6 messaggi stantii senza
# l'ultimo messaggio utente. Il rewrite è già applicato in-request: persistere lo
# stato cross-turno era il bug, non una feature.
