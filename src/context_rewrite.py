import json
import logging
from typing import Tuple

from trim_smart import build_shrink_summary, SHRINK_KEEP_TAIL
from router_utils import _repair_message_sequence
from token_counter import estimate_tokens_body
from model_context_map import get_safe_input_limit

log = logging.getLogger(__name__)

# ATTEMPT 1b (G5): degradazione progressiva prima di scendere agli ultimi 2 messaggi.
KEEP_RECENT_IMAGES = 2       # messaggi finali lasciati intatti (immagini comprese)
TOOL_RESULT_MAX_CHARS = 4000  # oltre questa soglia il tool_result viene troncato


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

    token_est = estimate_tokens_body(body)
    safe_limit = get_safe_input_limit(model)

    if token_est <= safe_limit:
        return (body, False)

    # ATTEMPT 1: tail + summary nel system per preservare contesto recente
    tail_msgs = _repair_message_sequence(msgs[-SHRINK_KEEP_TAIL:])
    budget = safe_limit * 3 // 4
    summary = build_shrink_summary(msgs, budget)

    # Helper per costruire il system preservando liste e cache_control
    def _build_system(system_raw, summary):
        # system_raw può essere lista (formato Claude Code), stringa, o assente/None/altro
        if isinstance(system_raw, list):
            # Preserva i blocchi originali invariati (con cache_control etc.)
            result = list(system_raw)  # shallow copy
            if summary:
                result.append({"type": "text", "text": summary})
            return result
        elif isinstance(system_raw, str):
            # Comportamento invariato per stringhe
            if system_raw:
                return (system_raw + "\n\n" + summary) if summary else system_raw
            return summary if summary else None
        else:
            # Assente, None o altro tipo
            return summary if summary else None

    system_content = _build_system(data.get("system"), summary)

    new = dict(data)
    new["messages"] = tail_msgs
    if system_content:
        new["system"] = system_content
    new.pop("thinking", None)

    new_bytes = json.dumps(new).encode()
    if estimate_tokens_body(new_bytes) <= safe_limit:
        return (new_bytes, True)

    # ATTEMPT 1b: degradazione progressiva - rimuove immagini vecchie e tronca tool_result
    new_1b = _degrade_images_and_tools(data, msgs, KEEP_RECENT_IMAGES, TOOL_RESULT_MAX_CHARS)
    new_1b_bytes = json.dumps(new_1b).encode()
    if estimate_tokens_body(new_1b_bytes) <= safe_limit:
        return (new_1b_bytes, True)

    # ATTEMPT 2: piu' aggressivo, solo ultimi 2 messaggi senza summary
    tail2 = _repair_message_sequence(msgs[-2:] if len(msgs) >= 2 else msgs)

    new2 = dict(data)
    new2["messages"] = tail2
    system_original = _build_system(data.get("system"), "")
    if system_original:
        new2["system"] = system_original
    new2.pop("thinking", None)

    new2_bytes = json.dumps(new2).encode()
    if estimate_tokens_body(new2_bytes) <= safe_limit:
        return (new2_bytes, True)

    # ATTEMPT 3: troncamento hard - un singolo messaggio piu' grande del limite non e' riducibile dai tentativi precedenti (tail, degradazione e ultimi-2 lo contengono comunque)
    def _hard_truncate_messages(candidate, target_tokens):
        """
        Helper inline per troncamento hard del testo.
        Ritorna una copia del candidato con testo troncato.
        Fail-safe: qualsiasi eccezione ritorna il candidato invariato.
        """
        TRUNC_SUFFIX = "\n…[troncato per limiti di contesto]"
        MIN_TEXT_CHARS = 500  # soglia sotto la quale un blocco non viene toccato
        try:
            result = json.loads(json.dumps(candidate))
            if estimate_tokens_body(json.dumps(result).encode()) <= target_tokens:
                return result

            def _get_text_size(msg):
                """Calcola la dimensione in caratteri del testo di un messaggio."""
                size = 0
                content = msg.get("content", "")
                if isinstance(content, str):
                    size = len(content)
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            size += len(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            tr_content = block.get("content", "")
                            if isinstance(tr_content, str):
                                size += len(tr_content)
                            elif isinstance(tr_content, list):
                                for sub in tr_content:
                                    if isinstance(sub, dict) and sub.get("type") == "text":
                                        size += len(sub.get("text", ""))
                return size

            def _truncate_text(text, min_chars):
                """Tronca il testo aggiungendo il suffisso di troncamento."""
                if len(text) <= min_chars:
                    return text
                return text[:min_chars] + TRUNC_SUFFIX

            # Ordina i messaggi per dimensione testuale (dal piu' grande)
            # L'ultimo messaggio viene troncato per ultimo: e' la richiesta corrente,
            # mutilarla per prima rende la risposta inutile
            messages_with_size = []
            total_msgs = len(result.get("messages", []))
            for i, msg in enumerate(result.get("messages", [])):
                size = _get_text_size(msg)
                messages_with_size.append((size, i, msg))

            messages_with_size.sort(key=lambda x: (1 if x[1] == total_msgs - 1 else 0, -x[0]))

            # Tronca iterativamente partendo dal messaggio piu' grande (non-ultimo)
            for size, idx, msg in messages_with_size:
                if size <= MIN_TEXT_CHARS:
                    continue

                content = msg.get("content", "")
                if isinstance(content, str):
                    msg["content"] = _truncate_text(content, MIN_TEXT_CHARS)
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            continue
                        if block.get("type") == "text":
                            block["text"] = _truncate_text(block.get("text", ""), MIN_TEXT_CHARS)
                        elif block.get("type") == "tool_result":
                            tr_content = block.get("content", "")
                            if isinstance(tr_content, str):
                                block["content"] = _truncate_text(tr_content, MIN_TEXT_CHARS)
                            elif isinstance(tr_content, list):
                                for sub in tr_content:
                                    if isinstance(sub, dict) and sub.get("type") == "text":
                                        sub["text"] = _truncate_text(sub.get("text", ""), MIN_TEXT_CHARS)

                # Verifica se siamo rientrati nel limite
                if estimate_tokens_body(json.dumps(result).encode()) <= target_tokens:
                    return result

            return result
        except Exception:
            return candidate

    new3 = _hard_truncate_messages(new2, safe_limit)
    new3_bytes = json.dumps(new3).encode()
    if estimate_tokens_body(new3_bytes) <= safe_limit:
        return (new3_bytes, True)

    # Fallback: ritorna il piu' piccolo fra tutti i candidati
    candidates = [
        (new_bytes, len(new_bytes)),
        (new_1b_bytes, len(new_1b_bytes)),
        (new2_bytes, len(new2_bytes)),
        (new3_bytes, len(new3_bytes)),
    ]
    best_bytes, best_len = min(candidates, key=lambda x: x[1])
    if best_len < len(body):
        return (best_bytes, True)

    return (body, False)


def _degrade_images_and_tools(
    data: dict, msgs: list, keep_recent: int, tool_max_chars: int
) -> dict:
    """Rimuove immagini e tronca contenuti tool dai messaggi non recenti.

    Considera recenti i messaggi con indice >= len(msgs) - keep_recent e li lascia intatti.
    Per i messaggi non recenti, degrada i blocchi content di tipo image e tool_result.
    Formato: Anthropic Messages API (nessun riferimento a OpenAI).
    """
    def _degrade_block(block: dict, tool_max_chars: int) -> dict:
        """Elabora un singolo blocco content per il degrado."""
        if not isinstance(block, dict):
            return block

        btype = block.get("type", "")

        # Immagine base64: sostituisci con placeholder testuale
        if btype == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                return {"type": "text", "text": "[immagine rimossa per limiti di contesto]"}
            return block

        # Risultato tool: tronca stringa o elabora lista di sottoblocchi
        if btype == "tool_result":
            content = block.get("content")
            if isinstance(content, str):
                if len(content) > tool_max_chars:
                    new_block = dict(block)
                    new_block["content"] = content[:tool_max_chars] + "\n…[troncato]"
                    return new_block
                return block

            if isinstance(content, list):
                new_content = []
                for sub in content:
                    if not isinstance(sub, dict):
                        new_content.append(sub)
                        continue

                    stype = sub.get("type", "")
                    if stype == "text":
                        text = sub.get("text", "")
                        if len(text) > tool_max_chars:
                            new_content.append({
                                "type": "text",
                                "text": text[:tool_max_chars] + "\n…[troncato]"
                            })
                        else:
                            new_content.append(sub)
                    elif stype == "image":
                        src = sub.get("source", {})
                        if src.get("type") == "base64":
                            new_content.append({"type": "text", "text": "[immagine rimossa per limiti di contesto]"})
                        else:
                            new_content.append(sub)
                    else:
                        new_content.append(sub)

                new_block = dict(block)
                new_block["content"] = new_content
                return new_block

            return block

        # Altri tipi di blocco: restituisci invariato
        return block

    new_msgs = []
    for idx, msg in enumerate(msgs):
        is_recent = idx >= len(msgs) - keep_recent
        if is_recent:
            new_msgs.append(msg)
            continue

        new_msg = dict(msg)
        content = msg.get("content")

        if isinstance(content, list):
            new_content = [_degrade_block(b, tool_max_chars) for b in content]
            new_msg["content"] = new_content

        new_msgs.append(new_msg)

    result = dict(data)
    result["messages"] = new_msgs
    result.pop("thinking", None)
    return result


# _save_trim_state RIMOSSA (fix 2026-07-21): scriveva il body riscritto (tail-6 +
# summary) in TRIM_STATE_DIR; il TRIM INTERCEPT (rimosso) lo caricava al turno DOPO
# al posto della richiesta vera → il modello riceveva 6 messaggi stantii senza
# l'ultimo messaggio utente. Il rewrite è già applicato in-request: persistere lo
# stato cross-turno era il bug, non una feature.
