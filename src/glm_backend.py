#!/usr/bin/env python3
"""
GLM Backend — Zhipu AI (Z.ai) Anthropic-compatible endpoint.

2 modelli GLM instradati per ruolo (role_routing):
  - THINK: glm-5.2  — 1M ctx, 128K output
  - ACT:   glm-4.7  — 128K ctx, cheap
(TOP/TURBO/MID/VISION/MULTIMODAL e GLM_MODEL_FOR_TIER sopravvivono
 solo come input alternativo di apply_peak_cap)

R3 decisions:
  R3-#1: GLMRateLimiter dedicato, non condiviso con minimax
  R3-#2: chiave da secrets.sh, mai stampata nei log
  R3-#3: circuit breaker con auto-recupero
  R3-#4: [2026-08-04] decaduto — classify_tier e heuristic_tier rimossi
  R3-#5: peak scheduler (Asia/Shanghai 14-18 UTC+8); cap collegato al proxy il 2026-08-04, declassa glm-5.2 e glm-5-turbo a glm-4.7
  R3-#6: forward_glm con retry loop 2 tentativi
"""
import asyncio
import gzip
import json
import os
import random
import re
import subprocess
import time
import aiohttp
import aiohttp.web
from aiohttp import ClientTimeout
from collections import deque
import paths
import secrets_provider

import tool_isolation
import debug_catalog
from synthetic_response import synthetic_error, synthetic_rate_limit, buffered_upstream


# _log_usage rimossa il 2026-08-07: nessun chiamante. Il ciclo di import che documentava resta valido e vale per _non_stream_timeout qui sotto:
# NON importare router_utils a livello di modulo, perche' router_constants importa glm_backend prima di aver definito le proprie costanti
# e router_utils importa router_constants; un import a livello di modulo crea il ciclo
# router_constants -> glm_backend -> router_utils -> router_constants incompleto, il cui ImportError finisce nell'except di
# router_constants, che silenziosamente imposta GLM_AVAILABLE=False e manda TUTTE le modalita' GLM in fallback Anthropic senza
# errori visibili (regressione del 2026-07-25).


def _non_stream_timeout():
    """Timeout di lettura per le richieste NON-streaming.

    Import LAZY per lo stesso ciclo descritto nel commento qui sopra. Qui NON si
    intercetta l'ImportError: a runtime `router_constants` e' gia' caricato, e
    un except silenzioso reintrodurrebbe la classe di regressione del 2026-07-25
    (fallback muto invece di un errore visibile). Il valore vive in un posto solo.
    """
    from router_constants import NON_STREAM_SOCK_READ_SEC
    return ClientTimeout(total=None, sock_connect=15,
                         sock_read=NON_STREAM_SOCK_READ_SEC)

# Z.ai Anthropic-compatible endpoint
# GLM_API_BASE (env da glm.env) ha priorità; fallback su hardcoded
GLM_UPSTREAM = os.environ.get(
    "GLM_API_BASE",
    os.environ.get("GLM_UPSTREAM", "https://api.z.ai/api/anthropic")
)

# GLM_IMAGE_ENDPOINT e GLM_VIDEO_ENDPOINT rimossi il 2026-08-03 perche definiti due volte in modo identico e usati solo dalle rotte generative morte
# (valori originali: "https://api.z.ai/api/paas/v4/images/generations" e "https://api.z.ai/api/paas/v4/videos/generations")

# Tier costanti
GLM_TIER_TOP = "TOP"
GLM_TIER_TURBO = "TURBO"
GLM_TIER_MID = "MID"
GLM_TIER_VISION = "VISION"      # glm-4.6V (visione base)
GLM_TIER_MULTIMODAL = "MULTIMODAL"  # glm-5V-Turbo (visione + video)

# Rimosso il 2026-08-04: _ANTHROPIC_BLOCKED, marker del vecchio dirottamento su Anthropic
# in fascia peak. Aveva la sola definizione e nessun lettore; oggi il peak cap declassa
# dentro GLM (glm-5.2 → glm-4.7) e non esce mai dal provider.

# Modello GLM per ogni tier
GLM_MODEL_FOR_TIER = {
    GLM_TIER_TOP: "glm-5.2",
    GLM_TIER_TURBO: "glm-5-turbo",
    GLM_TIER_MID: "glm-4.7",
    GLM_TIER_VISION: "glm-4.6V",
    GLM_TIER_MULTIMODAL: "glm-5V-Turbo",
}

# Mappa inversa: da nome modello a tier key
# Serve ad apply_peak_cap: dal 2026-07-25 il proxy riceve un nome modello reale da role_routing, non una tier key
GLM_TIER_FOR_MODEL = {model: tier for tier, model in GLM_MODEL_FOR_TIER.items()}

# Context limit sicuri per ogni modello (input tokens, con headroom).
#
# FONTE UNICA (2026-07-27): derivati da model_context_map, che ora contiene i
# valori della doc ufficiale docs.z.ai. Prima questa era una SECONDA tabella
# scritta a mano e divergente: diceva glm-4.7 = 115_000 mentre model_context_map
# diceva 128_000, e la doc ufficiale dice 200_000. Due tabelle per la stessa
# grandezza sono due verita' che divergono: ora ce n'e' una sola, e l'headroom
# e' l'unico BUFFER_PERCENT del sistema.
try:
    from model_context_map import get_safe_input_limit as _safe_limit

    _GLM_CONTEXT_LIMITS = {
        m: _safe_limit(m)
        for m in ("glm-5.2", "glm-5-turbo", "glm-4.7", "glm-4.6V", "glm-5V-Turbo")
    }
except Exception:  # pragma: no cover - fail-safe: il backend non deve morire per questo
    _GLM_CONTEXT_LIMITS = {
        "glm-5.2": 800_000, "glm-5-turbo": 160_000, "glm-4.7": 160_000,
        "glm-4.6V": 104_800, "glm-5V-Turbo": 160_000,
    }

ALERT_LOG = paths.log_file("glm-peak-alerts.log")

# ── API Key ──────────────────────────────────────────────────────────────────


async def get_glm_key() -> str:
    """Legge la chiave GLM: env GLM_API_KEY o ZAI_API_KEY, poi la catena di secrets_provider."""
    return await secrets_provider.get_secret_async(
        "glm.api_key", extra_env=("GLM_API_KEY", "ZAI_API_KEY"),
    )


# ── GLM Rate Limiter ─────────────────────────────────────────────────────────

# Safety factor: 80% dei limiti (headroom per jitter gateway)
GLM_SAFETY = float(os.environ.get("AIROUTER_GLM_SAFETY", "0.8"))

# GLM rate limits ufficiali (verificare dal piano Z.ai)
# ponytail: limits placeholder — aggiornare con dati reali Z.ai
GLM_RATE_LIMITS = {
    "glm-5.2": (200, 10_000_000),      # (RPM, TPM)
    "glm-5-turbo": (500, 20_000_000),
    "glm-4.7": (500, 20_000_000),
    "glm-4.6V": (200, 10_000_000),    # ponytail: placeholder — verificare limiti reali
    "glm-5V-Turbo": (100, 5_000_000),  # ponytail: placeholder — verificare limiti reali
}
GLM_RATE_LIMITS_DEFAULT = (200, 10_000_000)
GLM_RETRY_CAP_SEC = float(os.environ.get("AIROUTER_GLM_RETRY_CAP_SEC", "90"))
# Budget acquire ridotto per richieste stream: durante l'attesa del limiter il
# client non vede byte → oltre pochi secondi percepisce un freeze e ritenta.
GLM_STREAM_ACQUIRE_CAP_SEC = float(
    os.environ.get("AIROUTER_GLM_STREAM_ACQUIRE_CAP_SEC", "8"))
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
            wait += random.uniform(0.05, 0.2)
            if waited + wait > budget_sec:
                raise RateLimitExhausted(
                    f"glm rate-limit: budget {budget_sec:.0f}s esaurito (waited {waited:.0f}s)")
            await asyncio.sleep(wait)
            waited += wait

    def record(self, entry: list, actual_tokens: int, success: bool):
        """Aggiorna entry acquisita: token reali se success, 0 se fail."""
        if entry is not None:
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
        subprocess.Popen(["notify-send", "-u", "normal", "-t", "20000",
                          "GLM Quota", msg[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# Rimossa il 2026-08-04: has_multimodal_content, insieme alla sezione Multimodal detection.
# Il suo unico chiamante era classify_tier, rimossa lo stesso giorno poco piu sotto; gia dal
# 2026-08-03 nessuno interpretava piu i suoi valori image_gen e video_gen, perche il loro
# consumatore route_glm_request era stato tolto come codice morto.
# Vale la pena ricordare il difetto che quella funzione documentava (fix del 2026-07-22): un
# blocco type:"image" e SEMPRE un'immagine, sia source base64 sia url. Il ramo precedente
# classificava le base64 come "video" e mandava OGNI screenshot incollato su glm-5V-Turbo,
# che rispondeva 429 o chiudeva la connessione: era l'"errore api" della modalita glm.

# ── Body size check ────────────────────────────────────────────────────────────

# Rimossa il 2026-08-03: mai chiamata, duplicava il controllo sul contesto
# che il proxy fa gia con get_safe_input_limit (righe 391 e 545).
# Il guardrail sui byte del corpo (alive) e altrove.


# ── 429 classification ────────────────────────────────────────────────────────

def classify_429_glm(raw: bytes) -> str:
    """Classifica un 429 GLM: 'quota_5h' (attesa ore) vs 'rpm_tpm' (attesa secondi)."""
    low = raw[:2000].lower()
    if b"usage limit" in low or b"resets at" in low or b"5h" in low:
        return "quota_5h"
    return "rpm_tpm"


# Rimosse il 2026-08-04: la sezione Tier classification, cioe classify_tier e heuristic_tier.
# Il tiering per COMPLESSITA non esiste piu dal refactoring del 2026-07-25, quando il router
# e diventato un tunnel trasparente: classify_tier non aveva piu chiamanti, e heuristic_tier
# la chiamava soltanto lei. Oggi il modello GLM lo decide il RUOLO e non la dimensione del
# body: role_routing instrada glm-5.2 per il THINK e glm-4.7 per l'ACT. Cade con loro
# has_multimodal_content, di cui classify_tier era l'unico chiamante. Le costanti GLM_TIER_*
# e la mappa GLM_MODEL_FOR_TIER RESTANO: le usa apply_peak_cap.


def apply_peak_cap(tier_or_model: str):
    """Applica il cap peak: TURBO/TOP → MID e model names equivalenti se in fascia peak.

    VISION e MULTIMODAL non sono mai bloccati (servono per feature specifiche).
    Dal refactoring del 2026-07-25: accetta sia tier key che nome modello,
    restituendo nel dominio dell'input.
    """
    # Determina se l'input e' una tier key
    is_tier = tier_or_model in GLM_MODEL_FOR_TIER
    tier = tier_or_model if is_tier else GLM_TIER_FOR_MODEL.get(tier_or_model, tier_or_model)

    # VISION/MULTIMODAL esenti dal peak cap
    if tier in (GLM_TIER_VISION, GLM_TIER_MULTIMODAL):
        return tier_or_model, False

    # Import lazy per evitare circular
    import peak_scheduler as _ps
    if _ps.should_block_glm_model(tier):
        if is_tier:
            return GLM_TIER_MID, True
        else:
            return GLM_MODEL_FOR_TIER[GLM_TIER_MID], True

    return tier_or_model, False


def resolve_glm_upstream_model(tier: str) -> str:
    """Mappa una tier key (TOP/MID/TURBO/VISION/MULTIMODAL) al modello GLM
    reale da inviare a z.ai. Se `tier` è già un nome modello reale (non una
    tier key nota), lo ritorna invariato (fallback sicuro)."""
    return GLM_MODEL_FOR_TIER.get(tier, tier)


# z.ai/GLM accetta max_tokens solo nel range [1, 32768]; Claude Code (Sonnet/Opus)
# invia spesso valori più alti (es. 64000) → 400 [1210] "max_tokens parameter is
# illegal 限制数值范围[1,32768]". Clamp al choke-point forward_glm.
GLM_MAX_TOKENS_LIMIT = int(os.environ.get("AIROUTER_GLM_MAX_TOKENS_LIMIT", "32768"))

# Target byte per shrink testo preventivo (context-exceeded). GLM-5.2 ha 1M token
# (~4M byte), glm-4.7 ha 200k token (~800k byte). 600k e' prudente per entrambi:
# sotto il limite del modello piu' piccolo dopo il which model resolution.
GLM_SHRINK_TARGET_BYTES = int(os.environ.get(
    "AIROUTER_GLM_CONTEXT_SHRINK_TARGET", "600000"))


def clamp_glm_max_tokens(body: bytes, log_fn=None) -> bytes:
    """Clampa max_tokens nel body in uscita verso z.ai a GLM_MAX_TOKENS_LIMIT.
    z.ai rifiuta con 400 [1210] i valori fuori range [1, 32768]. No-op se assente
    o già valido."""
    try:
        d = json.loads(body)
    except Exception:
        return body
    mt = d.get("max_tokens")
    if not isinstance(mt, int):
        return body
    if mt > GLM_MAX_TOKENS_LIMIT:
        d["max_tokens"] = GLM_MAX_TOKENS_LIMIT
        if log_fn:
            log_fn(f"GLM clamp max_tokens {mt} -> {GLM_MAX_TOKENS_LIMIT}")
        return json.dumps(d).encode()
    if mt < 1:
        d["max_tokens"] = 1
        return json.dumps(d).encode()
    return body


def set_body_model(body: bytes, model: str) -> bytes:
    """Riscrive il campo 'model' nel body JSON della richiesta in uscita verso
    z.ai. Necessario perché z.ai onora il campo 'model' della request per
    scegliere il modello reale: se non viene sovrascritto con il modello
    risolto dal tiering, z.ai ignora la classificazione del proxy e usa il
    proprio default (bug verificato: senza questo fix ogni richiesta GLM gira
    sempre sul default z.ai, mai sul tier scelto dal classificatore)."""
    try:
        d = json.loads(body)
        d["model"] = model
        return json.dumps(d).encode()
    except Exception:
        return body


# Isolamento tool per provider centralizzato in tool_isolation.py, applicato
# dentro forward_glm (choke-point) — copre pure glm + mix-ag + mix-gm senza
# bisogno della vecchia strip_foreign_branded_tools_for_glm (rimossa 2026-07-19).

# Rimosse il 2026-08-04: build_glm_think_body e build_glm_verify_body, residui
# della pipeline THINK/ACT/VERIFY di mix-gm eliminata il 2026-07-25, quando il
# router è diventato un tunnel trasparente. La funzione che le consumava,
# _glm_minimax_think_act_verify, non esiste più; l'unico riferimento superstite
# era sviluppo/tests/test_mixgm_verify_retry.sh, già rotto (il suo caso [3]
# cercava _build_minimax_act_body_retry, assente dal proxy), rimosso insieme.
# Cadono con loro le costanti GLM_THINK_VERIFY_MODEL e GLM_THINK_TIMEOUT_SEC e,
# in cascata, l'helper _extract_text: nessun altro le leggeva.


# Rimosse 2026-08-03: _fire_and_forget, mai chiamata; con essa non resta alcun task lanciato in background.
# _background_tasks pure rimosso: esisteva solo per questo helper.


# Rimosse 2026-08-03: forward_glm_image, forward_glm_video e route_glm_request
# erano codice morto: nessun caller le raggiungeva mai (grep + grafo chiamate
# confermato). Se un giorno si vorranno cablare le rotte generative su GLM
# andranno riscritte con decompressione, semaforo _GLM_SEM e model ID da env.
# Storia completa: git log.


# ── Forward GLM ───────────────────────────────────────────────────────────────

# Semaphore per concorrenza GLM
_GLM_SEM = asyncio.Semaphore(int(os.environ.get("AIROUTER_GLM_SEMAPHORE", "8")))



def _glm_is_empty(decompressed: bytes) -> bool:
    if not decompressed:
        return True
    toks = re.findall(rb'"output_tokens"\s*:\s*(\d+)', decompressed)
    if not toks:
        return False
    return int(toks[-1]) == 0


async def forward_glm(request, body: bytes, session, model: str,
                      log_fn=print, passthrough: bool = False,
                      upstream_model: str = ""):
    """Invia request al backend GLM con retry loop 2 tentativi (R3-#6).

    Args:
        passthrough: se True, ritorna la ClientResponse raw (per relay streaming).
                     Il caller deve chiamare .release() o consumare il body.
                     se False (default), legge il body e ritorna web.Response.
    """

    def _err(status, etype, msg, headers=None):
        """Errore nel formato che il chiamante si aspetta.

        Con passthrough=True il chiamante passa il risultato al relay, che vuole
        la superficie di una ClientResponse (.release(), .content.iter_any()):
        una web.Response li' esplode con AttributeError e nasconde il messaggio
        vero dietro un 502 generico. Senza passthrough il chiamante vuole invece
        una web.Response gia' pronta.
        """
        if passthrough:
            return synthetic_error(status, etype, msg, headers=headers)
        payload = json.dumps({"type": "error", "error": {"type": etype, "message": msg}})
        return aiohttp.web.Response(status=status, text=payload,
                                    content_type="application/json",
                                    headers=headers or {})

    key = await get_glm_key()
    if not key:
        log_fn("GLM: chiave assente (GLM_API_KEY o secrets.sh glm.api_key)")
        return _err(502, "glm_unavailable", "GLM key missing")
    # ISOLAMENTO TOOL (2026-07-19): choke-point unico, vedi tool_isolation.py.
    body = tool_isolation.filter_tools_for_backend(body, "glm")

    # CLAMP max_tokens (2026-07-22): z.ai rifiuta > 32768 con 400 [1210]. Choke-point
    # unico → copre pure glm + mix-ag + mix-gm indipendentemente dalla costruzione body.
    body = clamp_glm_max_tokens(body, log_fn=log_fn)

    # SHRINK TESTO PREVENTIVO: se il body supera il target, riduce il contesto
    # prima di chiamare z.ai. GLM non risponde sempre con un 400 leggibile
    # (puo' dare 500 opaco o troncare), quindi la guardia e' preventiva.
    if len(body) > GLM_SHRINK_TARGET_BYTES:
        from context_shrink import shrink_body_to_budget
        _shrunk = await shrink_body_to_budget(body, GLM_SHRINK_TARGET_BYTES)
        if _shrunk is not None and len(_shrunk) < len(body):
            log_fn(f"GLM preventivo shrink {len(body)}b -> {len(_shrunk)}b")
            body = _shrunk

    url = GLM_UPSTREAM + request.path_qs

    for attempt in range(2):
        resp = None
        try:
            est_tokens = _estimate_tokens(body)
            lim_model = upstream_model or model
            budget = (GLM_STREAM_ACQUIRE_CAP_SEC if passthrough
                      else GLM_RETRY_CAP_SEC)
            entry = await GLM_LIMITER.acquire(lim_model, est_tokens,
                                              budget_sec=budget)

            # Passthrough (stream relay): niente timeout totale — total=120
            # taglierebbe lo stream a metà relay (stesso bug del fix 4a256ce
            # su mix-am). sock_read copre gli stall tra chunk.
            if passthrough:
                timeout = ClientTimeout(total=None, sock_connect=15, sock_read=120)
            else:
                # 2026-07-28: era total=120, il piu' stretto dei tre provider. Una
                # generazione non-streaming lunga non manda byte fino alla fine e
                # veniva troncata a 120s (stesso difetto dei 502 su MiniMax).
                timeout = _non_stream_timeout()
            async with _GLM_SEM:
                # FIX: NIENTE `async with session.request(...) as resp` — se la
                # funzione ritorna `resp` da dentro un async with, __aexit__
                # chiama resp.release() PRIMA che il chiamante possa leggere lo
                # stream (bug: connessione chiusa in passthrough mode). Pattern
                # allineato a forward_anthropic/forward_minimax: await esplicito,
                # release manuale nei soli path di retry/errore.
                resp = await session.request(
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
                )

            GLM_LIMITER.record(entry, est_tokens, resp.status < 400)
            if resp.status < 400:
                GLM_LIMITER.on_success()

            if resp.status == 429:
                _retry_after = resp.headers.get("retry-after")
                step = GLM_LIMITER.on_429()
                # senza questo aggancio una quota da ore veniva trattata come un rate limit da secondi
                # e nessuno avvisava l utente, mentre per MiniMax l avviso esisteva gia
                _raw429 = b""  # mai None: classify_429_glm fa slicing sui byte
                try:
                    _raw429 = await resp.read()
                finally:
                    resp.release()
                _kind = classify_429_glm(_raw429)
                log_fn(f"GLM 429 attempt {attempt + 1}: backoff {step}s kind={_kind}")
                debug_catalog.record_event(severity="block", category="glm",
                                            kind=f"glm_429_{_kind}", code=429,
                                            snippet=f"attempt={attempt + 1} backoff={step}s kind={_kind} model={model}")
                if _kind == "quota_5h":
                    glm_alert(f"Quota GLM esaurita (model={lim_model}): {_raw429[:200].decode('utf-8', errors='replace')}")
                    if passthrough:
                        return synthetic_rate_limit(
                            f"GLM quota esaurita, attesa nell'ordine delle ore, non si ritenta (model={lim_model})",
                            retry_after=_retry_after)
                    return _err(429, "rate_limit_error",
                                f"GLM quota esaurita, attesa nell'ordine delle ore, non si ritenta (model={lim_model})",
                                headers={"Retry-After": _retry_after} if _retry_after else None)
                if attempt == 0:
                    await asyncio.sleep(step + random.uniform(0.5, 2))
                    continue
                # 429 su entrambi i tentativi: e' un rate limit REALE, va inoltrato
                # come 429 con il Retry-After dell'upstream. Convertirlo in 502
                # (com'era) fa perdere al client l'informazione su quanto aspettare.
                if passthrough:
                    return synthetic_rate_limit(
                        f"GLM rate limit persistente dopo 2 tentativi (model={lim_model})",
                        retry_after=_retry_after)
                return _err(429, "rate_limit_error",
                            f"GLM rate limit persistente dopo 2 tentativi (model={lim_model})",
                            headers={"Retry-After": _retry_after} if _retry_after else None)
            if resp.status >= 500 and attempt == 0:
                debug_catalog.record_event(severity="error", category="glm",
                                            kind="glm_5xx_retry", code=resp.status,
                                            snippet=f"attempt={attempt + 1} model={model}")
                try:
                    await resp.read()
                finally:
                    resp.release()
                await asyncio.sleep(0.5)
                continue

            # Passthrough: ritorna ClientResponse raw per relay streaming.
            # Connessione volutamente APERTA: la release avviene nel finally
            # di StreamingRelay.relay() dopo aver consumato lo stream.
            if passthrough:
                if model.startswith("glm-5"):
                    raw = await resp.read()
                    resp.release()
                    _dec = raw
                    if raw[:2] == bytes.fromhex("1f8b"):
                        try:
                            _dec = gzip.decompress(raw)
                        except Exception as e:
                            log_fn(f"GLM empty-check gzip fail: {e}")
                    if _glm_is_empty(_dec):
                        log_fn(f"GLM empty-200 attempt {attempt + 1} model={model}")
                        debug_catalog.record_event(
                            severity="error", category="glm",
                            kind="glm_empty_response", code=200,
                            snippet=f"attempt={attempt + 1} model={model} bytes={len(raw)}")
                        if attempt == 0:
                            await asyncio.sleep(0.5 + random.uniform(0, 1))
                            continue
                        return _err(502, "glm_empty_response",
                                    "GLM returned an empty body after 2 attempts",
                                    headers={"x-should-retry": "true"})
                    return buffered_upstream(resp.status, raw, headers=dict(resp.headers))
                return resp

            # Non-passthrough: leggi body, gestisci gzip, ritorna web.Response
            raw = await resp.read()
            resp.release()
            if raw[:2] == b'\x1f\x8b':
                try:
                    raw = gzip.decompress(raw)
                    log_fn(f"GLM gzip decompressed: {len(raw)}b")
                except Exception as e:
                    log_fn(f"GLM gzip decompress failed: {e}")

            headers = dict(resp.headers)
            HOP = frozenset(("transfer-encoding", "connection",
                             "keep-alive", "content-encoding"))
            for k in list(headers.keys()):
                if k.lower() in HOP:
                    headers.pop(k)
            return aiohttp.web.Response(
                body=_rewrite_glm_model(raw, model),
                status=resp.status,
                headers=headers,
            )

        except RateLimitExhausted as e:
            # Fail-fast: mai attese silenziose lato client — 429 subito,
            # il client Anthropic ritenta da solo (x-should-retry).
            log_fn(f"GLM rate-limit fail-fast: {e}")
            debug_catalog.record_event(severity="block", category="glm",
                                        kind="glm_ratelimit_exhausted", code=429,
                                        snippet=f"model={model} {e}")
            if passthrough:
                return synthetic_rate_limit("glm limiter budget exhausted",
                                            retry_after="10")
            return _err(429, "rate_limit_error", "glm limiter budget exhausted",
                        headers={"Retry-After": "10", "x-should-retry": "true"})
        except asyncio.TimeoutError:
            log_fn(f"GLM timeout attempt {attempt + 1}")
            debug_catalog.record_event(severity="error", category="glm",
                                        kind="glm_timeout", snippet=f"attempt={attempt + 1} model={model}")
            if resp is not None:
                try:
                    resp.release()
                except Exception:
                    pass
            if attempt == 0:
                await asyncio.sleep(1)
                continue
        except aiohttp.ClientError as e:
            log_fn(f"GLM client error attempt {attempt + 1}: {e}")
            debug_catalog.record_event(severity="error", category="glm",
                                        kind="glm_client_error", snippet=str(e))
            if resp is not None:
                try:
                    resp.release()
                except Exception:
                    pass
            if attempt == 0:
                await asyncio.sleep(1)
                continue
        except Exception as e:
            log_fn(f"GLM error: {e}")
            debug_catalog.record_event(severity="bug", category="glm",
                                        kind="glm_unexpected_exception", snippet=str(e))
            if resp is not None:
                try:
                    resp.release()
                except Exception:
                    pass

    debug_catalog.record_event(severity="error", category="glm",
                                kind="glm_exhausted", code=502, snippet=f"model={model}")
    return _err(502, "glm_exhausted", "GLM exhausted after 2 attempts")


def _rewrite_glm_model(raw: bytes, orig_model: str) -> bytes:
    """AQ-FIX1: riscrive 'model' nel body della risposta GLM con il modello
    originale richiesto dal client (non il tier effettivo, es. 'glm-5.2').
    Gestisce JSON non-streaming e SSE (data: JSON lines)."""
    try:
        decoded = raw.decode("utf-8")
    except Exception:
        return raw

    # Caso 1: SSE (righe che iniziano con "data: ")
    if "data: " in decoded:
        lines = decoded.split("\n")
        rewritten = []
        for line in lines:
            if line.startswith("data: "):
                json_str = line[6:]  # togli "data: "
                try:
                    obj = json.loads(json_str)
                    if "model" in obj:
                        obj["model"] = orig_model
                    rewritten.append("data: " + json.dumps(obj, ensure_ascii=False))
                except Exception:
                    rewritten.append(line)
            else:
                rewritten.append(line)
        try:
            return "\n".join(rewritten).encode("utf-8")
        except Exception:
            return raw

    # Caso 2: JSON non-streaming
    try:
        obj = json.loads(decoded)
        if "model" in obj:
            obj["model"] = orig_model
            return json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except Exception:
        pass

    return raw


# Margine di sicurezza sulla stima che alimenta il rate limiter. Sovrastimare
# e' prudente (si chiedono piu' slot del necessario, quindi meno 429),
# sottostimare no. Il valore era cablato come "* 1.2" senza spiegazione, ed era
# l'unica differenza con la stima di qwen_backend: da qui la divergenza del 20%
# fra i due backend, rimasta aperta perche' non si sapeva quale delle due fosse
# giusta. Ora la fonte e' token_counter, che riporta misure reali (glm 4.06
# byte/token), e il margine resta ma è dichiarato.
_MARGINE_STIMA = 1.10


def _estimate_tokens(data: bytes) -> int:
    """Stima i token di un body per il rate limiter, con margine prudenziale.

    Usa i byte/token misurati in token_counter invece di un divisore cablato:
    per GLM la misura e' 4.06 byte/token. La vecchia formula divideva di fatto
    per 3.33 (4 / 1.2), sovrastimando del 20% rispetto alla misura.
    """
    try:
        from token_counter import bytes_per_token
        divisore = bytes_per_token("glm")
        return max(1, int(len(data) / divisore * _MARGINE_STIMA))
    except Exception:
        # Fallback identico al comportamento storico: mai lasciare il limiter
        # senza una stima, e in caso di dubbio sovrastimare.
        try:
            return max(1, int(len(data) / 4 * 1.2))
        except Exception:
            return 1
