"""Token counter con cache per richieste Anthropic-compatibili."""

import json
import time
from typing import Dict, Tuple, Optional

_token_count_cache: Dict[str, Tuple[int, float]] = {}  # fingerprint → (count, timestamp)
CACHE_TTL_SEC = 30

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

def count_tokens(body: bytes, fp: str, upstream_url: str = None) -> int:
    """Conta token reali. Prima controlla cache (30s), poi stima."""
    cache_key = fp
    now = time.time()

    # Cache hit
    if cache_key in _token_count_cache:
        count, ts = _token_count_cache[cache_key]
        if now - ts < CACHE_TTL_SEC:
            return count

    # Stima fallback
    est = estimate_tokens(body.decode('utf-8', errors='replace'))
    _token_count_cache[cache_key] = (est, now)
    return est

async def count_tokens_real(body: bytes, upstream_url: str, api_key: str = "") -> int | None:
    """Chiama /v1/messages/count_tokens per token reali (campionamento 1/10).

    AQ-8: campiona 1 su 10 richieste per calibrazione.
    Ritorna None se non campionata (caller usa stima).
    """
    import aiohttp, random  # json è già importato a livello modulo
    # Campionamento 1/10
    if random.random() > 0.1:
        return None
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                upstream_url.replace('/v1/messages', '/v1/messages/count_tokens'),
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=json.loads(body),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("usage", {}).get("input_tokens", 0)
    except Exception:
        pass
    return None
