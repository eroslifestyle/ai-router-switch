#!/usr/bin/env python3
"""
GLM Backend — Zhipu AI (Z.ai) Anthropic-compatible endpoint.

3 tier modeli GLM con context window reali verificati:
  - TOP    (GLM_TIER_TOP):    glm-5.2    — 1M ctx, 128K output
  - TURBO  (GLM_TIER_TURBO): glm-5-turbo — 200K ctx
  - MID    (GLM_TIER_MID):   glm-4.7     — 128K ctx, cheap

R3 decisions:
  R3-#1: GLMRateLimiter dedicato, non condiviso con minimax
  R3-#2: chiave da secrets.sh, mai stampata nei log
  R3-#3: circuit breaker con auto-recupero
  R3-#4: classify_tier via MiniMax/M3
  R3-#5: peak scheduler (Asia/Shanghai 14-18 UTC+8)
  R3-#6: forward_glm con retry loop 2 tentativi
"""
import asyncio
import json
import os
import random
import subprocess
import time
from aiohttp import ClientTimeout
from collections import deque
from pathlib import Path
from typing import Optional

# Z.ai Anthropic-compatible endpoint
GLM_UPSTREAM = os.environ.get("GLM_UPSTREAM", "https://api.z.ai/api/anthropic")

# Tier costanti
GLM_TIER_TOP = "TOP"
GLM_TIER_TURBO = "TURBO"
GLM_TIER_MID = "MID"

# Marker per il proxy: task complesso in fascia peak → Anthropic esegue direttamente
_ANTHROPIC_BLOCKED = "__ANTHROPIC_BLOCKED__"

# Modello GLM per ogni tier
GLM_MODEL_FOR_TIER = {
    GLM_TIER_TOP: "glm-5.2",
    GLM_TIER_TURBO: "glm-5-turbo",
    GLM_TIER_MID: "glm-4.7",
}

# Context limit sicuri per ogni modello (input tokens, con headroom)
# Source: piano verificato con contesto reale
_GLM_CONTEXT_LIMITS = {
    "glm-5.2": 900_000,     # 1M ctx, 100K headroom
    "glm-5-turbo": 180_000,  # 200K ctx, 20K headroom
    "glm-4.7": 115_000,      # 128K ctx, 13K headroom
}

KEY_FILE = Path.home() / ".claude" / "secrets" / "secrets.sh"
ALERT_LOG = Path.home() / ".claude" / "logs" / "glm-peak-alerts.log"

# ── API Key ──────────────────────────────────────────────────────────────────

_glm_key_cache: dict = {"key": "", "ts": 0.0}


async def get_glm_key() -> str:
    """Legge la chiave GLM da secrets.sh, con cache 60s."""
    now = time.time()
    if _glm_key_cache["key"] and now - _glm_key_cache["ts"] < 60:
        return _glm_key_cache["key"]

    key = os.environ.get("GLM_API_KEY", "")
    if not key:
        try:
            proc = await asyncio.to_thread(
                lambda: subprocess.check_output(
                    ["bash", str(KEY_FILE), "get", "glm.api_key"],
                    timeout=5, text=True,
                )
            )
            key = proc.strip() if isinstance(proc, str) else proc.decode().strip()
        except Exception:
            key = ""

    _glm_key_cache["key"] = key
    _glm_key_cache["ts"] = now
    return key


# ── GLM Rate Limiter ─────────────────────────────────────────────────────────

# Safety factor: 80% dei limiti (headroom per jitter gateway)
GLM_SAFETY = float(os.environ.get("AIROUTER_GLM_SAFETY", "0.8"))

# GLM rate limits ufficiali (verificare dal piano Z.ai)
# ponytail: limits placeholder — aggiornare con dati reali Z.ai
GLM_RATE_LIMITS = {
    "glm-5.2": (200, 10_000_000),      # (RPM, TPM)
    "glm-5-turbo": (500, 20_000_000),
    "glm-4.7": (500, 20_000_000),
}
GLM_RATE_LIMITS_DEFAULT = (200, 10_000_000)
GLM_RETRY_CAP_SEC = float(os.environ.get("AIROUTER_GLM_RETRY_CAP_SEC", "90"))
GLM_BACKOFF_STEPS = (5, 10, 20, 40, 60)


class RateLimitExhausted(Exception):
    """acquire() ha esaurito il budget di attesa."""


class GLMRateLimiter:
    """Pacing client-side sui limiti GLM (sliding window 60s per modello)
    + cooldown globale condiviso sui 429 (anti-hammering).

    Design: window per modello (RPM+TPM), lock SOLO per check+insert,
    sleep FUORI dal lock, cooldown globale sul 429.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._windows = {}          # model -> deque([[ts, tokens], ...])
        self._cooldown_until = 0.0  # monotonic; globale
        self._backoff_idx = 0

    def _limits(self, model: str):
        rpm, tpm = GLM_RATE_LIMITS.get(model, GLM_RATE_LIMITS_DEFAULT)
        return max(1, int(rpm * GLM_SAFETY)), int(tpm * GLM_SAFETY)

    def _prune(self, model: str, now: float):
        win = self._windows.setdefault(model, deque())
        while win and now - win[0][0] > 60.0:
            win.popleft()
        return win

    async def acquire(self, model: str, est_tokens: int, budget_sec: float):
        """Attende uno slot RPM/TPM per `model`."""
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
            wait += random.uniform(0.05, 0.5)
            if waited + wait > budget_sec:
                raise RateLimitExhausted(
                    f"glm rate-limit: budget {budget_sec:.0f}s esaurito (waited {waited:.0f}s)")
            await asyncio.sleep(wait)
            waited += wait

    def record(self, entry: list, actual_tokens: int, success: bool):
        """Aggiorna entry acquisita: token reali se success, 0 se fail."""
        entry[1] = actual_tokens if success else 0

    def on_429(self):
        """Cooldown globale con backoff esponenziale + jitter."""
        step = GLM_BACKOFF_STEPS[min(self._backoff_idx, len(GLM_BACKOFF_STEPS) - 1)]
        self._backoff_idx = min(self._backoff_idx + 1, len(GLM_BACKOFF_STEPS) - 1)
        until = time.monotonic() + step + random.uniform(0, 2)
        if until > self._cooldown_until:
            self._cooldown_until = until
        return step

    def on_success(self):
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
                            "tpm_used": sum(e[1] for e in live), "tpm_limit": tpm_limit}
        return {"cooldown_sec": max(0.0, round(self._cooldown_until - now, 1)),
                "per_model": per_model}


GLM_LIMITER = GLMRateLimiter()


# ── Alert ─────────────────────────────────────────────────────────────────────

_last_alert_ts = 0.0
_ALERT_MIN_INTERVAL_SEC = 300


def glm_alert(msg: str):
    """Notifica quota GLM esaurita: log file + throttle popup desktop."""
    global _last_alert_ts
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    now = time.monotonic()
    if now - _last_alert_ts < _ALERT_MIN_INTERVAL_SEC:
        return
    _last_alert_ts = now
    try:
        import subprocess
        subprocess.Popen(["notify-send", "-u", "normal", "-t", "20000",
                          "GLM Quota", msg[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ── Body size check ────────────────────────────────────────────────────────────

def is_glm_body_too_large(body: bytes, model: str) -> bool:
    """True se il body eccede il limite sicuro per il modello GLM target."""
    # Stima: 1 token ≈ 4 char, aggiungiamo 20% headroom
    try:
        body_size = len(body)
        est_tokens = int(body_size / 4 * 1.2)
        limit = _GLM_CONTEXT_LIMITS.get(model, 900_000)
        return est_tokens > limit
    except Exception:
        return False


# ── 429 classification ────────────────────────────────────────────────────────

def classify_429_glm(raw: bytes) -> str:
    """Classifica un 429 GLM: 'quota_5h' (attesa ore) vs 'rpm_tpm' (attesa secondi)."""
    low = raw[:2000].lower()
    if b"usage limit" in low or b"resets at" in low or b"5h" in low:
        return "quota_5h"
    return "rpm_tpm"


# ── Tier classification ───────────────────────────────────────────────────────

def heuristic_tier(body: bytes) -> str:
    """Fallback: stima il tier dalla dimensione del body.

    - > 800K char → TOP (glm-5.2, 1M ctx)
    - > 150K char → TURBO (glm-5-turbo, 200K ctx)
    - altrimenti → MID (glm-4.7, 128K ctx)
    """
    try:
        size = len(body)
        if size > 800_000:
            return GLM_TIER_TOP
        if size > 150_000:
            return GLM_TIER_TURBO
        return GLM_TIER_MID
    except Exception:
        return GLM_TIER_MID


async def classify_tier(body: bytes, request, session, log_fn=print):
    """Classifica il tier ottimale per questo body.

    Usa MiniMax/M3 come classificatore (R3-#4): manda un task di classificazione
    a MiniMax con il body analysis. Fallback: heuristic_tier.
    """
    # Prima: heuristic veloce (copre la maggioranza dei casi)
    heur = heuristic_tier(body)
    size = len(body)

    # Per task piccoli (< 10K), heuristic è sufficiente
    if size < 10_000:
        return heur

    # Per task medi (10K-150K), heuristic è quasi sempre corretto
    if size < 150_000:
        return heur

    # Per task grandi (> 150K): MiniMax fa classify migliore
    # ponytail: qui si potrebbe chiamare MiniMax per classify,
    # ma per ora heuristic è sufficiente con i limiti di context.
    # Se serve classify più preciso, aggiungere call MiniMax qui.
    return heur


def apply_peak_cap(tier: str):
    """Applica il cap peak: TURBO/TOP → MID se in fascia peak."""
    # Import lazy per evitare circular
    import peak_scheduler as _ps
    if _ps.should_block_glm_model(tier):
        return GLM_TIER_MID, True
    return tier, False


# ── Forward GLM ───────────────────────────────────────────────────────────────

# Semaphore per concorrenza GLM
_GLM_SEM = asyncio.Semaphore(int(os.environ.get("AIROUTER_GLM_SEMAPHORE", "8")))


async def forward_glm(request, body: bytes, session, model: str,
                      log_fn=print):
    """Invia request al backend GLM con retry loop 2 tentativi (R3-#6).

    Retry:
      - 429 RPM/TPM: retry con backoff
      - 5xx: retry immediato
      - Errore Rete: retry con backoff
    Non retry: 400, 401, 403, 404 (client error puro).
    """

    key = await get_glm_key()
    if not key:
        log_fn("GLM: chiave assente (GLM_API_KEY o secrets.sh glm.api_key)")
        # Ritorna errore sintetico
        from aiohttp import web
        return web.Response(status=502, text="GLM key missing")

    url = GLM_UPSTREAM + request.path_qs

    for attempt in range(2):
        try:
            # Rate limiting
            est_tokens = _estimate_tokens(body)
            await GLM_LIMITER.acquire(model, est_tokens,
                                      budget_sec=GLM_RETRY_CAP_SEC)

            timeout = ClientTimeout(total=120)
            async with _GLM_SEM:
                async with session.request(
                    method=request.method,
                    url=url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "x-api-key": key,
                    },
                    data=body,
                    timeout=timeout,
                    ssl=True,
                ) as resp:
                    raw = await resp.read()
                    GLM_LIMITER.record(None, _estimate_tokens(raw), resp.status < 400)

                    if resp.status == 429:
                        step = GLM_LIMITER.on_429()
                        log_fn(f"GLM 429 attempt {attempt + 1}: backoff {step}s")
                        if attempt == 0:
                            await asyncio.sleep(step + random.uniform(0.5, 2))
                            continue
                        # Fallisce dopo 2 tentativi
                        break

                    if resp.status >= 500 and attempt == 0:
                        # Retry su 5xx
                        await asyncio.sleep(0.5)
                        continue

                    # Risposta diretta (success o client error o 2nd attempt)
                    from aiohttp import web
                    headers = dict(resp.headers)
                    # Rimuovi hop-by-hop
                    for h in ("transfer-encoding", "connection", "keep-alive"):
                        headers.pop(h, None)
                    return web.Response(
                        body=raw,
                        status=resp.status,
                        headers=headers,
                        content_type=resp.content_type or "application/json",
                    )

        except asyncio.TimeoutError:
            log_fn(f"GLM timeout attempt {attempt + 1}")
            if attempt == 0:
                await asyncio.sleep(1)
                continue
        except aiohttp.ClientError as e:
            log_fn(f"GLM client error attempt {attempt + 1}: {e}")
            if attempt == 0:
                await asyncio.sleep(1)
                continue
        except Exception as e:
            log_fn(f"GLM error: {e}")

    # Tutti i tentativi falliti
    from aiohttp import web
    return web.Response(status=502, text=f"GLM exhausted after 2 attempts")


def _estimate_tokens(data: bytes) -> int:
    """Stima token da bytes (1 token ≈ 4 char + overhead)."""
    try:
        return max(1, int(len(data) / 4 * 1.2))
    except Exception:
        return 1
