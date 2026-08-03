#!/usr/bin/env python3
"""Qwen Backend — Alibaba Cloud Model Studio, endpoint Anthropic-compatible.

Modalità `qwen` (pura): THINK e ACT entrambi su Qwen, nessun fallback verso
Anthropic / MiniMax / GLM (stesso isolamento della modalità `glm` pura).

Endpoint: POST https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1/messages
La base URL termina con `/apps/anthropic` SENZA `/v1`: aggiungerlo fa comporre al
client `/v1/v1/models`. L'endpoint non espone `/v1/models` (404 innocuo) e non ha
la web search built-in — quella passa dal MCP WebSearch di Bailian o da
`enable_search` sull'endpoint OpenAI-compatible.

Auth: `x-api-key` e `Authorization: Bearer` (l'upstream accetta entrambi, si
mandano tutti e due come per z.ai).

I servizi nativi non-Anthropic (immagini, video, TTS, ASR, musica, embeddings,
rerank) vivono in `qwen_generative.py`.
"""
import asyncio
import json
import os
import random
import subprocess
import time
from collections import deque
from pathlib import Path

import aiohttp
import aiohttp.web
from aiohttp import ClientTimeout

import debug_catalog
import tool_isolation

# Import lazy per evitare ciclo: router_constants importa qwen_backend PRIMA di definire
# le proprie costanti, e router_utils importa router_constants. Un import a livello di
# modulo creerebbe router_constants -> qwen_backend -> router_utils -> router_constants
# (incompleto) e l'ImportError finirebbe nell'except di router_constants, che imposta
# QWEN_AVAILABLE=False in SILENZIO.


def _log_usage(**kw):
    """Import lazy di router_utils.log_router_usage."""
    try:
        from router_utils import log_router_usage
        log_router_usage(**kw)
    except Exception:
        pass


def _non_stream_timeout():
    """Timeout di lettura per le richieste NON-streaming. Import lazy, NIENTE except:
    a runtime router_constants e' gia' caricato e un except silenzioso reintrodurrebbe
    la classe di regressione del fallback muto."""
    from router_constants import NON_STREAM_SOCK_READ_SEC
    return ClientTimeout(total=None, sock_connect=15, sock_read=NON_STREAM_SOK_READ_SEC)


KEY_FILE = Path.home() / ".claude" / "secrets" / "secrets.sh"
ALERT_LOG = Path.home() / ".claude" / "logs" / "qwen-alerts.log"

# Costanti di configurazione
QWEN_REGION = os.environ.get("QWEN_REGION", "ap-southeast-1")
QWEN_WORKSPACE_ID = os.environ.get("QWEN_WORKSPACE_ID", "")
QWEN_API_BASE = os.environ.get("QWEN_API_BASE", "")
QWEN_FALLBACK_UPSTREAM = "https://dashscope-intl.aliyuncs.com/apps/anthropic"
QWEN_DASHSCOPE_HOST = os.environ.get("QWEN_DASHSCOPE_HOST", "https://dashscope-intl.aliyuncs.com")
QWEN_MULTIMODAL_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
QWEN_COMPATIBLE_PATH = "/compatible-mode/v1"

QWEN_TIER_TOP = "TOP"
QWEN_TIER_MID = "MID"
QWEN_TIER_CODER = "CODER"
QWEN_TIER_VISION = "VISION"
QWEN_MODEL_FOR_TIER = {
    QWEN_TIER_TOP: os.environ.get("QWEN_MODEL_TOP", "qwen3.7-max"),
    QWEN_TIER_MID: os.environ.get("QWEN_MODEL_MID", "qwen3.7-plus"),
    QWEN_TIER_CODER: os.environ.get("QWEN_MODEL_CODER", "qwen3-coder-plus"),
    QWEN_TIER_VISION: os.environ.get("QWEN_MODEL_VISION", "qwen3-vl-plus"),
}

# Modelli dei servizi nativi DashScope. NOMI DA VERIFICARE COL PROBE LIVE:
# i due mirror della doc ufficiale divergono (alibabacloud.com vs aliyun.com).
QWEN_MODEL_IMAGE = os.environ.get("QWEN_MODEL_IMAGE", "qwen-image-2.0-pro")
QWEN_MODEL_VIDEO = os.environ.get("QWEN_MODEL_VIDEO", "happyhorse-1.1-t2v")
QWEN_MODEL_TTS = os.environ.get("QWEN_MODEL_TTS", "qwen3-tts-flash")
QWEN_MODEL_ASR = os.environ.get("QWEN_MODEL_ASR", "fun-asr")
QWEN_MODEL_MUSIC = os.environ.get("QWEN_MODEL_MUSIC", "fun-music-v1")
QWEN_MODEL_EMBED = os.environ.get("QWEN_MODEL_EMBED", "text-embedding-v4")
QWEN_MODEL_RERANK = os.environ.get("QWEN_MODEL_RERANK", "qwen3-rerank")

QWEN_MAX_TOKENS_LIMIT = int(os.environ.get("AIROUTER_QWEN_MAX_TOKENS_LIMIT", "65536"))
QWEN_SAFETY = float(os.environ.get("AIROUTER_QWEN_SAFETY", "0.8"))
QWEN_RETRY_CAP_SEC = float(os.environ.get("AIROUTER_QWEN_RETRY_CAP_SEC", "90"))
QWEN_STREAM_ACQUIRE_CAP_SEC = float(os.environ.get("AIROUTER_QWEN_STREAM_ACQUIRE_CAP_SEC", "8"))
QWEN_BACKOFF_STEPS = (5, 10, 20, 40, 60)
QWEN_CONCURRENCY = int(os.environ.get("AIROUTER_QWEN_SEMAPHORE", "8"))
_QWEN_SEM = asyncio.Semaphore(QWEN_CONCURRENCY)

# Rate limits placeholder (da verificare con la doc ufficiale quando disponibile)
QWEN_RATE_LIMITS = {
    "qwen3.7-max": (60, 1_000_000),
    "qwen3.7-plus": (120, 2_000_000),
    "qwen3-coder-plus": (120, 2_000_000),
    "qwen3.6-flash": (300, 5_000_000),
    "qwen3-vl-plus": (60, 1_000_000),
}
QWEN_RATE_LIMITS_DEFAULT = (60, 1_000_000)

# Cache per le chiavi (60s)
_qwen_key_cache = {"key": "", "ts": 0.0}
_qwen_workspace_cache = {"workspace": "", "ts": 0.0}


async def get_qwen_key() -> str:
    """Ottiene la chiave API Qwen con cache 60s.

    Ordine: QWEN_API_KEY env, poi DASHSCOPE_API_KEY env, poi secrets.sh.
    """
    global _qwen_key_cache
    now = time.monotonic()
    if now - _qwen_key_cache["ts"] < 60.0:
        return _qwen_key_cache["key"]

    key = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
    if not key and KEY_FILE.exists():
        try:
            key = await asyncio.to_thread(
                subprocess.run,
                ["bash", str(KEY_FILE), "get", "qwen.api_key"],
                capture_output=True, timeout=5, text=True
            )
            if key.returncode == 0:
                key = key.stdout.strip()
            else:
                key = ""
        except Exception:
            key = ""

    _qwen_key_cache = {"key": key, "ts": now}
    return key


async def get_qwen_workspace() -> str:
    """Ottiene l'ID workspace Qwen con cache 60s.

    Ordine: QWEN_WORKSPACE_ID env, poi secrets.sh.
    """
    global _qwen_workspace_cache
    now = time.monotonic()
    if now - _qwen_workspace_cache["ts"] < 60.0:
        return _qwen_workspace_cache["workspace"]

    workspace = os.environ.get("QWEN_WORKSPACE_ID", "")
    if not workspace and KEY_FILE.exists():
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["bash", str(KEY_FILE), "get", "qwen.workspace_id"],
                capture_output=True, timeout=5, text=True
            )
            if result.returncode == 0:
                workspace = result.stdout.strip()
        except Exception:
            workspace = ""

    _qwen_workspace_cache = {"workspace": workspace, "ts": now}
    return workspace


async def qwen_upstream() -> str:
    """Costruisce l'URL upstream per le API Anthropic-compatible di Qwen.

    L'host workspace-dedicated (https://{ws}.{region}.maas.aliyuncs.com/apps/anthropic)
    e' quello raccomandato dalla doc ufficiale. Il fallback dashscope-intl serve quando
    il WorkspaceId non e' configurato.
    """
    if QWEN_API_BASE:
        return QWEN_API_BASE.rstrip("/")

    ws = await get_qwen_workspace()
    if ws:
        return f"https://{ws}.{QWEN_REGION}.maas.aliyuncs.com/apps/anthropic"

    return QWEN_FALLBACK_UPSTREAM


def resolve_qwen_upstream_model(tier: str) -> str:
    """Mappa il tier al modello Qwen corrispondente."""
    return QWEN_MODEL_FOR_TIER.get(tier, tier)


def clamp_qwen_max_tokens(body: bytes, log_fn=None) -> bytes:
    """Limita max_tokens nel body al limite QWEN_MAX_TOKENS_LIMIT.

    Come clamp_glm_max_tokens ma per Qwen. No-op se max_tokens assente o non int.
    Clamp a 1 se < 1.
    """
    try:
        data = json.loads(body)
        if "max_tokens" not in data:
            return body

        max_tokens = data["max_tokens"]
        if not isinstance(max_tokens, int):
            return body

        original = max_tokens
        if max_tokens < 1:
            max_tokens = 1
        elif max_tokens > QWEN_MAX_TOKENS_LIMIT:
            max_tokens = QWEN_MAX_TOKENS_LIMIT

        if max_tokens != original:
            data["max_tokens"] = max_tokens
            if log_fn:
                log_fn(f"QWEN clamp max_tokens {original} -> {max_tokens}")
            return json.dumps(data).encode()

        return body
    except (json.JSONDecodeError, KeyError):
        return body


def set_body_model(body: bytes, model: str) -> bytes:
    """Imposta il campo 'model' nel body della richiesta.

    L'upstream Qwen onora il campo model del body come z.ai.
    """
    try:
        data = json.loads(body)
        data["model"] = model
        return json.dumps(data).encode()
    except json.JSONDecodeError:
        return body


def is_qwen_body_too_large(body: bytes, model: str) -> bool:
    """Verifica se il body e' troppo grande per il modello specificato.

    Usa model_context_map.get_safe_input_limit() se disponibile.
    """
    try:
        from model_context_map import get_safe_input_limit
        limit = get_safe_input_limit(model)
        if limit <= 0:
            return False
        est = _estimate_tokens(body)
        return est > limit
    except ImportError:
        return False
    except Exception:
        return False


def _estimate_tokens(data: bytes) -> int:
    """Stima il numero di token per un body JSON."""
    try:
        decoded = json.loads(data)
        text = json.dumps(decoded, ensure_ascii=False)
        return max(1, len(text) // 4)
    except Exception:
        return max(1, len(data) // 4)


def qwen_alert(msg: str):
    """Logga un alert per Qwen su file dedicato e notifica desktop."""
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(ALERT_LOG, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass

    try:
        subprocess.Popen(
            ["notify-send", "-u", "critical", "-a", "Qwen Quota", msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


class RateLimitExhausted(Exception):
    """Sollevata quando il budget temporale per acquire() e' esaurito."""
    pass


class QwenRateLimiter:
    """Rate limiter per Qwen con gestione RPM/TPM e backoff esponenziale.

    Adattato da GLMRateLimiter per QWEN_RATE_LIMITS / QWEN_SAFETY / QWEN_BACKOFF_STEPS.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._windows = {}
        self._cooldown_until = 0.0
        self._backoff_idx = 0

    def _limits(self, model: str):
        """Ritorna (rpm_limit, tpm_limit) con fattore safety."""
        rpm, tpm = QWEN_RATE_LIMITS.get(model, QWEN_RATE_LIMITS_DEFAULT)
        return max(1, int(rpm * QWEN_SAFETY)), int(tpm * QWEN_SAFETY)

    def _prune(self, model: str, now: float):
        """Rimuove entries scadute dalla finestra RPM."""
        win = self._windows.setdefault(model, deque())
        while win and now - win[0][0] > 60.0:
            win.popleft()
        return win

    async def acquire(self, model: str, est_tokens: int, budget_sec: float):
        """Acquisisce un slot nel rate limiter o attende.

        Solleva RateLimitExhausted se budget_sec viene superato.
        """
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
                    f"qwen rate-limit: budget {budget_sec:.0f}s esaurito (waited {waited:.0f}s)"
                )

            await asyncio.sleep(wait)
            waited += wait

    def record(self, entry, actual_tokens: int, success: bool):
        """Registra i token effettivi consumati."""
        if entry is not None:
            entry[1] = actual_tokens if success else 0

    def on_429(self) -> float:
        """Gestisce errore 429 con backoff esponenziale."""
        step = QWEN_BACKOFF_STEPS[min(self._backoff_idx, len(QWEN_BACKOFF_STEPS) - 1)]
        self._backoff_idx = min(self._backoff_idx + 1, len(QWEN_BACKOFF_STEPS) - 1)
        until = time.monotonic() + step + random.uniform(0, 2)
        if until > self._cooldown_until:
            self._cooldown_until = until
        return step

    def on_success(self):
        """Reset backoff dopo successo."""
        self._backoff_idx = 0
        self._cooldown_until = 0.0

    def snapshot(self) -> dict:
        """Snapshot dello stato del rate limiter."""
        now = time.monotonic()
        per_model = {}
        for m, win in self._windows.items():
            live = [e for e in win if now - e[0] <= 60.0]
            rpm_limit, tpm_limit = self._limits(m)
            per_model[m] = {
                "rpm_used": len(live),
                "rpm_limit": rpm_limit,
                "tpm_used": sum(e[1] for e in live),
                "tpm_limit": tpm_limit
            }
        return {
            "cooldown_sec": max(0.0, round(self._cooldown_until - now, 1)),
            "per_model": per_model
        }


QWEN_LIMITER = QwenRateLimiter()


async def forward_qwen(request, body: bytes, session, model: str, log_fn=print,
                       passthrough: bool = False, upstream_model: str = ""):
    """Inoltra richiesta all'upstream Qwen Anthropic-compatible.

    DIFFERENZE da forward_glm:
    - Auth: header x-api-key E Authorization Bearer (entrambi)
    - URL base: /apps/anthropic senza /v1 finale
    - Passthrough: connessione lasciata APERTA (no async with)
    """
    key = await get_qwen_key()
    if not key:
        log_fn("QWEN: chiave assente (QWEN_API_KEY/DASHSCOPE_API_KEY o secrets.sh qwen.api_key)")
        return aiohttp.web.Response(status=502, text="qwen key missing")

    body = tool_isolation.filter_tools_for_backend(body, "qwen")
    body = clamp_qwen_max_tokens(body, log_fn=log_fn)

    url = (await qwen_upstream()) + request.path_qs

    for attempt in range(2):
        resp = None
        try:
            est_tokens = _estimate_tokens(body)
            lim_model = upstream_model or model
            budget = QWEN_STREAM_ACQUIRE_CAP_SEC if passthrough else QWEN_RETRY_CAP_SEC

            entry = await QWEN_LIMITER.acquire(lim_model, est_tokens, budget_sec=budget)

            if passthrough:
                timeout = ClientTimeout(total=None, sock_connect=15, sock_read=120)
            else:
                timeout = _non_stream_timeout()

            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01"
            }

            async with _QWEN_SEM:
                resp = await session.request(
                    method=request.method,
                    url=url,
                    headers=headers,
                    data=body,
                    timeout=timeout,
                    ssl=True
                )

            QWEN_LIMITER.record(entry, est_tokens, resp.status < 400)
            if resp.status < 400:
                QWEN_LIMITER.on_success()

            if resp.status == 429:
                step = QWEN_LIMITER.on_429()
                log_fn(f"QWEN 429 attempt {attempt+1}: backoff {step}s")
                debug_catalog.record_event(
                    severity="block",
                    category="qwen",
                    kind="qwen_429_backoff",
                    code=429,
                    snippet=f"backoff {step}s"
                )
                try:
                    await resp.read()
                finally:
                    resp.release()

                if attempt == 0:
                    await asyncio.sleep(step + random.uniform(0.5, 2))
                    continue
                break

            if resp.status >= 500 and attempt == 0:
                debug_catalog.record_event(
                    severity="error",
                    category="qwen",
                    kind="qwen_5xx_retry",
                    code=resp.status,
                    snippet=f"status={resp.status}"
                )
                try:
                    await resp.read()
                finally:
                    resp.release()
                await asyncio.sleep(0.5)
                continue

            if passthrough:
                # Connessione lasciata APERTA per StreamingRelay
                return resp

            # Non-passthrough: leggi body e ritorna
            raw = await resp.read()
            resp.release()
            return aiohttp.web.Response(
                status=resp.status,
                body=raw,
                content_type="application/json"
            )

        except RateLimitExhausted as e:
            log_fn(f"QWEN rate-limit exhausted: {e}")
            return aiohttp.web.Response(
                status=429,
                text=f"qwen rate-limit: budget esaurito. {e}"
            )

        except Exception as e:
            log_fn(f"QWEN forward error (attempt {attempt+1}): {e}")
            if resp is not None:
                try:
                    await resp.read()
                finally:
                    resp.release()

            if attempt == 0:
                await asyncio.sleep(0.5)
                continue
            return aiohttp.web.Response(
                status=502,
                text=f"qwen upstream error: {e}"
            )

    return aiohttp.web.Response(
        status=502,
        text="qwen: max retries exhausted (2 attempts)"
    )

