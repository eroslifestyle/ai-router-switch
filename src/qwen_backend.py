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
import hashlib
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
from role_routing import QWEN_THINK  # single source of truth (nessun ciclo: role_routing a top-level importa solo os)
import debug_catalog
import tool_isolation
import qwen_tool_trim
import token_counter
from synthetic_response import synthetic_error, synthetic_rate_limit

# Import lazy per evitare ciclo: router_constants importa qwen_backend PRIMA di definire
# le proprie costanti, e router_utils importa router_constants. Un import a livello di
# modulo creerebbe router_constants -> qwen_backend -> router_utils -> router_constants
# (incompleto) e l'ImportError finirebbe nell'except di router_constants, che imposta
# QWEN_AVAILABLE=False in SILENZIO.


# _log_usage rimossa il 2026-08-07: nessun chiamante, come la gemella in
# glm_backend. Era un wrapper con import lazy di router_utils.log_router_usage;
# il motivo dell'import lazy (ciclo router_constants -> backend -> router_utils)
# resta documentato accanto a _non_stream_timeout in glm_backend.py.


def _non_stream_timeout():
    """Timeout di lettura per le richieste NON-streaming. Import lazy, NIENTE except:
    a runtime router_constants e' gia' caricato e un except silenzioso reintrodurrebbe
    la classe di regressione del fallback muto."""
    from router_constants import NON_STREAM_SOCK_READ_SEC
    return ClientTimeout(total=None, sock_connect=15, sock_read=NON_STREAM_SOCK_READ_SEC)


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
    # Il THINK NON passa di qui: lo decide role_routing.QWEN_THINK ("qwen3.8-max",
    # 905 richieste nel sidecar contro 1 su 3.7). L'unico chiamante di
    # resolve_qwen_upstream_model è ai-router-proxy.py:840 e chiede sempre CODER.
    # Il default resta allineato al modello vero per non ingannare chi legge.
    QWEN_TIER_TOP: os.environ.get("QWEN_MODEL_TOP", "qwen3.8-max"),
    QWEN_TIER_MID: os.environ.get("QWEN_MODEL_MID", "qwen3.7-plus"),
    QWEN_TIER_CODER: os.environ.get("QWEN_MODEL_CODER", "deepseek-v4-pro"),  # token-plan: qwen3-coder-plus NON servito (probe 2026-10-06), deepseek-v4-pro leader SWE-Verified
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

QWEN_MAX_TOKENS_LIMIT = int(os.environ.get("AIROUTER_QWEN_MAX_TOKENS_LIMIT", "131072"))
# Tetto max_tokens PER-MODELLO (stile GLM_MAX_OUTPUT). I modelli del token-plan hanno
# finestre fino a 1M; 131072 e' un tetto d'output prudente e uniforme sui modelli serviti.
# Valori da affinare quando si misura l'output cap reale per modello. Fallback: QWEN_MAX_TOKENS_LIMIT.
QWEN_MAX_OUTPUT = {
    "qwen3.8-max": 131072,
    "qwen3.8-flash": 131072,
    "qwen3.7-plus": 131072,
    "deepseek-v4-pro": 131072,
    "deepseek-v4-flash": 131072,
    "deepseek-v4.1-flash": 131072,
    "kimi-k2.7-code": 131072,
}


def qwen_max_output_for(model: str | None) -> int:
    """Tetto max_tokens per il modello (fallback QWEN_MAX_TOKENS_LIMIT)."""
    if model and model in QWEN_MAX_OUTPUT:
        return QWEN_MAX_OUTPUT[model]
    return QWEN_MAX_TOKENS_LIMIT
# Tetto sulla DIMENSIONE DEL CORPO, ortogonale al context window: il gateway
# Model Studio risponde 413 RequestTooLarge guardando i byte, PRIMA di valutare
# il contesto del modello, e nella risposta non dichiara alcun limite.
# Misurato il 2026-08-03 sull'account: 4,8 MB accettati (1.000.009 token di
# input), 6,7 MB respinti. 4 MB lascia margine sotto il punto accettato noto.
QWEN_MAX_BODY_BYTES = int(os.environ.get("AIROUTER_QWEN_MAX_BODY_BYTES", str(4 * 1024 * 1024)))
# Target byte per shrink testo preventivo (context-exceeded). Qwen3-max ha 32k
# token context (~128k byte), qwen3-coder-plus 256k token (~1M byte).
# 600k e' sotto il limite del gateway (QWEN_MAX_BODY_BYTES=4MB) ma costoso:
# qwen a differenza di glm non da' 400 context-exceeded chiaro, tronca e basta.
QWEN_SHRINK_TARGET_BYTES = int(os.environ.get(
    "AIROUTER_QWEN_CONTEXT_SHRINK_TARGET", "600000"))
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


# Cooldown anti-retry-storm su 401/403: la credenziale rifiutata e' permanente,
# ritentare non risolve nulla e rischia un ban lato provider (2026-09-24).
QWEN_AUTH_COOLDOWN_SEC = 60
_qwen_auth_reject: dict[str, float] = {}  # hash chiave -> timestamp del rifiuto


def _key_fingerprint(key: str) -> str:
    # Hash della chiave per confrontarla senza mai manipolarne il valore.
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def qwen_key_source_hint(key: str) -> str:
    """Nome della sorgente PIU PROBABILE della chiave (nessun valore in log).

    secrets_provider non espone quale sorgente ha vinto (ponytail: no API nuova),
    quindi si deduce solo dai nomi env effettivamente impostati; se nessuna env
    c'e', la chiave viene dalla catena .env/script/keyring.
    """
    found = [v for v in ("QWEN_API_KEY", "DASHSCOPE_API_KEY") if os.environ.get(v)]
    if found:
        return f"env {found[0]}"
    return "catena secrets_provider (.env/script/keyring)"


def qwen_auth_is_cooling_down(key: str, now: float) -> bool:
    """True se questa chiave e' stata rifiutata da meno di QWEN_AUTH_COOLDOWN_SEC."""
    ts = _qwen_auth_reject.get(_key_fingerprint(key))
    return ts is not None and (now - ts) < QWEN_AUTH_COOLDOWN_SEC


def qwen_auth_mark_rejected(key: str, now: float) -> None:
    """Marca la chiave come rifiutata; scade da sola (cooldown) o al cambio chiave."""
    _qwen_auth_reject.clear()  # una sola entry attiva: la chiave corrente
    _qwen_auth_reject[_key_fingerprint(key)] = now


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


def normalize_qwen_thinking(body: bytes, upstream_model: str, log_fn=None) -> bytes:
    """Normalizza `thinking` per l'upstream token-plan (2026-10-06).

    L'upstream RIFIUTA thinking={"type":"enabled","budget_tokens":N} con 400
    "InvalidParameter: Request body format invalid" (misurato: è la forma che
    Claude Code manda di default -> le chat nuove in qwen morivano 400). Accetta
    invece adaptive / disabled / assente.

    - ACT (qualsiasi modello diverso da QWEN_THINK): thinking FORZATO disabled.
      Il thinking nativo di default e' 2x piu' lento e su max_tokens piccoli
      produce risposte SOLO-thinking (stop=max_tokens, zero testo).
    - THINK (QWEN_THINK): il ragionamento resta, ma enabled+budget -> adaptive.
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    thinking = data.get("thinking")
    if upstream_model != QWEN_THINK:
        if thinking != {"type": "disabled"}:
            data["thinking"] = {"type": "disabled"}
            if log_fn:
                log_fn("QWEN ACT: thinking forzato disabled")
    elif isinstance(thinking, dict) and thinking.get("type") == "enabled":
        data["thinking"] = {"type": "adaptive"}
        if log_fn:
            log_fn("QWEN THINK: thinking enabled+budget -> adaptive (400 upstream)")
    else:
        return body
    return json.dumps(data).encode()


def add_qwen_system_cache(body: bytes, log_fn=None) -> bytes:
    """Marca il prefisso system con cache_control ephemeral.

    Il vendor cachera da sé solo su prefisso identico (misurato: cache_read alla
    2a richiesta); il marker esplicito rende il riuso affidabile sul system prompt
    stabile, ~2x piu' veloce sul prefill dei contesti grandi.
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    system = data.get("system")
    if isinstance(system, str) and system:
        data["system"] = [{"type": "text", "text": system,
                          "cache_control": {"type": "ephemeral"}}]
    elif isinstance(system, list) and system:
        last = system[-1]
        if isinstance(last, dict) and "cache_control" not in last:
            last["cache_control"] = {"type": "ephemeral"}
    else:
        return body
    if log_fn:
        log_fn("QWEN: cache_control ephemeral sul system")
    return json.dumps(data).encode()


def strip_tool_mutation_blocks(body: bytes, log_fn=None) -> bytes:
    """Rimuove i content block tool_addition/tool_removal per l'upstream Qwen.

    L'upstream token-plan non conosce quei tag in nessuna posizione (400
    "Request body format invalid", relay_error_400 mode=qwen 2026-10-06 15:15).
    Anthropic li ammette solo dentro role=system (router_utils._must_stay_system),
    ma per Qwen sono ridondanti: il set di tool e' gia' nel campo `tools`.
    """
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    removed = 0
    for msg in data.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, list):
            keep = [b for b in content
                    if not (isinstance(b, dict) and b.get("type") in ("tool_addition", "tool_removal"))]
            removed += len(content) - len(keep)
            msg["content"] = keep
    if removed:
        if log_fn:
            log_fn(f"QWEN: rimossi {removed} blocchi tool_addition/tool_removal")
        return json.dumps(data).encode()
    return body


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
        limit = qwen_max_output_for(data.get("model"))
        if max_tokens < 1:
            max_tokens = 1
        elif max_tokens > limit:
            max_tokens = limit

        if max_tokens != original:
            data["max_tokens"] = max_tokens
            if log_fn:
                log_fn(f"QWEN clamp max_tokens {original} -> {max_tokens}")
            return json.dumps(data).encode()

        return body
    except (json.JSONDecodeError, KeyError):
        return body


def prepare_qwen_body(body: bytes, upstream_model: str, log_fn=None) -> bytes:
    """Preparazione completa del body per l'upstream Qwen in UN solo parse.

    Single-parse: prima il body veniva parsato/ri-serializzato 5 volte
    (set_body_model + normalize_thinking + system_cache + strip_tool_mutation
    + clamp_max_tokens dal proxy e da forward_qwen), ~50-150ms per body grandi.
    Qui le 5 trasformazioni vivono sullo STESSO dict, un json.loads + un
    json.dumps. Le funzioni esistenti restano intatte per i test.

    Ordine: model → clamp max_tokens (su upstream_model) → thinking →
    cache_control system → rimozione blocchi tool_addition/tool_removal.
    Body non-JSON: ritornato invariato.
    """
    t0 = time.monotonic()
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    n_tools_mut = 0
    # 1. model
    if isinstance(data, dict):
        data["model"] = upstream_model
        # 2. clamp max_tokens (ora usa upstream_model, non data.get("model"))
        clamp_from = clamp_to = None
        mt = data.get("max_tokens")
        if "max_tokens" in data and isinstance(mt, int):
            limit = qwen_max_output_for(upstream_model)
            new = 1 if mt < 1 else (limit if mt > limit else mt)
            if new != mt:
                data["max_tokens"] = new
                clamp_from, clamp_to = mt, new
        # 3. thinking (stesse regole di normalize_qwen_thinking)
        thinking = data.get("thinking")
        th_action = "keep"
        if upstream_model != QWEN_THINK:
            if thinking != {"type": "disabled"}:
                data["thinking"] = {"type": "disabled"}
                th_action = "disabled"
        elif isinstance(thinking, dict) and thinking.get("type") == "enabled":
            data["thinking"] = {"type": "adaptive"}
            th_action = "adaptive"
        # 4. cache_control ephemeral sul system
        cache_action = "skip"
        system = data.get("system")
        if isinstance(system, str) and system:
            data["system"] = [{"type": "text", "text": system,
                              "cache_control": {"type": "ephemeral"}}]
            cache_action = "on"
        elif isinstance(system, list) and system:
            last = system[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                last["cache_control"] = {"type": "ephemeral"}
                cache_action = "on"
        # 5. rimozione blocchi tool_addition/tool_removal
        for msg in data.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                keep = [b for b in content
                        if not (isinstance(b, dict) and b.get("type") in ("tool_addition", "tool_removal"))]
                n_tools_mut += len(content) - len(keep)
                msg["content"] = keep
    out = json.dumps(data).encode()
    if log_fn:
        ms = (time.monotonic() - t0) * 1000
        clamp_s = f"{clamp_from}->{clamp_to}" if clamp_from is not None else "-"
        log_fn(f"QWEN prep: model={upstream_model} thinking={th_action} "
               f"cache={cache_action} clamp={clamp_s} tools_mut={n_tools_mut} "
               f"{len(body)}b->{len(out)}b {ms:.1f}ms")
    return out


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


def _estimate_tokens(data: bytes, model: str | None = None) -> int:
    """Stima il numero di token per un body JSON (delega a token_counter)."""
    return token_counter.estimate_tokens_body(data, model)


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
                       passthrough: bool = False, upstream_model: str = "", on_shrink=None):
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

    # strip_tool_mutation_blocks + clamp_qwen_max_tokens non servono piu' qui:
    # vivono in prepare_qwen_body, che il proxy chiama prima di inoltrare.
    body = tool_isolation.filter_tools_for_backend(body, "qwen")
    body = qwen_tool_trim.strip_heavy_connectors(body)

    # SHRINK TESTO PREVENTIVO: se il body supera il target, riduce il contesto
    # prima di chiamare l'upstream Qwen. Qwen non risponde con un 400
    # context-exceeded chiaro: tronca la risposta o dà 500 opaco.
    if len(body) > QWEN_SHRINK_TARGET_BYTES:
        from context_shrink import shrink_body_to_budget
        _shrunk = await shrink_body_to_budget(body, QWEN_SHRINK_TARGET_BYTES)
        if _shrunk is not None and len(_shrunk) < len(body):
            _before = len(body)
            log_fn(f"QWEN preventivo shrink {_before}b -> {len(_shrunk)}b")
            debug_catalog.record_event(
                severity="info", category="qwen", kind="qwen_preventive_shrink",
                code=0,
                snippet=f"{_before}b -> {len(_shrunk)}b target={QWEN_SHRINK_TARGET_BYTES} model={upstream_model or model}")
            body = _shrunk
            if on_shrink is not None:
                on_shrink(_shrunk)

    # Il gateway risponde 413 RequestTooLarge sui byte, prima ancora di valutare
    # il contesto del modello, e non spiega perche'. Intercettarlo qui produce un
    # errore leggibile invece di un 413 grezzo che il client non sa interpretare.
    if len(body) > QWEN_MAX_BODY_BYTES:
        _mb = len(body) / (1024 * 1024)
        log_fn(f"QWEN corpo {_mb:.1f} MB > limite {QWEN_MAX_BODY_BYTES // (1024*1024)} MB -> 413 anticipato")
        debug_catalog.record_event(severity="block", category="qwen",
                                   kind="qwen_body_too_large", code=413,
                                   snippet=f"{len(body)}b > {QWEN_MAX_BODY_BYTES}b model={upstream_model or model}")
        return _err(413, "request_too_large",
                    f"qwen: corpo {_mb:.1f} MB oltre il limite del gateway "
                    f"({QWEN_MAX_BODY_BYTES // (1024*1024)} MB)")

    url = (await qwen_upstream()) + request.path_qs

    # Cooldown anti-retry-storm: un 401/403 e' permanente, non si risolve ritentando.
    if qwen_auth_is_cooling_down(key, time.time()):
        log_fn("[qwen] credenziale in cooldown: rifiuto immediato senza contattare l'upstream")
        return _err(401, "invalid_api_key",
                    "qwen: credenziale rifiutata dall'upstream (cooldown attivo)")

    for attempt in range(2):
        resp = None
        try:
            lim_model = upstream_model or model
            est_tokens = _estimate_tokens(body, lim_model)
            budget = QWEN_STREAM_ACQUIRE_CAP_SEC if passthrough else QWEN_RETRY_CAP_SEC

            _t_acq = time.monotonic()
            entry = await QWEN_LIMITER.acquire(lim_model, est_tokens, budget_sec=budget)
            wait_ms = (time.monotonic() - _t_acq) * 1000
            snap = QWEN_LIMITER.snapshot().get("per_model", {}).get(lim_model, {})
            log_fn(f"qwen digest: model={lim_model} body={len(body)}b tools={body.count(b'\"input_schema\"')} "
                   f"est={est_tokens}tok rpm={snap.get('rpm_used','?')}/{snap.get('rpm_limit','?')} "
                   f"tpm={snap.get('tpm_used','?')}/{snap.get('tpm_limit','?')} wait={wait_ms:.0f}ms attempt={attempt+1}")
            # ponytail: tools contato su b'"input_schema"' (una occorrenza per tool def), zero parse extra

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
                    snippet=f"backoff {step}s model={lim_model} est={est_tokens} attempt={attempt+1}"
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

            if resp.status in (401, 403):
                # Credenziale rifiutata: log azionabile (niente frammenti di chiave)
                # + cooldown, cosi' il burst del client non martella l'upstream.
                qwen_auth_mark_rejected(key, time.time())
                log_fn(
                    f"[qwen] {resp.status} dall'upstream: credenziale rifiutata "
                    f"(sorgente: {qwen_key_source_hint(key)}) — rinnova il segreto "
                    f"qwen.api_key (keyring ai-router-switch) e riavvia il servizio. "
                    f"Cooldown {QWEN_AUTH_COOLDOWN_SEC}s attivo."
                )
                debug_catalog.record_event(severity="block", category="qwen",
                                           kind="qwen_auth_rejected", code=resp.status,
                                           snippet=f"status={resp.status}")
                if passthrough:
                    return _err(resp.status, "invalid_api_key",
                                "qwen: credenziale rifiutata dall'upstream")
                return _err(resp.status, "invalid_api_key",
                            "qwen: credenziale rifiutata dall'upstream — "
                            "rinnova qwen.api_key e riavvia il servizio")

            if resp.status >= 500 and attempt == 0:
                debug_catalog.record_event(
                    severity="error",
                    category="qwen",
                    kind="qwen_5xx_retry",
                    code=resp.status,
                    snippet=f"status={resp.status} model={lim_model} attempt={attempt+1}"
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
            debug_catalog.record_event(
                severity="block", category="qwen", kind="qwen_rate_budget_exhausted",
                code=429,
                snippet=f"{e} model={upstream_model or model} body={len(body)}b")
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
