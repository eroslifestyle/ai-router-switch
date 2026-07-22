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
import gzip
import json
import os
import random
import subprocess
import time
import aiohttp
import aiohttp.web
from aiohttp import ClientTimeout
from collections import deque
from pathlib import Path
from typing import Optional

import tool_isolation
import debug_catalog

# Z.ai Anthropic-compatible endpoint
# GLM_API_BASE (env da glm.env) ha priorità; fallback su hardcoded
GLM_UPSTREAM = os.environ.get(
    "GLM_API_BASE",
    os.environ.get("GLM_UPSTREAM", "https://api.z.ai/api/anthropic")
)

# Endpoint per generazione media
GLM_IMAGE_ENDPOINT = "https://api.z.ai/api/paas/v4/images/generations"
GLM_VIDEO_ENDPOINT = "https://api.z.ai/api/paas/v4/videos/generations"

# Endpoint per generazione media
GLM_IMAGE_ENDPOINT = "https://api.z.ai/api/paas/v4/images/generations"
GLM_VIDEO_ENDPOINT = "https://api.z.ai/api/paas/v4/videos/generations"

# Tier costanti
GLM_TIER_TOP = "TOP"
GLM_TIER_TURBO = "TURBO"
GLM_TIER_MID = "MID"
GLM_TIER_VISION = "VISION"      # glm-4.6V (visione base)
GLM_TIER_MULTIMODAL = "MULTIMODAL"  # glm-5V-Turbo (visione + video)

# Marker per il proxy: task complesso in fascia peak → Anthropic esegue direttamente
_ANTHROPIC_BLOCKED = "__ANTHROPIC_BLOCKED__"

# Modello GLM per ogni tier
GLM_MODEL_FOR_TIER = {
    GLM_TIER_TOP: "glm-5.2",
    GLM_TIER_TURBO: "glm-5-turbo",
    GLM_TIER_MID: "glm-4.7",
    GLM_TIER_VISION: "glm-4.6V",
    GLM_TIER_MULTIMODAL: "glm-5V-Turbo",
}

# ── THINK-ACT-VERIFY constants ──────────────────────────────────────────────────
GLM_THINK_VERIFY_MODEL = "glm-5.2"
GLM_THINK_TIMEOUT_SEC = 60

# Context limit sicuri per ogni modello (input tokens, con headroom)
# Source: piano verificato con contesto reale
_GLM_CONTEXT_LIMITS = {
    "glm-5.2": 900_000,     # 1M ctx, 100K headroom
    "glm-5-turbo": 180_000,  # 200K ctx, 20K headroom
    "glm-4.7": 115_000,      # 128K ctx, 13K headroom
    "glm-4.6V": 120_000,    # 131K ctx, ~11K headroom
    "glm-5V-Turbo": 180_000, # ~200K ctx, ~20K headroom
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

    key = os.environ.get("GLM_API_KEY", "") or os.environ.get("ZAI_API_KEY", "")
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
            wait += random.uniform(0.05, 0.5)
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
        import subprocess
        subprocess.Popen(["notify-send", "-u", "normal", "-t", "20000",
                          "GLM Quota", msg[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ── Multimodal detection ───────────────────────────────────────────────────────

def has_multimodal_content(body: bytes) -> tuple[str, str]:
    """Rileva il tipo di contenuto nel body.

    Ritorna (content_type, detail):
    - ("text", ""): solo testo
    - ("image", ""): immagini presenti
    - ("video", ""): video (frame input)
    - ("pdf", ""): PDF/documenti
    - ("image_gen", ""): richiesta generazione immagine
    - ("video_gen", ""): richiesta generazione video
    """
    try:
        data = json.loads(body)
        messages = data.get("messages", [])

        # Check image blocks in messages
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        t = block.get("type", "")
                        if t == "image":
                            # Check per video frame (base64 source = frame)
                            source = block.get("source", {})
                            if source.get("type") == "base64":
                                return ("video", "")
                            return ("image", "")

        # Check per generazione immagine/video: SOLO via marker esplicito nel
        # body (type: image_generation/video_generation). FIX 2026-07-19:
        # rimosso il matching su substring del NOME tool ("image" in name,
        # "generation" in name, "video" in name) — troppo fragile, causava
        # falsi positivi con QUALSIASI tool disponibile nella richiesta che
        # contenesse quelle sottostringhe (es. mcp__MiniMax__understand_image,
        # presente di default in molte sessioni Claude Code), dirottando OGNI
        # messaggio verso l'endpoint immagini z.ai (senza credito sul piano
        # coding) invece della chat normale — causa root del 429
        # "Insufficient balance" apparentemente casuale e mai loggato
        # (forward_glm_image non logga sul percorso di successo/risposta).
        if '"type": "image_generation"' in body.decode(errors="ignore"):
            return ("image_gen", "")
        if '"type": "video_generation"' in body.decode(errors="ignore"):
            return ("video_gen", "")

        return ("text", "")
    except Exception:
        return ("text", "")


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

    1. Multimodal detection (vision/video) ha priorita assoluta
    2. Size-based tier per text-only
    """
    try:
        content_type, _ = has_multimodal_content(body)

        # Routing basato su tipo contenuto
        if content_type == "video":
            log_fn(f"GLM classify: video detected → MULTIMODAL (glm-5V-Turbo)")
            return GLM_TIER_MULTIMODAL
        if content_type == "image":
            log_fn(f"GLM classify: image detected → VISION (glm-4.6V)")
            return GLM_TIER_VISION

        # Text-only: usa size-based heuristic
        return heuristic_tier(body)
    except Exception:
        return GLM_TIER_MID


def apply_peak_cap(tier: str):
    """Applica il cap peak: TURBO/TOP → MID se in fascia peak.

    VISION e MULTIMODAL non sono mai bloccati (servono per feature specifiche).
    """
    # VISION/MULTIMODAL esenti dal peak cap
    if tier in (GLM_TIER_VISION, GLM_TIER_MULTIMODAL):
        return tier, False
    # Import lazy per evitare circular
    import peak_scheduler as _ps
    if _ps.should_block_glm_model(tier):
        return GLM_TIER_MID, True
    return tier, False


def resolve_glm_upstream_model(tier: str) -> str:
    """Mappa una tier key (TOP/MID/TURBO/VISION/MULTIMODAL) al modello GLM
    reale da inviare a z.ai. Se `tier` è già un nome modello reale (non una
    tier key nota), lo ritorna invariato (fallback sicuro)."""
    return GLM_MODEL_FOR_TIER.get(tier, tier)


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
# ── THINK-ACT-VERIFY ───────────────────────────────────────────────────────────

def _extract_text(content) -> str:
    """Estrae testo semplice da un campo `content` Anthropic (stringa, o lista
    di content-block dove tiene solo i blocchi di tipo 'text' — ignora
    tool_use/tool_result/image, che il THINK/VERIFY non deve vedere)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def build_glm_think_body(orig: dict, content_type: str) -> bytes:
    """Costruisce il body per il THINK con GLM-5.2.

    Chiede al modello di analizzare il task e produrre un piano di azione.

    FIX 2026-07-19 (400 ricorrente sul background THINK): `system` va inviato
    come campo TOP-LEVEL, non come messaggio `role: system` dentro `messages`
    — l'endpoint z.ai è Anthropic-compatible e rigetta quel ruolo con 400.
    Un solo messaggio `user` evita anche il vincolo di alternanza dei ruoli;
    se il content dell'ultimo messaggio è a blocchi (tool/immagine, non
    stringa) l'estrazione testuale garantisce comunque un messaggio non vuoto."""
    system = """Sei un orchestrator AI. Analizza la richiesta e produci un piano di azione.
Il piano deve specificare:
1. Tipo di task (coding, reasoning, creative, vision, etc.)
2. Modello consigliato per l'esecuzione
3. Approccio principale

Rispondi SOLO con il piano, nient'altro."""

    messages = orig.get("messages", [])

    history_lines = []
    for msg in messages[-6:-1]:
        text = _extract_text(msg.get("content", ""))
        if text:
            history_lines.append(f"[{msg.get('role', 'user')}] {text[:500]}")

    last_text = _extract_text(messages[-1].get("content", "")) if messages else ""
    if not last_text:
        last_text = f"(contenuto {content_type or 'non testuale'})"

    user_text = ""
    if history_lines:
        user_text = "\n".join(history_lines) + "\n\n"
    user_text += f"Analizza questo task: {last_text[:2000]}"

    think_body = {
        "model": GLM_THINK_VERIFY_MODEL,
        "system": system,
        "messages": [{"role": "user", "content": user_text[:5000]}],
        "max_tokens": 1000,
    }

    return json.dumps(think_body).encode()


def build_glm_verify_body(orig: dict, plan: str, act_output: str) -> bytes:
    """Costruisce il body per il VERIFY con GLM-5.2.

    Chiede al modello di verificare che l'output sia corretto.
    Stesso fix di build_glm_think_body: `system` top-level, non in messages."""
    system = """Sei un verifier AI. Verifica che l'output prodotto sia corretto e completo.
Se l'output è coerente col piano → rispondi SOLO con: VERIFIED
Se l'output è INCOERENTE col piano o ha errori → rispondi SOLO con: INCOERENTE: [motivo breve]"""

    verify_body = {
        "model": GLM_THINK_VERIFY_MODEL,
        "system": system,
        "messages": [
            {"role": "user", "content": "Piano:\n" + plan + "\n\nOutput:\n" + act_output[:3000]},
        ],
        "max_tokens": 500,
    }

    return json.dumps(verify_body).encode()


_background_tasks = set()


def _fire_and_forget(coro):
    """Lancia una coroutine in background senza attenderla, tenendo un
    riferimento nel set _background_tasks per evitare che asyncio la
    garbage-collecti prima del completamento (gotcha noto di create_task)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _glm_think_verify_background(request, body, orig, content_type, act_raw, session, log_fn):
    """THINK + VERIFY eseguiti DOPO aver già risposto al client (fire-and-forget).
    Servono solo per logging/osservabilità — il loro esito non modifica in alcun
    modo la risposta già inviata (vedi glm_think_act_verify: ACT risponde subito)."""
    think_body = build_glm_think_body(orig, content_type)
    log_fn(f"GLM THINK: analisi con {GLM_THINK_VERIFY_MODEL}")
    think_plan = ""
    try:
        think_resp = await forward_glm(request, think_body, session, GLM_THINK_VERIFY_MODEL, log_fn)
        if think_resp.status >= 400:
            log_fn(f"GLM THINK fail {think_resp.status}")
        else:
            think_raw = think_resp.body if isinstance(think_resp.body, (bytes, bytearray)) else b""
            try:
                think_data = json.loads(think_raw)
                think_plan = think_data.get("content", [{}])[0].get("text", "") if think_data.get("content") else ""
            except Exception:
                pass
    except Exception as e:
        log_fn(f"GLM THINK EXC: {e}")
    log_fn(f"GLM THINK done: plan={len(think_plan)}c")

    log_fn(f"GLM VERIFY: verifica con {GLM_THINK_VERIFY_MODEL}")
    try:
        verify_body = build_glm_verify_body(orig, think_plan, act_raw.decode(errors="ignore")[:5000])
        verify_resp = await forward_glm(request, verify_body, session, GLM_THINK_VERIFY_MODEL, log_fn)
        if verify_resp.status < 400:
            verify_raw = verify_resp.body if isinstance(verify_resp.body, (bytes, bytearray)) else b""
            try:
                verify_data = json.loads(verify_raw)
                verify_text = verify_data.get("content", [{}])[0].get("text", "") if verify_data.get("content") else ""
                log_fn(f"GLM VERIFY: {verify_text[:100]}")
            except Exception:
                pass
    except Exception as e:
        log_fn(f"GLM VERIFY EXC: {e}")


async def glm_think_act_verify(request, body: bytes, session, log_fn=print, relay=None):
    """Esegue GLM: ACT risponde SUBITO al client (fix 2026-07-19 — prima
    THINK/ACT/VERIFY erano sequenziali e bloccanti, 10-20s+ prima del primo
    byte, causavano retry-storm lato client per timeout percepito).
    THINK e VERIFY girano in background dopo la risposta, solo per log.
    """
    try:
        orig = json.loads(body)
    except Exception:
        orig = {}

    content_type, _ = has_multimodal_content(body)

    # Per generazione media, skip e vai diretto (invariato)
    if content_type in ("image_gen", "video_gen"):
        if content_type == "image_gen":
            return await forward_glm_image(request, body, session, log_fn)
        return await forward_glm_video(request, body, session, log_fn)

    # ACT: esegue e risponde subito. Il tier/modello non dipende dal THINK
    # (che non alimenta act_body), quindi non c'è motivo di aspettarlo.
    tier = await classify_tier(body, request, session, log_fn)
    eff_model, capped = apply_peak_cap(tier)
    if capped and is_glm_body_too_large(body, resolve_glm_upstream_model(eff_model)):
        # Peak-cap hole: pure glm è no-fallback — declassare a un modello con
        # ctx più piccolo del body = 400 garantito. Meglio pagare il tier alto.
        log_fn(f"GLM peak-cap bypass: body oltre ctx di {eff_model}, resto su {tier}")
        eff_model = tier
    real_model = resolve_glm_upstream_model(eff_model)
    act_body = set_body_model(body, real_model)
    # Isolamento tool: gestito dentro forward_glm (choke-point tool_isolation).

    log_fn(f"GLM ACT: esecuzione con {real_model} (tier={eff_model})")

    # STREAMING PASSTHROUGH (fix 2026-07-22): se il client chiede stream,
    # relay diretto dello stream upstream — prima il body veniva bufferizzato
    # per intero (primo byte al client = fine generazione → il client andava
    # in timeout e ritentava sui lavori lunghi). THINK/VERIFY background skip
    # dichiarato: sul path stream non abbiamo il body in memoria.
    want_stream = bool(orig.get("stream")) if isinstance(orig, dict) else False
    if want_stream and relay is not None:
        act_resp = await forward_glm(request, act_body, session,
                                     orig.get("model") or real_model, log_fn,
                                     passthrough=True, upstream_model=real_model)
        if isinstance(act_resp, aiohttp.web.Response):
            # errore sintetico (key missing / 429 finale): già web.Response
            return act_resp
        if act_resp.status >= 400:
            log_fn(f"GLM ACT fail {act_resp.status} (stream path)")
            raw_err = await act_resp.read()
            status_err = act_resp.status
            act_resp.release()
            return aiohttp.web.Response(body=raw_err, status=status_err,
                                        content_type="application/json")
        log_fn("GLM THINK/VERIFY: skip su stream passthrough (body non bufferizzato)")
        return await relay(act_resp,
                           extra_headers={"x-ai-verified": f"glm({real_model})"})

    act_resp = await forward_glm(request, act_body, session,
                                  orig.get("model") or real_model, log_fn,
                                  upstream_model=real_model)

    if act_resp.status >= 400:
        log_fn(f"GLM ACT fail {act_resp.status}")
        return act_resp

    # forward_glm ritorna una web.Response con il body già in memoria (.body).
    act_raw = act_resp.body if isinstance(act_resp.body, (bytes, bytearray)) else b""

    # THINK + VERIFY in background: non bloccano la risposta già pronta.
    _fire_and_forget(_glm_think_verify_background(
        request, body, orig, content_type, act_raw, session, log_fn))

    return act_resp


# ── Image & Video Generation ──────────────────────────────────────────────────

async def forward_glm_image(request, body: bytes, session, log_fn=print):
    """Genera immagine via /v4/images/generations."""
    key = await get_glm_key()
    if not key:
        return aiohttp.web.Response(status=502, text="GLM key missing")

    try:
        data = json.loads(body)
        prompt = data.get("prompt", "")
        size = data.get("size", "1024x1024")

        payload = {
            "model": "glm-image",
            "prompt": prompt,
            "size": size,
        }

        timeout = ClientTimeout(total=120)
        async with session.request(
            method="POST",
            url=GLM_IMAGE_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
            ssl=True,
        ) as resp:
            raw = await resp.read()
            return aiohttp.web.Response(
                body=raw,
                status=resp.status,
                content_type="application/json",
            )
    except Exception as e:
        log_fn(f"GLM image generation EXC: {e}")
        return aiohttp.web.Response(status=502, text=f"GLM image error: {e}")


async def forward_glm_video(request, body: bytes, session, log_fn=print):
    """Genera video via CogVideoX-3."""
    key = await get_glm_key()
    if not key:
        return aiohttp.web.Response(status=502, text="GLM key missing")

    try:
        data = json.loads(body)
        prompt = data.get("prompt", "")
        image_url = data.get("image_url")
        quality = data.get("quality", "quality")
        size = data.get("size", "1920x1080")
        fps = data.get("fps", 30)
        with_audio = data.get("with_audio", False)

        payload = {
            "model": "cogvideox-3",
            "prompt": prompt,
            "quality": quality,
            "size": size,
            "fps": fps,
            "with_audio": with_audio,
        }
        if image_url:
            payload["image_url"] = image_url

        timeout = ClientTimeout(total=300)  # Video takes longer
        async with session.request(
            method="POST",
            url=GLM_VIDEO_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
            ssl=True,
        ) as resp:
            raw = await resp.read()
            return aiohttp.web.Response(
                body=raw,
                status=resp.status,
                content_type="application/json",
            )
    except Exception as e:
        log_fn(f"GLM video generation EXC: {e}")
        return aiohttp.web.Response(status=502, text=f"GLM video error: {e}")


async def route_glm_request(request, body: bytes, session, log_fn=print):
    """Route la richiesta all'endpoint appropriato in base al tipo."""
    content_type, _ = has_multimodal_content(body)

    if content_type == "image_gen":
        log_fn(f"GLM route: image generation → /v4/images/generations")
        return await forward_glm_image(request, body, session, log_fn)

    if content_type == "video_gen":
        log_fn(f"GLM route: video generation → /v4/videos/generations")
        return await forward_glm_video(request, body, session, log_fn)

    # Per tutto il resto, usa il flow standard
    return None


# ── Forward GLM ───────────────────────────────────────────────────────────────

# Semaphore per concorrenza GLM
_GLM_SEM = asyncio.Semaphore(int(os.environ.get("AIROUTER_GLM_SEMAPHORE", "8")))


async def forward_glm(request, body: bytes, session, model: str,
                      log_fn=print, passthrough: bool = False,
                      upstream_model: str = ""):
    """Invia request al backend GLM con retry loop 2 tentativi (R3-#6).

    Args:
        passthrough: se True, ritorna la ClientResponse raw (per relay streaming).
                     Il caller deve chiamare .release() o consumare il body.
                     se False (default), legge il body e ritorna web.Response.
    """

    key = await get_glm_key()
    if not key:
        log_fn("GLM: chiave assente (GLM_API_KEY o secrets.sh glm.api_key)")
        return aiohttp.web.Response(status=502, text="GLM key missing")

    # ISOLAMENTO TOOL (2026-07-19): choke-point unico, vedi tool_isolation.py.
    body = tool_isolation.filter_tools_for_backend(body, "glm")

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
                timeout = ClientTimeout(total=120)
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
                step = GLM_LIMITER.on_429()
                log_fn(f"GLM 429 attempt {attempt + 1}: backoff {step}s")
                debug_catalog.record_event(severity="block", category="glm",
                                            kind="glm_429_backoff", code=429,
                                            snippet=f"attempt={attempt + 1} backoff={step}s model={model}")
                try:
                    await resp.read()
                finally:
                    resp.release()
                if attempt == 0:
                    await asyncio.sleep(step + random.uniform(0.5, 2))
                    continue
                break

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
            return aiohttp.web.Response(
                status=429,
                text='{"type":"error","error":{"type":"rate_limit_error","message":"glm limiter budget exhausted"}}',
                content_type="application/json",
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
    return aiohttp.web.Response(status=502, text="GLM exhausted after 2 attempts")


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


def _estimate_tokens(data: bytes) -> int:
    """Stima token da bytes (1 token ≈ 4 char + overhead)."""
    try:
        return max(1, int(len(data) / 4 * 1.2))
    except Exception:
        return 1
