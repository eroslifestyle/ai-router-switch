"""Token counter con cache per richieste Anthropic-compatibili."""

import json
import time
from typing import Dict, Tuple

_token_count_cache: Dict[str, Tuple[int, float]] = {}  # fingerprint → (count, timestamp)
CACHE_TTL_SEC = 30

def estimate_tokens(text: str) -> int:
    """Stima token: char/4 per English+code mix."""
    return max(len(text) // 4, 1)

IMAGE_TOKEN_COST = 1600

def estimate_tokens_body(body: bytes) -> int:
    """Stima token per un body che può contenere immagini base64.
    I byte base64 delle immagini non sono testo; char/4 li sovrastima di ~200x
    (es. 1,4 MB base64 ≈ 350 k token stimati contro ~1 600 reali).
    LIMITE NOTO: i blocchi type=="document" (PDF base64) restano fuori scope
    in questa versione.
    """
    # Prova a parsare il body come JSON
    try:
        data = json.loads(body)
    except Exception:
        # JSON non valido: fallback sulla stima plain‑text
        return estimate_tokens(body.decode("utf-8", errors="replace"))

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
        return estimate_tokens(body.decode("utf-8", errors="replace"))

    # Calcolo finale: si sottraggono i caratteri base64 dal totale e si aggiunge
    # il costo fisso per ogni immagine rilevata.
    return max(1, (len(body) - b64_bytes) // 4 + images_count * IMAGE_TOKEN_COST)

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
