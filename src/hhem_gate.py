"""
HHEM Gate — validazione fattuale zero-token via giudice locale HHEM (:4002).
Endpoint: POST /score {"source": str, "claim": str} → {"score": float}
Score < 0.5 = probabile allucinazione.
"""
import aiohttp
import asyncio

HHEM_URL = "http://127.0.0.1:4002/score"
HHEM_THRESHOLD = 0.5
HHEM_TIMEOUT_SEC = 10


async def hhem_score(source: str, claim: str, timeout_sec: float = HHEM_TIMEOUT_SEC) -> float | None:
    """Chiama HHEM per valutare un claim vs la sua fonte.
    Ritorna float [0-1] o None se HHEM non risponde (fail-open)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_sec)) as session:
            async with session.post(
                HHEM_URL,
                json={"source": source, "claim": claim},
                timeout=timeout_sec,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("score", -1))
    except Exception:
        pass
    return None


def hhem_is_hallucination(source: str, claim: str) -> bool | None:
    """Sync wrapper fail-open: ritorna True se score < threshold, False se >= threshold,
    None se HHEM non raggiungibile."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    score = loop.run_until_complete(hhem_score(source, claim))
    if score is None:
        return None  # HHEM down → fail open
    return score < HHEM_THRESHOLD
