"""anthropic_rate_limiter.py — Rate limiter preventivo per Anthropic.

Stesso pattern di GLMRateLimiter / MinimaxRateLimiter / QwenRateLimiter:
sliding window 60s per-modello (RPM+TPM), cooldown globale sui 429, backoff
esponenziale con jitter. Anthropic aveva solo retry reattivo (pipeline_common)
ma nessun pacing preventivo → burst di richieste generavano 429 evitabili.

Classificazione 429 Anthropic (fonte: docs Anthropic + memoria vault):
  - burst-limiter (per-modello, "Server is temporarily limiting requests"):
    rate temporaneo, x-should-retry:true o assente → cooldown + ritenta.
  - org-level quota (persistente, "Usage limit reached", x-should-retry:false):
    NON si ritenta, l'errore va onestamente al client. Gestito dal retry
    certificato esistente in pipeline_common (che gia onora x-should-retry).

Design fail-open: se il limiter solleva o e' incerto, si propaga l'eccezione e
il chiamante (forward_anthropic) ricade sul retry reattivo esistente. Il
limiter non rompe MAI il path critico anthropic.

Costanti configurabili via env per tuning senza deploy.
"""
import asyncio
import os
import random
import time
from collections import deque


# ── Costanti ──────────────────────────────────────────────────────────────────

# Safety factor: 80% dei limiti nominali (headroom per jitter gateway e
# richieste concorrenti di sessioni diverse sulla stessa org).
ANTHROPIC_SAFETY = float(os.environ.get("AIROUTER_ANTHROPIC_SAFETY", "0.8"))

# Rate limits per-modello Anthropic (RPM, TPM). Sorgente: platform.claude.com
# docs/en/api/rate-limits. I limiti reali dipendono dal piano (Free/Pro/Team/
# Enterprise) e NON sono pubblici per il piano subscription OAuth usato qui.
# Valori prudenziali: 50 RPM e' il burst tipico per piano Pro, 100k TPM copre
# conversazioni con prompt caching attivo (345k cache_read misurati). Aggiornare
# dopo misura sul traffico reale. ponytail: placeholder — verificare col piano.
ANTHROPIC_RATE_LIMITS = {
    "claude-opus-4-20250514": (50, 100_000),
    "claude-sonnet-4-20250514": (50, 100_000),
    "claude-haiku-4-5-20251001": (100, 100_000),
    "claude-3-5-sonnet-20241022": (50, 100_000),
    "claude-3-5-haiku-20241022": (100, 100_000),
}
ANTHROPIC_RATE_LIMITS_DEFAULT = (50, 100_000)

# Backoff sui 429 burst: scala corta (Anthropic burst si risolve in secondi,
# non minuti come GLM quota). ponytail: corta per non congelare stream.
ANTHROPIC_BACKOFF_STEPS = (2, 5, 10, 20, 30)

# Budget acquire: durante l'attesa del limiter il client non vede byte → oltre
# pochi secondi percepisce freeze e ritenta. Stesso valore di GLM stream.
ANTHROPIC_ACQUIRE_BUDGET_SEC = float(
    os.environ.get("AIROUTER_ANTHROPIC_ACQUIRE_BUDGET_SEC", "8"))

# Concorrenza massima richieste upstream verso Anthropic (come _GLM_SEM).
ANTHROPIC_CONCURRENCY = int(
    os.environ.get("AIROUTER_ANTHROPIC_SEMAPHORE", "8"))

# Margine stima token (stesso di GLM: +10% prudenziale).
_MARGINE_STIMA = 1.10


class RateLimitExhausted(Exception):
    """acquire() ha esaurito il budget di attesa senza trovare uno slot."""


def _classify_anthropic_429(status: int, headers, raw: bytes) -> str:
    """Classifica un 429 Anthropic in 'burst' (ritentabile) o 'org_quota'
    (persistente, non ritentare).

    Sorgente: memoria vault anthropic-org-throttle-429 + docs Anthropic.
      - x-should-retry:false → definitivo (org quota, piano esaurito).
      - "Overloaded" / "temporarily limiting" / retry-after presente → burst.
      - Default: burst (conservativo, il retry certificato decide poi).
    """
    if str(headers.get("x-should-retry", "")).lower() == "false":
        return "org_quota"
    low = (raw or b"")[:500].lower()
    if b"usage limit" in low or b"resets at" in low or b"your credit" in low:
        return "org_quota"
    return "burst"


def _estimate_tokens(data: bytes) -> int:
    """Stima token per il rate limiter, con margine prudenziale.

    Usa bytes_per_token("anthropic") se disponibile (misura in token_counter),
    altrimenti fallback 4 byte/token (costante storica)."""
    try:
        from token_counter import bytes_per_token
        divisore = bytes_per_token("anthropic")
        return max(1, int(len(data) / divisore * _MARGINE_STIMA))
    except Exception:
        try:
            return max(1, int(len(data) / 4 * 1.2))
        except Exception:
            return 1


class AnthropicRateLimiter:
    """Pacing client-side sui limiti Anthropic (sliding window 60s per modello)
    + cooldown globale sui 429 burst (anti-hammering).

    Design identico a GLMRateLimiter: window per modello (RPM+TPM), lock SOLO
    per check+insert, sleep FUORI dal lock, cooldown globale sul 429 burst.
    I 429 org-quota NON attivano cooldown (sono di piano, non di rate: il
    retry certificato li relay onestamente al client).
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._windows = {}          # model -> deque([[ts, tokens], ...])
        self._cooldown_until = 0.0  # monotonic; globale
        self._backoff_idx = 0

    def _limits(self, model: str):
        rpm, tpm = ANTHROPIC_RATE_LIMITS.get(
            model, ANTHROPIC_RATE_LIMITS_DEFAULT)
        return max(1, int(rpm * ANTHROPIC_SAFETY)), int(tpm * ANTHROPIC_SAFETY)

    def _prune(self, model: str, now: float):
        win = self._windows.setdefault(model, deque())
        while win and now - win[0][0] > 60.0:
            win.popleft()
        return win

    async def acquire(self, model: str, est_tokens: int,
                      budget_sec: float = ANTHROPIC_ACQUIRE_BUDGET_SEC):
        """Attende uno slot RPM/TPM per `model`. Ritorna l'entry da passare a
        record(). Solleva RateLimitExhausted se il budget e' esaurito."""
        waited = 0.0
        while True:
            async with self._lock:
                now = time.monotonic()
                if self._cooldown_until > now:
                    wait = min(self._cooldown_until - now, 60.0)
                else:
                    win = self._prune(model, now)
                    rpm_limit, tpm_limit = self._limits(model)
                    tpm_used = sum(e[1] for e in win)
                    if len(win) < rpm_limit and tpm_used + est_tokens <= tpm_limit:
                        entry = [now, est_tokens]
                        win.append(entry)
                        return entry
                    wait = max(0.5, 60.0 - (now - win[0][0])) if win else 1.0
            wait += random.uniform(0.05, 0.2)
            if waited + wait > budget_sec:
                raise RateLimitExhausted(
                    f"anthropic rate-limit: budget {budget_sec:.0f}s "
                    f"esaurito (waited {waited:.0f}s)")
            await asyncio.sleep(wait)
            waited += wait

    def record(self, entry: list, actual_tokens: int, success: bool):
        """Aggiorna entry acquisita: token reali se success, 0 se fail."""
        if entry is not None:
            entry[1] = actual_tokens if success else 0

    def on_429(self, kind: str = "burst") -> float:
        """Cooldown globale con backoff esponenziale + jitter.

        kind='burst' (default): attiva il cooldown (rate temporaneo).
        kind='org_quota': NOOP (e' un limite di piano, non di rate: il cooldown
            non aiuta, l'errore va relayato al client dal retry certificato).
        Ritorna i secondi di backoff (0 se NOOP)."""
        if kind == "org_quota":
            return 0.0
        step = ANTHROPIC_BACKOFF_STEPS[
            min(self._backoff_idx, len(ANTHROPIC_BACKOFF_STEPS) - 1)]
        self._backoff_idx = min(
            self._backoff_idx + 1, len(ANTHROPIC_BACKOFF_STEPS) - 1)
        until = time.monotonic() + step + random.uniform(0, 2)
        if until > self._cooldown_until:
            self._cooldown_until = until
        return step

    def on_success(self):
        """Reset backoff + cooldown dopo una risposta 2xx."""
        self._backoff_idx = 0
        self._cooldown_until = 0.0

    def snapshot(self) -> dict:
        """Stato per /health."""
        now = time.monotonic()
        per_model = {}
        for m, win in self._windows.items():
            live = [e for e in win if now - e[0] <= 60.0]
            rpm_limit, tpm_limit = self._limits(m)
            per_model[m] = {"rpm_used": len(live), "rpm_limit": rpm_limit,
                            "tpm_used": sum(e[1] for e in live),
                            "tpm_limit": tpm_limit}
        return {"cooldown_sec": max(
                    0.0, round(self._cooldown_until - now, 1)),
                "per_model": per_model}


# Istanza singleton (come GLM_LIMITER / MINIMAX_LIMITER / QWEN_LIMITER).
ANTHROPIC_LIMITER = AnthropicRateLimiter()
# Semaforo concorrenza upstream (come _GLM_SEM / _MINIMAX_SEM / _QWEN_SEM).
_ANTHROPIC_SEM = asyncio.Semaphore(ANTHROPIC_CONCURRENCY)


if __name__ == "__main__":
    # Self-check: sliding window + classificazione. Lazy: 3 assert, no pytest.
    import asyncio as _aio

    async def _demo():
        lim = AnthropicRateLimiter()
        # acquire entro limiti → immediato
        e = await lim.acquire("claude-sonnet-4-20250514", 1000, budget_sec=1)
        assert e is not None, "acquire entro limiti deve riuscire"
        lim.record(e, 1000, True)
        # classificazione
        assert _classify_anthropic_429(
            429, {"x-should-retry": "false"}, b"") == "org_quota"
        assert _classify_anthropic_429(
            429, {}, b"Server is temporarily limiting requests") == "burst"
        # on_429 burst attiva cooldown, org_quota NOOP
        step = lim.on_429("burst")
        assert step > 0 and lim._cooldown_until > time.monotonic()
        lim2 = AnthropicRateLimiter()
        assert lim2.on_429("org_quota") == 0.0
        assert lim2._cooldown_until == 0.0
        # snapshot
        s = lim.snapshot()
        assert "per_model" in s and "cooldown_sec" in s
        print("OK: sliding window + classificazione + cooldown")

    _aio.run(_demo())
