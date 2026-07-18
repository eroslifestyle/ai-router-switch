"""pipelines/primitives.py — Costruzione body per pipeline mix-am."""

import json

# Costanti estratte dal proxy
THINK_MAX_TOKENS = 200  # overridable via AIROUTER_THINK_MAX_TOKENS env
THINK_MODEL = "claude-haiku-4-5-20251001"  # overridable via AIROUTER_THINK_MODEL env
VERIFY_MODEL = "claude-haiku-4-5-20251001"  # overridable via AIROUTER_VERIFY_MODEL env


def _anthropic_system(text: str) -> str:
    """Formato system message per Anthropic."""
    return text


def build_think_body(orig: dict) -> dict:
    """Version D 2026-07-03: il THINK gira sul MODELLO SELEZIONATO DALL'UTENTE
    (Fable/Opus/Sonnet) — l'orchestratore è sempre il modello Anthropic scelto.
    THINK_MODEL (Haiku) è solo fallback se il client non passa un model Anthropic."""
    sys_msg = (
        "Sei un ORCHESTRATORE. Leggi la richiesta utente e scrivi un PIANO D'AZIONE "
        "BREVE (2-3 frasi) in italiano: cosa va fatto e in che ordine. "
        "Scrivi SOLO il piano come testo semplice. NON eseguire nulla, NON chiamare "
        "strumenti, NON rispondere alla domanda — solo il piano operativo essenziale."
    )
    body = dict(orig)
    body["system"] = _anthropic_system(sys_msg)
    body["stream"] = False
    body["max_tokens"] = THINK_MAX_TOKENS
    _m = (orig.get("model") or "").strip()
    # Modello utente = orchestratore; Haiku solo se il client non ha un model Anthropic
    body["model"] = _m if _m and not _m.startswith("MiniMax") else THINK_MODEL
    # Togli tools (il modello non deve emettere tool_use) e thinking (mangia budget).
    body.pop("tools", None)
    body.pop("thinking", None)
    return body


def build_act_body(orig: dict, plan: str, tools_to_call: list = None,
                   executor: str = "") -> dict:
    """Version D 2026-07-03: l'executor MiniMax ESEGUE il piano-guida Anthropic.
    L'esecutore sceglie e chiama i tool concreti (ha il body originale con tutti
    i tools); il piano è solo una guida di orchestrazione. `executor` (es.
    MiniMax-M2.7 code) forza il modello: inizia con 'MiniMax' → remap lo preserva."""
    sys_msg = (
        "Sei l'esecutore. Un orchestratore Anthropic ha analizzato la richiesta e "
        "prodotto questo PIANO-GUIDA. Segui il piano usando i tuoi strumenti come "
        "necessario. Rispondi normalmente all'utente eseguendo le azioni del piano.\n\n"
        f"PIANO-GUIDA:\n{plan}"
    )
    body = dict(orig)  # conserva i tools originali → l'executor può chiamarli
    body["system"] = sys_msg
    body["stream"] = bool(orig.get("stream"))
    if executor:
        body["model"] = executor
    return body


def build_finalize_body(orig: dict, question: str, draft_v2: str) -> dict:
    """Round 3: Anthropic finalizza — gateway di qualità, decide se la v2 e' pubblicabile."""
    sys_msg = (
        "Sei il finalizzatore. Ricevi DOMANDA e v2 prodotta da M3 dopo la tua critica. "
        "Se la v2 risponde correttamente, restituiscila identica. "
        "Se contiene ancora errori gravi, correggili SOLO dove necessario. "
        "Rispondi in italiano, SOLO la risposta finale, senza meta-commenti."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nRISPOSTA v2 (M3):\n{draft_v2}\n\n"
        "Restituisci la risposta finale."
    )
    return {
        "model": VERIFY_MODEL,
        "max_tokens": int(orig.get("max_tokens", 1024)),
        "system": _anthropic_system(sys_msg),
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }


def to_json_bytes(body: dict) -> bytes:
    """Serializza un body dict in bytes JSON (per compatibilità col proxy)."""
    return json.dumps(body).encode()
