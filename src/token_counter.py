"""Token counter con cache per richieste Anthropic-compatibili."""

import time
from typing import Dict, Tuple

_token_count_cache: Dict[str, Tuple[int, float]] = {}  # fingerprint → (count, timestamp)
CACHE_TTL_SEC = 30

def estimate_tokens(text: str) -> int:
    """Stima token: char/4 per English+code mix."""
    return max(len(text) // 4, 1)

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
    import aiohttp, json, random
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
