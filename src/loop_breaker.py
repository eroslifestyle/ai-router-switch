"""Ferma le sessioni che riemettono all'infinito lo stesso turno.

Perché (misurato 2026-08-19): la sessione 74070e0d in mode `gpt` ha ripetuto per
oltre undici turni identici lo stesso preambolo e le stesse tre Read, ~3 minuti a
giro, con il contesto al 167% della finestra di code-max. Il router non se ne
accorgeva: vedeva richieste valide, le riscriveva e le inoltrava una per una. La
causa prima (i rimandi del dedup che puntavano a messaggi tagliati) è risolta in
`context_rewrite`; questo modulo è la rete sotto, per il caso generale in cui un
modello si impianta comunque.

Il segnale è l'ULTIMO messaggio assistant del corpo: se la sua firma non cambia per
`LOOP_BREAKER_N` turni consecutivi della stessa chat, il giro è chiuso e nessun
altro inoltro lo aprirà. Meglio un errore leggibile che ore di prefill sprecato.

Disattivabile con AIROUTER_LOOP_BREAKER=0; soglia in AIROUTER_LOOP_BREAKER_N.
"""

import hashlib
import json
import os

ENABLED = os.environ.get("AIROUTER_LOOP_BREAKER", "1") not in ("0", "false", "no")
LOOP_BREAKER_N = int(os.environ.get("AIROUTER_LOOP_BREAKER_N", "4"))

# fp -> [firma, ripetizioni consecutive]
_seen: dict = {}
# Oltre questa soglia lo stato viene azzerato: e' una cache di sessioni vive, non un log.
MAX_TRACKED_CHATS = 512


def _signature(body: bytes) -> str:
    """Firma dell'ultimo turno assistant: testo + tool chiamati con i loro argomenti."""
    try:
        msgs = json.loads(body).get("messages", [])
    except Exception:
        return ""

    last = next((m for m in reversed(msgs) if m.get("role") == "assistant"), None)
    if not last:
        return ""

    content = last.get("content")
    if isinstance(content, str):
        payload = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                parts.append(block.get("name", "") + json.dumps(block.get("input"), sort_keys=True))
        payload = " ".join(parts)
    else:
        return ""

    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest() if payload.strip() else ""


def check(fp: str, body: bytes) -> int:
    """Ripetizioni consecutive dell'ultimo turno per questa chat (0 = nessun segnale)."""
    if not ENABLED or not fp:
        return 0

    sig = _signature(body)
    if not sig:
        return 0

    if len(_seen) > MAX_TRACKED_CHATS:
        _seen.clear()

    prev = _seen.get(fp)
    repeats = prev[1] + 1 if prev and prev[0] == sig else 1
    _seen[fp] = [sig, repeats]
    return repeats


def reset(fp: str) -> None:
    """Da chiamare quando la chat riparte (es. dopo /compact), per non lasciarla bloccata."""
    _seen.pop(fp, None)


def message(mode: str, repeats: int) -> str:
    return (
        f"Loop rilevato: {repeats} turni consecutivi identici in modalita' '{mode}'. "
        "Il router ha interrotto il giro invece di inoltrarne un altro. "
        "Fai /compact (o apri una chat nuova); se il contesto supera la finestra del "
        "modello locale, passa a una modalita' con finestra piu' grande. "
        "Per disattivare questo controllo: AIROUTER_LOOP_BREAKER=0."
    )
