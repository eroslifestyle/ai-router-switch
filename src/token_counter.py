"""Stima dei token per richieste Anthropic-compatibili."""

import json
from typing import Optional

# Byte per token misurati il 2026-07-27 con /v1/messages/count_tokens
# su payload rappresentativo di 50107 byte (codice Python + prosa italiana + JSON).
# I valori sono arrotondati per DIFETTO: sottostimare i byte per token
# significa sovrastimare i token, che è il lato sicuro.
BYTES_PER_TOKEN = {
    "anthropic_new": 2.5,   # misurato: 2.55 byte/token (Opus 4.7, Opus 4.8, Opus 5, Sonnet 5, Fable 5, Mythos 5)
    "anthropic_old": 3.5,   # misurato: 3.53 byte/token (Haiku 4-5, Sonnet 4-5, Sonnet 4-6, Opus 4-5, Opus 4-6)
    "minimax": 3.8,         # misurato: 3.85 byte/token
    "glm": 4.0,             # misurato: 4.06 byte/token
}

DEFAULT_BYTES_PER_TOKEN = 4.0  # fallback quando il modello non è noto
# Preserva il comportamento storico per i chiamanti non aggiornati
LEGACY_BYTES_PER_TOKEN = 4.0

# Prefissi dei modelli Anthropic che usano il tokenizer NUOVO (introdotto con Opus 4.7)
ANTHROPIC_NEW_TOKENIZER_PREFIXES = [
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable",
    "claude-mythos",
]


def bytes_per_token(model: Optional[str]) -> float:
    """Classifica il modello e ritorna il divisore byte/token appropriato.

    Criterio basato su misure del 2026-07-27:
    - prefissi ANTHROPIC_NEW_TOKENIZER_PREFIXES → tokenizer Anthropic nuovo (2.5 byte/token)
    - altri modelli che iniziano con "claude" → tokenizer Anthropic vecchio (3.5 byte/token)
    - modelli che iniziano con "minimax" → 3.8 byte/token
    - modelli che iniziano con "glm" → 4.0 byte/token
    - altrimenti DEFAULT_BYTES_PER_TOKEN (4.0)

    La classificazione è case-insensitive.
    """
    if model is None:
        return DEFAULT_BYTES_PER_TOKEN

    model_lower = model.lower()

    # Controlla i prefissi Anthropic nuovi
    for prefix in ANTHROPIC_NEW_TOKENIZER_PREFIXES:
        if model_lower.startswith(prefix.lower()):
            return BYTES_PER_TOKEN["anthropic_new"]

    # Altri modelli Anthropic
    if model_lower.startswith("claude"):
        return BYTES_PER_TOKEN["anthropic_old"]

    # Altri provider
    if model_lower.startswith("minimax"):
        return BYTES_PER_TOKEN["minimax"]

    if model_lower.startswith("glm"):
        return BYTES_PER_TOKEN["glm"]

    return DEFAULT_BYTES_PER_TOKEN


IMAGE_TOKEN_COST = 1600


def estimate_tokens(text: str, model: Optional[str] = None) -> int:
    """Stima token: byte/divisore in base al modello.

    Se model è None usa LEGACY_BYTES_PER_TOKEN (4.0) per compatibilità storica.
    """
    divisor = LEGACY_BYTES_PER_TOKEN if model is None else bytes_per_token(model)
    return max(int(len(text) / divisor), 1)


def estimate_tokens_body(body: bytes, model: Optional[str] = None) -> int:
    """Stima token per un body che può contenere immagini base64.
    I byte base64 delle immagini non sono testo; char/4 li sovrastima di ~200x
    (es. 1,4 MB base64 ≈ 350 k token stimati contro ~1 600 reali).
    LIMITE NOTO: i blocchi type=="document" (PDF base64) restano fuori scope
    in questa versione.

    Se model è None usa LEGACY_BYTES_PER_TOKEN (4.0) per compatibilità storica.
    """
    divisor = LEGACY_BYTES_PER_TOKEN if model is None else bytes_per_token(model)

    # Prova a parsare il body come JSON
    try:
        data = json.loads(body)
    except Exception:
        # JSON non valido: fallback sulla stima plain‑text
        return estimate_tokens(body.decode("utf-8", errors="replace"), model)

    # Ricerca ricorsiva di blocchi immagine con profondità massima 12
    images_count = 0
    b64_bytes = 0
    max_depth = 12

    def walk(obj: object, depth: int) -> None:
        nonlocal images_count, b64_bytes
        if depth > max_depth:
            return
        if isinstance(obj, dict):
            # Rileva un blocco di tipo "image"
            if obj.get("type") == "image":
                source = obj.get("source")
                if isinstance(source, dict) and source.get("type") == "base64":
                    data_str = source.get("data")
                    if isinstance(data_str, str):
                        images_count += 1
                        b64_bytes += len(data_str)
            # Continua la camminata su tutti i valori
            for value in obj.values():
                walk(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)

    try:
        walk(data, 0)
    except Exception:
        # Errore imprevisto durante il walk: fallback
        return estimate_tokens(body.decode("utf-8", errors="replace"), model)

    # Calcolo finale: si sottraggono i caratteri base64 dal totale e si aggiunge
    # il costo fisso per ogni immagine rilevata.
    return max(1, int((len(body) - b64_bytes) / divisor) + images_count * IMAGE_TOKEN_COST)

# Rimosse il 2026-08-04: count_tokens e count_tokens_real, il sotto-sistema del campionamento
# token AQ-8, mai completato. count_tokens era importata da ai-router-proxy.py ma non veniva
# chiamata da nessuno; la sua docstring prometteva "conta token reali" mentre restituiva solo
# cache più stima, e il parametro upstream_url non veniva mai usato: esisteva solo per
# count_tokens_real, che a sua volta non aveva chiamanti. La stima resta affidata a
# estimate_tokens_body, viva e ampiamente usata da context_rewrite e context_manager. Con le
# due funzioni cadono in cascata _token_count_cache e CACHE_TTL_SEC, che nessun altro leggeva.
