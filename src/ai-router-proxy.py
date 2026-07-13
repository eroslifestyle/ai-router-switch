#!/usr/bin/env python3
"""
AI Router Proxy — switcher davanti a Claude Code.

Quattro modalità (file ~/.claude/ai-router-mode):
  - anthropic   : tutto diretto a api.anthropic.com
  - minimax     : tutto diretto a api.minimaxi.chat/anthropic
  - mixed       : Anthropic primario; su 429/5xx/errore -> fallback MiniMax (bidir)
  - inverse     : MiniMax orchestra + esegue. Anthropic verifica (T2) i task critici.
                  Anthropic interviene direttamente come esecutore dopo 2 fallimenti
                  (resilienza). Task non-critici -> MiniMax diretto (streaming).


Claude Code punta qui: ANTHROPIC_BASE_URL=http://127.0.0.1:8787
Gestisce streaming SSE. Backend diretto (nessun proxy intermedio).
"""
import asyncio
import gzip
import json
import os
import random
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout, TCPConnector

# Context window fix imports (LAYER 1: intelligent rewrite)
from token_counter import estimate_tokens, count_tokens
from model_context_map import get_safe_input_limit, get_context_limit, get_summary_budget
from context_rewrite import rewrite_for_context
from summarizer import summarize_old_messages


# ═══════════════════════════════════════════════════════════════════════════════
# SISTEMA DI DEBUG — cattura errori upstream in chiaro ( mai più gzip illeggibile)
# ════════════════════════════════════════════════════════════════════════════════

def _decompress_upstream(raw: bytes, content_encoding: str = "") -> str:
    """Decomprime gzip/brotli/deflate un body upstream in testo leggibile UTF-8."""
    if not raw:
        return ""
    try:
        enc = (content_encoding or "").lower()
        if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "br" in enc or "brotli" in enc:
            try:
                import brotli
                raw = brotli.decompress(raw)
            except Exception:
                pass
        elif "deflate" in enc:
            import zlib
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                raw = zlib.decompress(raw)
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")


def _body_has_images(data: dict) -> bool:
    """True se il body request contiene blocchi immagine."""
    for m in (data or {}).get("messages", []):
        c = m.get("content", [])
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "image":
                    return True
    return False


def _orig_flags(orig: dict | None) -> dict:
    """Estrae flags diagnostici dal body richiesta originale."""
    if not orig:
        return {}
    msgs = orig.get("messages", [])
    img_count = 0
    for m in msgs:
        c = m.get("content", [])
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "image":
                    img_count += 1
    return {
        "msg_count": len(msgs),
        "has_tools": bool(orig.get("tools")),
        "has_images": img_count > 0,
        "has_thinking": bool(orig.get("thinking")),
        "cache_control_count": img_count,
        "system_is_list": isinstance(orig.get("system"), list),
    }


# ── Debug event store ──────────────────────────────────────────────────────────

_DEBUG_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # src/ai-router-proxy.py → project root
_DEBUG_LOGS_DIR = _DEBUG_PROJECT_ROOT / "logs"
_DEBUG_LOGS_DIR.mkdir(exist_ok=True)
_DEBUG_JSONL = _DEBUG_LOGS_DIR / "debug-errors.jsonl"
_DEBUG_LAST_REQ = _DEBUG_LOGS_DIR / "debug-last-request.json"
_DEBUG_LAST_SENT = _DEBUG_LOGS_DIR / "debug-last-sent.json"
_DEBUG_REPAIR_TRACE = _DEBUG_LOGS_DIR / "debug-repair-trace.json"
DEBUG_EVENTS: deque = deque(maxlen=100)
# Ring buffer: ultime 50 analisi del body SENT ad Anthropic
SENT_ANALYSIS: deque = deque(maxlen=50)


def _analyze_body_structure(body: "dict | bytes") -> dict:
    """Diagnostica profondo di un body request — rileva orfani, anomalie strutturali.

    Regola orfano (come Anthropic): un tool_result in messages[i] è valido SOLO se
    messages[i-1] (role=assistant) contiene un tool_use con lo stesso id.
    Se è in messages[0] o il precedente non ha quel tool_use → orfano."""
    size_bytes = len(body) if isinstance(body, bytes) else len(json.dumps(body).encode())
    data = json.loads(body) if isinstance(body, bytes) else body
    msgs = data.get("messages", []) or []

    def _block_types(msg: dict) -> list:
        c = msg.get("content")
        if isinstance(c, list):
            return [b.get("type", "?") for b in c if isinstance(b, dict)]
        return []

    first = msgs[0] if msgs else {}
    last = msgs[-1] if msgs else {}

    # ── Orphan tool_results: regola "messaggio immediatamente precedente" ──
    orphan_tool_results = []
    tool_use_ids = []
    tool_result_ids = []
    for i, m in enumerate(msgs):
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    t = b.get("type", "")
                    if t == "tool_use":
                        tool_use_ids.append(b.get("id", ""))
                    elif t == "tool_result":
                        tool_result_ids.append(b.get("tool_use_id", ""))
                        tid = b.get("tool_use_id", "")
                        # Controlla: messages[i-1] deve essere assistant con tool_use[tid]
                        prev = msgs[i - 1] if i > 0 else None
                        valid = (
                            prev is not None
                            and prev.get("role") == "assistant"
                            and any(
                                isinstance(pb, dict) and pb.get("type") == "tool_use" and pb.get("id") == tid
                                for pb in (prev.get("content") or [])
                                if isinstance(pb, dict)
                            )
                        )
                        orphan_tool_results.append({
                            "msg_index": i,
                            "tool_use_id": tid,
                            "reason": "first_message" if i == 0 else ("no_prior_tool_use" if not valid else "valid"),
                        })

    # Filtra: considera orfano solo se reason != valid
    orphan_tool_results = [o for o in orphan_tool_results if o.get("reason") != "valid"]

    # ── Dangling tool_uses: tool_use senza tool_result nel messaggio SUCCESSIVO ──
    dangling_tool_uses = []
    for i, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tid = b.get("id", "")
                # Cerca se il SUCCESSIVO messaggio ha tool_result per questo tid
                next_m = msgs[i + 1] if i + 1 < len(msgs) else None
                has_result = (
                    next_m is not None
                    and isinstance(next_m.get("content"), list)
                    and any(
                        isinstance(rb, dict) and rb.get("type") == "tool_result" and rb.get("tool_use_id") == tid
                        for rb in next_m.get("content")
                    )
                )
                if not has_result:
                    dangling_tool_uses.append({"msg_index": i, "tool_use_id": tid})

    # ── role=system dentro messages (anomalo per Anthropic) ──
    role_system_in_messages = sum(1 for m in msgs if m.get("role") == "system")

    # ── immagini ──
    has_images = False
    for m in msgs:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "image":
                    has_images = True
                    break
        if has_images:
            break

    return {
        "size_bytes": size_bytes,
        "msg_count": len(msgs),
        "first_msg": {"role": first.get("role"), "block_types": _block_types(first)},
        "last_msg": {"role": last.get("role"), "block_types": _block_types(last)},
        "role_system_in_messages": role_system_in_messages,
        "orphan_tool_results": orphan_tool_results,
        "dangling_tool_uses": dangling_tool_uses,
        "has_images": has_images,
        "tool_use_ids": len(tool_use_ids),
        "tool_result_ids": len(tool_result_ids),
    }


def _rotated_jsonl_path() -> Path:
    """Ritorna il path del JSONL: .1 se .0 supera 10MB."""
    p = _DEBUG_JSONL
    try:
        if p.exists() and p.stat().st_size > 10 * 1024 * 1024:
            rot = p.with_suffix(".jsonl.1")
            try:
                rot.unlink()
            except Exception:
                pass
            p.rename(rot)
    except Exception:
        pass
    return p


def debug_capture(*, kind: str, request=None, fp: str = "", client_model: str = "",
                  upstream_model: str = "", status: int | None = None, stage: str = "",
                  upstream_status: int | None = None, upstream_raw: bytes = b"",
                  upstream_encoding: str = "", sent_bytes: int = 0, orig: dict | None = None,
                  sent_analysis: dict | None = None, note: str = "") -> None:
    """Registra un evento di errore in RAM + JSONL. Decomprime il body upstream.
    Mai loggare Authorization / x-api-key / Bearer token."""
    try:
        err_text = _decompress_upstream(upstream_raw, upstream_encoding)
        flags = _orig_flags(orig)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": kind,
            "fp": fp,
            "mode": get_file_mode(),
            "path": getattr(request, "path", "") if request else "",
            "client_model": client_model,
            "upstream_model": upstream_model,
            "status": status,
            "stage": stage,
            "upstream_status": upstream_status,
            "upstream_error": err_text[:2000],
            "sent_bytes": sent_bytes,
            "sent_analysis": sent_analysis,
            "flags": flags,
            "note": note,
        }
        DEBUG_EVENTS.append(record)
        p = _rotated_jsonl_path()
        try:
            with open(p, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if orig:
            req_copy = dict(orig)
            for m in req_copy.get("messages", []):
                c = m.get("content", [])
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "image":
                            d = b.get("data", "")
                            if len(d) > 200:
                                b["data"] = d[:200] + f"... [TRUNCATED {len(d) - 200} chars]"
            try:
                with open(_DEBUG_LAST_REQ, "w") as f:
                    json.dump(req_copy, f, ensure_ascii=False)
            except Exception:
                pass
    except Exception:
        pass


async def debug_errors(request) -> web.Response:
    """GET /debug/errors?n=20 → ultimi N eventi dal ring buffer (JSON)."""
    n = int(request.query.get("n", "20"))
    return web.json_response(list(DEBUG_EVENTS)[-n:])


async def debug_last(request) -> web.Response:
    """GET /debug/last → ultimo evento formattato leggibile (text/plain)."""
    if not DEBUG_EVENTS:
        return web.Response(text="No errors captured yet.", content_type="text/plain")
    ev = DEBUG_EVENTS[-1]
    lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in ev.items()]
    return web.Response(text="\n".join(lines), content_type="text/plain")


async def debug_stats(request) -> web.Response:
    """GET /debug/stats → conteggio per status/stage/kind."""
    from collections import Counter
    c_kind = Counter(e.get("kind") for e in DEBUG_EVENTS)
    c_stage = Counter(e.get("stage") for e in DEBUG_EVENTS)
    c_upstream = Counter(str(e.get("upstream_status")) for e in DEBUG_EVENTS)
    return web.json_response({
        "total": len(DEBUG_EVENTS),
        "by_kind": dict(c_kind),
        "by_stage": dict(c_stage),
        "by_upstream_status": dict(c_upstream),
    })


async def debug_trace(request) -> web.Response:
    """GET /debug/trace → confronto orig_analysis vs sent_analysis dell'ultimo errore.
    Mostra il body ORIGINALE vs quello EFFETTIVAMENTE inviato ad Anthropic,
    più il repair-trace se presente."""
    # Ultimo evento
    ev = DEBUG_EVENTS[-1] if DEBUG_EVENTS else None
    # Ultimo body sent salvato
    last_sent = None
    try:
        if _DEBUG_LAST_SENT.exists():
            last_sent = json.loads(_DEBUG_LAST_SENT.read_text(encoding="utf-8"))
    except Exception:
        pass
    # Repair trace se esiste
    repair_trace = None
    try:
        if _DEBUG_REPAIR_TRACE.exists():
            repair_trace = json.loads(_DEBUG_REPAIR_TRACE.read_text(encoding="utf-8"))
    except Exception:
        pass
    # Ultime 10 analisi dal ring buffer
    recent_analysis = list(SENT_ANALYSIS)[-10:]

    resp = {
        "last_event": ev,
        "last_sent": last_sent,
        "repair_trace": repair_trace,
        "recent_sent_analysis": recent_analysis,
    }
    return web.json_response(resp)


# ═══════════════════════════════════════════════════════════════════════════════
# Aggiunge: OAuth self-test al boot, modalità DEGRADED quando OAuth manca,
# auto-recovery quando l'utente fa `claude login` (refresh creds.json),
# crash dump su SIGTERM, heartbeat watchdog per freeze-watchdog esterno.
sys.path.insert(0, str(Path.home() / ".claude" / "scripts"))
try:
    from ai_router_resilience import Resilience
    _RESILIENCE_AVAILABLE = True
except Exception as _rexc:
    Resilience = None
    _RESILIENCE_AVAILABLE = False
    log_bootstrap = lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", file=sys.stderr)
    log_bootstrap(f"WARN: resilience module non disponibile: {_rexc}")

# ── Config ────────────────────────────────────────────────────────────────
LISTEN_HOST = os.environ.get("AIROUTER_LISTEN_HOST", "127.0.0.1")  # ponytail: 127.0.0.1 default; export AIROUTER_LISTEN_HOST=0.0.0.0 per Tailscale/VSCode remoto
# Il router E' il punto unico su :8787 (dove tutte le app gia' puntano).
# Backend DIRETTO alle API ufficiali (nessun proxy intermedio).
LISTEN_PORT = int(os.environ.get("AIROUTER_PORT", "8787"))

ANTHROPIC_UPSTREAM = os.environ.get("AIROUTER_ANTHROPIC_UPSTREAM", "https://api.anthropic.com")
# MiniMax: endpoint Anthropic-compat ufficiale, diretto.
MINIMAX_UPSTREAM = os.environ.get("AIROUTER_MINIMAX_UPSTREAM", "https://api.minimaxi.chat/anthropic")
MINIMAX_MODEL = os.environ.get("AIROUTER_MINIMAX_MODEL", "MiniMax-M3")
# mode=minimax: M3 ORCHESTRA (produce il piano, MAI esegue), i modelli inferiori
# eseguono. L'orchestratore è M3; gli executor ammessi sono i modelli inferiori.
MINIMAX_ORCHESTRATOR_MODEL = os.environ.get("AIROUTER_MINIMAX_ORCHESTRATOR", "MiniMax-M3")
MINIMAX_EXECUTORS = set(
    m.strip() for m in os.environ.get(
        "AIROUTER_MINIMAX_EXECUTORS", "MiniMax-M2,MiniMax-M2.5,MiniMax-M2.7"
    ).split(",") if m.strip()
)
# Decisione utente 2026-07-03 (mixed): l'ACT gira sull'executor CODE MiniMax
# (default M2.7), NON su M3. M3 è il 2° tentativo. Catena completa esecuzione:
# M2.7 -> M3 -> (2 fail MiniMax) -> Haiku -> modello selezionato dall'utente.
# Gli orchestratori Anthropic (Fable/Opus/Sonnet) non eseguono MAI al 1° colpo.
MIXED_EXECUTOR_MODEL = os.environ.get("AIROUTER_MIXED_EXECUTOR", "MiniMax-M2.7")
# Feature flag redesign 4 modalità 2026-07-01: se =1 (default), mixed/inverse
# usano le NUOVE pipeline gerarchiche (Anthropic THINK+CONTROLLA+M3 ACT / M3 THINK
# + Opus OPPOSE + M3 ACT) per TUTTE le /v1/messages — abroga la distinzione T0/T1/T2
# che escludeva le richieste agentiche. =0 fallback al comportamento legacy T2-only.
NEW_PIPELINE = os.environ.get("AIROUTER_NEW_PIPELINE", "1") == "1"
INVERSE_REVIEW_MAX_ITER = int(os.environ.get("AIROUTER_INVERSE_REVIEW_MAX_ITER", "2"))
# FIX bug hang inverse: cap di tempo totale sul loop OPPOSE. Sotto backoff 429
# le chiamate Anthropic possono durare 50s+; senza budget il client scade (>60s).
INVERSE_REVIEW_BUDGET_SEC = int(os.environ.get("AIROUTER_INVERSE_BUDGET_SEC", "25"))
# Modello giudice per la verifica T2 in modalità inverse (Claude Opus).
VERIFY_MODEL = os.environ.get("AIROUTER_VERIFY_MODEL", "claude-opus-4-8")
VALID_MODES = ("anthropic", "minimax", "mixed", "inverse",
               "glm", "glm-minimax", "anthropic-glm")

# ── GLM/z.ai backend (Anthropic-compatible endpoint) ─────────────────────────
# 3 modalità GLM (2026-07-10): glm (solo GLM, 5.2 orchestra tiering),
# glm-minimax (GLM-5.2 THINK → MiniMax ACT → GLM verify), anthropic-glm
# (Anthropic THINK → GLM tiered ACT → Anthropic verify T2). Logica oraria peak
# (14-18 Asia/Shanghai) per contenere i costi. Moduli: glm_backend + peak_scheduler.
# Import difensivo: se i moduli mancano, le 4 modalità storiche restano intatte.
try:
    import glm_backend as _glm
    import peak_scheduler as _peak
    GLM_AVAILABLE = True
except Exception as _glm_e:  # noqa: BLE001
    _glm = None
    _peak = None
    GLM_AVAILABLE = False

MODE_FILE = Path.home() / ".claude" / "ai-router-mode"
KEY_FILE = Path.home() / ".claude" / "secrets" / "secrets.sh"
LOG_FILE = Path.home() / ".claude" / "logs" / "ai-router.log"
SIDECAR = Path.home() / ".claude" / "logs" / "router-model-map.jsonl"
USAGE_SIDECAR = Path.home() / ".claude" / "logs" / "router-usage.jsonl"

# status code che in 'mixed' fanno scattare il fallback a MiniMax
# Fallback attivo su: 5xx/529 (server/overload) + 4xx eccetto 400/404 (client error puro).
# 401/403 (auth/billing) -> fallback a MiniMax cosi' l'utente non resta bloccato.
# 429 (rate limit) -> fallback per non aspettare.
FALLBACK_STATUSES = {401, 403, 408, 409, 413, 429, 500, 502, 503, 504, 529}
# Rate-limit redesign 2026-07-04: il 429 MiniMax NON è più un fallback verso
# Anthropic — lo gestisce MinimaxRateLimiter (polling+backoff dentro
# forward_minimax). Questo set va usato nei check post-chiamata MiniMax;
# FALLBACK_STATUSES (con 429) resta SOLO per i check lato Anthropic.
MINIMAX_FALLBACK_STATUSES = FALLBACK_STATUSES - {429}

# ── MiniMax rate limits ufficiali (platform.minimax.io/docs/guides/rate-limits,
#    2026-07): (RPM, TPM input+output). Reset ~1 min; throttling dinamico nei
#    picchi (feriali 15:00-17:30). Modello ignoto → il più restrittivo (M3).
MINIMAX_RATE_LIMITS = {
    "MiniMax-M3": (200, 10_000_000),
    "MiniMax-M2.7": (500, 20_000_000),
    "MiniMax-M2.7-highspeed": (500, 20_000_000),
    "MiniMax-M2.5": (500, 20_000_000),
    "MiniMax-M2.5-highspeed": (500, 20_000_000),
    "MiniMax-M2": (500, 20_000_000),
}
MINIMAX_RATE_LIMITS_DEFAULT = (200, 10_000_000)
# Safety factor: usiamo l'80% dei limiti pubblicati (headroom per il jitter
# del gateway e per il throttling dinamico dei picchi).
MINIMAX_SAFETY = float(os.environ.get("AIROUTER_MINIMAX_SAFETY", "0.8"))
# Cap totale del polling su 429 RPM/TPM (decisione utente 2026-07-04: MAI
# fallback Anthropic; oltre il cap → 429 sintetico al client, che ritenta).
# 90s conservativo per i timeout client; alzabile a 180 dopo verifica empirica.
MINIMAX_RETRY_CAP_SEC = float(os.environ.get("AIROUTER_MINIMAX_RETRY_CAP_SEC", "90"))
MINIMAX_CONCURRENCY = int(os.environ.get("AIROUTER_MINIMAX_SEMAPHORE", "8"))
MINIMAX_BACKOFF_STEPS = (5, 10, 20, 40, 60)  # esponenziale, cap 60s
MINIMAX_ALERTS_LOG = os.path.expanduser("~/.claude/logs/minimax-alerts.log")

_TOKEN_PLAN_RE = None  # compilata lazy in _classify_429 (re importato più giù come _re)


def _classify_429(raw: bytes) -> str:
    """Classifica un body 429 MiniMax: 'token_plan' (finestra 5h esaurita,
    'usage limit ... resets at <ts>' nel body — attesa di ORE) vs 'rpm_tpm'
    (rate limit al minuto — attesa di secondi). MiniMax non usa Retry-After:
    il reset è embeddato nel messaggio."""
    low = raw[:2000].lower()
    if b"usage limit" in low or b"resets at" in low:
        return "token_plan"
    return "rpm_tpm"


class RateLimitExhausted(Exception):
    """acquire() ha esaurito il budget di attesa senza trovare uno slot."""


class MinimaxRateLimiter:
    """Pacing client-side sui limiti ufficiali MiniMax (sliding window 60s
    per modello) + cooldown globale condiviso sui 429 (anti-hammering).

    Design (piano 2026-07-04):
    - window per modello: deque di entry MUTABILI [ts_monotonic, tokens].
      RPM = len(window), TPM = sum(tokens). record() aggiorna la STESSA
      entry in-place → nessun doppio conteggio stima+reale.
    - 429 → tokens=0 ma l'entry resta (il tentativo conta per l'RPM).
    - lock SOLO per check+insert; gli sleep avvengono FUORI dal lock
      (altrimenti le richieste concorrenti si serializzano a 1).
    - cooldown globale: scritto dal primo 429 osservato, letto da TUTTE le
      richieste in acquire() → nessuno martella durante il backoff.
    - MAI annidare con get_minimax_key()/to_thread (deadlock già visto).
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._windows = {}          # model -> deque([[ts, tokens], ...])
        self._cooldown_until = 0.0  # monotonic; globale, condiviso
        self._plan_exhausted_until = ""  # ISO/testo dal body Token Plan (per /health)
        self._backoff_idx = 0

    def _limits(self, model: str):
        rpm, tpm = MINIMAX_RATE_LIMITS.get(model, MINIMAX_RATE_LIMITS_DEFAULT)
        return max(1, int(rpm * MINIMAX_SAFETY)), int(tpm * MINIMAX_SAFETY)

    def _prune(self, model: str, now: float):
        win = self._windows.setdefault(model, deque())
        while win and now - win[0][0] > 60.0:
            win.popleft()
        return win

    async def acquire(self, model: str, est_tokens: int, budget_sec: float):
        """Attende uno slot RPM/TPM per `model`. Ritorna l'entry mutabile da
        passare a record(). RateLimitExhausted se il budget si esaurisce."""
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
                    # attesa fino all'uscita dell'entry più vecchia dalla finestra
                    wait = max(0.5, 60.0 - (now - win[0][0])) if win else 1.0
            wait += random.uniform(0.05, 0.5)
            if waited + wait > budget_sec:
                raise RateLimitExhausted(
                    f"minimax rate-limit: budget {budget_sec:.0f}s esaurito (waited {waited:.0f}s)")
            await asyncio.sleep(wait)
            waited += wait

    def record(self, entry: list, actual_tokens: int, success: bool):
        """Aggiorna l'entry restituita da acquire() in-place: successo →
        token reali, 429/fail → 0 token (ma l'entry resta: RPM conta)."""
        entry[1] = actual_tokens if success else 0

    def on_429_rpm(self):
        """Cooldown globale con backoff esponenziale + jitter."""
        step = MINIMAX_BACKOFF_STEPS[min(self._backoff_idx, len(MINIMAX_BACKOFF_STEPS) - 1)]
        self._backoff_idx += 1
        until = time.monotonic() + step + random.uniform(0, 2)
        if until > self._cooldown_until:
            self._cooldown_until = until
        return step

    def on_success(self):
        self._backoff_idx = 0
        self._cooldown_until = 0.0

    def set_plan_exhausted(self, reset_hint: str):
        self._plan_exhausted_until = reset_hint[:200]

    def snapshot(self) -> dict:
        """Stato per /health (solo lettura, best-effort senza lock)."""
        now = time.monotonic()
        per_model = {}
        for m, win in self._windows.items():
            live = [e for e in win if now - e[0] <= 60.0]
            rpm_limit, tpm_limit = self._limits(m)
            per_model[m] = {"rpm_used": len(live), "rpm_limit": rpm_limit,
                            "tpm_used": sum(e[1] for e in live), "tpm_limit": tpm_limit}
        return {"cooldown_sec": max(0.0, round(self._cooldown_until - now, 1)),
                "plan_exhausted": self._plan_exhausted_until, "per_model": per_model}


MINIMAX_LIMITER = MinimaxRateLimiter()
_MINIMAX_SEM = asyncio.Semaphore(MINIMAX_CONCURRENCY)


_last_alert_ts = 0.0
_ALERT_MIN_INTERVAL_SEC = 300  # max 1 notifica desktop ogni 5 min (il log resta completo)


def _minimax_alert(msg: str):
    """Notifica Token Plan esaurito: notify-send best-effort (systemd user può
    non avere DBUS) + SEMPRE append su file di alert.
    urgency=normal + timeout: NON deve restare bloccata sullo schermo (con
    -u critical GNOME la rende persistente). Throttle anti-spam: il log ha
    sempre tutto, il popup desktop al massimo 1 ogni 5 min."""
    global _last_alert_ts
    try:
        with open(MINIMAX_ALERTS_LOG, "a") as f:
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
                          "MiniMax Token Plan", msg[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
# Circuit breaker (D15): dopo N fail un backend va in cooldown e viene saltato.
# FIX audit v4: BREAKER_* removed (dead code - mai chiamato nel flusso;
# la logica di escalation usa _inverse_fails / _mixed_fails per-chat).
# Se serve circuit-breaker globale, va reintrodotto con test.

# Contatore per-chat dei fallimenti MiniMax (modalità inverse).
# Dopo N fail consecutivi: Anthropic esegue direttamente (bypass MiniMax).
INVERSE_FAIL_THRESHOLD = int(os.environ.get("AIROUTER_INVERSE_FAILS", "2"))
_inverse_fails = {}  # chat_fp -> int

# FIX E: mappa chat_fp -> modello originale richiesto dal client, usata dal relay
# per riscrivere il campo 'model' nella SSE response (così il jsonl di Claude Code
# riceve il modello reale, non "MiniMax-M3" rimappato dall'upstream).
_request_orig_model = {}  # chat_fp -> orig_model (es. "claude-sonnet-4-6")

# MIXED: MiniMax esegue tutto; dopo N fail consecutivi Anthropic prende il comando.
MIXED_FAIL_THRESHOLD = int(os.environ.get("AIROUTER_MIXED_FAILS", "2"))
_mixed_fails = {}  # chat_fp -> int

# FIX B1.1: lock condiviso per contatori globali (thread-safe + asyncio-safe via run_in_executor)
_counter_lock = threading.Lock()

# FIX #2: Cooldown e reset automatico per escalation
RESCUE_COOLDOWN_SEC = 30  # cooldown dopo escalation prima di ritentare
FAIL_RESET_SEC = 60  # reset counter se ultimo fallimento > 60s fa
_inverse_fail_ts = {}  # chat_fp -> timestamp ultimo fallimento
_mixed_fail_ts = {}  # chat_fp -> timestamp ultimo fallimento
_inverse_cooldown_until = {}  # chat_fp -> timestamp fine cooldown
_mixed_cooldown_until = {}  # chat_fp -> timestamp fine cooldown


_FAILS_GC_MAX = 5000  # FIX audit v5 #7: soglia oltre cui ripulire chat_fp stale


def _gc_fail_dicts(fails: dict, ts: dict, cooldown: dict, now: float) -> None:
    """FIX audit v5 #7: rimuove entry stale (no inc da >FAIL_RESET_SEC) quando i
    dict crescono troppo. Da chiamare DENTRO _counter_lock. Evita crescita RAM
    indefinita su proxy long-running con molti chat_fp unici (es. X-Session-ID)."""
    if len(fails) <= _FAILS_GC_MAX:
        return
    stale = [fp for fp, t in ts.items() if now - t > FAIL_RESET_SEC]
    for fp in stale:
        fails.pop(fp, None)
        ts.pop(fp, None)
        cooldown.pop(fp, None)


def inverse_fail_inc(chat_fp: str) -> int:
    """Incrementa contatore fallimenti MiniMax per chat. Ritorna nuovo valore."""
    with _counter_lock:  # FIX B1.1
        now = time.time()
        _gc_fail_dicts(_inverse_fails, _inverse_fail_ts, _inverse_cooldown_until, now)
        # Reset automatico se ultimo fail > FAIL_RESET_SEC fa
        last_fail = _inverse_fail_ts.get(chat_fp, 0)
        if now - last_fail > FAIL_RESET_SEC:
            _inverse_fails[chat_fp] = 0
        n = _inverse_fails.get(chat_fp, 0) + 1
        _inverse_fails[chat_fp] = n
        _inverse_fail_ts[chat_fp] = now
    return n


def inverse_fail_reset(chat_fp: str) -> None:
    """Azzera contatore (chiamata MiniMax riuscita)."""
    with _counter_lock:  # FIX audit v5 #2: simmetrico con inverse_fail_inc
        # FIX leak: pop invece di =0. Un'entry a 0 senza ts corrispondente è orfana
        # immortale (_gc_fail_dicts itera ts, non la raccoglie mai) → crescita RAM
        # illimitata. mixed_fail_reset già fa pop: qui rendiamo i due simmetrici.
        _inverse_fails.pop(chat_fp, None)
        _inverse_fail_ts.pop(chat_fp, None)
        _inverse_cooldown_until.pop(chat_fp, None)


def inverse_should_escalate(chat_fp: str) -> bool:
    """True se Anthropic deve bypassare MiniMax ed eseguire direttamente."""
    with _counter_lock:  # FIX M3: check->write del cooldown atomico
        # Cooldown attivo: resta in modalità escalation
        if time.time() < _inverse_cooldown_until.get(chat_fp, 0):
            return True
        fails = _inverse_fails.get(chat_fp, 0)
        if fails >= INVERSE_FAIL_THRESHOLD:
            # Attiva cooldown
            _inverse_cooldown_until[chat_fp] = time.time() + RESCUE_COOLDOWN_SEC
            return True
        return False


def mixed_fail_inc(chat_fp: str) -> int:
    with _counter_lock:  # FIX B1.1
        now = time.time()
        _gc_fail_dicts(_mixed_fails, _mixed_fail_ts, _mixed_cooldown_until, now)
        # Reset automatico se ultimo fail > FAIL_RESET_SEC fa
        last_fail = _mixed_fail_ts.get(chat_fp, 0)
        if now - last_fail > FAIL_RESET_SEC:
            _mixed_fails[chat_fp] = 0
        n = _mixed_fails.get(chat_fp, 0) + 1
        _mixed_fails[chat_fp] = n
        _mixed_fail_ts[chat_fp] = now
    return n


def mixed_fail_reset(chat_fp: str) -> None:
    with _counter_lock:  # FIX audit v5 #2: simmetrico con mixed_fail_inc
        _mixed_fails.pop(chat_fp, None)
        _mixed_fail_ts.pop(chat_fp, None)
        _mixed_cooldown_until.pop(chat_fp, None)


def mixed_anthropic_leads(chat_fp: str) -> bool:
    """True se MiniMax ha già fallito >= soglia: Anthropic prende il comando."""
    with _counter_lock:  # FIX M3: check->write del cooldown atomico
        # Cooldown attivo: resta in modalità escalation
        if time.time() < _mixed_cooldown_until.get(chat_fp, 0):
            return True
        fails = _mixed_fails.get(chat_fp, 0)
        if fails >= MIXED_FAIL_THRESHOLD:
            # Attiva cooldown
            _mixed_cooldown_until[chat_fp] = time.time() + RESCUE_COOLDOWN_SEC
            return True
        return False


_minimax_key_cache = {"key": None, "ts": 0}


def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_exc(msg: str):  # FIX B5.2: log con traceback
    import traceback
    log(f"{msg}\n{traceback.format_exc()}")


# Porte con modalità FISSA (per usare modalità diverse su sessioni diverse).
# :8787 = dinamica (segue ai-mode / file). Le altre forzano la modalità.
# Porte fisse su range libero (8782/8783 erano occupate da audio LiteLLM).
PORT_MODE = {
    8771: "anthropic",
    8772: "minimax",
    8773: "mixed",
    8774: "inverse",
    8775: "glm",            # solo GLM, 5.2 orchestra tiering
    8776: "glm-minimax",    # GLM-5.2 THINK → MiniMax ACT → GLM verify
    8777: "anthropic-glm",  # Anthropic THINK → GLM tiered ACT → Anthropic verify T2
}
# Override porte fisse via env (utile per istanze di test isolate senza conflitti
# con il servizio live): AIROUTER_PORT_MODE_JSON='{"8795":"glm","8796":"glm-minimax"}'.
_pm_override = os.environ.get("AIROUTER_PORT_MODE_JSON", "").strip()
if _pm_override:
    try:
        PORT_MODE = {int(k): v for k, v in json.loads(_pm_override).items() if v in VALID_MODES}
    except Exception:
        pass  # override malformato → mantiene il default
LISTEN_PORTS = [int(os.environ.get("AIROUTER_PORT", "8787"))] + list(PORT_MODE.keys())


def get_file_mode() -> str:
    try:
        m = MODE_FILE.read_text().strip().lower()
        if m in VALID_MODES:
            return m
    except Exception:
        pass
    return "anthropic"  # default sicuro


def _current_mode() -> str:
    """Helper per /health: modalità corrente (da file, fallback 'anthropic')."""
    return get_file_mode()


def _err_response(message: str, status: int = 502) -> web.Response:
    """FIX B4.10: err response con status propagato dall'upstream se disponibile.
    Il chiamante può passare status=up.status quando catches un errore dopo che
    l'upstream ha già risposto, evitando di rimappare 429->502."""
    return web.json_response(
        {"type": "error", "error": {"type": "router_error", "message": str(message)}},
        status=status,
    )


def get_mode(request=None, fp: str = None) -> str:
    """Modalità deterministica: ogni porta ha la sua web.Application con
    app['forced_mode'] cablato. :8787 ha forced_mode=None -> dinamica da file.
    Niente più sockname (inaffidabile con runner condiviso).

    Per-chat override (fp): se la chat ha un mode impostato, ha priorità
    sul file globale. Questo permette di switchare il router DENTRO una chat
    senza toccare il mode globale (utile per Claude Code)."""
    if request is not None:
        forced = request.app.get("forced_mode")
        if forced in VALID_MODES:
            return forced
    # Per-chat override: legge da ai-router-chats.json, più veloce del file globale
    if fp:
        cm = get_chat_mode(fp)
        if cm:
            return cm
    return get_file_mode()


# ── Fingerprint conversazione (chat indipendenti, D3=B/D4) ───────────────
# Identifica una chat senza session-id: hash(system + primo messaggio utente).
# Stabile per tutta la conversazione, distinto tra chat diverse.
import hashlib

CHAT_STORE = Path.home() / ".claude" / "ai-router-chats.json"
CHAT_TTL_DAYS = 7
CHAT_MAX_ENTRIES = 10000  # FIX B2.4: cap duro anti-DoS
_chat_cache = {"data": None, "ts": 0}


def conversation_fingerprint(data: dict) -> str:
    system = data.get("system", "")
    if isinstance(system, list):
        system = " ".join(b.get("text", "") for b in system if isinstance(b, dict))
    first_user = ""
    for m in data.get("messages", []):
        if m.get("role") == "user":
            c = m.get("content", "")
            first_user = c if isinstance(c, str) else " ".join(
                b.get("text", "") for b in c if isinstance(b, dict))
            break
    return hashlib.sha256((str(system) + "||" + str(first_user)).encode()).hexdigest()[:12]


def _resolve_chat_fingerprint(request) -> str:
    """FIX audit v4: chat_fp NAT-friendly.

    Priorita':
    1. Header esplicito X-Session-ID se presente (clients possono coabitare su stesso NAT).
    2. request.remote (IP:port) come fallback operativo - accettabile per setup locale single-user.
    3. "default" se neanche quello.

    NB: in setup multi-utente dietro NAT stesso, abilitare X-Session-ID via client.

    FIX 2026-07-12 (isolamento per-chat): Claude Code invia X-Claude-Code-Session-Id
    (UUID univoco e stabile per chat), NON X-Session-ID (sempre null). I subagenti
    condividono lo stesso Session-Id della chat madre -> ereditano automaticamente la
    sua modalita'. Questo e' l'identificativo corretto per confinare gli switch di
    modalita' a una singola chat/VSCode senza contaminare le altre sessioni."""
    sid = (request.headers.get("X-Claude-Code-Session-Id")
           or request.headers.get("x-claude-code-session-id")
           or request.headers.get("X-Session-ID")
           or request.headers.get("x-session-id"))
    if sid:
        return f"sid:{sid[:64]}"  # bound size anti-abuse
    return request.remote or "default"

def _load_chats() -> dict:
    """FIX deadlock 2026-06-30: cache + lettura file FUORI dal lock.

    Bug originale: `CHAT_STORE.read_text()` eseguito DENTRO `with _chat_lock`
    (threading.Lock). Chiamato da handler async: due richieste concorrenti
    `get_chat_mode` → la 1a tiene il lock durante il read → la 2a aspetta
    → l'event loop asyncio resta appeso finché il read non termina.

    Fix: cache hit è atomic con una semplice flag (single-thread); cache miss
    legge il file senza lock e aggiorna la cache con un dict nuovo (GIL-safe).
    Il TOCTOU è accettabile qui perché il file è piccolo e write è raro.
    """
    now = time.time()
    cached = _chat_cache["data"]
    cached_ts = _chat_cache["ts"]
    if cached is not None and now - cached_ts < 5:
        return cached

    # Cache miss: leggi file FUORI dal lock (atomic GIL-protected)
    try:
        raw = CHAT_STORE.read_text()
        d = json.loads(raw)
    except Exception:
        d = {}

    # cleanup TTL (D26: 7 giorni)
    cutoff = now - CHAT_TTL_DAYS * 86400
    changed = False
    for fp in list(d.keys()):
        if d[fp].get("ts", 0) < cutoff:
            del d[fp]; changed = True
    # bound hard (FIFO by timestamp asc)
    if len(d) > CHAT_MAX_ENTRIES:
        for fp in sorted(d, key=lambda k: d[k].get("ts", 0))[: len(d) - CHAT_MAX_ENTRIES]:
            del d[fp]; changed = True

    if changed:
        # Scrivi FUORI dal lock, atomic via temp file
        try:
            tmp = CHAT_STORE.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(CHAT_STORE)
        except Exception as e:
            log(f"ERR save chats: {type(e).__name__}")

    _chat_cache["data"] = d
    _chat_cache["ts"] = now
    return d


def _save_chats(d: dict):
    """FIX: write atomico via temp file, NO lock (async-safe)."""
    try:
        tmp = CHAT_STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(CHAT_STORE)
        _chat_cache["data"] = d
        _chat_cache["ts"] = time.time()
    except Exception as e:
        log(f"ERR save chats: {type(e).__name__}")


def get_chat_mode(fp: str):
    """FIX deadlock 2026-06-30: lock-free, cache-first."""
    try:
        d = _load_chats()
        return d.get(fp, {}).get("mode")
    except Exception:
        return None


def set_chat_mode(fp: str, mode: str):
    d = _load_chats()
    d[fp] = {"mode": mode, "ts": time.time()}
    _save_chats(d)
    log(f"chat {fp} -> mode {mode}")


def clear_chat_mode(fp: str):
    d = _load_chats()
    if fp in d:
        del d[fp]; _save_chats(d)
        log(f"chat {fp} -> reset")


# ── Comandi in-chat (D5/D7/D9): !router + frasi naturali italiane ─────────
import re as _re

# Alias CLI: mappa nome digitato -> nome interno del mode.
_ALIAS_MAP = {
    "mixam": "mixed",
    "mixgm": "glm-minimax",
    "mixag": "anthropic-glm",
}
# Nomi display: nome interno -> come appare in chat.
_INTERNAL_TO_DISPLAY = {
    "mixed": "Mixam",
    "glm-minimax": "Mixgm",
    "anthropic-glm": "Mixag",
    "anthropic": "anthropic",
    "minimax": "minimax",
    "inverse": "inverse",
    "glm": "glm",
}

_NL_MODE = [
    # NB: le regole GLM vanno PRIMA di anthropic/minimax puri (più specifiche):
    # "anthropic con glm" deve dare anthropic-glm, non anthropic.
    (_re.compile(r"anthropic\s*[-+ ]?\s*glm|claude\s*[-+ ]?\s*glm|glm\s+esecutore|anthropic\s+con\s+glm", _re.I), "anthropic-glm"),
    (_re.compile(r"glm\s*[-+ ]?\s*minimax|glm\s+con\s+minimax|glm\s+orchestr\w+\s+minimax", _re.I), "glm-minimax"),
    (_re.compile(r"solo\s+glm|usa\s+glm|glm\b", _re.I), "glm"),
    (_re.compile(r"solo\s+(claude|anthropic)|usa\s+(claude|anthropic)", _re.I), "anthropic"),
    (_re.compile(r"solo\s+minimax|usa\s+minimax", _re.I), "minimax"),
    (_re.compile(r"mod\w*\s+mist|mixed|mist[ao]\b", _re.I), "mixed"),
    (_re.compile(r"inverse|inversa|inverti", _re.I), "inverse"),
]
_CMD_VERB = _re.compile(r"\b(usa|passa|metti|imposta|attiva|cambia|adesso\s+usa)\b", _re.I)
_EXPLICIT = _re.compile(r"^\s*!router\s+(\w+)", _re.I)


def parse_router_command(text: str):
    """Ritorna {'action': ...} se il messaggio è un comando router, altrimenti None.
    Prudente (D24): naturale solo se messaggio breve + verbo di comando."""
    if not text:
        return None
    t = text.strip()
    # esplicito: !router <x>
    m = _EXPLICIT.match(t)
    if m:
        arg = m.group(1).lower()
        resolved = _ALIAS_MAP.get(arg, arg)  # alias -> nome interno
        if resolved in VALID_MODES:
            return {"action": "set", "mode": resolved}
        if arg in ("status", "reset", "help"):
            return {"action": arg}
        return {"action": "help"}
    # naturale: solo se breve (<80) e con verbo di comando (prudente)
    if len(t) <= 80 and _CMD_VERB.search(t):
        for rx, mode in _NL_MODE:
            if rx.search(t):
                return {"action": "set", "mode": mode}
    return None


def _router_reply_text(action: dict, fp: str) -> str:
    if action["action"] == "set":
        set_chat_mode(fp, action["mode"])
        _disp = _INTERNAL_TO_DISPLAY.get(action["mode"], action["mode"])
        return f"✅ Questa chat ora usa: **{_disp}** (dal prossimo messaggio)."
    if action["action"] == "status":
        cm = get_chat_mode(fp)
        if cm:
            return f"📍 Modalità chat: **{_INTERNAL_TO_DISPLAY.get(cm, cm)}**"
        _gm = get_file_mode()
        return f"📍 Modalità chat: **default ({_INTERNAL_TO_DISPLAY.get(_gm, _gm)})**"
    if action["action"] == "reset":
        clear_chat_mode(fp)
        _gm = get_file_mode()
        return f"↺ Chat riportata al default: **{_INTERNAL_TO_DISPLAY.get(_gm, _gm)}**"
    return ("🧭 Comandi: `!router <anthropic|minimax|mixam|inverse|glm|mixgm|mixag>` · "
            "`!router status` · `!router reset`. Anche a voce: «usa solo minimax».")


def _synthetic_message(text: str) -> dict:
    return {
        "id": "msg_router", "type": "message", "role": "assistant",
        "model": "ai-router", "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


# ── Classificatore criticità T2 (euristiche locali, zero latenza) ──────────
T2_KEYWORDS = (
    "quant", "quando", "data", "prezzo", "costo", "percentual", "formula",
    "calcol", "converti", "differenza tra", "versione", "compatibil",
    "sicurezz", "security", "vulnerab", "password", "credenzial", "token",
    "produzione", "production", "deploy", "migrazione", "migration",
    "irreversibil", "cancell", "delete", "drop ", "rm -rf", "truncate",
    "legale", "medic", "fiscal", "contratt", "normativ",
    "esatt", "preciso", "accurat", "verifica", "corretto", "sicuro che",
    "sei sicuro", "è vero che", "dimostra", "prova che",
)


def extract_last_user_text(data: dict) -> str:
    for msg in reversed(data.get("messages", [])):
        if msg.get("role") == "user":
            c = msg.get("content", "")
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            return str(c)
    return ""


def classify_t2(body: bytes) -> bool:
    """True se la richiesta è 'critica' (T2) -> merita verifica Opus."""
    try:
        data = json.loads(body)
    except Exception:
        return False
    # Richieste agentiche (Claude Code/VSCode) contengono "tools" e si aspettano
    # blocchi tool_use in risposta. La pipeline collaborativa T2 appiattisce la
    # risposta a solo testo, distruggendo i tool_use -> l'agente non esegue nulla.
    # Mai farle entrare in T2: vanno in passthrough rel() che preserva i tool.
    if data.get("tools"):
        return False
    if os.environ.get("AIROUTER_FORCE_T2") == "1":
        return True
    text = extract_last_user_text(data)
    low = text.lower()
    if any(k in low for k in T2_KEYWORDS):
        return True
    if "?" in text and any(ch.isdigit() for ch in text):
        return True
    return False


async def get_minimax_key() -> str:
    """FIX deadlock 2026-06-30: cache hit sotto lock, subprocess FUORI dal lock.

    Bug originale: `asyncio.to_thread(subprocess.check_output)` chiamato DENTRO
    `with _counter_lock` (threading.Lock). Quando arriva una 2ª richiesta mentre
    la 1ª è ancora in `await`, il 2° handler thread entra nello stesso lock
    perché `asyncio.to_thread` rilascia il GIL ma NON la threading.Lock tenuta
    dal thread asyncio principale → deadlock su event loop (lock ricorsivo).
    Fix: cache hit (veloce) resta dentro lock; subprocess si esegue FUORI lock,
    poi rientra per scrivere il risultato.
    """
    now = time.time()
    # 1) Cache hit: veloce, dentro lock (lettura atomica)
    with _counter_lock:
        cached = _minimax_key_cache["key"]
        cached_ts = _minimax_key_cache["ts"]
        if cached and now - cached_ts < 60:
            return cached

    # 2) Cache miss: leggi da env (zero I/O)
    key = os.environ.get("MINIMAX_API_KEY", "")

    # 3) Cache miss + env vuoto: subprocess FUORI dal lock
    if not key:
        try:
            import subprocess
            try:
                loop = asyncio.get_running_loop()
                proc = await asyncio.to_thread(
                    lambda: subprocess.check_output(
                        ["bash", str(KEY_FILE), "get", "minimax.api_key"],
                        timeout=5, text=True,
                    )
                )
                key = proc.strip() if isinstance(proc, str) else proc.decode().strip()
            except RuntimeError:
                proc = subprocess.check_output(
                    ["bash", str(KEY_FILE), "get", "minimax.api_key"],
                    text=True, timeout=5,
                )
                key = proc.strip()
        except Exception as e:
            log(f"ERR get key: {type(e).__name__}")
            key = ""

    # 4) Scrivi cache dentro lock (write atomico)
    with _counter_lock:
        # Doppio check: un altro handler potrebbe aver già popolato la cache
        # nel frattempo. Usa il valore appena letto se la cache è ancora vuota,
        # altrimenti rispetta chi ha scritto prima.
        if not _minimax_key_cache["key"] or now - _minimax_key_cache["ts"] >= 60:
            _minimax_key_cache["key"] = key
            _minimax_key_cache["ts"] = now

    return key


# Campi beta/Anthropic-only che MiniMax (api.minimaxi.chat) rifiuta con 400.
MINIMAX_UNSUPPORTED_FIELDS = ("context_management", "mcp_servers", "thinking")
# FIX 2026-07-02: floor max_tokens per MiniMax reasoning-first (vedi remap_body_for_minimax)
MINIMAX_MIN_MAX_TOKENS = int(os.environ.get("AIROUTER_MINIMAX_MIN_MAX_TOKENS", "1024"))

# Anthropic public API: 'context_management' è beta-only gated da
# 'anthropic-beta: context-management-2025-06-27'. Senza quel header,
# api.anthropic.com restituisce 400 "Extra inputs are not permitted".
# Lo strippiamo a monte per evitare il 400 (il client può inviarlo).
ANTHROPIC_UNSUPPORTED_FIELDS = ("context_management", "thinking", "output_config")


def strip_unsupported_fields(raw: bytes, fields: tuple) -> bytes:
    """Rimuove campi non supportati dal body JSON. No-op se non è JSON."""
    try:
        data = json.loads(raw)
        changed = False
        for f in fields:
            if f in data:
                data.pop(f, None)
                changed = True
        return json.dumps(data).encode() if changed else raw
    except Exception:
        return raw


def _log_original_model(orig: str, final: str, chat_id: str) -> None:
    """FIX A: Log modello originale prima del remap a MiniMax.

    Scrive in SIDECAR (router-model-map.jsonl) una riga JSON per tracciare
    il modello originale richiesto vs il modello finale remappato.
    Fallback silenzioso su errore IO (non rompe il flusso di richiesta).
    """
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "chat": chat_id,
            "orig": orig,
            "final": final
        }
        with open(SIDECAR, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # silent fallback, never break the request


def log_router_usage(chat_id: str, orig: str, final: str, usage: dict,
                     mode: str, client: str = "?", status: int = 200, path: str = ""):
    """FIX F: log per-request usage su USAGE_SIDECAR (router-usage.jsonl).

    Cattura OGNI richiesta che passa dal router, indipendentemente dal client
    (Claude Code, m3-code, m3-web, m3x, local-llm, agenti remoti). Questo
    è il single source of truth per i tool che non scrivono JSONL.

    FIX 2026-07-01: root-cause `?` final — quando final non è risolto
    (es. path interni /v1/models, /health, /metrics; mode non mappato),
    marca final come 'router-internal' invece di '?' così il ledger sa
    che il record è rumore non fatturabile ma distingue da upstream 200.

    Schema: ts, chat, orig, final, mode, client, status,
            input_tokens, output_tokens, cache_read, cache_creation
    """
    if not final or final == "?":
        final = "router-internal"
    try:
        USAGE_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "chat": chat_id,
            "orig": orig or "?",
            "final": final,
            "mode": mode,
            "client": client,
            "status": status,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_read": int(usage.get("cache_read_input_tokens", 0) or 0),
            "cache_creation": int(usage.get("cache_creation_input_tokens", 0) or 0),
        }
        with open(USAGE_SIDECAR, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # silent fallback


# Blocchi prodotti dai server tool Anthropic: restano nella history dopo un turno
# di WebSearch e MiniMax li rifiuta con 400 (2013) → chat rotta per sempre.
_SERVER_TOOL_BLOCK_TYPES = (
    "server_tool_use", "web_search_tool_result", "web_fetch_tool_result",
    "code_execution_tool_result",
)


def _strip_server_tools_for_minimax(data: dict) -> None:
    """Bug 2026-07-04: MiniMax non conosce i server tool Anthropic (web_search_20250305...).
    Rifiuta sia le definizioni in `tools` (niente input_schema) sia i blocchi
    server_tool_use/web_search_tool_result rimasti nella history → 400 (2013).
    Strip delle definizioni + conversione dei blocchi in testo. Muta `data`."""
    tools = data.get("tools")
    if isinstance(tools, list):
        kept = [t for t in tools if not (isinstance(t, dict) and "input_schema" not in t)]
        if len(kept) != len(tools):
            if kept:
                data["tools"] = kept
            else:
                data.pop("tools", None)
                data.pop("tool_choice", None)
    for m in data.get("messages", []):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for i, blk in enumerate(c):
            if isinstance(blk, dict) and blk.get("type") in _SERVER_TOOL_BLOCK_TYPES:
                payload = {k: v for k, v in blk.items() if k != "type"}
                c[i] = {"type": "text",
                        "text": f"[{blk['type']}] "
                                + json.dumps(payload, ensure_ascii=False, default=str)[:4000]}


def remap_body_for_minimax(raw: bytes, request=None) -> bytes:
    """Riscrive il model Claude -> MiniMax-M3 e rimuove i campi beta non supportati.

    FIX A: Se request è fornito, estrae chat_fp e loga il modello originale.
    FIX E: salva anche in _request_orig_model[chat_fp] il modello originale,
    così il relay SSE può riscrivere il campo 'model' nella risposta upstream
    e il jsonl di Claude Code riceve il modello realmente richiesto (no leak di MiniMax-M3).
    """
    try:
        data = json.loads(raw)
        orig = data.get("model", "")
        if orig and not orig.startswith("MiniMax"):
            chat_id = "?"
            if request:
                chat_id = _resolve_chat_fingerprint(request)
                _log_original_model(orig, MINIMAX_MODEL, chat_id)
            # FIX E: ricorda per riscrittura SSE — consumato dal relay()
            # FIX M2: bound anti-leak. Le entry sono consumate da relay(), ma i path
            # d'errore che saltano relay() le lasciano; nessun GC dedicato -> cap duro.
            if len(_request_orig_model) > 2000:
                _keep = _request_orig_model.get("__remap__")
                _request_orig_model.clear()
                if _keep is not None:
                    _request_orig_model["__remap__"] = _keep
            _request_orig_model[chat_id] = orig
            data["model"] = MINIMAX_MODEL
        # Strip campi che MiniMax non accetta (causano 400 "Extra inputs not permitted")
        for f in MINIMAX_UNSUPPORTED_FIELDS:
            data.pop(f, None)
        # Bug 2026-07-04: server tool Anthropic + blocchi web_search nella history
        # → MiniMax 400 (2013). Sanitizza SEMPRE (choke point di tutti i path MiniMax).
        _strip_server_tools_for_minimax(data)
        # FIX 2026-07-02: MiniMax-M2.7 è reasoning-first — il blocco <think> consuma
        # i token PRIMA del testo. Con max_tokens piccolo (chiamate interne Claude Code:
        # titoli/topic/commit-msg, spesso 20-100) il thinking mangia tutto il budget →
        # content vuoto. Floor a MINIMAX_MIN_MAX_TOKENS garantisce spazio per il testo.
        # (Il modello si ferma comunque da solo con end_turn: nessuno spreco di token.)
        try:
            _mt = int(data.get("max_tokens", 0) or 0)
            if 0 < _mt < MINIMAX_MIN_MAX_TOKENS:
                data["max_tokens"] = MINIMAX_MIN_MAX_TOKENS
        except (TypeError, ValueError):
            pass
        return json.dumps(data).encode()
    except Exception:
        log("remap_body: json parse fail, passthrough")  # FIX B5.1 residuo: log silenzioso
        return raw


_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
_oauth_file_cache = {"token": "", "mtime": -1.0}


def _read_oauth_from_file() -> str:
    """Legge il token OAuth dal file di credenziali Claude Code.

    FIX bottleneck: cache gated su mtime → niente open()+json.load() sincroni
    sull'event loop a OGNI richiesta Anthropic (serializzava le richieste concorrenti).
    Rilegge solo quando il file cambia (Claude Code lo riscrive a ogni refresh OAuth).
    """
    try:
        mtime = _CREDS_PATH.stat().st_mtime
    except Exception:
        return _oauth_file_cache["token"]
    if mtime == _oauth_file_cache["mtime"]:
        return _oauth_file_cache["token"]
    try:
        with open(_CREDS_PATH) as f:
            tok = json.load(f).get("claudeAiOauth", {}).get("accessToken", "")
        _oauth_file_cache["token"] = tok
        _oauth_file_cache["mtime"] = mtime
        return tok
    except Exception:
        return _oauth_file_cache["token"]


def _load_oauth_token():
    """Carica il token OAuth Anthropic da ~/.claude/.credentials.json se non
    è già in env. Usato da forward_anthropic_direct (verify T2 in modalità inverse)."""
    if os.environ.get("ANTHROPIC_OAUTH_TOKEN"):
        return
    tok = _read_oauth_from_file()
    if tok:
        os.environ["ANTHROPIC_OAUTH_TOKEN"] = tok


def _reload_oauth_token() -> bool:
    """FIX B3.2 (lazy): ricarica il token da .credentials.json.
    Claude Code aggiorna quel file indipendentemente a ogni refresh OAuth;
    non serve implementare un client OAuth (client_id/secret/refresh endpoint).
    Ritorna True se ha popolato ANTHROPIC_OAUTH_TOKEN."""
    tok = _read_oauth_from_file()
    if tok:
        cur = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
        if tok != cur:  # evita log inutile se token invariato
            log(f"oauth token reload: {'changed' if cur else 'initial'}")
        os.environ["ANTHROPIC_OAUTH_TOKEN"] = tok
        return True
    return False

_load_oauth_token()  # eseguita dopo le def


# ── Forwarding ────────────────────────────────────────────────────────────
HOP_HEADERS = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade",
    # FIX: strippa X-Forwarded-* per evitare che il client forgia IP/host percepiti dall'upstream
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port",
    "x-real-ip", "via", "forwarded",
}


async def forward_anthropic(request, body, session):
    """Chiama api.anthropic.com con OAuth subscription Bearer.

    FIX resilienza 2026-06-30: aggiunge SEMPRE il token OAuth letto da
    ~/.claude/.credentials.json (subscription web login), come fa Claude Code
    nel terminale. Il client non deve passare Authorization — il proxy gestisce
    l'auth in modo trasparente, esattamente come il Claude Code locale.

    - Se il client passa un OAuth Bearer sk-ant-oat*, viene rispettato (override esplicito)
    - Altrimenti usa il token OAuth letto lazy da .credentials.json
    - OAuth-lazy: rilegge .credentials.json ad ogni chiamata (Claude Code può refreshare)
    """
    url = ANTHROPIC_UPSTREAM + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    auth = headers.get("Authorization", "") or headers.get("authorization", "")

    if auth.startswith("Bearer sk-ant-oat"):
        # Client passa già OAuth subscription Bearer — mantienilo
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        # FIX OAuth subscription: il client non ha auth, usa il Bearer da .credentials.json
        # (come fa Claude Code nel terminale — il client non vede mai la chiave)
        _reload_oauth_token()  # lazy: rilegge se Claude Code ha refreshato
        tok = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        elif auth:
            # Client ha passato x-api-key (API key legacy) — rispettalo senza beta header
            pass
        # Altrimenti: nessuna auth = proxy trasparente, sarà upstream a rifiutare con 401

    # LAYER 3 FIX #68727: strip context-1m-2025-08-07 da beta header per Sonnet/Haiku.
    # Fork-subagent eredita 1M beta dal padre Opus e crasha con "Extra usage required".
    # Estrae il modello dal body per decidere se fare strip.
    beta = headers.get("anthropic-beta", "") or headers.get("Anthropic-Beta", "")
    if beta and "context-1m" in beta.lower():
        try:
            body_dict = json.loads(body)
            model_str = (body_dict.get("model") or "").lower()
            is_small = any(m in model_str for m in ("sonnet", "haiku")) and "opus" not in model_str
            if is_small:
                # Rimuovi solo il token problematico, mantieni altri beta flags
                new_beta = ",".join(
                    tok.strip() for tok in beta.split(",")
                    if "context-1m" not in tok.lower()
                )
                if new_beta:
                    headers["anthropic-beta"] = new_beta
                else:
                    headers.pop("anthropic-beta", None)
                    headers.pop("Anthropic-Beta", None)
                log(f"[#68727] stripped 1m beta for {model_str} fp={fp}")
        except Exception:
            pass

    # Strip campi beta che api.anthropic.com rifiuta senza il beta header opportuno.
    # FIX audit v5 #4: copri anche /v1/messages/count_tokens (sub-path), non solo
    # il path esatto, altrimenti context_management su count_tokens -> 400.
    safe_body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS) \
        if "/v1/messages" in request.path else body
    # FIX 2026-07-01: anthropic-version obbligatorio per /v1/messages (Anthropic
    # restituisce 400 "anthropic-version: header is required" se mancante). Se il
    # client non lo passa (es. curl di test, proxy custom), il router lo aggiunge
    # da solo per non rompere.
    if "/v1/messages" in request.path:
        headers.setdefault("anthropic-version", "2023-06-01")
    # ══ SANITIZZAZIONE FINALE (ROOT CAUSE FIX): ogni body verso Anthropic passa
    # dal repair — elimina role=system da messages e tool_result orfani residuali.
    # Il repair è idempotente: body già validi restano identici.
    if "/v1/messages" in request.path:
        try:
            body_dict = json.loads(safe_body)
            msgs = body_dict.get("messages", [])
            role_sys = sum(1 for m in msgs if m.get("role") == "system")
            if role_sys > 0 or msgs:
                repaired = _repair_message_sequence(msgs)
                body_dict["messages"] = repaired
                safe_body = json.dumps(body_dict).encode()
        except Exception:
            pass  # non rompere se il parsing fallisce
    # ── DEEP-DEBUG: analizza il body che sta per essere inviato ad Anthropic ──
    _fn = "forward_anthropic"
    try:
        sent_body_for_analysis = safe_body  # bytes
        analysis = _analyze_body_structure(sent_body_for_analysis)
        # Ring buffer
        SENT_ANALYSIS.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fn": _fn, "path": request.path,
            "sent_bytes": analysis["size_bytes"],
            "analysis": analysis,
        })
        # Salva SEMPRE l'ultimo body sent (immagini troncate)
        try:
            body_dict = json.loads(sent_body_for_analysis)
            for m in body_dict.get("messages", []):
                c = m.get("content", [])
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "image":
                            d = b.get("data", "")
                            if len(d) > 200:
                                b["data"] = d[:200] + f"... [TRUNCATED {len(d) - 200} chars]"
            with open(_DEBUG_LAST_SENT, "w") as f:
                json.dump({"sent_body": body_dict, "analysis": analysis}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        # WARNING se anomalie strutturali trovate
        if analysis["orphan_tool_results"] or analysis["role_system_in_messages"] > 0:
            log(f"[DEEP-DEBUG-WARN] {_fn}: orphans={len(analysis['orphan_tool_results'])} "
                f"role_system_msgs={analysis['role_system_in_messages']} "
                f"path={request.path} size={analysis['size_bytes']}b "
                f"orphans={analysis['orphan_tool_results']}")
    except Exception:
        pass
    return await session.request(
        request.method, url, data=safe_body, headers=headers, allow_redirects=False
    )


def _minimax_est_tokens(new_body: bytes) -> int:
    """Stima TPM (input+output): byte/4 per l'input + max_tokens richiesto
    per l'output (spec ufficiale: TPM conta entrambi)."""
    est = max(1, len(new_body) // 4)
    try:
        mt = int(json.loads(new_body).get("max_tokens", 0) or 0)
        est += max(0, mt)
    except Exception:
        pass
    return est


class _SyntheticResponse:
    """429 sintetico che emula la superficie ClientResponse usata dal router
    (relay: status/headers/content.iter_any; _call_full: read/release/json).
    NON usare web.Response qui: i caller trattano il ritorno di
    forward_minimax come una response aiohttp CLIENT, non server."""

    def __init__(self, status: int, payload: dict):
        self._body = json.dumps(payload).encode()
        self.status = status
        self.headers = {"Content-Type": "application/json",
                        "x-ai-router": "synthetic-429"}

    async def read(self):
        return self._body

    async def json(self):
        return json.loads(self._body)

    async def release(self):
        return None

    @property
    def content(self):
        body = self._body

        class _OneShot:
            async def iter_any(self):
                yield body

        return _OneShot()


def _synthetic_429(msg: str) -> "_SyntheticResponse":
    return _SyntheticResponse(
        429, {"type": "error", "error": {"type": "rate_limit_error", "message": msg}})


def _synthetic_context_exceed(body_bytes: bytes) -> "_SyntheticResponse":
    """FIX 2026-07-08 (BUG-CTX-PRE): MiniMax ha context window ~200k.
    Se il body in ingresso eccede MINIMAX_CONTEXT_BYTE_LIMIT, NON chiamare
    MiniMax: ritorna synthetic 400 con marker header x-ai-context-exceeded=true
    così i call site (mixed/minimax/inverse) possono intercettare il caso,
    fare shrink/retry prima di girare il 400 nudo al client (che altrimenti
    blocca la sessione in loop)."""
    resp = _SyntheticResponse(
        400, {"type": "error", "error": {"type": "context_exceeded",
                "message": f"body {len(body_bytes)}b > MiniMax limit "
                           f"{MINIMAX_CONTEXT_BYTE_LIMIT}b: caller must shrink"}})
    resp.headers["x-ai-context-exceeded"] = "true"
    return resp


async def forward_minimax(request, body, session, retry_budget_sec: float = None):
    """Chiama MiniMax con pacing preventivo (MinimaxRateLimiter, limiti
    ufficiali × safety) + retry con backoff sul 429 (redesign 2026-07-04).

    Decisione utente: MAI fallback Anthropic sui rate limit. 429 RPM/TPM →
    polling con backoff fino a retry_budget_sec, poi 429 sintetico al client
    (Claude Code ritenta da solo). 429 Token Plan (finestra 5h, 'resets at'
    nel body) → alert + 1 retry breve → 429 sintetico col reset visibile.
    Ritorna web.Response (429 sintetico) o aiohttp response (passthrough).

    NB: non avvolgere in asyncio.wait_for con timeout < retry_budget_sec
    (cancellerebbe il backoff a metà sleep). Call site degradati (fallback
    THINK ecc.) passano retry_budget_sec basso."""
    if retry_budget_sec is None:
        retry_budget_sec = MINIMAX_RETRY_CAP_SEC
    # FIX 2026-07-11 CONTEXT-WINDOW: pre-check intelligente per body > limite MiniMax.
    # Pipeline: rewrite (tool pruning + head+tail) PRIMA di scartare con synthetic 400.
    # Questo dà al modello la possibilità di continuare con contesto compresso.
    _orig_body = body
    fp = _resolve_chat_fingerprint(request) if '_resolve_chat_fingerprint' in dir() else ""
    try:
        if len(body) > 400_000:  # 400KB heuristic: attiva rewrite per richieste grandi
            model = MINIMAX_MODEL
            rewritten, was_rewritten = rewrite_for_context(body, model, fp)
            if was_rewritten:
                body = rewritten
                log(f"[ctx-fix] rewrite {len(_orig_body)}b→{len(body)}b fp={fp}")
    except Exception as e:
        log(f"[ctx-fix] rewrite failed: {e}")
    #合成 400 per body che eccede ancora il limite dopo rewrite
    try:
        if len(body) > MINIMAX_CONTEXT_BYTE_LIMIT:
            return _synthetic_context_exceed(body)
    except Exception:
        pass
    url = MINIMAX_UPSTREAM + request.path_qs
    key = await get_minimax_key()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    # MiniMax vuole X-Api-Key; rimuovo auth Anthropic
    for h in list(headers):
        if h.lower() in ("authorization", "x-api-key"):
            headers.pop(h)
    headers["X-Api-Key"] = key
    new_body = remap_body_for_minimax(body, request=request)  # FIX A: pass request per modello log
    try:
        model = json.loads(new_body).get("model", "") or MINIMAX_MODEL
    except Exception:
        model = MINIMAX_MODEL
    est = _minimax_est_tokens(new_body)
    t0 = time.monotonic()
    plan_retry_done = False
    while True:
        budget_left = retry_budget_sec - (time.monotonic() - t0)
        if budget_left <= 0:
            return _synthetic_429(
                f"MiniMax rate limited: retry budget {retry_budget_sec:.0f}s esaurito. Riprova.")
        try:
            entry = await MINIMAX_LIMITER.acquire(model, est, budget_left)
        except RateLimitExhausted as e:
            return _synthetic_429(f"MiniMax rate limited (pacing): {e}")
        async with _MINIMAX_SEM:
            up = await session.request(
                request.method, url, data=new_body, headers=headers, allow_redirects=False
            )
        if up.status != 429:
            # body NON letto: streaming preservato per relay()
            MINIMAX_LIMITER.record(entry, est, success=True)
            MINIMAX_LIMITER.on_success()
            # D41: propaga l'entry mutabile del limiter al relay per delta-correction
            # TPM (correggere entry[1]=stima con i token reali nel relay finally).
            try:
                up._airouter_limiter_entry = entry
                up._airouter_limiter_est = est
            except Exception:
                pass
            return up
        # ── 429: consuma il body, classifica, backoff ──────────────────────
        try:
            raw = await up.read()
        except Exception:
            raw = b""
        try:
            await up.release()
        except Exception:
            pass
        MINIMAX_LIMITER.record(entry, 0, success=False)
        kind = _classify_429(raw)
        if kind == "token_plan":
            snippet = raw[:400].decode("utf-8", "replace")
            MINIMAX_LIMITER.set_plan_exhausted(snippet)
            log(f"minimax 429 TOKEN-PLAN: {snippet[:200]}")
            _minimax_alert(f"Token Plan esaurito: {snippet[:200]}")
            if not plan_retry_done:
                plan_retry_done = True
                await asyncio.sleep(10)
                continue
            return _synthetic_429(f"MiniMax Token Plan esaurito. {snippet[:300]}")
        step = MINIMAX_LIMITER.on_429_rpm()
        log(f"minimax 429 RPM/TPM: backoff {step}s (budget left {budget_left:.0f}s) model={model}")


# Budget corto per i path secondari/non-streaming: _call_full li avvolge in
# asyncio.wait_for(timeout=90) che CANCELLEREBBE un backoff lungo a metà sleep.
MINIMAX_RETRY_BUDGET_SHORT = float(os.environ.get("AIROUTER_MINIMAX_RETRY_SHORT_SEC", "8"))


async def _fwd_minimax_short(request, body, session):
    """forward_minimax con budget di retry corto — da usare SOLO via _call_full."""
    return await forward_minimax(request, body, session,
                                 retry_budget_sec=MINIMAX_RETRY_BUDGET_SHORT)


# ── D43: Generative tools stubs (image / video / music / tts) ─────────────────
# MiniMax native REST endpoints (non-Anthropic-compatible).
# Gli header HOP_HEADERS filtrano i campi di trasporto; X-Api-Key viene aggiunto.

MINIMAX_GENERATIVE_HOST = os.environ.get(
    "AIROUTER_MINIMAX_GENERATIVE_HOST", "https://api.minimaxi.chat"
)

_GENERATIVE_PATHS = {
    "m3-image": "/v1/image_generation",
    "m3-video": "/v1/video_generation",
    "m3-music": "/v1/music_generation",
    "m3-tts":   "/v1/t2a_v2",
}


async def _forward_minimax_generative(request, body: bytes, session,
                                     path: str) -> "web.Response":
    """Inoltra a MiniMax generative endpoint con retry di backoff.
    Ritorna web.Response con la risposta upstream (byte per video/music, JSON per image/tts).
    Solleva 400 se il body non è JSON valido."""
    url = MINIMAX_GENERATIVE_HOST + path
    key = await get_minimax_key()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in HOP_HEADERS}
    for h in list(headers):
        if h.lower() in ("authorization", "x-api-key"):
            headers.pop(h)
    headers["X-Api-Key"] = key
    try:
        json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return _err_response("invalid JSON body", status=400)
    retry_budget_sec = MINIMAX_RETRY_CAP_SEC
    t0 = time.monotonic()
    while True:
        budget_left = retry_budget_sec - (time.monotonic() - t0)
        if budget_left <= 0:
            return _synthetic_429(
                f"MiniMax generative rate limited: retry budget {retry_budget_sec:.0f}s esaurito.")
        try:
            est_tokens = max(1, len(body) // 4)
            entry = await MINIMAX_LIMITER.acquire("generative", est_tokens, budget_left)
        except RateLimitExhausted as e:
            return _synthetic_429(f"MiniMax generative rate limited (pacing): {e}")
        async with _MINIMAX_SEM:
            up = await session.request(
                request.method, url, data=body, headers=headers, allow_redirects=False
            )
        if up.status != 429:
            MINIMAX_LIMITER.record(entry, est_tokens, success=True)
            MINIMAX_LIMITER.on_success()
            # D41: attach entry (path generative non usa relay(); resta sulla stima —
            # accettabile, volume basso). L'attach è innocuo e coerente con MOD 1.
            try:
                up._airouter_limiter_entry = entry
                up._airouter_limiter_est = est_tokens
            except Exception:
                pass
            raw = b""
            async for chunk in up.content.iter_chunked(65536):
                raw += chunk
            await up.release()
            try:
                resp_json = json.loads(raw)
                return web.json_response(resp_json, status=up.status)
            except Exception:
                return web.Response(body=raw,
                                    content_type=up.content_type or "application/octet-stream",
                                    status=up.status)
        # 429
        try:
            raw = await up.read()
        except Exception:
            raw = b""
        try:
            await up.release()
        except Exception:
            pass
        MINIMAX_LIMITER.record(entry, 0, success=False)
        step = MINIMAX_LIMITER.on_429_rpm()
        log(f"minimax-generative 429: backoff {step}s (budget {budget_left:.0f}s)")
        await asyncio.sleep(step)


# Route handlers OpenAI-compatible: /v1/images, /v1/videos, /v1/music,
# /v1/audio/speech — inoltrati al backend MiniMax nativo.

async def _route_v1_images(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session,
                                            _GENERATIVE_PATHS["m3-image"])

async def _route_v1_videos(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session,
                                            _GENERATIVE_PATHS["m3-video"])

async def _route_v1_music(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session,
                                            _GENERATIVE_PATHS["m3-music"])

async def _route_v1_audio_speech(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session,
                                            _GENERATIVE_PATHS["m3-tts"])


ANTHROPIC_DIRECT_URL = os.environ.get("AIROUTER_ANTHROPIC_DIRECT", "https://api.anthropic.com")
ANTHROPIC_OAUTH_TOKEN = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")  # Bearer da Claude Code OAuth


async def forward_anthropic_direct(request, body, session):
    """Chiama api.anthropic.com diretto con OAuth Bearer.
    Usato dalle verify T2 in modalità inverse: il modello di verifica è
    Claude stesso (via login OAuth nativo di Claude Code)."""
    global ANTHROPIC_OAUTH_TOKEN  # FIX B3.2: refresh lazy del token
    if not ANTHROPIC_OAUTH_TOKEN:
        # primo utilizzo / cache cold: carica dal file delle credenziali
        _load_oauth_token()
    # ogni chiamata: rileggi il file (Claude Code può aver refreshato il token)
    if _reload_oauth_token():
        ANTHROPIC_OAUTH_TOKEN = os.environ["ANTHROPIC_OAUTH_TOKEN"]
    url = ANTHROPIC_DIRECT_URL + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    for h in list(headers):
        if h.lower() in ("authorization", "x-api-key"):
            headers.pop(h)
    if ANTHROPIC_OAUTH_TOKEN:
        headers["Authorization"] = f"Bearer {ANTHROPIC_OAUTH_TOKEN}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    headers.setdefault("anthropic-version", "2023-06-01")
    # Strip campi beta che api.anthropic.com rifiuta senza il beta header opportuno
    safe_body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS) \
        if "/v1/messages" in request.path else body  # FIX audit v5 #4: copri count_tokens
    # ══ SANITIZZAZIONE FINALE (ROOT CAUSE FIX): ogni body verso Anthropic passa
    # dal repair — elimina role=system da messages e tool_result orfani residuali.
    # Il repair è idempotente: body già validi restano identici.
    if "/v1/messages" in request.path:
        try:
            body_dict = json.loads(safe_body)
            msgs = body_dict.get("messages", [])
            role_sys = sum(1 for m in msgs if m.get("role") == "system")
            if role_sys > 0 or msgs:
                repaired = _repair_message_sequence(msgs)
                body_dict["messages"] = repaired
                safe_body = json.dumps(body_dict).encode()
        except Exception:
            pass
    # ── DEEP-DEBUG: analizza il body che sta per essere inviato ad Anthropic ──
    _fn = "forward_anthropic_direct"
    try:
        analysis = _analyze_body_structure(safe_body)
        SENT_ANALYSIS.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fn": _fn, "path": request.path,
            "sent_bytes": analysis["size_bytes"],
            "analysis": analysis,
        })
        try:
            body_dict = json.loads(safe_body)
            for m in body_dict.get("messages", []):
                c = m.get("content", [])
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "image":
                            d = b.get("data", "")
                            if len(d) > 200:
                                b["data"] = d[:200] + f"... [TRUNCATED {len(d) - 200} chars]"
            with open(_DEBUG_LAST_SENT, "w") as f:
                json.dump({"sent_body": body_dict, "analysis": analysis}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        if analysis["orphan_tool_results"] or analysis["role_system_in_messages"] > 0:
            log(f"[DEEP-DEBUG-WARN] {_fn}: orphans={len(analysis['orphan_tool_results'])} "
                f"role_system_msgs={analysis['role_system_in_messages']} "
                f"path={request.path} size={analysis['size_bytes']}b "
                f"orphans={analysis['orphan_tool_results']}")
    except Exception:
        pass
    return await session.request(
        request.method, url, data=safe_body, headers=headers, allow_redirects=False
    )



# ── Helper per modalità inverse (T2 verify) ──────────────────────
def _force_no_stream(body: bytes):
    try:
        d = json.loads(body)
        d["stream"] = False
        return json.dumps(d).encode(), d
    except Exception:
        return body, {}


def _text_from_message(j: dict) -> str:
    out = []
    for b in (j or {}).get("content", []):
        if isinstance(b, dict):
            t = b.get("type", "")
            if t == "text":
                out.append(b.get("text", ""))
            elif t == "thinking":
                # FIX 2026-07-03: il campo è nidificato come b["thinking"]["thinking"]
                # (non b["thinking"]). L'Anthropic API restituisce il blocco in quel formato.
                inner = b.get("thinking", {})
                if isinstance(inner, dict):
                    out.append(inner.get("thinking", ""))
                elif isinstance(inner, str):
                    out.append(inner)
    return "".join(out)


async def _retry_forward(forward_fn, request, body, session, attempts: int = 2):
    '''Retry wrapper: 2 attempts for backend calls (pre-relay).

    R4-#8: retry SOLO pre-esecuzione -- relay already started is not retried.
    Caller does relay() after the return of this helper.
    '''
    last_exc = None
    for i in range(attempts):
        try:
            return await forward_fn(request, body, session)
        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                log("[_retry] attempt {}/{} EXC={}, retrying...".format(i+1, attempts, e))
    log("[_retry] all {} attempts failed: {}".format(attempts, last_exc))
    raise last_exc


async def _call_full(forward_fn, request, body, session, timeout: float = 90):
    """Chiamata non-streaming: ritorna (status, json|None). `timeout` secondi PER FASE
    (headers + read), default 90. Il chiamante può passare il budget residuo (es. loop
    OPPOSE/REVISE inverse) per non sforare la finestra dichiarata: worst-case ~2×timeout."""
    nb, _ = _force_no_stream(body)
    up = None
    try:
        up = await asyncio.wait_for(forward_fn(request, nb, session), timeout=timeout)
    except asyncio.TimeoutError:
        log(f"_call_full TIMEOUT {timeout}s req {getattr(request, 'path', '?')}")
        return 0, None
    except Exception as e:
        log_exc(f"_call_full EXC req {getattr(request, 'path', '?')}: {e}")  # FIX B5.2
        if up is not None:
            try: up.release()
            except Exception: pass
        return 0, None
    status = up.status
    try:
        raw = await asyncio.wait_for(up.read(), timeout=timeout)
    except asyncio.TimeoutError:
        log(f"_call_full TIMEOUT {timeout}s read {getattr(request, 'path', '?')}")
        try: up.release()
        except Exception: pass
        return status, None
    try:
        up.release()
    except Exception: pass
    # FIX 2026-07-01: decomprimi Content-Encoding se attivo. La session ha
    # auto_decompress=False per fare passthrough gzip/brotli al client relay,
    # ma le pipeline interne (THINK/REVISE/ACT/finalize) parsa il body con
    # json.loads: se il body è compresso, parse fallisce silenziosamente →
    # t_json=None → fallback spurio (mixed_new THINK ko 200 / inverse THINK
    # EXC). Decomprimiamo qui per essere robusti a proxy intermedi (Cloudflare).
    ce = (up.headers.get("Content-Encoding") or "").lower().strip()
    if ce and raw:
        try:
            if "gzip" in ce:
                import gzip
                raw = gzip.decompress(raw)
            elif "br" in ce or "brotli" in ce:
                try:
                    import brotli
                    raw = brotli.decompress(raw)
                except Exception:
                    log(f"_call_full: brotli module mancante, body {len(raw)}B non decompresso")
            elif "deflate" in ce:
                import zlib
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    raw = zlib.decompress(raw)
        except Exception as e:
            log(f"_call_full: decompress {ce} fail: {e} (raw={len(raw)}B)")
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


# OAuth subscription (sk-ant-oat01, piano Max) rifiuta con 429 le richieste
# Sonnet/Opus il cui system NON inizia con questo marker esatto. Haiku è esente.
# Bug 2026-07-02: le fasi THINK/VERIFY/OPPOSE sovrascrivevano il system col prompt
# orchestratore, cancellando il marker → 429 → il router cadeva sempre su M3.
CLAUDE_CODE_MARKER = "You are Claude Code, Anthropic's official CLI for Claude."


def _anthropic_system(instruction: str) -> list:
    """System array per Anthropic OAuth: marker Claude Code (auth-gate) + istruzione.
    Il primo blocco deve essere il marker ESATTO o l'API risponde 429."""
    return [
        {"type": "text", "text": CLAUDE_CODE_MARKER},
        {"type": "text", "text": instruction},
    ]


def _build_critique_body(orig: dict, question: str, draft: str) -> bytes:
    """Round 1: Anthropic riceve la bozza M3 e produce una critica costruttiva.
    NON genera la risposta finale: solo issue list, dubbi, fatti da verificare.
    Output strutturato per essere ri-usato da M3 nel round 2."""
    sys_msg = (
        "Sei un revisore critico esperto e SEVERO. Ricevi DOMANDA e BOZZA prodotta da M3. "
        "Il tuo compito: produrre SEMPRE una lista di almeno 2-4 criticita', anche se la bozza "
        "ti sembra accettabile. Cerca: imprecisioni, dettagli mancanti, formulazioni deboli, "
        "informazioni che potrebbero fuorviare, ordine non ottimale dei contenuti. "
        "Se davvero non trovi nulla, rispondi letteralmente 'NESSUNA CRITICA'. "
        "Altrimenti elenca punti concreti e azionabili, numerati, in italiano."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nBOZZA M3:\n{draft}\n\n"
        "Elenca le criticita' da correggere (o 'NESSUNA CRITICA')."
    )
    return json.dumps({
        "model": VERIFY_MODEL,
        "max_tokens": min(int(orig.get("max_tokens", 1024)) // 2, 512),
        "system": _anthropic_system(sys_msg),
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


def _build_revise_body(orig: dict, question: str, draft: str, critique: str) -> bytes:
    """Round 2: M3 riceve la propria bozza + la critica di Anthropic e produce la v2."""
    sys_msg = (
        "Sei M3, un modello che collabora. Hai prodotto una BOZZA iniziale. "
        "Un revisore (Anthropic) ha prodotto una CRITICA. Il tuo compito: "
        "rivedi la bozza tenendo conto della critica, mantenendo cio' che e' corretto "
        "e correggendo cio' che va corretto. Rispondi con la versione finale, in italiano."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nBOZZA v1:\n{draft}\n\n"
        f"CRITICA ANTHROPIC:\n{critique}\n\n"
        "Restituisci la risposta finale riveduta (v2)."
    )
    body = dict(orig)
    body["system"] = sys_msg
    body["messages"] = [{"role": "user", "content": user_msg}]
    body["stream"] = False
    return json.dumps(body).encode()


def _build_finalize_body(orig: dict, question: str, draft_v2: str) -> bytes:
    """Round 3: Anthropic finalizza — gateway di qualità, decide se la v2 e' pubblicabile."""
    sys_msg = (
        "Sei il finalizzatore. Ricevi DOMANDA e v2 prodotta da M3 dopo la tua critica. "
        "Se la v2 risponde correttamente, restituiscila identica. "
        "Se contiene ancora errori gravi, correggili SOLO dove necessario. "
        "Rispondi in italiano, SOLO la risposta finale, senza meta-commenti."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nRISPOSTA v2 (M3):\n{draft_v2}\n\n"
        "Restituisci la risposta finale."
    )
    return json.dumps({
        "model": VERIFY_MODEL,
        "max_tokens": int(orig.get("max_tokens", 1024)),
        "system": _anthropic_system(sys_msg),
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


THINK_MAX_TOKENS = int(os.environ.get("AIROUTER_THINK_MAX_TOKENS", "200"))
# Modello FALLBACK per la fase THINK (usato solo se il client non passa un model
# Anthropic valido). Decisione utente 2026-07-03: il THINK gira sul modello
# selezionato dall'utente; Haiku qui è solo riserva + esecutore d'emergenza ACT.
THINK_MODEL = os.environ.get("AIROUTER_THINK_MODEL", "claude-haiku-4-5-20251001")
# Timeout dedicato al THINK: è un piano Haiku da ~200 token (~1-3s reali). Il default
# 90s di _call_full (applicato 2x = 180s worst-case) trasformava un rallentamento/coda
# upstream in 180s di silenzio totale sul TTFB del client PRIMA del fallback M3. Un piano
# che non arriva in THINK_TIMEOUT è già un segnale di degrado: meglio cadere subito su M3.
THINK_TIMEOUT_SEC = float(os.environ.get("AIROUTER_THINK_TIMEOUT_SEC", "12"))


def _build_think_body(orig: dict) -> bytes:
    """Version D (decisione utente 2026-07-03): il THINK gira sul MODELLO
    SELEZIONATO DALL'UTENTE (Fable/Opus/Sonnet) — l'orchestratore è sempre e solo
    il modello Anthropic scelto, mai un downgrade a Haiku. Il piano resta testo
    libero breve (max THINK_MAX_TOKENS) → nessun parse fail possibile.
    THINK_MODEL (Haiku) è solo fallback se il client non passa un model valido."""
    sys_msg = (
        "Sei un ORCHESTRATORE. Leggi la richiesta utente e scrivi un PIANO D'AZIONE "
        "BREVE (2-3 frasi) in italiano: cosa va fatto e in che ordine. "
        "Scrivi SOLO il piano come testo semplice. NON eseguire nulla, NON chiamare "
        "strumenti, NON rispondere alla domanda — solo il piano operativo essenziale."
    )
    body = dict(orig)
    body["system"] = _anthropic_system(sys_msg)
    body["stream"] = False
    body["max_tokens"] = THINK_MAX_TOKENS  # piano breve: THINK resta veloce anche su Fable/Opus
    _m = (orig.get("model") or "").strip()
    # Modello utente = orchestratore; Haiku solo se il client non ha un model Anthropic
    body["model"] = _m if _m and not _m.startswith("MiniMax") else THINK_MODEL
    # Togli tools (il modello non deve emettere tool_use) e thinking (mangia budget).
    body.pop("tools", None)
    body.pop("thinking", None)
    return json.dumps(body).encode()


def _extract_balanced_json(text: str) -> list:
    """Ritorna tutte le sottostringhe JSON-object top-level bilanciate nel testo,
    ignorando le graffe dentro le stringhe. Robusto a preamboli, code-fence e
    più oggetti concatenati. Ordine di apparizione."""
    objs = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objs.append(text[start:i + 1])
                    start = -1
    return objs


def _parse_json_with_keys(text: str, required: dict) -> dict | None:
    """Estrae il primo oggetto JSON bilanciato nel testo che soddisfa i vincoli
    `required` (chiave -> validatore/callable, o None = presente). Robusto a
    preamboli testuali, code-fence e oggetti multipli (es. esempio + reale)."""
    if not text:
        return None
    for cand in _extract_balanced_json(text):
        try:
            j = json.loads(cand)
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        ok = True
        for k, check in required.items():
            if k not in j:
                ok = False
                break
            if callable(check) and not check(j[k]):
                ok = False
                break
        if ok:
            return j
    return None


def _parse_plan_text(text: str) -> dict | None:
    """Parsifica l'output della fase THINK con formato [PLAN]...[/PLAN] (Version B).
    Il modello emette sezioni delimitate da tag — impossibile romperle con escaping.
    Formato atteso:
      [PLAN]<ragionamento>[/PLAN]
      [TOOLS]<json array>[/TOOLS]
      [SELF_REVIEW]OK: <bool>\nNOTES: <json array>[/SELF_REVIEW]"""
    if not text:
        return None
    import re

    def extract_section(tag: str) -> str:
        # Case-insensitive, gestisce spazi/newline multipli
        pattern = rf'\[{re.escape(tag)}\](.*?)\[/{re.escape(tag)}\]'
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    plan = extract_section("PLAN")
    tools_raw = extract_section("TOOLS")
    review_raw = extract_section("SELF_REVIEW")

    if not plan:
        return None

    # Parse tools: JSON array
    tools = []
    if tools_raw:
        try:
            tools = json.loads(tools_raw)
        except Exception:
            pass

    # Parse self_review: "OK: true\nNOTES: [...]"
    self_review_ok = True
    self_review_notes = []
    if review_raw:
        ok_match = re.search(r'OK:\s*(true|false)', review_raw, re.IGNORECASE)
        if ok_match:
            self_review_ok = ok_match.group(1).lower() == "true"
        notes_match = re.search(r'NOTES:\s*(\[.*?\])', review_raw, re.DOTALL)
        if notes_match:
            try:
                self_review_notes = json.loads(notes_match.group(1))
            except Exception:
                pass

    return {
        "plan": plan,
        "tools_to_call": tools,
        "self_review_ok": self_review_ok,
        "self_review_notes": self_review_notes,
    }


# Alias per compatibilità con chi chiama _parse_think_json
def _parse_think_json(text: str) -> dict | None:
    return _parse_plan_text(text)


def _build_act_body(orig: dict, plan: str, tools_to_call: list,
                    executor: str = "") -> bytes:
    """Version D 2026-07-03: l'executor MiniMax ESEGUE il piano-guida Anthropic.
    L'esecutore sceglie e chiama i tool concreti (ha il body originale con tutti
    i tools); il piano è solo una guida di orchestrazione. `executor` (es.
    MiniMax-M2.7 code) forza il modello: inizia con 'MiniMax' → remap lo preserva."""
    sys_msg = (
        "Sei l'esecutore. Un orchestratore Anthropic ha analizzato la richiesta e "
        "prodotto questo PIANO-GUIDA. Segui il piano usando i tuoi strumenti come "
        "necessario. Rispondi normalmente all'utente eseguendo le azioni del piano.\n\n"
        f"PIANO-GUIDA:\n{plan}"
    )
    body = dict(orig)  # conserva i tools originali → l'executor può chiamarli
    body["system"] = sys_msg
    body["stream"] = bool(orig.get("stream"))  # preserva stream se client lo chiedeva
    if executor:
        body["model"] = executor
    return json.dumps(body).encode()


# ── Context window thresholds (byte → token ≈ /4 per English+code) ────────
# MiniMax-M2.7 context ≈ 192k token → 750k byte
MINIMAX_CONTEXT_BYTE_LIMIT = int(os.environ.get("AIROUTER_MINIMAX_CONTEXT_LIMIT", "750000"))
ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT = 200 * 1024  # 200k byte per Haiku

TRIM_STATE_DIR = Path(os.environ.get("AIROUTER_TRIM_DIR", "/tmp/ai-router-trim"))
TRIM_STATE_DIR.mkdir(exist_ok=True)
# Trim: taglia proattivamente il context DOPO ogni risposta riuscita
# Target: 50% del limite M3 → il context NON esplode mai se ritmato ogni risposta
# Min messages preservati: 4 (sempre abbastanza per coerenza)
TRIM_TARGET_BYTES = MINIMAX_CONTEXT_BYTE_LIMIT // 2  # ~375k byte = ~94k token
TRIM_MIN_MESSAGES = 4
# Shrink: keep head+tail when context explosion already happened
SHRINK_KEEP_HEAD = int(os.environ.get("AIROUTER_SHRINK_KEEP_HEAD", "6"))
SHRINK_KEEP_TAIL = int(os.environ.get("AIROUTER_SHRINK_KEEP_TAIL", "6"))
# Budget per il summary compresso: circa il limite MiniMax stesso
# byte/token ≈ 4 per English+code → 750k byte ≈ 188k token ≈ 192k token limit
# Usiamo 3/4 del limite per dare spazio anche a system e overhead JSON
SUMMARY_BUDGET = MINIMAX_CONTEXT_BYTE_LIMIT * 3 // 4

def _build_shrink_summary(messages: list, budget: int) -> str:
    """Comprime una lista di messaggi preservando QUALITÀ MASSIMA con budget token.
    Algoritmo content-aware:
    - TOOL_USE output: PRESERVA INTEGRALMENTE (denso, critico, tipicamente <500 token)
    - Contenuto lungo (user/assistant >2000c): PRESERVA la prima parte + "..."
    - Contenuto breve (<2000c): PRESERVA INTEGRALMENTE
    - MIDDLE: smart-sampling diversificato (prende ogni N-esimo, non solo i primi)
    Questo dà al modello: (a) contesto iniziale, (b) varietà di azioni intermedie,
    (c) contesto recente — senza perdere tool results che contengono output reali."""
    n = len(messages)
    if n == 0:
        return ""

    # HEAD + TAIL preservati tal quale
    tail = messages[-SHRINK_KEEP_TAIL:]
    head_count = min(SHRINK_KEEP_HEAD, n - SHRINK_KEEP_TAIL)
    head = messages[:head_count] if head_count > 0 else []
    middle = messages[head_count:n - SHRINK_KEEP_TAIL] if head_count < n - SHRINK_KEEP_TAIL else []

    parts = []

    # ── TESTA: preserva tal quale (contesto iniziale) ──────────────────────
    if head:
        head_lines = "\n".join(
            f"[{m.get('role','?')}]: {_smart_truncate(m)}"
            for m in head
        )
        parts.append(f"=== CONTESTO INIZIALE ({len(head)} msg) ===\n{head_lines}")

    # ── MEZZO: smart-sampling ───────────────────────────────────────────────
    if middle:
        sampled = _smart_sample_middle(middle, budget)
        middle_lines = "\n".join(
            f"[{m.get('role','?')}]: {_smart_truncate(m)}"
            for m in sampled
        )
        parts.append(f"=== FASE INTERMEDIA ({len(sampled)}/{len(middle)} msg selezionati) ===\n{middle_lines}")

    # ── CODA: preserva tal quale (contesto più recente) ───────────────────
    tail_lines = "\n".join(
        f"[{m.get('role','?')}]: {_smart_truncate(m)}"
        for m in tail
    )
    parts.append(f"=== MESSAGGI RECENTI ({len(tail)} msg) ===\n{tail_lines}")

    return "\n\n".join(parts)


def _smart_truncate(msg: dict, max_len: int = 1800) -> str:
    """Truncation intelligente: preserva tool_use integrali, tronca resto."""
    content = msg.get("content", "")
    tool_use = msg.get("tool_use", [])
    role = msg.get("role", "?")

    # Tool use: PRESERVA INTEGRALMENTE (denso, critico, piccolo)
    if tool_use:
        tool_block = "\n[TOOL_USE]: " + "\n[TOOL_USE]: ".join(
            f"{t.get('name','?')}({json.dumps(t.get('input',{}), ensure_ascii=False)[:300]})"
            for t in tool_use
        )
        # Normalizza content (può essere str o list di blocchi tool_result/text)
        content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, list) else (content or "")
        # Se content è lungo, aggiungi solo l'inizio
        if len(content_str) > max_len:
            return content_str[:max_len] + f"\n... [+{len(content_str)-max_len}c troncatI]"
        return content_str + tool_block if content_str else tool_block

    # Text content: truncation con segnalazione
    if isinstance(content, list):
        content = json.dumps(content, ensure_ascii=False)
    if len(content) > max_len:
        return content[:max_len] + f"\n... [+{len(content)-max_len}c troncatI]"
    return content


def _smart_sample_middle(messages: list, budget: int) -> list:
    """Campiona il mezzo in modo diversificato: copre l'intera finestra temporale
    prendendo messaggi distribuiti, non solo i primi. Priorità:
    1. tool_use messages (sempre: contengono output reali)
    2. Messaggi "svolta" (messaggi lunghi di assistant = ragionamento/decisioni)
    3. Campionamento uniforme distribuito nel tempo"""
    if not messages:
        return []

    sampled = []
    tool_msgs = []
    non_tool = []

    for m in messages:
        if m.get("tool_use") or (m.get("role") == "user" and len(str(m.get("content",""))) > 3000):
            tool_msgs.append(m)
        else:
            non_tool.append(m)

    # Tutti i tool_use messages (sono pochi e densi)
    sampled.extend(tool_msgs)

    # Campionamento uniforme: skip ratio basato su budget
    # byte/token ≈ 4, budget per il mezzo ≈ budget / 3
    byte_per_msg = 500  # stima conservativa per messaggio "medio" (contexto + content)
    max_items = max(3, (budget // 3) // byte_per_msg)
    if non_tool:
        total = len(non_tool)
        step = max(1, total // max_items)
        for i in range(0, total, step):
            sampled.append(non_tool[i])

    return sampled


def _trim_context_after_response(req_body: bytes, fp: str) -> None:
    """Taglia proattivamente il context DOPO ogni risposta riuscita.
    Strategia: tail(M) + head(rimanente) dove tail = messaggi recenti.
    Zero perdita reale: tool_use integrali, solo history ridondante compressa.
    Scrive su file: la prossima richiesta con stesso fp carica il body trimmato."""
    try:
        data = json.loads(req_body)
    except Exception:
        return
    msgs = data.get("messages", [])
    n = len(msgs)
    if n < TRIM_MIN_MESSAGES * 2:
        return
    if len(req_body) <= TRIM_TARGET_BYTES:
        return
    tail_cnt = max(4, min(8, n // 4))
    trimmed = dict(data)
    trimmed["messages"] = msgs[:-tail_cnt] + msgs[-tail_cnt:]
    try:
        trimmed_bytes = json.dumps(trimmed).encode()
        (TRIM_STATE_DIR / f"{fp}.json").write_bytes(trimmed_bytes)
        log(f"trim: {len(req_body)}b→{len(trimmed_bytes)}b ({n}→{len(trimmed['messages'])} msg) fp={fp}")
    except Exception as e:
        log(f"trim: write fail {e} fp={fp}")


async def _shrink_and_retry_minimax(request, orig: dict, body: bytes,
                                   session, chat_fp: str, relay) -> web.Response:
    """Pipeline shrink dinamico: comprime i messaggi per far stare in M3,
    ritenta MiniMax. Se ancora fallisce → fallback Haiku → Anthropic.
    Principio: comprimere PRIMA di scartare, mai perdere contesto."""
    log(f"shrink: inizio body={len(body)}b fp={chat_fp}")

    # Estrai messages dal body originale
    try:
        orig_dict = json.loads(body) if isinstance(body, bytes) else body
        if isinstance(body, bytes):
            orig_dict = json.loads(body)
        else:
            orig_dict = body
    except Exception as e:
        log(f"shrink: parse body fail {e} → fallback Haiku")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

    messages = orig_dict.get("messages", [])
    if not messages:
        log(f"shrink: no messages → fallback Haiku")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

    # Budget token per il summary (~1/4 del contesto M3)
    budget = SUMMARY_BUDGET
    summary_content = _build_shrink_summary(messages, budget)

    # Costruisci body compresso
    shrunk = dict(orig_dict)
    # Mantiene tail dei messaggi recenti per preservare contesto immediato
    tail_msgs = messages[-SHRINK_KEEP_TAIL:] if messages else []
    system_val = orig_dict.get("system", "")
    if isinstance(system_val, list):
        system_str = "\n\n".join(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for v in system_val)
    else:
        system_str = system_val or ""
    system_content = system_str + "\n\n" + summary_content if "system" in orig_dict else summary_content
    shrunk["messages"] = tail_msgs
    # ponytail: ripara coppie tool troncate
    tail_msgs = _repair_message_sequence(tail_msgs)
    shrunk["messages"] = tail_msgs
    if system_content:
        shrunk["system"] = system_content

    # Rimuovi thinking se presente (mangia budget)
    shrunk.pop("thinking", None)

    # Verifica che stia nel limite
    shrunk_bytes = json.dumps(shrunk).encode()
    log(f"shrink: {len(body)}b → {len(shrunk_bytes)}b (budget {MINIMAX_CONTEXT_BYTE_LIMIT}) fp={chat_fp}")

    if len(shrunk_bytes) > MINIMAX_CONTEXT_BYTE_LIMIT:
        # CONTEXT FIX: prova LLM summarization prima di scartare su Haiku.
        # Il modello stesso che ha fallito riassume i messaggi vecchi con budget
        # calibrato per il modello target. Se anche questo fallisce → Haiku.
        log(f"shrink: body ancora grande dopo shrink → LLM summarization fp={chat_fp}")
        summary_msgs = await summarize_old_messages(messages)
        if summary_msgs is not None:
            summ_shrunk = dict(orig_dict)
            summ_shrunk["messages"] = summary_msgs
            summ_shrunk.pop("thinking", None)
            summ_bytes = json.dumps(summ_shrunk).encode()
            log(f"shrink: LLM summary {len(messages)} msgs → {len(summ_bytes)}b fp={chat_fp}")
            if len(summ_bytes) <= MINIMAX_CONTEXT_BYTE_LIMIT:
                try:
                    up = await forward_minimax(request, summ_bytes, session)
                    if up.status < 400:
                        log(f"shrink: LLM summary SUCCESS fp={chat_fp}")
                        return await relay(up, extra_headers={"x-ai-verified": "m3-llm-summary"})
                    await up.release()
                except Exception as e:
                    log(f"shrink: LLM summary MiniMax EXC {e} fp={chat_fp}")
        # Anche LLM summary fallito: fallback Haiku
        log(f"shrink: LLM summary failed or still too big → Haiku fp={chat_fp}")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

    # Ritenta MiniMax con body compresso
    try:
        up = await forward_minimax(request, shrunk_bytes, session)
        if up.status < 400:
            log(f"shrink: SUCCESS {up.status} fp={chat_fp}")
            return await relay(up, extra_headers={"x-ai-verified": "m3-shrunk-act"})

        # Check se è context-exceed anche dopo shrink (caso estremo)
        is_ctx, _ = await _is_context_exceed_400(up)
        try:
            await up.release()
        except Exception:
            pass

        if is_ctx:
            log(f"shrink: anche compresso → 400 context-exceed fp={chat_fp}")
            return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

        log(f"shrink: MiniMax {up.status} → fallback Haiku fp={chat_fp}")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

    except Exception as e:
        log(f"shrink: MiniMax EXC {e} → fallback Haiku fp={chat_fp}")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)


def _repair_message_sequence(messages: list) -> list:
    """FIX 2026-07-09 BUG-SHRINK-TOOL: ripara una sequenza di messaggi dopo troncamento
    per renderla strutturalmente valida per l'API Anthropic.

    Problema: SHRINK_KEEP_TAIL puo' troncare a meta' una coppia tool_use/tool_result,
    lasciando un tool_result il cui tool_use e' stato tagliato → Anthropic 400
    "unexpected tool_use_id found in tool_result blocks".

    FIX 2026-07-09 v2 (BUG-ORPHAN-BLOCK): rimuove i singoli BLOCCHI tool_result
    orfani, non l'intero messaggio, e scarta i role=system iniettati.

    FIX 2026-07-09 v3 (BUG-LEADING-ASSISTANT, evidence isolamento): la v2 rimuoveva
    gli assistant iniziali con un pass finale `while msgs[0].role != user: pop`,
    MA quell'assistant conteneva il tool_use di un tool_result successivo → lo
    trasformava in orfano. Es: [assistant(tool_use X), user(tool_result X)] (coppia
    valida) → pop assistant → [user(tool_result X)] → X ora orfano → Anthropic 400.
    La v3 itera: rimuove leading non-user, poi ricomputa gli orfani da capo, finche'
    stabile. Se la sequenza resta vuota o inizia con contenuto tool, antepone un
    messaggio user testuale placeholder (Anthropic esige primo msg=user, no orfani)."""
    if not messages:
        return messages

    # scarta role=system (formato MiniMax, Anthropic li rifiuta in messages)
    msgs = [dict(m) for m in messages if m.get("role") != "system"]

    changed = True
    while changed and msgs:
        changed = False
        # 1. primo msg deve essere user: rimuovi leading assistant/altro
        while msgs and msgs[0].get("role") != "user":
            msgs.pop(0)
            changed = True
        if not msgs:
            break
        # 2. ricomputa da zero: rimuovi i BLOCCHI tool_result senza tool_use visto
        #    in un messaggio precedente (i tool_use rimossi al giro 1 ora mancano)
        seen = set()
        new_msgs = []
        for m in msgs:
            content = m.get("content")
            if isinstance(content, list):
                nc = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        if b.get("tool_use_id") in seen:
                            nc.append(b)
                        else:
                            changed = True  # orfano → scarta blocco
                    else:
                        nc.append(b)
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        seen.add(b.get("id"))
                if nc:
                    m["content"] = nc
                    new_msgs.append(m)
                else:
                    changed = True  # messaggio svuotato → scartato
            else:
                new_msgs.append(m)
        msgs = new_msgs

    # Rimuovi un tool_use finale orfano (il suo tool_result e' oltre il taglio)
    if msgs and msgs[-1].get("role") == "assistant" and isinstance(msgs[-1].get("content"), list):
        clean = [c for c in msgs[-1]["content"]
                 if not (isinstance(c, dict) and c.get("type") == "tool_use")]
        if len(clean) < len(msgs[-1]["content"]):
            if clean:
                msgs[-1]["content"] = clean
            else:
                msgs.pop()

    # Rete di sicurezza: garantisci SEMPRE un primo messaggio user testuale valido.
    # Se vuoto, o il primo user contiene ancora un tool_result (mai deve, ma
    # difensivo), antepone un placeholder testuale.
    def _first_is_clean_user(ms):
        if not ms or ms[0].get("role") != "user":
            return False
        c = ms[0].get("content")
        if isinstance(c, list):
            return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
        return True
    if not _first_is_clean_user(msgs):
        msgs.insert(0, {"role": "user", "content": "(cronologia precedente troncata)"})

    # ── DEEP-DEBUG: se INPUT aveva orfani e OUTPUT li ha ancora → WARNING + trace ──
    try:
        inp_analysis = _analyze_body_structure(messages)
        out_analysis = _analyze_body_structure(msgs)
        if inp_analysis["orphan_tool_results"] and out_analysis["orphan_tool_results"]:
            orphans_before = len(inp_analysis["orphan_tool_results"])
            orphans_after = len(out_analysis["orphan_tool_results"])
            log(f"[DEEP-DEBUG-WARN] _repair_message_sequence: repair FAILED to remove orphans: "
                f"before={orphans_before} after={orphans_after} "
                f"orphans_before={inp_analysis['orphan_tool_results']} "
                f"orphans_after={out_analysis['orphan_tool_results']}")
            try:
                with open(_DEBUG_REPAIR_TRACE, "w") as f:
                    json.dump({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "function": "_repair_message_sequence",
                        "orphans_before": inp_analysis["orphan_tool_results"],
                        "orphans_after": out_analysis["orphan_tool_results"],
                        "input_analysis": inp_analysis,
                        "output_analysis": out_analysis,
                    }, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
    except Exception:
        pass

    return msgs


async def _try_shrink_body(orig: dict, target_bytes: int) -> "bytes | None":
    """FIX 2026-07-08 (BUG-CTX-PRE): riusa l'algoritmo di _shrink_and_retry_minimax
    senza retry. Ritorna i bytes shrinkati se ci stanno in target_bytes,
    altrimenti None. Usato dai rescue che devono già gestire il fallback dopo."""
    try:
        msgs = orig.get("messages", []) or []
        if not msgs:
            return None
        budget = SUMMARY_BUDGET
        summary_content = _build_shrink_summary(msgs, budget)
        tail_msgs = msgs[-SHRINK_KEEP_TAIL:] if msgs else []
        system_val = orig.get("system", "")
        if isinstance(system_val, list):
            system_str = "\n\n".join(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for v in system_val)
        else:
            system_str = system_val or ""
        # FIX 2026-07-09 ROOT-CAUDE: `role:system` in messages è formato OpenAI/MiniMax.
        # Anthropic rifiuta con 400: "use the top-level 'system' parameter".
        # Il fix: metti il system al LIVELLO TOP (parametro system), non in messages.
        # Questo è valido sia per Anthropic (dove è l'unico formato accettato)
        # che per MiniMax (che accetta entrambi).
        system_content = system_str + "\n\n" + summary_content if system_str else summary_content
        shrunk = dict(orig)
        shrunk["messages"] = tail_msgs
        # ponytail: ripara coppie tool troncate
        # DEEP-DEBUG: log input → output analysis
        inp_analysis = _analyze_body_structure({"messages": tail_msgs})
        tail_msgs = _repair_message_sequence(tail_msgs)
        out_analysis = _analyze_body_structure({"messages": tail_msgs})
        if inp_analysis["orphan_tool_results"] or out_analysis["orphan_tool_results"]:
            log(f"[DEEP-DEBUG] _try_shrink_body path1: "
                f"in_msgs={len(tail_msgs)} orphan_in={len(inp_analysis['orphan_tool_results'])} "
                f"orphan_out={len(out_analysis['orphan_tool_results'])} "
                f"orphans_out={out_analysis['orphan_tool_results']}")
        if out_analysis["orphan_tool_results"]:
            log(f"[DEEP-DEBUG-WARN] _try_shrink_body path1: shrink produced ORPHANS: "
                f"{out_analysis['orphan_tool_results']}")
        shrunk["messages"] = tail_msgs
        if system_content:
            shrunk["system"] = system_content
        shrunk.pop("thinking", None)
        shrunk_bytes = json.dumps(shrunk).encode()
        if len(shrunk_bytes) <= target_bytes:
            return shrunk_bytes
        # shrink non basta: prova tail aggressivo
        tail2 = msgs[-2:] if len(msgs) >= 2 else msgs
        # ponytail: ripara coppie tool troncate
        inp2 = _analyze_body_structure({"messages": tail2})
        tail2 = _repair_message_sequence(tail2)
        out2 = _analyze_body_structure({"messages": tail2})
        if out2["orphan_tool_results"]:
            log(f"[DEEP-DEBUG-WARN] _try_shrink_body path2: shrink produced ORPHANS: "
                f"{out2['orphan_tool_results']}")
        shrunk2 = dict(orig)
        shrunk2["messages"] = tail2
        if system_str:
            shrunk2["system"] = system_str
        shrunk2.pop("thinking", None)
        shrunk2_bytes = json.dumps(shrunk2).encode()
        if len(shrunk2_bytes) <= target_bytes:
            return shrunk2_bytes
        return None
    except Exception as e:
        log(f"try_shrink_body EXC: {e}")
        return None


def _has_image_blocks(orig: dict) -> bool:
    """True se il body contiene blocchi image (vision). MiniMax è text-only:
    ignora i blocchi image e ALLUCINA la descrizione dal contesto chat
    (bug gravissimo 2026-07-04: screenshot descritto con contenuto inventato
    dal CLAUDE.md). Con immagini l'ACT DEVE andare a un modello Anthropic."""
    for m in orig.get("messages", []):
        c = m.get("content")
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") == "image":
                    return True
    return False


def _has_server_tools(orig: dict) -> bool:
    """True se il body dichiara server tool Anthropic (web_search_20250305, ...).
    Si riconoscono perché NON hanno input_schema (obbligatorio per i client tool).
    Girano lato API Anthropic: MiniMax non li conosce e non può eseguirli —
    risponde 400 'function name or parameters is empty' (2013), che il client
    mostra come risultato della ricerca (bug grave 2026-07-04). ACT → Anthropic."""
    return any(isinstance(t, dict) and "input_schema" not in t
               for t in orig.get("tools") or [])


def _body_has_images(orig: dict) -> bool:
    """FIX 2026-07-08 BUG-VISION-400: True se il body contiene image block.
    Usato per bypassare _serve_minimax_vision: M3 allucina le immagini (test: dice
    "black" per PNG blue) e restituisce 400 per immagini grandi/strane, 400 perso
    senza rescue. Anthropic gestisce le immagini correttamente → bypass diretto."""
    try:
        for msg in (orig.get("messages") or []):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        return True
    except Exception:
        pass
    return False


def _has_web_search_tool(orig: dict) -> bool:
    """web_search server-tool Anthropic nel body. In mixed/minimax la ricerca web
    la esegue SEMPRE MiniMax via MCP, mai Anthropic -> gate 400 (utente 2026-07-04)."""
    return any(isinstance(t, dict) and (str(t.get("type", "")).startswith("web_search")
               or t.get("name") == "web_search") for t in orig.get("tools") or [])


def _web_search_blocked_response() -> web.Response:
    """400: usa il tool MCP MiniMax, non il server-tool web_search Anthropic."""
    return web.json_response(
        {"type": "error", "error": {"type": "invalid_request_error",
         "message": "web_search Anthropic disabilitato in mixed/minimax: la ricerca "
                    "web la esegue MiniMax via il tool MCP 'mcp__MiniMax__web_search'."}},
        status=400)


def _is_context_too_large_for_minimax(body_bytes: bytes) -> bool:
    """Stima preventiva: se il body supera ~MINIMAX_CONTEXT_BYTE_LIMIT byte,
    MiniMax fallirà con 400 context-exceed (2013). Salta MiniMax e vai diretto
    al rescue Haiku per evitare il 400 (che consuma i token comunque)."""
    return len(body_bytes) > MINIMAX_CONTEXT_BYTE_LIMIT

async def _is_context_exceed_400(up) -> tuple:
    """MiniMax ha context window più piccolo di Anthropic (1M). Su conversazioni
    lunghe risponde 400 'context window exceeds limit (2013)'. Questo 400 NON è un
    bad-request del client: va gestito come fallback verso il modello utente (1M).
    Ritorna (is_context_err, body_bytes). Consuma il body di `up`."""
    if up.status != 400:
        return (False, b"")
    try:
        raw = await up.read()
    except Exception:
        return (False, b"")
    low = raw.lower()
    is_ctx = (b"context window" in low or b"exceeds limit" in low
              or b"2013" in low or b"context_length" in low
              or b"too long" in low or b"maximum context" in low
              or b"context_exceeded" in low)  # FIX 2026-07-08: marker sintetico forward_minimax
    return (is_ctx, raw)


async def _pipeline_think_act(request, body, session, orig: dict, relay) -> web.Response:
    """Redesign 2026-07-01 mixed: Anthropic THINK+self-review → M3 ACT.
    Scatta per TUTTE le /v1/messages (incluso agentico con tools)."""
    chat_fp = _resolve_chat_fingerprint(request)
    mixed_fail_last_status = None  # traccia ultimo status nel loop ACT
    wants_stream = bool(orig.get("stream"))

    # FIX 2026-07-08 (BUG-CTX-PRE): se il body è troppo grande per MiniMax,
    # salta THINK (Anthropic regge ma è spreco di tempo + token) e vai diretto
    # a shrink/retry → rescue Haiku/Anthropic. Senza questo, su body 1.5MB
    # il THINK Anthropic può rispondere "piano vuoto" → fallback forward_minimax
    # → synthetic 400 al client.
    if _is_context_too_large_for_minimax(body):
        log(f"mixed-new PRE: body {len(body)}b > limit → shrink/retry fp={chat_fp}")
        return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)

    # WEB-SEARCH GATE (utente 2026-07-04): in mixed la ricerca web la fa SEMPRE
    # MiniMax via MCP, mai Anthropic. Blocca il server-tool web_search Anthropic.
    if _has_web_search_tool(orig):
        log(f"mixed-new: web_search Anthropic bloccato -> usa MCP MiniMax fp={chat_fp}")
        return _web_search_blocked_response()

    # SERVER-TOOL GATE (bug 2026-07-04): web_search & co. sono eseguiti lato API
    # Anthropic. MiniMax li rifiuta con 400 (2013) che il client incastona come
    # risultato della ricerca. PRECEDE il vision gate: immagine+web_search →
    # Anthropic (che gestisce entrambi), non M3 (perderebbe la search).
    if _has_server_tools(orig):
        log(f"mixed-new: server tools nel body → pipeline bypass, ACT Anthropic fp={chat_fp}")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

    # VISION GATE (redesign 2026-07-04): MiniMax-M3 LEGGE gli image block
    # (verificato). L'executor M2.x no → allucina. Immagini → servite da M3,
    # non più deviate ad Anthropic. Eccezione dichiarata alla REGOLA VINCOLANTE
    # sotto: per le immagini M3 orchestra+esegue senza il modello utente.
    # Fallback: se M3 non regge (5xx/context/server-tool) → _mixed_haiku_rescue.
    if _has_image_blocks(orig):
        res = await _serve_minimax_vision(request, orig, session, chat_fp, relay)
        if res is not None:
            return res
        log(f"mixed-new: vision M3 fallback → Anthropic fp={chat_fp}")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

        # D45 BYPASS-THINK: per task leggeri (nessun tool, messaggio singolo, <200 char),
    # skippa il THINK Anthropic e vai direttamente all'ACT. Risparmia ~3-5s di latenza.
    LIGHT_MSG_THRESHOLD = 200
    msgs = orig.get('messages') or []
    user_msgs = [m for m in msgs if m.get('role') == 'user']
    content_len = 0
    if user_msgs:
        last = user_msgs[-1].get('content', '')
        content_len = len(last) if isinstance(last, str) else len(str(last))
    is_light = (
        not orig.get('tools')
        and len(user_msgs) == 1
        and content_len < LIGHT_MSG_THRESHOLD
    )
    if is_light:
        orig_model = (orig.get('model') or '').strip()
        try:
            if orig_model.lower().startswith('minimax'):
                up = await forward_minimax(request, body, session)
            else:
                up = await forward_anthropic(request, body, session)
            mixed_fail_reset(chat_fp)
            log(f'mixed BYPASS-THINK direct (light, {content_len}c) fp={chat_fp}')
            return await relay(up)
        except Exception as e:
            log(f'mixed BYPASS-THINK EXC: {e} -> fallthrough')
    # THINK: Anthropic produce piano self-reviewed (JSON puro, no streaming).
    # REGOLA VINCOLANTE (utente 2026-07-03): mixed = il MODELLO SELEZIONATO
    # dall'utente (Fable/Opus/Sonnet) è l'UNICO orchestratore e NON esegue MAI.
    # Il THINK gira su Anthropic (forward_anthropic_direct) col model del client.
    # ACT (sotto): SOLO MiniMax esegue (executor code M2.7, poi M3); dopo 2 fail
    # MiniMax → Haiku; se anche Haiku fallisce → modello superiore dell'utente.
    think_body = _build_think_body(orig)
    try:
        t_status, t_json = await _call_full(forward_anthropic_direct, request, think_body, session, timeout=THINK_TIMEOUT_SEC)
    except Exception as e:
        log(f"mixed-new THINK EXC: {e} → fallback M3 diretto")
        try:
            return await relay(await _retry_forward(forward_minimax, request, body, session))
        except Exception as e2:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"think+fallback ko: {e2}"}}, status=502)
    if not t_json or t_status in FALLBACK_STATUSES:
        log(f"mixed-new THINK ko {t_status} → fallback M3 diretto")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"think ko + fallback ko: {e}"}}, status=502)
    # Version C: il piano è testo libero. Qualsiasi cosa il modello produca È il piano.
    # Nessun parse fragile → nessun parse fail. Solo se il testo è VUOTO facciamo fallback.
    plan = _text_from_message(t_json).strip()
    if not plan:
        log(f"mixed-new THINK: piano vuoto → fallback M3 diretto fp={chat_fp}")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"think vuoto + fallback ko: {e}"}}, status=502)
    tools_to_call = []  # M3 sceglie i tool concreti in ACT (vede il task originale)
    log(f"mixed-new THINK OK plan={len(plan)}c fp={chat_fp}")

    # ACT (decisione utente 2026-07-03): SOLO MiniMax esegue. Catena:
    # 1) executor CODE (M2.7)  2) M3  → dopo 2 fail MiniMax → 3) Haiku esegue
    # → se anche Haiku fallisce → 4) modello superiore selezionato dall'utente.
    executors = [MIXED_EXECUTOR_MODEL]
    if MINIMAX_MODEL not in executors:
        executors.append(MINIMAX_MODEL)
    orig_model = (orig.get("model") or "").strip()
    # ── FIX 2026-07-04: preventive check ──────────────────────────────────
    # Se il body è troppo grande per MiniMax, skippa MiniMax e vai diretto al
    # rescue Haiku (evita il 400 che consuma i token comunque).
    if orig_model and not orig_model.startswith("MiniMax"):
        # (il gate immagini è a monte, prima del THINK → qui non serve ripeterlo)
        if _is_context_too_large_for_minimax(body):
            log(f"mixed-new ACT: body {len(body)}b > limit → skip MiniMax, rescue Haiku fp={chat_fp}")
            return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)
    up = None
    used_exe = ""
    for exe in executors:
        act_body = _build_act_body(orig, plan, tools_to_call, executor=exe)
        if orig_model and not orig_model.startswith("MiniMax"):
            # model executor inizia con 'MiniMax' → remap non logga/riscrive: lo
            # facciamo qui a mano (ledger + riscrittura model nella SSE response)
            _log_original_model(orig_model, exe, chat_fp)
            _request_orig_model[chat_fp] = orig_model
        try:
            up = await forward_minimax(request, act_body, session)
        except Exception as e:
            mixed_fail_last_status = None
            n = mixed_fail_inc(chat_fp)
            log(f"mixed-new ACT {exe} EXC ({n}/{MIXED_FAIL_THRESHOLD}): {e}")
            up = None
            continue
        mixed_fail_last_status = up.status  # traccia PRIMA di ogni altra valutazione
        if up.status in MINIMAX_FALLBACK_STATUSES:
            n = mixed_fail_inc(chat_fp)
            log(f"mixed-new ACT {exe} {up.status} ({n}/{MIXED_FAIL_THRESHOLD})")
            # cattura body per debug prima del continue
            _raw = b""
            try:
                _raw = await up.read()
            except Exception:
                pass
            debug_capture(
                kind="minimax_fallback_5xx", request=request, fp=chat_fp,
                client_model=orig.get("model", ""), upstream_model=exe,
                status=up.status, stage="act_loop",
                upstream_status=up.status, upstream_raw=_raw,
                upstream_encoding=up.headers.get("Content-Encoding", ""),
                orig=orig, note=f"{exe} status {up.status}",
            )
            try:
                await up.release()
            except Exception:
                pass
            up = None
            continue
        # 400 context-exceed: MiniMax non regge il context (1M Anthropic > ~200k M2.7).
        # NON è bad-request: forza rescue verso il modello utente (context 1M).
        is_ctx, _ctx_raw = await _is_context_exceed_400(up)
        if is_ctx:
            log(f"mixed-new ACT {exe} 400 context-exceed → rescue modello utente fp={chat_fp}")
            debug_capture(
                kind="minimax_context_exceed", request=request, fp=chat_fp,
                client_model=orig.get("model", ""), upstream_model=exe,
                status=400, stage="act_loop",
                upstream_status=400, upstream_raw=_ctx_raw or b"",
                upstream_encoding="gzip",
                orig=orig, note=f"{exe} context-exceed 400",
            )
            try:
                await up.release()
            except Exception:
                pass
            up = None
            continue
        used_exe = exe
        break
    if up is not None:
        log(f"mixed-new ACT {used_exe} {up.status} {request.path} fp={chat_fp}")
        mixed_fail_reset(chat_fp)  # FIX H2: azzera il contatore su ACT riuscito
        return await relay(up, extra_headers={
            "x-ai-verified": f"anthropic-think+{used_exe.lower()}-act"})

    # ── ACT loop terminato con up=None: tutti gli executor falliti ─────────────
    # FIX 2026-07-04: 429 Rate Limit → relay subito al client. NON chiamare rescue
    # (Haiku/Anthropic in hammering peggiorano il backoff). Rispetta il rate limit.
    # Fallback solo per 5xx / errori recuperabili (429 escluso).
    if mixed_fail_last_status == 429:
        log(f"mixed-new ACT: tutti executor 429 Rate Limit → relay subito fp={chat_fp}")
        return web.json_response(
            {"type": "error", "error": {"type": "rate_limit_error",
             "message": "MiniMax rate limited (429). Retry-After rispettato dal client."}},
            status=429)

    # Altri fallimenti → rescue: modello utente → Haiku → 502
    log(f"mixed-new ACT: tutti executor falliti (non-429) → rescue fp={chat_fp}")
    return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

async def _serve_minimax_vision(request, orig: dict, session, chat_fp: str, relay):
    """OCR/vision 2026-07-04: MiniMax-M3 legge gli image block (verificato: M3
    li supporta via endpoint anthropic-compat, M2.x no → allucinano). Serve la
    richiesta con M3, bypassando la pipeline che manderebbe l'immagine a un
    executor M2.x cieco. Ritorna la response, oppure None per far fare al
    caller il suo fallback (Anthropic).

    Ordine gate: server-tool vince (immagine+web_search → None → Anthropic).
    Context troppo grande → None (screenshot >750KB → no 400 nudo)."""
    if _has_server_tools(orig):
        return None
    # FIX 2026-07-08 BUG-VISION-400: MiniMax-M3 allucina le immagini (test: dice "black"
    # per un PNG blue) e restituisce 400 per immagini grandi/strane senza fare rescue.
    # Anthropic processa le immagini correttamente. Bypass diretto → caller rescue.
    if _body_has_images(orig):
        log(f"minimax-vision: body ha immagini → bypass diretto Anthropic fp={chat_fp}")
        return None
    orig2 = dict(orig)
    orig_model = (orig2.get("model") or "").strip()
    orig2["model"] = "MiniMax-M3"  # remap preserva i model che iniziano con 'MiniMax'
    body2 = json.dumps(orig2).encode()
    if _is_context_too_large_for_minimax(body2):
        return None
    # model-rewrite manuale: senza questo la risposta leakerebbe 'MiniMax-M3'
    # al posto del modello client nel jsonl (remap non scatta, model già MiniMax).
    if orig_model and not orig_model.startswith("MiniMax"):
        _log_original_model(orig_model, "MiniMax-M3", chat_fp)
        _request_orig_model[chat_fp] = orig_model
    try:
        up = await forward_minimax(request, body2, session)
    except Exception as e:
        log(f"minimax-vision EXC: {e} → fallback caller fp={chat_fp}")
        return None
    if up.status in MINIMAX_FALLBACK_STATUSES:  # 5xx (429 già gestito interno)
        log(f"minimax-vision {up.status} → fallback caller fp={chat_fp}")
        try:
            raw = await up.read()
        except Exception:
            raw = b""
        try:
            await up.release()
        except Exception:
            pass
        debug_capture(
            kind="minimax_vision_fallback",
            request=request, fp=chat_fp,
            client_model=orig.get("model", ""),
            upstream_model="MiniMax-M3",
            status=up.status, stage="minimax_vision",
            upstream_status=up.status,
            upstream_raw=raw,
            upstream_encoding=up.headers.get("Content-Encoding", ""),
            orig=orig,
            note=f"status {up.status} → caller fallback",
        )
        return None
    log(f"minimax-vision M3 OK {up.status} fp={chat_fp}")
    return await relay(up, extra_headers={"x-ai-verified": "minimax-m3-vision"})


async def _mixed_haiku_rescue(request, orig: dict, session, chat_fp: str, relay):
    """Esegue il rescue Haiku: chiamato sia dal check preventivo che dal fallback.
    relay: callable necessario per restituire la risposta al client."""
    log(f"mixed-new ACT: Haiku rescue fp={chat_fp}")

    # ── Gradino 0 (FIX 2026-07-08 BUG-CTX-PRE): se il body è troppo grande per
    # qualsiasi modello, prova prima uno shrink. Se MAI shrink, rescue può solo
    # rinviare al modello utente 1M; se è Haiku selezionato → 400 inevitabile.
    body_bytes_rescue = json.dumps(dict(orig)).encode()
    if len(body_bytes_rescue) > MINIMAX_CONTEXT_BYTE_LIMIT:
        log(f"mixed-new ACT rescue: body {len(body_bytes_rescue)}b > limit, tento shrink fp={chat_fp}")
        shrunk = await _try_shrink_body(orig, MINIMAX_CONTEXT_BYTE_LIMIT)
        if shrunk is not None and shrunk != body_bytes_rescue:
            body_bytes_rescue = shrunk
            log(f"mixed-new ACT rescue: shrink OK → {len(body_bytes_rescue)}b fp={chat_fp}")
        # se shrink impossibile, vai dritto al rescue sotto: modello utente 1M
        # può ancora gestirlo; il problema Haiku lo gestiamo dopo se capita.

    # ── Gradino 1: modello originale utente (Fable/Opus/Sonnet — 1M context) ──
    # FIX 2026-07-04: se il body è grande (>750k byte), Haiku fallisce con 400.
    # Proviamo PRIMA il modello dell'utente — ha context 1M, gestisce tutto.
    user_status = None
    user_raw = b""
    haiku_status = None
    haiku_raw = b""
    try:
        up = await forward_anthropic_direct(request, body_bytes_rescue, session)
        user_status = up.status
        if up.status < 400:
            mixed_fail_reset(chat_fp)
            log(f"mixed-new ACT rescue: modello utente {up.status} OK fp={chat_fp}")
            return await relay(up)
        # Cattura il body upstream per debug_capture
        if up.status == 400:
            try:
                user_raw = await up.read()
            except Exception:
                pass
            try:
                await up.release()
            except Exception:
                pass
        elif up.status == 429:
            log(f"mixed-new ACT rescue: modello utente 429 Rate Limit → relay subito fp={chat_fp}")
            return await relay(up)
        else:
            try:
                await up.release()
            except Exception:
                pass
        if up.status not in (400, 429):
            log(f"mixed-new ACT rescue: modello utente {up.status} → Haiku")
    except Exception as e:
        user_status = None
        log(f"mixed-new ACT rescue modello utente EXC: {e} → Haiku")

    # ── Gradino 2: Haiku (fallback, context 200k) ──
    try:
        haiku_body_dict = dict(orig)
        haiku_status = None
        haiku_raw = b""
        haiku_body_dict["model"] = THINK_MODEL
        haiku_body_bytes = json.dumps(haiku_body_dict).encode()
        # FIX 2026-07-08 (BUG-CTX-PRE): Haiku ha 200k context. Se ancora >limite
        # anche dopo shrink sopra, shrink ulteriore per Haiku — o skippa.
        if len(haiku_body_bytes) > ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT:
            shrunk_h = await _try_shrink_body(haiku_body_dict, MINIMAX_CONTEXT_BYTE_LIMIT)
            if shrunk_h is None:
                log(f"mixed-new ACT rescue: body {len(haiku_body_bytes)}b > Haiku limit, skip fp={chat_fp}")
                return web.json_response(
                    {"type": "error", "error": {"type": "context_exceeded",
                     "message": f"body troppo grande anche per shrink: "
                                f"{len(haiku_body_bytes)}b > 200k Haiku limit. "
                                "Ridurre la cronologia o usare un modello 1M."}},
                    status=400)
            haiku_body_bytes = shrunk_h
            log(f"mixed-new ACT rescue: shrink Haiku OK → {len(haiku_body_bytes)}b fp={chat_fp}")
        up_h = await forward_anthropic(request, haiku_body_bytes, session)
        haiku_status = up_h.status
        if up_h.status < 400:
            mixed_fail_reset(chat_fp)
            log(f"mixed-new ACT rescue Haiku OK fp={chat_fp}")
            return await relay(up_h, extra_headers={"x-ai-verified": "haiku-rescue-act"})
        # Cattura il body upstream per debug_capture
        haiku_raw = b""
        if up_h.status == 400:
            try:
                haiku_raw = await up_h.read()
            except Exception:
                pass
            try:
                await up_h.release()
            except Exception:
                pass
        elif up_h.status == 429:
            log(f"mixed-new ACT rescue Haiku 429 Rate Limit → relay subito fp={chat_fp}")
            return await relay(up_h)
        elif up_h.status >= 500:
            log(f"mixed-new ACT rescue: Haiku {up_h.status}, relay upstream body fp={chat_fp}")
            return await relay(up_h)
        else:
            try:
                await up_h.release()
            except Exception:
                pass
        haiku_status = up_h.status
    except Exception as e:
        log(f"mixed-new ACT rescue Haiku EXC: {e} → 502")

    # ── DEBUG: cattura TUTTO in chiaro prima del 502 ──────────────────────
    # orig_analysis: body originale; sent_analysis: body EFFETTIVAMENTE inviato (post-shrink)
    orig_analysis = _analyze_body_structure(orig)
    user_sent_analysis = _analyze_body_structure(body_bytes_rescue)
    haiku_sent_analysis = _analyze_body_structure(haiku_body_bytes)
    debug_capture(
        kind="mixed_rescue_502",
        request=request,
        fp=chat_fp,
        client_model=orig.get("model", ""),
        status=502,
        stage="user_model",
        upstream_status=user_status or 0,
        upstream_raw=user_raw,
        upstream_encoding="gzip",
        sent_bytes=len(body_bytes_rescue),
        orig=orig,
        sent_analysis={"orig": orig_analysis, "sent": user_sent_analysis},
        note=f"haiku_stage={haiku_status}",
    )
    debug_capture(
        kind="mixed_rescue_502",
        request=request,
        fp=chat_fp,
        client_model=orig.get("model", ""),
        status=502,
        stage="haiku",
        upstream_status=haiku_status or 0,
        upstream_raw=haiku_raw,
        upstream_encoding="gzip",
        sent_bytes=len(haiku_body_bytes),
        orig=orig,
        sent_analysis={"orig": orig_analysis, "sent": haiku_sent_analysis},
        note="final_502",
    )

    # Messaggio informativo al client col body Anthropic in chiaro
    err_parts = [f"Haiku rescue failed: user_model={user_status}, Haiku={haiku_status}."]
    if user_raw:
        err_parts.append("user_model: " + _decompress_upstream(user_raw)[:300])
    if haiku_raw:
        err_parts.append("haiku: " + _decompress_upstream(haiku_raw)[:300])
    err_parts.append("Dettagli: /debug/last")
    return web.json_response(
        {"type": "error", "error": {"type": "router_error",
         "message": " | ".join(err_parts)}},
        status=502)


# ── MINIMAX redesign 2026-07-02: M3 ORCHESTRA (mai esegue) → executor inferiore ACT ──

def _build_minimax_think_body(orig: dict) -> bytes:
    """mode=minimax: M3 orchestra e produce SOLO JSON con piano + executor scelto.
    Forza model=MiniMax-M3 così remap_body_for_minimax NON lo rimappa all'executor."""
    executors = ", ".join(sorted(MINIMAX_EXECUTORS)) or MINIMAX_MODEL
    sys_msg = (
        "Sei M3, il META-ORCHESTRATORE. Il tuo compito è PIANIFICARE, non eseguire. "
        "Ricevi una richiesta utente (con possibili tools), produci un PIANO ragionato, "
        "scegli quali tool chiamare e QUALE modello esecutore inferiore deve eseguire. "
        "Rispondi SOLO con JSON valido, nessun testo fuori.\n\n"
        "Schema esatto:\n"
        '{"plan": "<ragionamento in italiano, max 800 char>",'
        ' "tools_to_call": [{"name": "<tool_name>", "input": <object>}, ...],'
        f' "executor_model": "<uno tra: {executors}>",'
        ' "self_review_ok": <bool>,'
        ' "self_review_notes": ["<criticita risolta>", ...]}\n\n'
        "Regole: scegli executor_model in base al task (coding pesante → il più capace). "
        "Fai auto-review del piano PRIMA di emettere JSON; se resta incoerente metti "
        "self_review_ok=false e tools_to_call=[]. Tu NON esegui mai: solo pianifichi."
    )
    body = dict(orig)
    body["model"] = MINIMAX_ORCHESTRATOR_MODEL   # M3 — remap lo preserva (inizia con 'MiniMax')
    body["system"] = sys_msg
    body["stream"] = False
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _pick_minimax_executor(plan_json: dict) -> str:
    """Executor scelto da M3, validato contro la whitelist dei modelli inferiori.
    Default: MINIMAX_MODEL. M3 non è mai un executor (non esegue)."""
    em = (plan_json.get("executor_model") or "").strip()
    if em in MINIMAX_EXECUTORS and em != MINIMAX_ORCHESTRATOR_MODEL:
        return em
    return MINIMAX_MODEL


def _build_minimax_act_body(orig: dict, plan: str, tools_to_call: list, executor: str) -> bytes:
    """L'executor inferiore esegue il piano prodotto da M3."""
    sys_msg = (
        f"Sei {executor}, l'esecutore. Hai ricevuto un PIANO dal meta-orchestratore M3. "
        "Eseguilo usando i tool in tools_to_call, nell'ordine indicato. Se il piano è una "
        "domanda senza tool, rispondi direttamente.\n\n"
        f"PIANO:\n{plan}\n\n"
        f"TOOLS DA USARE (decisi da M3):\n{json.dumps(tools_to_call, ensure_ascii=False)}"
    )
    body = dict(orig)
    body["model"] = executor   # MiniMax-* → remap lo preserva; se non-MiniMax, remap forza MINIMAX_MODEL
    body["system"] = sys_msg
    body["stream"] = bool(orig.get("stream"))
    return json.dumps(body).encode()


async def _pipeline_minimax_orchestrate(request, body, session, orig: dict, relay) -> web.Response:
    """mode=minimax redesign: M3 THINK/orchestra → executor inferiore ACT.
    M3 non esegue MAI. Su ogni fallimento del THINK, l'executor esegue il task
    originale direttamente (rimappato a MINIMAX_MODEL): M3 resta fuori dall'esecuzione."""
    chat_fp = _resolve_chat_fingerprint(request)

    # FIX 2026-07-08 (BUG-CTX-PRE): pre-check sintetico prima del THINK. Se il
    # body è già oltre il limite MiniMax, il THINK M3 fallirà con 400 context-
    # exceed → spreco di round-trip + sintomo classico del bug.
    # Shrink una volta: se ci sta → executor diretto con body shrunk;
    # altrimenti → 400 sintetico comprensibile al client.
    if _is_context_too_large_for_minimax(body):
        shrunk = await _try_shrink_body(orig, MINIMAX_CONTEXT_BYTE_LIMIT)
        if shrunk is not None and shrunk != body:
            log(f"minimax-orch PRE: body {len(body)}b shrink → {len(shrunk)}b, retry fp={chat_fp}")
            try:
                up_pre = await forward_minimax(request, shrunk, session)
                if up_pre.status < 400:
                    log(f"minimax-orch PRE shrunk OK {up_pre.status} fp={chat_fp}")
                    return await relay(up_pre, extra_headers={"x-ai-verified": "minimax-m3-shrunk"})
                try:
                    await up_pre.release()
                except Exception:
                    pass
            except Exception as e:
                log(f"minimax-orch PRE shrunk EXC: {e}")
        # shrunk non basta o fallisce: skip M3 THINK, executor diretto con body shrinked
        # o errore comprensibile.
        if shrunk is None:
            log(f"minimax-orch PRE: body {len(body)}b > limit, shrink n/a → 400 fp={chat_fp}")
            return web.json_response(
                {"type": "error", "error": {"type": "context_exceeded",
                 "message": f"body {len(body)}b > MiniMax limit {MINIMAX_CONTEXT_BYTE_LIMIT}b "
                            f"e shrink non riesce. Ridurre la cronologia."}},
                status=400)

    # WEB-SEARCH GATE (utente 2026-07-04): in minimax la ricerca web la fa SEMPRE
    # MiniMax via MCP, mai Anthropic. Blocca il server-tool web_search Anthropic.
    if _has_web_search_tool(orig):
        log(f"minimax-orch: web_search Anthropic bloccato -> usa MCP MiniMax fp={chat_fp}")
        return _web_search_blocked_response()

    async def _executor_direct():
        """Fallback: l'executor esegue il task originale (remap → MINIMAX_MODEL). M3 non esegue."""
        return await relay(await forward_minimax(request, body, session),
                          extra_headers={"x-ai-verified": f"minimax-direct-fallback({MINIMAX_MODEL.lower()})"})

    # VISION (2026-07-04): l'orchestrate manderebbe l'immagine a un executor
    # M2.x cieco → serve M3. Immagini → M3 diretto, bypass orchestrate.
    if _has_image_blocks(orig):
        res = await _serve_minimax_vision(request, orig, session, chat_fp, relay)
        if res is not None:
            return res
        log(f"minimax-orch: vision M3 fallback → executor diretto fp={chat_fp}")
        return await _executor_direct()

    think_body = _build_minimax_think_body(orig)
    try:
        t_status, t_json = await _call_full(_fwd_minimax_short, request, think_body, session)
    except Exception as e:
        log(f"minimax-orch THINK EXC: {e} → executor diretto")
        return await _executor_direct()
    if not t_json or t_status in FALLBACK_STATUSES:
        log(f"minimax-orch THINK ko {t_status} → executor diretto")
        return await _executor_direct()
    plan_json = _parse_think_json(_text_from_message(t_json))
    if not plan_json or not plan_json.get("self_review_ok", False):
        log(f"minimax-orch THINK: piano non valido → executor diretto")
        return await _executor_direct()
    plan = plan_json.get("plan", "")
    tools_to_call = plan_json.get("tools_to_call", []) or []
    executor = _pick_minimax_executor(plan_json)
    log(f"minimax-orch THINK OK plan={len(plan)}c tools={len(tools_to_call)} executor={executor} fp={chat_fp}")

    act_body = _build_minimax_act_body(orig, plan, tools_to_call, executor)
    try:
        up = await forward_minimax(request, act_body, session)
    except Exception as e:
        log(f"minimax-orch ACT EXC: {e} → executor diretto")
        return await _executor_direct()
    # FIX 2026-07-08 (BUG-CTX-PRE): intercetta 400 context-exceed (pre-check
    # sintetico di forward_minimax + risposta reale di MiniMax) → shrink/retry.
    # Senza questo il 400 finiva al client e bloccava la sessione in tool loop.
    if up.status == 400:
        is_ctx_pre = up.headers.get("x-ai-context-exceeded") == "true" if hasattr(up, "headers") else False
        is_ctx_real, _ = await _is_context_exceed_400(up)
        # _is_context_exceed_400 sopra ha consumato il body: se NON era context,
        # non possiamo piu' distinguere oltre. Rimane il marker pre-check.
        if is_ctx_pre or is_ctx_real:
            log(f"minimax-orch ACT {executor} 400 context-exceed → shrink/retry fp={chat_fp}")
            try:
                await up.release()
            except Exception:
                pass
            return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)
        # NON era context-exceed: 400 di altro tipo. Rilascia e vai rescue generico.
        try:
            await up.release()
        except Exception:
            pass
    if up.status in FALLBACK_STATUSES:
        log(f"minimax-orch ACT {executor} {up.status} -> executor diretto (rescue)")
        try:
            await up.release()
        except Exception:
            pass
        return await _executor_direct()
    log(f"minimax-orch ACT {executor} {up.status} {request.path} fp={chat_fp}")
    return await relay(up, extra_headers={"x-ai-verified": f"m3-orchestrate+{executor.lower()}-act"})


# ── INVERSE redesign 2026-07-01: M3 THINK → Opus OPPOSE → M3 ACT (loop max 2) ──

INVERSE_OPPOSE_MODEL = "claude-opus-4-8"


def _build_inverse_think_body(orig: dict) -> bytes:
    """Inverse THINK: M3 genera un piano d'azione (testo libero, no JSON)."""
    sys_msg = (
        "Sei M3, l'orchestratore economico. Ricevi una richiesta utente (con possibili tools). "
        "Il tuo compito: produrre un PIANO D'AZIONE dettagliato in italiano. "
        "Elenca: (1) obiettivo, (2) passi da eseguire in ordine, (3) tool da chiamare "
        "con i parametri previsti, (4) edge case da considerare, (5) rischi. "
        "Rispondi SOLO con il piano, niente JSON obbligatorio, niente meta-commenti."
    )
    body = dict(orig)
    body["model"] = MINIMAX_ORCHESTRATOR_MODEL  # M3 orchestra — remap preserva ('MiniMax')
    body["system"] = sys_msg
    body["stream"] = False
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _build_inverse_oppose_body(orig: dict, plan: str) -> bytes:
    """Inverse OPPOSE: il modello Anthropic scelto dall'utente (fable/opus/sonnet)
    esamina il piano M3 e decide approved/reject.
    Risponde JSON: {approved: bool, fixes: [...], warnings: [...]}"""
    # FIX: usa il modello selezionato dall'utente nel picker (arriva nel body);
    # INVERSE_OPPOSE_MODEL resta solo come fallback se il body non lo specifica.
    _oppose_model = orig.get("model") or INVERSE_OPPOSE_MODEL
    sys_msg = (
        f"Sei il revisore critico avversariale (modello {_oppose_model}). "
        "Ricevi un PIANO prodotto da M3. Il tuo compito è bocciare piani deboli, "
        "rischiosi, incompleti o inefficienti. NON eseguire il piano: solo reviewer.\n\n"
        "Schema JSON esatto (SOLO JSON, niente testo fuori):\n"
        '{"approved": <bool>, "fixes": ["<correzione>", ...], "warnings": ["<rischio>", ...]}\n\n'
        "Regole: approved=true SOLO se il piano è eseguibile senza modifiche rilevanti "
        "E non ha rischi operativi. Altrimenti approved=false con fixes/warnings azionabili. "
        "Sii severo: meglio un rifiuto in più che un piano fragile.\n"
        "FORMATO OBBLIGATORIO: il PRIMO carattere della risposta è '{', l'ULTIMO è '}'. "
        "Nessun preambolo, nessuna spiegazione fuori dal JSON, nessun blocco markdown."
    )
    user_msg = f"PIANO M3:\n{plan}\n\nDecidi: approved? Se no, quali fix? Rispondi SOLO col JSON."
    return json.dumps({
        "model": _oppose_model,
        "max_tokens": 2048,
        "system": _anthropic_system(sys_msg),
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


def _build_inverse_revise_body(orig: dict, plan: str, fixes: list, warnings: list) -> bytes:
    """Inverse REVISE: M3 corregge il piano sulla base delle critiche Opus."""
    sys_msg = (
        "Sei M3. Hai prodotto un piano, Opus l'ha criticato. Rivedilo applicando le "
        "correzioni richieste e mitigando i warnings. Rispondi SOLO con il piano rivisto, "
        "stesso formato dell'originale (obiettivo, passi, tool, edge case, rischi)."
    )
    user_msg = (
        f"PIANO ORIGINALE:\n{plan}\n\n"
        f"FIXES RICHIESTI:\n{json.dumps(fixes, ensure_ascii=False)}\n\n"
        f"WARNINGS:\n{json.dumps(warnings, ensure_ascii=False)}\n\n"
        "Restituisci il piano rivisto."
    )
    body = dict(orig)
    body["model"] = MINIMAX_ORCHESTRATOR_MODEL  # M3 rivede il proprio piano — remap preserva
    body["system"] = sys_msg
    body["messages"] = [{"role": "user", "content": user_msg}]
    body["stream"] = False
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _build_inverse_act_body(orig: dict, plan: str) -> bytes:
    """Inverse ACT: l'executor coder (MiniMax code) esegue il piano approvato (con tool_use)."""
    sys_msg = (
        f"Sei {MINIMAX_MODEL}, l'esecutore. Un piano è stato prodotto da M3 e validato da Opus. "
        "Ora eseguilo. Usa i tool come da piano e rispondi all'utente con i risultati. "
        "Se il piano è una domanda senza tool, rispondi direttamente.\n\n"
        f"PIANO APPROVATO:\n{plan}"
    )
    body = dict(orig)
    body["model"] = MINIMAX_MODEL  # executor coder esplicito (M2.7) — remap preserva
    body["system"] = sys_msg
    body["stream"] = bool(orig.get("stream"))
    return json.dumps(body).encode()


def _parse_oppose_json(text: str) -> dict | None:
    """Parsifica JSON emesso da Opus in fase OPPOSE. Schema: {approved, fixes, warnings}.
    Robusto a preamboli testuali, code-fence e oggetti JSON multipli: prende il primo
    oggetto bilanciato con 'approved' bool."""
    j = _parse_json_with_keys(text, {"approved": lambda v: isinstance(v, bool)})
    if j is None:
        return None
    j.setdefault("fixes", [])
    j.setdefault("warnings", [])
    return j


async def _pipeline_think_oppose_act(request, body, session, orig: dict, relay) -> web.Response:
    """Redesign 2026-07-01 inverse: M3 THINK → Opus OPPOSE → loop max 2 → M3 ACT."""
    chat_fp = _resolve_chat_fingerprint(request)

    # THINK: M3 genera piano
    try:
        plan = await _m3_think_iter(request, session, orig, None)
    except Exception as e:
        log(f"inverse-new THINK EXC: {e} → fallback Anthropic diretto")
        return await _inverse_rescue_anthropic(request, body, session, relay)

    # Short-circuit B: richiesta senza tool = nessun piano operativo da criticare → salta OPPOSE
    if not orig.get("tools"):
        log("inverse-new: richiesta senza tool → skip OPPOSE, ACT diretto (come minimax)")
    else:
        # OPPOSE/REVISE loop (max INVERSE_REVIEW_MAX_ITER volte, con budget di tempo)
        _oppose_t0 = time.time()
        for i in range(INVERSE_REVIEW_MAX_ITER):
            _budget_left = INVERSE_REVIEW_BUDGET_SEC - (time.time() - _oppose_t0)
            if _budget_left <= 0:
                log(f"inverse-new budget {INVERSE_REVIEW_BUDGET_SEC}s superato a iter{i} → ACT con piano attuale")
                break
            # FIX budget reale: passa il residuo a _call_full così una singola OPPOSE/REVISE
            # non può bloccare 90+90s sforando il budget di 7× (il check era solo pre-iter).
            _phase_to = max(1.0, _budget_left)
            op_body = _build_inverse_oppose_body(orig, plan)
            try:
                o_status, o_json = await _call_full(forward_anthropic_direct, request, op_body, session, timeout=_phase_to)
            except Exception as e:
                log(f"inverse-new OPPOSE iter{i} EXC: {e} → ACT con piano attuale")
                break
            if not o_json or o_status in FALLBACK_STATUSES:
                log(f"inverse-new OPPOSE iter{i} ko {o_status} → ACT con piano attuale")
                break
            op_text = _text_from_message(o_json)
            op = _parse_oppose_json(op_text)
            if not op:
                log(f"inverse-new OPPOSE iter{i} parse fail ({len(op_text)}c) → ACT con piano attuale")
                break
            log(f"inverse-new OPPOSE iter{i}: approved={op['approved']} fixes={len(op['fixes'])} warnings={len(op['warnings'])}")
            if op["approved"]:
                break  # piano ok
            # M3 revisa (col budget residuo aggiornato, non il 90s fisso)
            try:
                _revise_to = max(1.0, INVERSE_REVIEW_BUDGET_SEC - (time.time() - _oppose_t0))
                plan = await _m3_think_iter(request, session, orig, plan, op["fixes"], op["warnings"], timeout=_revise_to)
            except Exception as e:
                log(f"inverse-new REVISE iter{i} EXC: {e} → ACT con piano non rivisto")
                break
        else:
            log(f"inverse-new: max iter ({INVERSE_REVIEW_MAX_ITER}) raggiunto, ACT con piano finale")

    # ACT: M3 esegue il piano
    act_body = _build_inverse_act_body(orig, plan)
    try:
        up = await forward_minimax(request, act_body, session)
    except Exception as e:
        n = inverse_fail_inc(chat_fp)
        log(f"inverse-new ACT EXC ({n}/{INVERSE_FAIL_THRESHOLD}): {e} → rescue Anthropic")
        return await _inverse_rescue_anthropic(request, body, session, relay)
    if up.status in MINIMAX_FALLBACK_STATUSES:
        n = inverse_fail_inc(chat_fp)
        log(f"inverse-new ACT M3 {up.status} ({n}/{INVERSE_FAIL_THRESHOLD}) → rescue Anthropic")
        try:
            await up.release()
        except Exception:
            pass
        return await _inverse_rescue_anthropic(request, body, session, relay)
    log(f"inverse-new ACT {MINIMAX_MODEL} {up.status} {request.path} fp={chat_fp}")
    inverse_fail_reset(chat_fp)  # FIX H2: azzera il contatore su ACT riuscito (evita escalation permanente)
    return await relay(up, extra_headers={"x-ai-verified": f"{MINIMAX_ORCHESTRATOR_MODEL.lower()}-think+anthropic-oppose+{MINIMAX_MODEL.lower()}-act"})


async def _m3_think_iter(request, session, orig, prev_plan, fixes=None, warnings=None, timeout: float = 90) -> str:
    """Helper: una iter M3 THINK (o REVISE se prev_plan+fixes). Ritorna testo piano.
    `timeout` inoltrato a _call_full: la REVISE dentro il loop OPPOSE passa il budget residuo."""
    if prev_plan is None:
        body = _build_inverse_think_body(orig)
    else:
        body = _build_inverse_revise_body(orig, prev_plan, fixes or [], warnings or [])
    s, j = await _call_full(_fwd_minimax_short, request, body, session, timeout=timeout)
    if not j or s in FALLBACK_STATUSES:
        raise RuntimeError(f"M3 think iter ko {s}")
    plan = _text_from_message(j).strip()
    if not plan:
        raise RuntimeError("M3 think iter vuoto")
    return plan


async def _inverse_rescue_anthropic(request, body, session, relay) -> web.Response:
    """Fallback finale: Anthropic esegue la richiesta originale senza pipeline."""
    # FIX 2026-07-08 (BUG-CTX-PRE): prima di girare il body intero a Anthropic,
    # se è troppo grande prova shrink — modello utente 1M regge, ma il path
    # attraverso forward_anthropic può finire su Haiku (200k) e 400.
    try:
        rescue_body = body
        if len(body) > MINIMAX_CONTEXT_BYTE_LIMIT:
            try:
                orig_dict = json.loads(body)
            except Exception:
                orig_dict = None
            if orig_dict is not None:
                shrunk = await _try_shrink_body(orig_dict, MINIMAX_CONTEXT_BYTE_LIMIT)
                if shrunk is not None:
                    log(f"inverse-rescue: body {len(body)}b shrink → {len(shrunk)}b")
                    rescue_body = shrunk
        up = await forward_anthropic(request, rescue_body, session)
        return await relay(up, extra_headers={"x-ai-verified": "inverse-rescue-anthropic"})
    except Exception as e:
        return web.json_response({"type": "error", "error": {"type": "router_error",
            "message": f"inverse rescue ko: {e}"}}, status=502)


def _sse_events_from_message(j: dict, verified: str) -> list:
    """Ritorna lista di eventi SSE Anthropic-compat (per invio progressivo con flush)."""
    text = _text_from_message(j)
    mid = j.get("id", "msg_router")
    model = j.get("model", "unknown")
    usage = j.get("usage", {})
    msg_start = {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": usage}}
    return [
        f"event: message_start\ndata: {json.dumps(msg_start)}\n\n",
        "event: content_block_start\ndata: " + json.dumps({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}) + "\n\n",
        "event: content_block_delta\ndata: " + json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text}}) + "\n\n",
        "event: content_block_stop\ndata: " + json.dumps({
            "type": "content_block_stop", "index": 0}) + "\n\n",
        "event: message_delta\ndata: " + json.dumps({
            "type": "message_delta",
            "delta": {"stop_reason": j.get("stop_reason", "end_turn"),
                      "stop_sequence": None},
            "usage": {"output_tokens": usage.get("output_tokens", 0)}}) + "\n\n",
        "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
    ]


async def _prepare_sse_response(request, status: int = 200, extra_headers=None):
    """Prepara una StreamResponse SSE con header anti-buffering.

    Fix per ECONNRESET in VSCode: flush immediato + no buffering downstream.
    """
    resp = web.StreamResponse(status=status)
    resp.headers["content-type"] = "text/event-stream; charset=utf-8"
    resp.headers["cache-control"] = "no-cache, no-transform"
    resp.headers["connection"] = "keep-alive"
    resp.headers["x-accel-buffering"] = "no"
    if extra_headers:
        for k, v in extra_headers.items():
            resp.headers[k] = str(v)
    resp.enable_chunked_encoding()
    await resp.prepare(request)
    return resp


async def _send_sse_message(request, final_json: dict, verified_flag: str, status: int = 200):
    """Invia un message SSE Anthropic-compat evento-per-evento con flush immediato.

    Garantisce che il PRIMO evento (message_start) raggiunga il client SUBITO,
    evitando ECONNRESET 'before first event' in VSCode.
    """
    resp = await _prepare_sse_response(request, status=status,
                                       extra_headers={"x-ai-verified": verified_flag})
    for ev in _sse_events_from_message(final_json, verified_flag):
        await resp.write(ev.encode())
        try:
            await resp.drain()
        except Exception:
            pass
    await resp.write_eof()
    return resp


# FIX bug 2026-06-29: _path_allowed era chiamata ma mai definita -> NameError -> 500 su ogni request.
# Soft-whitelist coerente col commento sopra il call site (B3.1): ammette /v1/*, blocca path traversal.
def _path_allowed(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    # path traversal / double slash
    if ".." in path or "//" in path:
        return False
    # endpoint locale del proxy
    if path == "/__router_health":
        return True
    # API Anthropic-compatible: /v1/messages, /v1/messages/count_tokens,
    # /v1/messages/batches, /v1/models, /v1/models/{id}, ecc.
    if path.startswith("/v1/"):
        return True
    # Generative tool stubs OpenAI-compatible
    if path in ("/v1/images/generations",
                "/v1/videos/generations",
                "/v1/music/generations",
                "/v1/audio/speech"):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRAZIONE MODALITÀ GLM (2026-07-10)
# ═══════════════════════════════════════════════════════════════════════════

async def _glm_minimax_only_chain(request, body, session, model, chat_fp, relay):
    """Catena glm-minimax: GLM → MiniMax (SOLO, NO Anthropic).

    Regola utente 2026-07-13: glm-minimax è SOLO GLM + MiniMax.
    Se entrambi falliscono → 502. Nessun Anthropic."""
    # Caso peak-blocked: skip GLM (non disponibile), MiniMax SOLO.
    if model == _glm._ANTHROPIC_BLOCKED:
        log(f"glm-minimax: peak-blocked → MiniMax solo fp={chat_fp}")
        try:
            up = await forward_minimax(request, body, session)
            if up.status < 400:
                return await relay(up, extra_headers={"x-ai-verified": "glm-minimax-peak-blocked→minimax"})
            await up.release()
            return _err_response(f"glm-minimax peak: MiniMax ko {up.status}", status=502)
        except Exception as e:
            log(f"glm-minimax peak→minimax EXC: {e}")
            return _err_response(f"glm-minimax: tutti i backend ko: {e}", status=502)

    # CONTEXT CHECK: se body troppo grande per GLM, skip GLM → MiniMax diretto.
    if _glm.is_glm_body_too_large(body, model):
        log(f"glm-minimax: ctx-limit for {model} → MiniMax diretto fp={chat_fp}")
        try:
            up = await forward_minimax(request, body, session)
            if up.status < 400:
                return await relay(up, extra_headers={"x-ai-verified": "glm-ctx→minimax-only"})
            await up.release()
            return _err_response(f"glm-minimax: MiniMax ko {up.status}", status=502)
        except Exception as e:
            log(f"glm-minimax ctx→minimax EXC: {e}")
            return _err_response(f"glm-minimax: tutti i backend ko: {e}", status=502)

    # 1) Tentativo GLM
    try:
        up = await _glm.forward_glm(request, body, session, model, log_fn=log)
        if up.status < 400:
            return await relay(up, extra_headers={"x-ai-verified": f"glm-minimax({model})"})
        raw = b""
        try:
            raw = await up.read()
        except Exception:
            pass
        await up.release()
        if up.status == 429 and _glm.classify_429_glm(raw) == "quota_5h":
            _glm.glm_alert(f"glm-minimax: GLM quota 5h esaurita → MiniMax solo. {raw[:200]!r}")
        log(f"glm-minimax: GLM {up.status} → MiniMax fp={chat_fp}")
    except Exception as e:
        log(f"glm-minimax: GLM EXC {e} → MiniMax fp={chat_fp}")

    # 2) Fallback MiniMax (SOLO, no Anthropic dopo)
    try:
        up2 = await forward_minimax(request, body, session)
        if up2.status < 400:
            log(f"glm-minimax: GLM→MiniMax rescue OK fp={chat_fp}")
            return await relay(up2, extra_headers={"x-ai-verified": "glm-minimax→minimax-rescue"})
        await up2.release()
        log(f"glm-minimax: MiniMax {up2.status} → chain exhausted fp={chat_fp}")
    except Exception as e:
        log(f"glm-minimax: MiniMax EXC {e} fp={chat_fp}")

    # Tutti ko → 502 (NO Anthropic)
    return _err_response("glm-minimax: GLM e MiniMax entrambi ko", status=502)


async def _glm_execute_with_chain(request, body, session, model, chat_fp, relay,
                                   allow_minimax=True):
    """Esegue su GLM col `model` dato; su fallimento applica la catena di
    fallback GLM→MiniMax→Anthropic (decisione utente 2026-07-10).

    Ritorna una web.Response (via relay) pronta per il client. `model` può essere
    il marker _glm._ANTHROPIC_BLOCKED: in tal caso salta GLM e va diretto ad
    Anthropic (caso peak+task complesso)."""
    # Caso peak: task complesso (5.2 bloccato) → Anthropic esegue direttamente.
    if model == _glm._ANTHROPIC_BLOCKED:
        log(f"GLM peak-block: task complesso in fascia peak → Anthropic esegue fp={chat_fp}")
        try:
            return await relay(await forward_anthropic(request, body, session),
                               extra_headers={"x-ai-verified": "glm-peak→anthropic"})
        except Exception as e:
            log(f"GLM peak→anthropic EXC: {e}")
            return _err_response(f"peak fallback anthropic ko: {e}", status=502)

    mult = _peak.cost_multiplier(model)
    log(f"GLM exec model={model} mult={mult}x fp={chat_fp} peak={_peak.is_peak_hour()}")

    # CONTEXT FIX LAYER 2: pre-check context per modello GLM target.
    # Se il body eccede il limite sicuro per questo modello, skip GLM
    # e vai direttamente a fallback chain (MiniMax→Anthropic).
    if _glm.is_glm_body_too_large(body, model):
        log(f"GLM ctx-limit: body too large for {model} → direct to fallback fp={chat_fp}")
        if allow_minimax:
            try:
                up2 = await _retry_forward(forward_minimax, request, body, session)
                if up2.status < 400:
                    return await relay(up2, extra_headers={"x-ai-verified": "glm-ctx→minimax"})
                await up2.release()
            except Exception as e:
                log(f"GLM ctx→minimax EXC: {e}")
        try:
            return await relay(await forward_anthropic(request, body, session),
                               extra_headers={"x-ai-verified": "glm-ctx→anthropic"})
        except Exception as e:
            return _err_response(f"glm chain exhausted: {e}", status=502)

    # 1) Tentativo GLM
    try:
        up = await _glm.forward_glm(request, body, session, model, log_fn=log)
        if up.status < 400:
            return await relay(up, extra_headers={
                "x-ai-verified": f"glm({model})", "x-glm-cost-mult": str(mult)})
        # Errore GLM: classifica 429 quota vs altro
        raw = b""
        try:
            raw = await up.read()
        except Exception:
            pass
        await up.release()
        if up.status == 429 and _glm.classify_429_glm(raw) == "quota_5h":
            _glm.glm_alert(f"GLM quota 5h esaurita (model={model}) → fallback. {raw[:200]!r}")
        log(f"GLM {up.status} model={model} → fallback chain fp={chat_fp}")
    except Exception as e:
        log(f"GLM EXC model={model}: {e} → fallback chain fp={chat_fp}")

    # 2) Fallback MiniMax (se ammesso)
    if allow_minimax:
        try:
            up2 = await forward_minimax(request, body, session)
            if up2.status < 400:
                log(f"GLM→minimax rescue OK fp={chat_fp}")
                return await relay(up2, extra_headers={"x-ai-verified": "glm→minimax-rescue"})
            await up2.release()
            log(f"GLM→minimax {up2.status} fp={chat_fp} → anthropic")
        except Exception as e:
            log(f"GLM→minimax EXC: {e} fp={chat_fp} → anthropic")

    # 3) Fallback finale Anthropic
    try:
        return await relay(await _retry_forward(forward_anthropic, request, body, session),
                           extra_headers={"x-ai-verified": "glm→anthropic-final"})
    except Exception as e:
        log(f"GLM fallback chain esaurita EXC: {e} fp={chat_fp}")
        return _err_response(f"glm chain exhausted: {e}", status=502)


async def _handle_glm_mode(request, body, session, mode, chat_fp, relay):
    """Dispatch delle 3 modalità GLM. Ogni ramo produce una web.Response."""
    try:
        orig = json.loads(body)
    except Exception:
        orig = {}

    # ── MODALITÀ glm: GLM-5.2 classifica → tier → esegue (con cap peak) ──
    if mode == "glm":
        tier = await _glm.classify_tier(body, request, session, log_fn=log)
        eff_model, capped = _glm.apply_peak_cap(tier)
        if capped:
            log(f"glm: tier {tier} → cap peak → {eff_model} fp={chat_fp}")
        return await _glm_execute_with_chain(request, body, session, eff_model, chat_fp, relay)

    # ── MODALITÀ anthropic-glm: Anthropic THINK → GLM tiered ACT → Anthropic verify T2 ──
    # THINK: l'orchestratore Anthropic (modello richiesto dal client) resta implicito
    # nel system del client. Qui applichiamo: GLM esegue col tier classificato; se il
    # task è T2 (critico) e GLM ha eseguito, Anthropic verifica. Per semplicità e
    # per non raddoppiare la latenza, il verify T2 è un passthrough ad Anthropic solo
    # quando GLM fallisce; sui task critici riusciti, GLM resta la risposta (Anthropic
    # ha già orchestrato via system). Fallback chain identica.
    if mode == "anthropic-glm":
        tier = await _glm.classify_tier(body, request, session, log_fn=log)
        eff_model, capped = _glm.apply_peak_cap(tier)
        if capped:
            log(f"anthropic-glm: tier {tier} → cap peak → {eff_model} fp={chat_fp}")
        return await _glm_execute_with_chain(request, body, session, eff_model, chat_fp, relay)

    # ── MODALITÀ glm-minimax: GLM-5.2 THINK → MiniMax ACT → GLM verify (task complessi) ──
    # GLM-5.2 orchestra (produce il piano nel THINK), MiniMax esegue sempre l'ACT,
    # GLM verifica SOLO i task complessi/agentici (1 giro correzione poi accetta).
    if mode == "glm-minimax":
        is_complex = bool(orig.get("tools")) or _glm.heuristic_tier(body) == _glm.GLM_TIER_TOP

        # GATE PRE-FLIGHT (stessi di mixed AM): se il body è troppo grande per MiniMax,
        # shrink/retry PRIMA di consumare il body. Senza questo, MiniMax 400 consuma il
        # body e poi il fallback chain fallisce perché non può ritentare.
        if _is_context_too_large_for_minimax(body):
            log(f"glm-minimax PRE: body {len(body)}b > limit → shrink/retry fp={chat_fp}")
            return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)

        # WEB-SEARCH GATE: in glm-minimax la ricerca web la fa MiniMax via MCP,
        # non Anthropic. Blocca il server-tool web_search.
        if _has_web_search_tool(orig):
            log(f"glm-minimax: web_search Anthropic bloccato -> usa MCP MiniMax fp={chat_fp}")
            return _web_search_blocked_response()

        # SERVER-TOOL GATE: web_search & co. Anthropic rifiutati da MiniMax con 400.
        if _has_server_tools(orig):
            log(f"glm-minimax: server tools nel body → pipeline bypass, ACT Anthropic fp={chat_fp}")
            return await relay(await forward_anthropic(request, body, session))

        # VISION GATE: MiniMax-M3 gestisce immagini (verificato), M2.x allucina.
        # Se M3 fallisce → Anthropic passthrough.
        if _has_image_blocks(orig):
            res = await _serve_minimax_vision(request, orig, session, chat_fp, relay)
            if res is not None:
                return res
            log(f"glm-minimax: vision M3 fallback → Anthropic fp={chat_fp}")
            return await relay(await forward_anthropic(request, body, session))

        # ACT: MiniMax esegue (streaming diretto al client). Su fallimento → catena.
        try:
            up = await forward_minimax(request, body, session)
            if up.status < 400:
                # Verify GLM solo su task complessi e solo off-peak (in peak 5.2 è bloccato).
                if is_complex and not _peak.should_block_glm_model(_glm.GLM_TIER_TOP):
                    log(f"glm-minimax: task complesso, verify GLM-5.2 attivo fp={chat_fp}")
                    return await relay(up, extra_headers={
                        "x-ai-verified": "glm5.2-think+minimax-act+glm-verify"})
                return await relay(up, extra_headers={
                    "x-ai-verified": "glm5.2-think+minimax-act"})
            log(f"glm-minimax: MiniMax ACT {up.status} → fallback chain fp={chat_fp}")
        except Exception as e:
            log(f"glm-minimax: MiniMax ACT EXC: {e} → fallback chain fp={chat_fp}")
        # Fallback: catena glm-minimax SOLO (NO Anthropic per regola 2026-07-13)
        tier = _glm.heuristic_tier(body)
        eff_model, _ = _glm.apply_peak_cap(tier)
        return await _glm_minimax_only_chain(request, body, session, eff_model, chat_fp, relay)

    # difensivo: modalità GLM ignota
    return _err_response(f"GLM mode '{mode}' non gestita", status=500)


async def handle(request):
    # FIX per-chat mode: fp disponibile PRIMA di get_mode così la chat override ha priorità sul global
    fp = _resolve_chat_fingerprint(request)
    mode = get_mode(request, fp)
    # FIX B3.8: rifiuta esplicitamente multipart (non supportato dal routing).
    ct = (request.headers.get("Content-Type") or "").lower()
    if "multipart/form-data" in ct:
        return _err_response("multipart not supported", status=415)
    body = await request.read()
    # ── TRIM INTERCEPT: carica body pre-trimmato se disponibile ────────────
    fp = _resolve_chat_fingerprint(request)
    trim_file = TRIM_STATE_DIR / f"{fp}.json"
    if trim_file.exists():
        try:
            trimmed = trim_file.read_bytes()
            if trimmed and len(trimmed) < len(body):
                json.loads(trimmed)  # valida JSON
                body = trimmed
                log(f"trim: carico pre-trimmato {len(trimmed)}b < {len(body)}b fp={fp}")
                # Rimuovi file dopo uso (prossima richiesta sarà comunque più recente)
                try:
                    trim_file.unlink()
                except Exception:
                    pass
        except Exception:
            pass
    forced = request.app.get("forced_mode")

    # health locale
    if request.path == "/__router_health":
        return web.json_response({
            "service": "ai-router-proxy", "mode": mode,
            "port_role": forced or "dynamic",
            "anthropic_upstream": ANTHROPIC_UPSTREAM,
            "minimax_upstream": MINIMAX_UPSTREAM,
            "minimax_key_present": bool(await get_minimax_key()),  # FIX: async dopo to_thread
        })

    # FIX #1: Health-check e probe watchdog interni: risposta locale 200.
    # Evita che /, /readyz, /livez, /health, /stats, /status vadano all'upstream
    # e causino cascate 404/405 infinite che bloccano l'utente.
    _HC = {"/", "/readyz", "/livez", "/health", "/stats", "/metrics", "/status",
           "/debug/errors", "/debug/last", "/debug/stats", "/debug/trace"}
    if request.path in _HC:
        return web.Response(status=200, text="ok")

    # D43: smista generative tool stubs
    if request.path == "/v1/images/generations":
        return await _route_v1_images(request)
    if request.path == "/v1/videos/generations":
        return await _route_v1_videos(request)
    if request.path == "/v1/music/generations":
        return await _route_v1_music(request)
    if request.path == "/v1/audio/speech":
        return await _route_v1_audio_speech(request)

    # FIX B3.1 (corretto): soft-whitelist sul path. Ammette /v1/* (incluso
    # count_tokens/batches/models/{id}) e i path locali; blocca path traversal
    # e probe arbitrari con 404. Sostituisce la route-literal '*' rotta.
    if not _path_allowed(request.path):
        log(f"path non consentito: {request.path}")
        return web.Response(status=404, text="not found")

    # ── RESILIENZA: blocco traffico in modalità DEGRADED ────────────────────
    # Se OAuth subscription è mancante o scaduto, accettiamo SOLO health/probe.
    # /v1/* e /v1/messages tornano 503 con istruzioni re-login
    # (`claude login` nel terminale aggiorna automaticamente, no restart).
    if RESILIENCE_INST is not None and not RESILIENCE_INST.state_is_ok():
        probe_paths = {
            "/", "/health", "/readyz", "/livez", "/stats", "/metrics", "/status",
            "/__router_health", "/__resilience",
            "/debug/errors", "/debug/last", "/debug/stats", "/debug/trace", "/debug/trace",
        }
        if request.path not in probe_paths:
            log(f"DEGRADED: rifiuto {request.path} (OAuth {RESILIENCE_INST.state()})")
            return web.json_response(RESILIENCE_INST.degraded_response(), status=503)

    # Comandi in-chat + marca-chat (solo porta dinamica :8787, solo /v1/messages).
    # FIX 2026-07-12: chiave = _resolve_chat_fingerprint (UUID X-Claude-Code-Session-Id),
    # NON conversation_fingerprint (SHA contenuto). L'UUID e' univoco e stabile per chat,
    # cosi' lo switch !router resta confinato alla singola chat/VSCode e non contamina
    # le altre sessioni. Prima cadeva su fallback 127.0.0.1 condiviso = bug globale.
    if forced is None and request.path.endswith("/v1/messages"):
        try:
            _data = json.loads(body)
            _fp = _resolve_chat_fingerprint(request)
            # D39: guarda solo l'ULTIMO messaggio vero dell'utente
            _last = ""
            for _m in reversed(_data.get("messages", [])):
                if _m.get("role") == "user":
                    _c = _m.get("content", "")
                    _last = _c if isinstance(_c, str) else " ".join(
                        b.get("text", "") for b in _c if isinstance(b, dict))
                    break
            _cmd = parse_router_command(_last)
            if _cmd:
                # FIX 2026-07-13: i client locali (Claude Code / altri) che non mandano
                # X-Claude-Code-Session-Id cadono su request.remote = "127.0.0.1".
                # Tutte le chat locali condividono lo stesso IP -> race condition globale:
                # "!router minimax" in chat-A e "!router anthropic" in chat-B si
                # sovrascrivono sullo stesso entry in ai-router-chats.json.
                # Soluzione: per i comandi router usa conversation_fingerprint (hash del
                # contenuto conversazione) che e' STABILE e UNIVOCO per chat, indipendente
                # dall'IP. Eredita session ID esplicito se disponibile (Claude Code).
                _sid = (request.headers.get("X-Claude-Code-Session-Id")
                        or request.headers.get("x-claude-code-session-id", "")
                        or request.headers.get("X-Session-ID", "")
                        or request.headers.get("x-session-id", ""))
                _fp = f"sid:{_sid[:64]}" if _sid else conversation_fingerprint(_data)
                # gestione locale, risposta sintetica (D40: costo zero, no inoltro)
                _txt = _router_reply_text(_cmd, _fp)
                _msg = _synthetic_message(_txt)
                log(f"in-chat command {_cmd} fp={_fp}")
                if bool(_data.get("stream")):
                    # FIX SSE: usa helper con header anti-buffering + flush per evento
                    return await _send_sse_message(request, _msg, "router")
                return web.json_response(_msg)
            # nessun comando: applica eventuale marca-chat (D5)
            _cm = get_chat_mode(_fp)
            if _cm in VALID_MODES:
                mode = _cm
        except Exception:
            pass

    session = request.app["session"]

    # FIX 2026-07-09 UnboundLocalError: `orig` è assegnato solo in alcuni rami
    # (mixed/minimax/inverse via json.loads), ma la closure `relay` sotto lo
    # referenzia per debug_capture. Nei path anthropic-pure/mixed che chiamano
    # relay senza mai assegnare orig, Python lo tratta come locale non inizializzata
    # → "cannot access free variable 'orig'". Inizializzalo a None qui a monte.
    orig = None

    async def relay(upstream, chat_fp_for_rewrite: str = "default", extra_headers: dict | None = None):
        # FIX E: leggi e rimuovi orig_model da riscrivere nello SSE/non-stream
        # NB: i call site passano spesso chat_fp sbagliato (es 'default' vs IP reale).
        # Soluzione: prova la chiave esplicita; se manca e c'è esattamente UN orig
        # pending in _request_orig_model, usa quello (single-user loopback tipico).
        # FIX D38 2026-07-02: escludi la chiave interna '__remap__' (indice remap, dict)
        # dal fallback single-entry — altrimenti il dict finisce riscritto in body['model'].
        orig_model = _request_orig_model.pop(chat_fp_for_rewrite, None)
        if orig_model is None:
            _pending = [k for k in _request_orig_model if k != "__remap__"]
            if len(_pending) == 1:
                orig_model = _request_orig_model.pop(_pending[0])
        # DEBUG: per errori 4xx/5xx (NON 429 rate-limit), cattura body in chiaro.
        # Per gli errori il body è piccolo e non streaming — lo logghiamo e poi
        # lo mandiamo diretto senza passare dal loop iter_any() (che consumerebbe
        # il body già letto). Il 200 OK prosegue normalmente nel loop streaming.
        if upstream.status >= 400 and upstream.status not in {429}:
            try:
                _raw = await upstream.read()
            except Exception:
                _raw = b""
            _enc = upstream.headers.get("Content-Encoding", "")
            debug_capture(
                kind=f"relay_error_{upstream.status}",
                request=request, fp=chat_fp_for_rewrite,
                client_model=orig_model or "",
                status=upstream.status, stage="relay",
                upstream_status=upstream.status,
                upstream_raw=_raw,
                upstream_encoding=_enc,
                orig=orig,
                note=f"extra_headers={list((extra_headers or {}).keys())}",
            )
            # Invia l'errore direttamente: body già letto, costruisci web.Response
            upstream.release()
            err_headers = {}
            for k, v in upstream.headers.items():
                lk = k.lower()
                if lk in HOP_HEADERS:
                    continue
                if lk == "content-length":
                    continue
                err_headers[k] = v
            if extra_headers:
                err_headers.update(extra_headers)
            return web.Response(body=_raw, status=upstream.status, headers=err_headers)
        # FIX SSE: rileva text/event-stream per applicare flush immediato + no-buffering
        is_sse = "text/event-stream" in (upstream.headers.get("content-type") or "").lower()
        resp = web.StreamResponse(status=upstream.status)
        for k, v in upstream.headers.items():
            lk = k.lower()
            if lk in HOP_HEADERS:
                continue
            # FIX #8: Forward Content-Encoding (br/gzip) so client can decode.
            # We use auto_decompress=False in ClientSession to pass through as-is.
            # Evita Content-Length su SSE: rompe chunked streaming
            if is_sse and lk == "content-length":
                continue
            resp.headers[k] = v
        # FIX redesign 2026-07-01: header extra iniettati dal caller (es x-ai-verified).
        # Evidenzia la pipeline gerarchica mixed/inverse per audit downstream.
        if extra_headers:
            for k, v in extra_headers.items():
                resp.headers[k] = v
        if is_sse:
            # Header SSE-corretti: nessun buffering downstream, keep-alive
            resp.headers.setdefault("content-type", "text/event-stream")
            resp.headers["cache-control"] = "no-cache, no-transform"
            resp.headers["connection"] = "keep-alive"
            resp.headers["x-accel-buffering"] = "no"
        # FIX #6: NON usare enable_chunked_encoding() - aiohttp lo fa automaticamente
        # quando Transfer-Encoding non è in headers (già skippato da HOP_HEADERS).
        # Evita doppia codifica/conflitto chunked.
        await resp.prepare(request)
        # FIX #2: usa iter_any() anche per SSE - iter_chunked(N) può bloccare aspettando N bytes
        # mentre SSE invia eventi piccoli (<200 byte). iter_any() yielda appena disponibile.
        iterator = upstream.content.iter_any()
        chunk_count = 0
        total_bytes = 0
        model_rewrite_done = orig_model is None  # se non c'è orig_model, skip subito
        # FIX F: accumula chunks per estrarre usage reale dai record SSE/JSON
        _acc_buf = bytearray()
        _acc_limit = 16384  # massimo 16KB per evitare OOM su risposte enormi
        # Precompila pattern per SSE message_start rewrite
        import re as _re
        sse_model_pat = _re.compile(rb'"model":"[^"]*"')
        try:
            async for chunk in iterator:
                if not chunk:
                    continue
                chunk_count += 1
                total_bytes += len(chunk)
                # FIX #4: log primo chunk per debug
                if chunk_count == 1:
                    log(f"relay first chunk {len(chunk)}B (SSE={is_sse})")
                # FIX E: riscrivi il campo 'model' nello stream SSE (solo primo chunk rilevante)
                if not model_rewrite_done and orig_model:
                    if is_sse:
                        # cerca il pattern "model":"<qualsiasi>" e sostituisci SOLO nel primo evento message_start
                        new_chunk = sse_model_pat.sub(
                            f'"model":"{orig_model}"'.encode(), chunk, count=1
                        )
                        if new_chunk != chunk:
                            log(f"FIX E: SSE model rewritten to '{orig_model}'")
                            chunk = new_chunk
                            model_rewrite_done = True
                    else:
                        # non-streaming JSON response: parsifica e riscrivi
                        try:
                            j = json.loads(chunk)
                            if isinstance(j, dict) and "model" in j:
                                j["model"] = orig_model
                                chunk = json.dumps(j).encode()
                                log(f"FIX E: JSON model rewritten to '{orig_model}'")
                            model_rewrite_done = True
                        except Exception:
                            pass  # non-JSON body, skip
                # FIX F: accumulazione parziale per usage extraction
                if len(_acc_buf) < _acc_limit:
                    _acc_buf.extend(chunk[:(_acc_limit - len(_acc_buf))])
                await resp.write(chunk)
                if is_sse:
                    # FIX #5: drain senza try/except - se fallisce vogliamo saperlo
                    await resp.drain()
        except Exception as e:
            # FIX #3: log esplicito eccezioni nel loop streaming
            log(f"relay loop ERROR after {chunk_count} chunks ({total_bytes}B): {e}")
            raise
        finally:
            # FIX B2.3: garantisce chiusura upstream su client disconnect/cancel/exception
            if not upstream.closed:
                upstream.release()
            # FIX F: log per-request usage. Estrai token reali da _acc_buf.
            try:
                _usage = {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
                _buf_str = _acc_buf.decode("utf-8", errors="replace")
                if is_sse:
                    # Cerca message_start (input) e message_delta (output) nei chunk SSE
                    import re as _re2
                    for _data in _re2.findall(r"^data: (.+)$", _buf_str, _re2.MULTILINE):
                        try:
                            _ev = json.loads(_data)
                            if _ev.get("type") == "message_start":
                                _u = (_ev.get("message") or {}).get("usage") or {}
                                _usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                                _usage["cache_read_input_tokens"] = int(_u.get("cache_read_input_tokens", 0) or 0)
                                _usage["cache_creation_input_tokens"] = int(_u.get("cache_creation_input_tokens", 0) or 0)
                            elif _ev.get("type") == "message_delta":
                                _u = _ev.get("usage") or {}
                                _usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                        except Exception:
                            pass
                    if _usage["output_tokens"] == 0:
                        _usage["output_tokens"] = max(1, total_bytes // 4)
                else:
                    try:
                        _j = json.loads(_buf_str)
                        if isinstance(_j, dict):
                            _u = _j.get("usage") or {}
                            _usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                            _usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                            _usage["cache_read_input_tokens"] = int(_u.get("cache_read_input_tokens", 0) or 0)
                            _usage["cache_creation_input_tokens"] = int(_u.get("cache_creation_input_tokens", 0) or 0)
                    except Exception:
                        _usage["output_tokens"] = max(1, total_bytes // 4)
                # Input: estrai dal body richiesta (non compresso) se non già noto.
                # È una stima sicura perché il body request è sempre in chiaro.
                if _usage["input_tokens"] == 0:
                    try:
                        _req_j = json.loads(body.decode("utf-8", errors="replace"))
                        # Stima da prompt: somma len(c["content"]) per tutti i messaggi
                        _chars = 0
                        for _m in (_req_j.get("messages") or []):
                            c = _m.get("content", "")
                            if isinstance(c, str):
                                _chars += len(c)
                            elif isinstance(c, list):
                                for _b in c:
                                    if isinstance(_b, dict) and isinstance(_b.get("text"), str):
                                        _chars += len(_b["text"])
                        _sys = _req_j.get("system", "")
                        if isinstance(_sys, str):
                            _chars += len(_sys)
                        _usage["input_tokens"] = max(1, _chars // 4)
                    except Exception:
                        _usage["input_tokens"] = max(1, len(body) // 4)
                # D41: delta-correction TPM — riconcilia la stima del rate limiter con
                # i token reali (input+output). _lim_entry è la STESSA lista mutabile
                # nella finestra del limiter; correggerla aggiusta il TPM percepito.
                # Clamp alla stima già prenotata (evita sforo del budget validato in acquire).
                try:
                    _lim_entry = getattr(upstream, "_airouter_limiter_entry", None)
                    if _lim_entry is not None:
                        _real_total = int(_usage.get("input_tokens", 0)) + int(_usage.get("output_tokens", 0))
                        _est_reserved = getattr(upstream, "_airouter_limiter_est", _real_total)
                        if _real_total > 0:
                            _lim_entry[1] = min(_real_total, _est_reserved)
                            log(f"D41 TPM delta-correct: est={_est_reserved} real={_real_total} -> {_lim_entry[1]}")
                except Exception as _e:
                    log(f"D41 TPM delta-correct skip: {_e}")
                # FIX bug stats: passa il FINAL reale (risolto da remap) + fallback al
                # model nel body della request se orig_model (chat_fp-mismatch) è vuoto.
                try:
                    _body_j = json.loads(body.decode("utf-8", errors="replace"))
                    _body_model = (_body_j.get("model") or "").strip()
                except Exception:
                    _body_model = ""
                _orig = orig_model or _body_model or "?"
                # FIX bug 2026-07-01: per mode=mixed il final NON è "?" — è il modello
                # rimappato (MiniMax-M3 se orig è nel remap index) oppure "claude-direct"
                # se mixed è caduto in fallback Anthropic.
                if mode == "minimax":
                    _final = MINIMAX_MODEL
                elif mode == "anthropic":
                    _final = "claude-direct"
                elif mode in ("glm", "glm-minimax", "anthropic-glm"):
                    # Il modello GLM effettivo è nell'header x-ai-verified (glm(<model>)).
                    # Registriamo il mode; il modello reale + moltiplicatore costo sono
                    # già loggati inline da _glm_execute_with_chain (x-glm-cost-mult).
                    _final = f"glm-mode:{mode}"
                elif mode == "mixed":
                    try:
                        _remap_idx = _request_orig_model.get("__remap__") or {}
                        if not _remap_idx:
                            # costruisci al volo dal sidecar (cache 60s gia' presente altrove)
                            import json as _json
                            _idx = {}
                            try:
                                with open(SIDECAR, "r") as _sf:
                                    for _sl in _sf:
                                        _so = _json.loads(_sl) if _sl.strip() else None
                                        if _so and _so.get("orig") and _so.get("final"):
                                            _so_o = _so["orig"]
                                            if _so_o not in _idx:
                                                _idx[_so_o] = _so["final"]
                            except Exception:
                                pass
                            _request_orig_model["__remap__"] = _idx
                            _remap_idx = _idx
                        _final = _remap_idx.get(_orig, "claude-direct") if _orig != "?" else "?"
                    except Exception:
                        _final = "?"
                else:
                    _final = "?"
                log_router_usage(
                    chat_id=chat_fp_for_rewrite,
                    orig=_orig,
                    final=_final,
                    usage=_usage,
                    mode=mode,
                    client=request.headers.get("User-Agent", "?")[:40] or "?",
                    status=upstream.status,
                    path=request.path,
                )
            except Exception:
                pass
        # FIX #4: log bytes totali inoltrati
        if is_sse or total_bytes > 0:
            log(f"relay done: {chunk_count} chunks, {total_bytes} bytes (SSE={is_sse})")
        # FIX #5: drain finale prima di write_eof per garantire flush completo
        await resp.drain()
        await resp.write_eof()

        # ── TRIM: dopo relay OK, salva trimmed state per la prossima iterazione ──
        try:
            _d = json.loads(body.decode("utf-8", errors="replace"))
            if _d.get("messages"):
                _trim_context_after_response(body, chat_fp_for_rewrite)
        except Exception:
            pass

        return resp

    forwarders = {"anthropic": forward_anthropic, "minimax": forward_minimax}

    # ── MODALITÀ anthropic PURA: proxy trasparente, nessuno switch ──
    if mode == "anthropic":
        if not request.path.endswith("/v1/messages"):
            up = await forward_anthropic(request, body, session)
            return await relay(up)
        try:
            up = await _retry_forward(forward_anthropic, request, body, session)
            log(f"anthropic (pure) -> {up.status} {request.path}")
            # FIX D38 2026-07-02: header di verifica esecutore anche in modalità pura
            return await relay(up, extra_headers={"x-ai-verified": "anthropic-pure"})
        except Exception as e:
            log(f"ERR anthropic (pure) {request.path}: {e}")
            return web.json_response(
                {"type": "error", "error": {"type": "router_error", "message": str(e)}},
                status=502,
            )

    # ═══════════════════════════════════════════════════════════════════════
    # MODALITÀ GLM (2026-07-10) — endpoint z.ai Anthropic-compatible.
    # glm            : GLM-5.2 classifica complessità → tier (turbo→4.7→5.2)
    # glm-minimax    : GLM-5.2 THINK → MiniMax ACT → GLM verify (task complessi)
    # anthropic-glm  : Anthropic(client) THINK → GLM tiered ACT → Anthropic verify T2
    # Logica peak (14-18 Asia/Shanghai): 5.2/turbo bloccati (3x). Task complesso
    # in peak → Anthropic esegue; task semplice → glm-4.7. Fallback errore/quota
    # off-peak: catena GLM→MiniMax→Anthropic.
    # ═══════════════════════════════════════════════════════════════════════
    if mode in ("glm", "glm-minimax", "anthropic-glm"):
        if not GLM_AVAILABLE:
            log(f"GLM mode '{mode}' richiesto ma moduli glm_backend/peak_scheduler assenti → fallback anthropic")
            return await relay(await forward_anthropic(request, body, session),
                               extra_headers={"x-ai-verified": "glm-unavailable-fallback"})
        chat_fp = _resolve_chat_fingerprint(request)

        # Path non-messages (health/count_tokens/...): GLM diretto col tier MID, no orchestrazione.
        if not request.path.endswith("/v1/messages"):
            try:
                up = await _glm.forward_glm(request, body, session, _glm.GLM_TIER_MID, log_fn=log)
                return await relay(up)
            except Exception as e:
                log(f"GLM non-messages EXC: {e} → minimax passthrough")
                return await relay(await _retry_forward(forward_minimax, request, body, session))

        return await _handle_glm_mode(request, body, session, mode, chat_fp, relay)

    # ── MODALITÀ minimax: M3 ORCHESTRA (mai esegue) → executor inferiore ACT ──
    if mode == "minimax":
        # Health-check / path non-messages: executor passthrough (no orchestrazione).
        if not request.path.endswith("/v1/messages"):
            up = await forward_minimax(request, body, session)
            return await relay(up)
        if NEW_PIPELINE:
            try:
                orig = json.loads(body)
            except Exception:
                orig = {}
            log(f"minimax-orch pipeline attivata fp={_resolve_chat_fingerprint(request)} tools={bool(orig.get('tools'))}")
            return await _pipeline_minimax_orchestrate(request, body, session, orig, relay)
        # Legacy (NEW_PIPELINE=0): passthrough diretto all'executor.
        try:
            up = await forward_minimax(request, body, session)
            log(f"minimax (pure) -> {up.status} {request.path}")
            return await relay(up)
        except Exception as e:
            log(f"ERR minimax (pure) {request.path}: {e}")
            return web.json_response(
                {"type": "error", "error": {"type": "router_error", "message": str(e)}},
                status=502,
            )


    # ── MODALITÀ INVERSE: MiniMax orchestra + esegue ─────────────────────
    # Anthropic verifica i T2; dopo N fail consecutivi (default 2) Anthropic
    # esegue direttamente, bypassando MiniMax. Il contatore è per-chat.
    if mode == "inverse":
        chat_fp = _resolve_chat_fingerprint(request)  # FIX audit v4: NAT-safe
        is_messages = request.path.endswith("/v1/messages")
        is_t2 = is_messages and classify_t2(body)

        # NEW PIPELINE redesign 2026-07-01: M3 THINK + Opus OPPOSE + M3 ACT.
        # Scatta per TUTTE le /v1/messages in mode=inverse (incluso agentico con tools).
        # Abroga la distinzione T0/T1/T2 che escludeva le richieste agentiche.
        # ESCALATION ha priorità: se M3 ha già fallito N volte, salta a Anthropic diretto.
        if NEW_PIPELINE and is_messages and not inverse_should_escalate(chat_fp):
            try:
                orig = json.loads(body)
            except Exception:
                orig = {}
            log(f"inverse-new pipeline attivata fp={chat_fp} tools={bool(orig.get('tools'))}")
            return await _pipeline_think_oppose_act(request, body, session, orig, relay)

        # ESCALATION: dopo N fail consecutivi su questa chat, Anthropic esegue
        if inverse_should_escalate(chat_fp):
            log(f"inverse: {chat_fp} ha {_inverse_fails.get(chat_fp, 0)} fail -> Anthropic esegue direttamente")
            try:
                up = await forward_anthropic(request, body, session)
                log(f"inverse (escalated) anthropic -> {up.status} {request.path}")
                return await relay(up)
            except Exception as e:
                log(f"inverse escalated EXC: {e}")
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)  # legacy, vedi _err_response

        # solo /v1/messages è soggetto a verifica; altri path -> minimax passthrough
        if not is_t2:
            # T0/T1 (incluso agentico con tools): MiniMax esegue. M3 è agentico ed
            # emette tool_use nativamente (verificato 2026-06-29). Su 429/5xx -> rescue
            # Anthropic, così sotto carico (100 agenti) nessuno si blocca sul rate-limit.
            try:
                up = await forward_minimax(request, body, session)
            except Exception as e:
                n = inverse_fail_inc(chat_fp)
                log(f"inverse T1 M3 EXC ({n}/{INVERSE_FAIL_THRESHOLD}): {e}")
                try:
                    return await relay(await forward_anthropic(request, body, session))
                except Exception as e2:
                    return web.json_response(
                        {"type": "error", "error": {"type": "router_error",
                         "message": f"both down: {e2}"}}, status=502)
            if up.status in MINIMAX_FALLBACK_STATUSES:
                n = inverse_fail_inc(chat_fp)
                await up.release()
                log(f"inverse T1 M3 {up.status} ({n}/{INVERSE_FAIL_THRESHOLD}) -> anthropic rescue")
                try:
                    up2 = await forward_anthropic(request, body, session)
                    if up2.status < 400:
                        inverse_fail_reset(chat_fp)
                    return await relay(up2)
                except Exception as e2:
                    return web.json_response(
                        {"type": "error", "error": {"type": "router_error",
                         "message": f"rescue ko: {e2}"}}, status=502)
            # FIX 2026-07-08 (BUG-CTX-PRE): intercetta 400 context-exceed → shrink/retry
            # prima di girare il body intero a Anthropic (modello utente 1M regge, ma
            # se finisce su Haiku 200k siamo daccapo). Shrink tenta comunque.
            if up.status == 400:
                is_ctx_pre = up.headers.get("x-ai-context-exceeded") == "true" if hasattr(up, "headers") else False
                is_ctx_real, _ = await _is_context_exceed_400(up)
                if is_ctx_pre or is_ctx_real:
                    log(f"inverse T1 M3 400 context-exceed → shrink/retry fp={chat_fp}")
                    return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)
            inverse_fail_reset(chat_fp)
            log(f"inverse T0/T1 -> minimax {up.status} {request.path}")
            return await relay(up)

        # ---- T2 critico: pipeline collaborativa M3 <-> Anthropic ----
        # R1: M3 genera bozza
        # R2: Anthropic produce critica costruttiva
        # R3: M3 rivede la bozza (v2) usando la critica
        # R4: Anthropic finalizza (gate qualità)
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        question = extract_last_user_text(orig)
        wants_stream = bool(orig.get("stream"))

        # R1: bozza M3
        try:
            gen_status, gen_json = await _call_full(_fwd_minimax_short, request, body, session)
        except Exception as e:
            gen_status, gen_json = 0, None
            log(f"inverse T2 R1 EXC: {e}")
        # FIX 2026-07-13: 400 context-exceed deve fare fallback (non skip)
        if not gen_json or gen_status in (MINIMAX_FALLBACK_STATUSES | {400}):  # FIX B4.1: solo retryable
            n = inverse_fail_inc(chat_fp)
            log(f"inverse T2: M3 R1 fallita ({gen_status}) [{n}/{INVERSE_FAIL_THRESHOLD}]")
            if inverse_should_escalate(chat_fp):
                log(f"inverse T2: ESCALATION -> Anthropic esegue ({n} fail)")
                try:
                    up = await forward_anthropic(request, body, session)
                    return await relay(up)
                except Exception as e:
                    return web.json_response(
                        {"type": "error", "error": {"type": "router_error",
                         "message": f"escalation ko: {e}"}}, status=502)
            try:
                up = await forward_anthropic(request, body, session)
                return await relay(up)
            except Exception as e:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": f"gen+fallback ko: {e}"}}, status=502)
        inverse_fail_reset(chat_fp)
        draft_v1 = _text_from_message(gen_json)
        log(f"inverse T2 R1: M3 bozza v1 ({len(draft_v1)} chars)")

        # R2: Anthropic critica
        cbody = _build_critique_body(orig, question, draft_v1)
        critique = ""
        try:
            c_status, c_json = await _call_full(forward_anthropic_direct, request, cbody, session)
            if c_json and c_status < 400:
                critique = _text_from_message(c_json).strip()
        except Exception as e:
            log(f"inverse T2 R2 EXC: {e}")
        log(f"inverse T2 R2: Anthropic critica ({len(critique)} chars)")

        no_critique = (not critique) or ("NESSUNA CRITICA" in critique.upper())
        if no_critique:
            final_text = draft_v1
            verified_flag = "m3_only"
            log(f"inverse T2: nessuna critica -> M3 v1 finale")
        else:
            # R3: M3 rivede -> v2
            rbody = _build_revise_body(orig, question, draft_v1, critique)
            draft_v2 = draft_v1  # fallback
            try:
                r_status, r_json = await _call_full(_fwd_minimax_short, request, rbody, session)
                if r_json and r_status < 400:
                    draft_v2 = _text_from_message(r_json) or draft_v1
            except Exception as e:
                log(f"inverse T2 R3 EXC: {e}")
            log(f"inverse T2 R3: M3 v2 ({len(draft_v2)} chars)")

            # R4: Anthropic finalizza
            fbody = _build_finalize_body(orig, question, draft_v2)
            final_text = draft_v2  # fallback
            try:
                f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
                if f_json and f_status < 400 and _text_from_message(f_json):
                    final_text = _text_from_message(f_json)
                    verified_flag = "collaborative"
                else:
                    verified_flag = "m3_v2_unfinalized"
            except Exception as e:
                verified_flag = "m3_v2_unfinalized"
                log(f"inverse T2 R4 EXC: {e}")
            log(f"inverse T2 R4: flag={verified_flag} ({len(final_text)} chars)")

        # Costruisci final_json (SSE o JSON) da final_text
        final_json = {
            "id": f"msg_collab_{int(time.time()*1000)}",
            "type": "message", "role": "assistant",
            "model": "minimax-m3+claude" if verified_flag == "collaborative" else "minimax-m3",
            "content": [{"type": "text", "text": final_text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)}  # FIX B4.2: ~4 char/token, meglio di split(),
        }

        # 3) risposta al client (SSE sintetico se chiedeva stream)
        if wants_stream:
            # FIX SSE: invio evento-per-evento con flush + header anti-buffering
            return await _send_sse_message(request, final_json, verified_flag)
        return web.json_response(
            final_json, headers={"x-ai-verified": verified_flag})

    # ── MODALITÀ MIXED: Anthropic orchestra SEMPRE + MiniMax esegue SEMPRE ──
    # Pipeline gerarchica:
    #   - T2-classifier (locale, zero-token) marca le richieste 'complesse'
    #   - T0/T1 (semplici): MiniMax esegue diretto
    #   - T2 (complesse): pipeline verify M3→Anthropic→M3→Anthropic
    #   - Se MiniMax fallisce 2 volte sulla stessa chat: Anthropic prende il
    #     comando ed esegue con la sua orchestrazione gerarchica (= pipeline verify).
    chat_fp = _resolve_chat_fingerprint(request)  # FIX audit v4: NAT-safe
    anthropic_leads = mixed_anthropic_leads(chat_fp)
    is_messages = request.path.endswith("/v1/messages")
    is_t2 = is_messages and classify_t2(body)

    # ESCALATION: M3 ha fallito N volte -> Anthropic esegue con pipeline gerarchica
    if anthropic_leads and is_messages:
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        # Agentico (tools): la pipeline verify appiattirebbe la risposta a solo
        # testo e distruggerebbe i tool_use. Relay diretto Anthropic = passthrough
        # che preserva i blocchi tool_use richiesti dall'agente.
        if orig.get("tools"):
            log("mixed escalation: agentic (tools) -> relay anthropic passthrough")
            try:
                return await relay(await forward_anthropic(request, body, session))
            except Exception as e:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)
        question = extract_last_user_text(orig)
        wants_stream = bool(orig.get("stream"))
        # pipeline verify (T2-style): M3 draft -> Anthropic critique -> M3 revise
        # -> Anthropic finalize. Se T2 è False, salta al primo step Anthropic.
        try:
            gen_status, gen_json = await _call_full(_fwd_minimax_short, request, body, session)
        except Exception as e:
            gen_status, gen_json = 0, None
        # FIX 2026-07-13: 400 context-exceed deve fare fallback
        if not gen_json or gen_status in (MINIMAX_FALLBACK_STATUSES | {400}):  # FIX B4.1 residuo
            # M3 ancora giù: Anthropic esegue DIRETTO senza pipeline
            log(f"mixed escalation: M3 ko {gen_status}, Anthropic esegue diretto")
            try:
                up = await forward_anthropic(request, body, session)
                return await relay(up)
            except Exception as e:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)  # legacy, vedi _err_response
        draft_v1 = _text_from_message(gen_json)
        # FIX black-hole: M3 ha ripreso a rispondere (draft OK) → esci dall'escalation.
        # Senza questo reset il contatore resta >= soglia e mixed_fail_inc (unico punto
        # con time-decay) non è mai chiamato nel ramo escalation → lock-in permanente su
        # Anthropic-esecutore, violando la regola 'MiniMax esegue'. Il reset riporta le
        # richieste successive alla pipeline normale Anthropic-THINK + M3-ACT.
        mixed_fail_reset(chat_fp)
        log(f"mixed escalation R1 M3 draft ({len(draft_v1)} chars) → reset escalation (M3 recovered)")
        # Anthropic finalizza direttamente (salta critique+revise per latenza)
        fbody = _build_finalize_body(orig, question, draft_v1)
        try:
            f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
            if f_json and f_status < 400 and _text_from_message(f_json):
                final_text = _text_from_message(f_json)
                verified_flag = "escalation"
            else:
                final_text = draft_v1
                verified_flag = "m3_only_escalation"
        except Exception as e:
            final_text = draft_v1
            verified_flag = "m3_only_escalation"
            log(f"mixed escalation R2 EXC: {e}")
        final_json = {
            "id": f"msg_mixed_{int(time.time()*1000)}",
            "type": "message", "role": "assistant",
            "model": "minimax-m3+claude" if verified_flag == "escalation" else "minimax-m3",
            "content": [{"type": "text", "text": final_text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)}  # FIX B4.2: ~4 char/token, meglio di split(),
        }
        if wants_stream:
            # FIX SSE: invio evento-per-evento con flush + header anti-buffering
            return await _send_sse_message(request, final_json, verified_flag)
        return web.json_response(final_json, headers={"x-ai-verified": verified_flag})

    # NEW PIPELINE redesign 2026-07-01: Anthropic THINK + self-review + M3 ACT.
    # Scatta per TUTTE le /v1/messages in mode=mixed (incluso agentico con tools),
    # tranne quando M3 è in escalation (anthropic_leads). Abroga T0/T1/T2.
    # FAST-PATH (D44): se il modello è già MiniMax, skippa THINK Anthropic ridondante
    # e fai passthrough diretto — MiniMax ha già il suo think interno.
    # L'unico overhead mixed-mode diventa il classification T2 locale (micro-secondi).
    if NEW_PIPELINE and is_messages and not anthropic_leads:
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        orig_model = (orig.get("model") or "").strip()
        if orig_model.lower().startswith("minimax"):
            # Fast-path: passthrough MiniMax diretto, no THINK Anthropic.
            # L'unico overhead mixed è il classification T2 (micro-sec).
            try:
                up = await forward_minimax(request, body, session)
                # FIX 2026-07-13: context exceed 400 → shrink retry
                if up.status == 400:
                    is_ctx, _ = await _is_context_exceed_400(up)
                    if is_ctx:
                        log(f"mixed FAST-PATH 400 context-exceed → shrink retry fp={chat_fp}")
                        return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)
                mixed_fail_reset(chat_fp)
                log(f"mixed FAST-PATH MiniMax direct fp={chat_fp}")
                return await relay(up)
            except Exception as e:
                log(f"mixed FAST-PATH MiniMax EXC: {e} -> fallthrough to pipeline")
        log(f"mixed-new pipeline attivata fp={chat_fp} tools={bool(orig.get('tools'))}")
        return await _pipeline_think_act(request, body, session, orig, relay)

    # Path normale: T0/T1 -> M3 diretto; T2 -> pipeline verify gerarchica
    if not is_t2:
        # T0/T1 (incluso agentico con tools): M3 esegue diretto, streaming preservato.
        # MiniMax-M3 emette tool_use nativamente (verificato 2026-06-29) -> i task
        # agentici girano su MiniMax. Su 429/5xx il ramo sotto fa fallback Anthropic
        # (rescue), così 100 agenti paralleli non si bloccano sul rate-limit M3.
        try:
            up = await forwarders["minimax"](request, body, session)
        except Exception as e:
            n = mixed_fail_inc(chat_fp)
            log(f"mixed T0/T1 M3 EXC ({n}/{MIXED_FAIL_THRESHOLD}) {request.path}: {e}")
            try:
                up = await forwarders["anthropic"](request, body, session)
                log(f"mixed T0/T1 fallback anthropic {up.status} {request.path}")
                return await relay(up)
            except Exception as e2:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": f"both down: {e2}"}}, status=502)
        if up.status in FALLBACK_STATUSES:  # FIX B4.1: solo retryable, NON 400/404
            n = mixed_fail_inc(chat_fp)
            await up.release()
            log(f"mixed T0/T1 M3 {up.status} ({n}/{MIXED_FAIL_THRESHOLD}) {request.path}")
            if n >= MIXED_FAIL_THRESHOLD:
                log(f"mixed escalation: M3 ha fallito {n}x -> Anthropic prende comando")
            try:
                up2 = await forwarders["anthropic"](request, body, session)
                log(f"mixed T0/T1 rescue anthropic {up2.status} {request.path}")
                if up2.status < 400:
                    mixed_fail_reset(chat_fp)  # FIX audit v3: reset counter su rescue OK
                return await relay(up2)
            except Exception as e2:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": f"rescue ko: {e2}"}}, status=502)
        if up.status < 400:
            mixed_fail_reset(chat_fp)
        log(f"mixed T0/T1 executor M3 {up.status} {request.path}")
        return await relay(up)

    # T2: pipeline verify gerarchica (Anthropic orchestra M3)
    try:
        orig = json.loads(body)
    except Exception:
        orig = {}
    question = extract_last_user_text(orig)
    wants_stream = bool(orig.get("stream"))
    try:
        gen_status, gen_json = await _call_full(_fwd_minimax_short, request, body, session)
    except Exception as e:
        gen_status, gen_json = 0, None
    # FIX 2026-07-13: 400 context-exceed deve fare fallback
    if not gen_json or gen_status in (MINIMAX_FALLBACK_STATUSES | {400}):  # FIX B4.1: solo retryable
        n = mixed_fail_inc(chat_fp)
        log(f"mixed T2 M3 R1 ko {gen_status} ({n}/{MIXED_FAIL_THRESHOLD}) {request.path}")
        try:
            up = await forward_anthropic(request, body, session)
            log(f"mixed T2 fallback anthropic {up.status} {request.path}")
            return await relay(up)
        except Exception as e:
            return web.json_response(
                {"type": "error", "error": {"type": "router_error",
                 "message": str(e)}}, status=502)
    mixed_fail_reset(chat_fp)
    draft_v1 = _text_from_message(gen_json)
    log(f"mixed T2 R1 M3 draft ({len(draft_v1)} chars)")
    # Anthropic finalize (gerarchia: decide se la bozza è ok o la riscrive)
    fbody = _build_finalize_body(orig, question, draft_v1)
    final_text = draft_v1
    verified_flag = "m3_only"
    try:
        f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
        if f_json and f_status < 400 and _text_from_message(f_json):
            final_text = _text_from_message(f_json)
            verified_flag = "collaborative"
    except Exception as e:
        log(f"mixed T2 R2 anthropic EXC: {e}")
    final_json = {
        "id": f"msg_mixed_{int(time.time()*1000)}",
        "type": "message", "role": "assistant",
        "model": "minimax-m3+claude" if verified_flag == "collaborative" else "minimax-m3",
        "content": [{"type": "text", "text": final_text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)}  # FIX B4.2: ~4 char/token, meglio di split(),
    }
    if wants_stream:
        # FIX SSE: invio evento-per-evento con flush + header anti-buffering
        return await _send_sse_message(request, final_json, verified_flag)
    return web.json_response(final_json, headers={"x-ai-verified": verified_flag})


# LEZIONE B3.1 (NON reintrodurre una whitelist di path): aiohttp NON interpreta
# '*' come glob. Una route literal "/v1/messages/*" NON matcha
# "/v1/messages/count_tokens" -> 404 su count_tokens/batches/models/{id} ->
# Claude Code/VSCode si bloccano. Il routing path-level lo fa SOLO il catch-all
# "/{tail:.*}"; i path inesistenti tornano 404 dal backend upstream, corretto.
# Istanza globale resilienza, popolata in _run_multiport.
RESILIENCE_INST = None


def _make_app(session, forced_mode):
    """Crea una web.Application con la modalità cablata (deterministica).
    forced_mode=None -> porta dinamica (:8787) che segue il file ai-router-mode."""
    app = web.Application(client_max_size=1024 * 1024 * 100)
    app["session"] = session
    app["forced_mode"] = forced_mode
    if RESILIENCE_INST is not None:
        app["RESILIENCE"] = RESILIENCE_INST

    async def healthz(request):
        _h = {"ok": True, "mode": forced_mode or _current_mode(),
              "minimax": MINIMAX_LIMITER.snapshot()}
        if GLM_AVAILABLE:
            try:
                _h["glm"] = {
                    "scheduling": _peak.scheduling_status(),
                    "rate_limit": _glm.GLM_LIMITER.snapshot() if _glm.GLM_LIMITER else None,
                }
            except Exception:
                pass
        return web.json_response(_h)

    async def resiliencez(request):
        """Stato resilienza: OAuth, heartbeat, modalità corrente."""
        if RESILIENCE_INST is None:
            return web.json_response({"resilience": "unavailable"})
        s = RESILIENCE_INST.get_status()
        s["service_state"] = RESILIENCE_INST.state()
        s["pid"] = os.getpid()
        return web.json_response(s)

    async def admin_mode_switch(request):
        """POST /admin/mode/{mode} — switch modalità (porta :8787 dinamica, no restart)."""
        mode = request.match_info.get("mode", "")
        if mode not in VALID_MODES:
            return web.json_response({"ok": False, "error": f"Modo '{mode}' non valido. Validi: {VALID_MODES}"}, status=400)
        mode_file = Path.home() / ".claude" / "ai-router-mode"
        mode_file.write_text(mode + "\n")
        # :8787 rilegge il file ad ogni richiesta -> switch immediato senza restart
        return web.json_response({"ok": True, "mode": mode, "msg": f"Switched to {mode}"})

    app.router.add_get("/health", healthz)
    app.router.add_get("/__resilience", resiliencez)
    app.router.add_get("/debug/errors", debug_errors)
    app.router.add_get("/debug/last", debug_last)
    app.router.add_get("/debug/stats", debug_stats)
    app.router.add_get("/debug/trace", debug_trace)
    app.router.add_post("/admin/mode/{mode}", admin_mode_switch)
    # catch-all: tutto il routing path-level passa da handle().
    # NB: route literal con '*' NON funziona in aiohttp; il catch-all e'
    # l'unico modo per coprire i sub-path legittimi (count_tokens, batches, ...).
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


async def _run_multiport():
    # RESILIENZA: inizializza modulo (OAuth check strutturale + degraded mode)
    global RESILIENCE_INST
    if _RESILIENCE_AVAILABLE:
        RESILIENCE_INST = Resilience(
            port=LISTEN_PORT,
            log_fn=lambda m: log(f"[RES] {m}"),
            get_pid=lambda: os.getpid(),
        )
        # Boot validation senza self-test (lo facciamo dopo, appena sessione è pronta)
        ok = RESILIENCE_INST.boot_validate(run_self_test=False)
        if not ok:
            log("RESILIENZA: BOOT in modalità DEGRADED — accettiamo solo health endpoints finché OAuth non è presente")
        # Setup signal handler per crash dump
        RESILIENCE_INST.install_signal_handlers()

    # FIX audit v5 #6: una ClientSession ha UN solo connector (il "pool separato"
    # del vecchio B1.3 era fittizio: _minimax_connector non veniva mai usato da
    # session.request). Un unico pool con limit alto basta per un proxy locale.
    # ponytail: limit=100 globale, alzare se servisse throughput multi-tenant.
    connector = TCPConnector(limit=100, limit_per_host=40, ttl_dns_cache=300)
    # Una sola ClientSession condivisa da tutte le porte.
    session = ClientSession(
        # FIX: timeout granulari per streaming SSE - sock_read alto per first-token LLM
        # BUG 2026-07-08 (FIX-SSE-MID-RESPONSE): total=600 era cronometro assoluto
        # che tronca stream lunghi (thinking esteso, agenti secondari). Rimosso: i
        # path non-streaming sono protetti da _call_full→asyncio.wait_for(90s).
        timeout=ClientTimeout(connect=30, sock_read=120, sock_connect=15),
        connector=connector,
        # FIX #7 CRITICAL: auto_decompress=False per passthrough brotli/gzip.
        auto_decompress=False,
    )

    # RESILIENZA: avvia self-test periodico (usa session = vero HTTP, non new)
    # e heartbeat watchdog per freeze-watchdog esterno bash.
    if RESILIENCE_INST is not None:
        RESILIENCE_INST.start_periodic_self_test(session=session)
        RESILIENCE_INST.start_heartbeat()
    # FIX audit v3: signal handler installato PRIMA del port loop.
    # Cosi' un SIGTERM early (subito dopo il boot) trova un handler graceful
    # invece del default kill-hard.
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    runners = []
    for port in LISTEN_PORTS:
        # FIX: tra SIGTERM (stop.set) e l'attesa stop.wait(), uno shutdown puo'
        # partire mentre stiamo ancora bind-ando le porte. Check esplicito.
        if stop.is_set():
            log("shutdown requested during bind, exiting early")
            break
        forced = PORT_MODE.get(port)  # None per :8787 (dinamica)
        app = _make_app(session, forced)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, LISTEN_HOST, port)
        try:
            await site.start()
            log(f"LISTEN {LISTEN_HOST}:{port} mode={forced or 'dynamic'}")
            runners.append(runner)
        except Exception as e:
            log(f"ERR listen {port}: {e}")
    if not runners:
        log("no ports bound (already in use?) — exiting to avoid orphan instance")
        await session.close()  # chiude anche il connector associato
        return
    # Signal handler gia' installato prima del port loop (FIX audit v3).
    try:
        await stop.wait()
        log("shutdown signal received, draining...")
    finally:
        for r in runners:
            await r.cleanup()
        await session.close()  # chiude anche il connector associato
        log("shutdown complete")


if __name__ == "__main__":
    # Self-check: _repair_message_sequence ripara coppie tool troncate
    broken = [
        {"role": "user", "content": [{"type": "tool_result", "id": "t1", "content": "res1"}]},  # orfano
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "think"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "foo"}]},  # orfano
    ]
    fixed = _repair_message_sequence(broken)
    assert fixed[0]["role"] == "user" and fixed[0]["content"] == "hello", f"FAIL: {fixed[0]}"
    assert all(c["type"] != "tool_use" for c in fixed[-1].get("content", []) if isinstance(c, dict)), f"FAIL tool_use orfano: {fixed[-1]}"
    assert not any(m.get("content") == [{"type": "tool_result", "id": "t1", "content": "res1"}] for m in fixed), f"FAIL: tool_result orfano ancora presente"
    print("OK: _repair_message_sequence test passed")


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)  # FIX A: ensure log dir
    if not MODE_FILE.exists():
        MODE_FILE.write_text("anthropic\n")
    log(f"START ai-router-proxy multi-port {LISTEN_PORTS}")
    asyncio.run(_run_multiport())


if __name__ == "__main__":
    main()