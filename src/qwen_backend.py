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

import aiohttp
import aiohttp.web
from aiohttp import ClientTimeout

import paths
import secrets_provider
import debug_catalog
import tool_isolation
import qwen_tool_trim
from synthetic_response import synthetic_error, synthetic_rate_limit

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
    return ClientTimeout(total=None, sock_connect=15, sock_read=NON_STREAM_SOCK_READ_SEC)


KEY_FILE = paths.secrets_script()
ALERT_LOG = paths.log_file("qwen-alerts.log")

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
# Tetto sulla DIMENSIONE DEL CORPO, ortogonale al context window: il gateway
# Model Studio risponde 413 RequestTooLarge guardando i byte, PRIMA di valutare
# il contesto del modello, e nella risposta non dichiara alcun limite.
# Misurato il 2026-08-03 sull'account: 4,8 MB accettati (1.000.009 token di
# input), 6,7 MB respinti. 4 MB lascia margine sotto il punto accettato noto.
QWEN_MAX_BODY_BYTES = int(os.environ.get("AIROUTER_QWEN_MAX_BODY_BYTES", str(4 * 1024 * 1024)))
QWEN_SAFETY = float(os.environ.get("AIROUTER_QWEN_SAFETY", "0.8"))
QWEN_RETRY_CAP_SEC = float(os.environ.get("AIROUTER_QWEN_RETRY_CAP_SEC", "90"))
# 8s era troppo poco: il 25,6% delle richieste qwen veniva rifiutato dal limiter
# INTERNO (120 "budget esaurito", zero 429 da Alibaba). Meglio aspettare il turno
# che restituire un 429 sintetico al client (2026-08-04).
QWEN_STREAM_ACQUIRE_CAP_SEC = float(os.environ.get("AIROUTER_QWEN_STREAM_ACQUIRE_CAP_SEC", "45"))
QWEN_BACKOFF_STEPS = (5, 10, 20, 40, 60)
QWEN_CONCURRENCY = int(os.environ.get("AIROUTER_QWEN_SEMAPHORE", "8"))
_QWEN_SEM = asyncio.Semaphore(QWEN_CONCURRENCY)

# Valori ufficiali rate limits per regione ap-southeast-1 Singapore (2026-08-03).
# Fonte: https://www.alibabacloud.com/help/en/model-studio/rate-limit
# ATTENZIONE: i limiti VARIANO per regione e NON valgono per Pechino o Francoforte.
# Le versioni datate con suffisso data hanno limiti molto piu bassi delle stabili,
# tipicamente 60 RPM; se si passa a un modello datato questi valori non valgono.
# Rate limiting NON si applica alle chiamate tramite Batch API.
# Il gateway NON espone header di quota (verificato 2026-08-03 ispezionando le risposte),
# per cui questi limiti non sono osservabili a runtime e vanno tenuti allineati alla doc a mano.
QWEN_RATE_LIMITS = {
    "qwen3.8-max": (600, 1_000_000),
    "qwen3.7-max": (600, 1_000_000),
    "qwen3.7-plus": (15_000, 5_000_000),
    "qwen3.6-plus": (15_000, 5_000_000),
    "qwen3.7-flash": (15_000, 5_000_000),
    "qwen3.6-flash": (15_000, 5_000_000),
    "qwen3-max": (600, 1_000_000),
    "qwen3-coder-plus": (2_400, 2_000_000),
    "qwen3-coder-next": (600, 1_000_000),
    "qwen3-coder-flash": (600, 5_000_000),
    "qwen3-vl-plus": (1_200, 1_000_000),
    "qwen-plus": (600, 1_000_000),
    "qwen-flash": (600, 5_000_000),
    "qwen-max": (600, 1_000_000),
}
QWEN_RATE_LIMITS_DEFAULT = (60, 1_000_000)  # Default prudente per modelli non elencati


async def get_qwen_key() -> str:
    """Chiave Qwen: env QWEN_API_KEY o DASHSCOPE_API_KEY, poi la catena di secrets_provider."""
    return await secrets_provider.get_secret_async(
        "qwen.api_key", extra_env=("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    )


async def get_qwen_workspace() -> str:
    """ID workspace Qwen: env QWEN_WORKSPACE_ID, poi la catena di secrets_provider."""
    return await secrets_provider.get_secret_async(
        "qwen.workspace_id", extra_env=("QWEN_WORKSPACE_ID",),
    )


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


async def qwen_dashscope_base() -> str:
    """Host dei servizi nativi DashScope (immagini, video, TTS, ASR, musica,
    embeddings, rerank).

    VERIFICATO 2026-08-03 dal CSV delle credenziali emesso dalla console: il
    campo 'dashScope' vale https://{ws}.{region}.maas.aliyuncs.com/api/v1,
    cioe' l'host DEDICATO del workspace, non il vecchio dashscope-intl. Usare
    quello generico funzionava per l'endpoint Anthropic-compatible ma qui
    avrebbe puntato al tenant sbagliato.

    Ritorna la sola RADICE (senza /api/v1): i path del modulo generativo lo
    includono gia'. Override totale con QWEN_DASHSCOPE_HOST.
    """
    if os.environ.get("QWEN_DASHSCOPE_HOST"):
        return os.environ["QWEN_DASHSCOPE_HOST"].rstrip("/")
    ws = await get_qwen_workspace()
    if ws:
        return f"https://{ws}.{QWEN_REGION}.maas.aliyuncs.com"
    return QWEN_DASHSCOPE_HOST.rstrip("/")


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


# Rimossa il 2026-08-03: non era mai chiamata e duplicava il controllo
# sul contesto gia' svolto dal proxy (ai-router-proxy.py righe 391 e 545).
# Il guardrail sui byte QWEN_MAX_BODY_BYTES resta attivo ed e' cosa diversa.


def _estimate_tokens(data: bytes) -> int:
    """Stima il numero di token per un body JSON."""
    try:
        decoded = json.loads(data)
        text = json.dumps(decoded, ensure_ascii=False)
        return max(1, len(text) // 4)
    except Exception:
        return max(1, len(data) // 4)


_last_qwen_alert_ts = 0.0  # istante dell'ultimo popup
_QWEN_ALERT_MIN_INTERVAL_SEC = 300  # intervallo minimo fra due popup


def qwen_alert(msg: str):
    """Logga un alert per Qwen su file dedicato e notifica desktop.

    Il popup desktop e' soggetto a throttle: viene emesso solo se sono
    passati almeno _QWEN_ALERT_MIN_INTERVAL_SEC secondi dall'ultimo.
    """
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(ALERT_LOG, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass

    global _last_qwen_alert_ts
    now = time.monotonic()
    if now - _last_qwen_alert_ts >= _QWEN_ALERT_MIN_INTERVAL_SEC:
        try:
            _last_qwen_alert_ts = now
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

            # spendi il budget PRIMA di rinunciare, perche a finestra appena riempita
            # la stima e circa 60s e superava qualsiasi budget, facendo fallire
            # all istante con waited 0s (2026-08-04); ora si attende e poi si riprova.
            wait = min(wait, budget_sec - waited)
            if wait <= 0:
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
    def _err(status, etype, msg, headers=None):
        """Errore nel formato che il chiamante si aspetta.

        Con passthrough=True il chiamante passa il risultato al relay, che vuole
        la superficie di una ClientResponse (.release(), .content.iter_any()):
        una web.Response li' esplode con AttributeError e nasconde il messaggio.
        Senza passthrough il chiamante vuole invece una web.Response gia' pronta.
        """
        if passthrough:
            return synthetic_error(status, etype, msg, headers=headers)
        payload = json.dumps({"type": "error", "error": {"type": etype, "message": msg}})
        return aiohttp.web.Response(status=status, text=payload,
                                    content_type="application/json",
                                    headers=headers or {})

    key = await get_qwen_key()
    if not key:
        log_fn("QWEN: chiave assente (QWEN_API_KEY/DASHSCOPE_API_KEY o secrets.sh qwen.api_key)")
        return _err(502, "qwen_unavailable", "qwen key missing")

    body = tool_isolation.filter_tools_for_backend(body, "qwen")
    body = qwen_tool_trim.strip_heavy_connectors(body)
    body = clamp_qwen_max_tokens(body, log_fn=log_fn)

    # Il gateway risponde 413 RequestTooLarge sui byte, prima ancora di valutare
    # il contesto del modello, e non spiega perche'. Intercettarlo qui produce un
    # errore leggibile invece di un 413 grezzo che il client non sa interpretare.
    if len(body) > QWEN_MAX_BODY_BYTES:
        _mb = len(body) / (1024 * 1024)
        log_fn(f"QWEN corpo {_mb:.1f} MB > limite {QWEN_MAX_BODY_BYTES // (1024*1024)} MB -> 413 anticipato")
        debug_catalog.record_event(severity="block", category="qwen",
                                   kind="qwen_body_too_large", code=413,
                                   snippet=f"{len(body)}b > {QWEN_MAX_BODY_BYTES}b model={model}")
        return _err(413, "request_too_large",
                    f"qwen: corpo {_mb:.1f} MB oltre il limite del gateway "
                    f"({QWEN_MAX_BODY_BYTES // (1024*1024)} MB)")

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
                _raw429 = b""  # mai None: piu' sotto viene decodificato
                _retry_after = resp.headers.get("retry-after")
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
                    _raw429 = await resp.read()
                finally:
                    resp.release()

                if attempt == 0:
                    await asyncio.sleep(step + random.uniform(0.5, 2))
                    continue
                # Non allertiamo sul primo 429 perche il backoff con retry spesso risolve il problema da solo
                qwen_alert(
                    f"Rate limit Qwen persistente dopo due tentativi (model={lim_model}): "
                    f"{_raw429.decode('utf-8', errors='replace')[:200]}"
                )
                if passthrough:
                    return synthetic_rate_limit(
                        f"qwen rate limit persistente dopo 2 tentativi (model={lim_model})",
                        retry_after=_retry_after)
                return _err(429, "rate_limit_error",
                            f"qwen rate limit persistente dopo 2 tentativi (model={lim_model})",
                            headers={"Retry-After": _retry_after} if _retry_after else None)

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
            if passthrough:
                return synthetic_rate_limit(f"qwen rate-limit: budget esaurito. {e}",
                                            retry_after="10")
            return _err(429, "rate_limit_error", f"qwen rate-limit: budget esaurito. {e}",
                        headers={"Retry-After": "10", "x-should-retry": "true"})

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
            return _err(502, "qwen_upstream_error", f"qwen upstream error: {e}")

    return _err(502, "qwen_exhausted", "qwen: max retries exhausted (2 attempts)")
