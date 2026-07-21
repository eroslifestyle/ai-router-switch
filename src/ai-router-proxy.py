#!/usr/bin/env python3
# ~500 lines
"""
AI Router Proxy — switcher davanti a Claude Code.

Modalita' (file ~/.claude/ai-router-mode):
  - anthropic   : tutto diretto a api.anthropic.com
  - minimax     : tutto diretto a api.minimaxi.chat/anthropic
  - mix-am      : Anthropic THINK + MiniMax ACT
  - mix-ag      : Anthropic THINK + GLM ACT
  - mix-gm      : GLM THINK + MiniMax ACT
  - glm         : GLM tiered (5.2->4.7->4)

Claude Code punta qui: ANTHROPIC_BASE_URL=http://127.0.0.1:8787
Gestisce streaming SSE. Backend diretto (nessun proxy intermedio).
"""
import asyncio
import json
import os
import signal
import threading
import time
from collections import deque
from pathlib import Path

import tool_isolation
import debug_catalog
from aiohttp import web, ClientSession, ClientTimeout, TCPConnector

# ponytail: reach modules at project root (providers/, pipelines/)
import sys as _sys
# ai-router-proxy.py lives in src/; project root is parent of src/.
# resolve() is required: when deployed via symlink (~/.claude/scripts),
# __file__ is the symlink path, not the real src/ location.
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sys  # for resilience module

from fail_tracker import fail_tracker, mixed_fail_inc, mixed_fail_reset, mixed_anthropic_leads
from streaming_relay import StreamingRelay
from context_manager import ContextManager

# Istanza globale ContextManager (AQ-REF3)
CTX = ContextManager()

# ── Moduli condivisi (gia' estratti in file separati) ───────────────────────
from minimax_body import remap_body_for_minimax, strip_server_tools_for_minimax
from trim_smart import (SHRINK_KEEP_HEAD, SHRINK_KEEP_TAIL,
                        build_shrink_summary, _smart_truncate, _smart_sample_middle)
from token_counter import estimate_tokens, count_tokens
from model_context_map import get_safe_input_limit, get_context_limit, get_summary_budget
from context_rewrite import rewrite_for_context
from context_alert import notify_context_threshold, maybe_prepend_banner
from summarizer import summarize_old_messages
from providers.base import (
    FALLBACK_STATUSES, MINIMAX_FALLBACK_STATUSES,
    extract_last_user_text, _text_from_message as _pmsg_text,
    _is_context_too_large_for_minimax, _is_context_exceed_400,
    strip_images_body, call_full,
    T2_KEYWORDS, trim_old_messages,
    _body_has_images,
    classify_t2,
)
from pipelines.primitives import (
    build_think_body, build_act_body, build_finalize_body,
    to_json_bytes,
)
from sse_utils import _sse_events_from_message, _prepare_sse_response, _send_sse_message

# ── Router modules (this refactoring) ─────────────────────────────────────────
from router_constants import (
    LISTEN_HOST, LISTEN_PORT, LISTEN_PORTS,
    ANTHROPIC_UPSTREAM, MINIMAX_UPSTREAM, MINIMAX_MODEL,
    MINIMAX_ORCHESTRATOR_MODEL, MINIMAX_EXECUTORS, MIXED_EXECUTOR_MODEL,
    NEW_PIPELINE, VALID_MODES, GLM_AVAILABLE,
    MODE_FILE, KEY_FILE, LOG_FILE, SIDECAR, USAGE_SIDECAR,
    CHAT_STORE, TRIM_STATE_DIR,
    HOP_HEADERS, FALLBACK_STATUSES as _FS, MINIMAX_FALLBACK_STATUSES as _MFS,
    MINIMAX_CONTEXT_BYTE_LIMIT, ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT,
    SUMMARY_BUDGET, TRIM_TARGET_BYTES, TRIM_MIN_MESSAGES,
    PORT_MODE, _GENERATIVE_PATHS, THINK_MODEL, THINK_TIMEOUT_SEC,
    THINK_MAX_TOKENS, CLAUDE_CODE_MARKER,
    _HEALTH_CHECK_PATHS,
    trim_locks,
)
from router_utils import (
    log, log_exc, debug_capture, debug_errors, debug_last,
    debug_stats, debug_trace, debug_catalog_endpoint, debug_catalog_entry,
    MINIMAX_LIMITER, _MINIMAX_SEM,
    _request_orig_model,
)
from router_debug import dl
from router_mode import (
    get_file_mode, _current_mode, _err_response, get_mode,
    conversation_fingerprint, _resolve_chat_fingerprint,
    get_chat_mode, set_chat_mode, clear_chat_mode,
)
from router_commands import (
    parse_router_command, _router_reply_text, _synthetic_message,
)
from router_auth import get_minimax_key, _reload_oauth_token, _load_oauth_token
from forward_anthropic import forward_anthropic, forward_anthropic_direct
from forward_minimax import (
    forward_minimax, _fwd_minimax_short,
    _route_v1_images, _route_v1_videos, _route_v1_music, _route_v1_audio_speech,
)
from pipeline_anthropic import (
    _build_think_body, _build_finalize_body_p, _build_act_body_p,
    _text_from_message, _parse_plan_text, _parse_think_json,
    _call_full, _retry_forward, _force_no_stream,
    _has_server_tools, _has_web_search_tool, _web_search_blocked_response,
    _is_context_too_large_for_minimax as _is_ctx_too_large,
    _is_context_exceed_400 as _is_ctx_exceed,
    _repair_message_sequence,
    _shrink_and_retry_minimax,
    _mixed_haiku_rescue, _anthropic_rescue,
    _pipeline_think_act,
)
from pipeline_minimax import (
    _build_minimax_think_body, _pick_minimax_executor,
    _build_minimax_act_body, _build_minimax_act_body_retry,
    _parse_think_json as _parse_minimax_think,
    _pipeline_minimax_orchestrate, _try_shrink_body,
)
from pipeline_glm import _handle_glm_mode

# Load OAuth token at startup
_load_oauth_token()

# ── Resilience module ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path.home() / ".claude" / "scripts"))
try:
    from ai_router_resilience import Resilience
    _RESILIENCE_AVAILABLE = True
except Exception as _rexc:
    Resilience = None
    _RESILIENCE_AVAILABLE = False
    log_bootstrap = lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", file=sys.stderr)
    log_bootstrap(f"WARN: resilience module non disponibile: {_rexc}")

RESILIENCE_INST = None

# Aliases for backward compat with pipeline modules
def _log_original_model(orig: str, final: str, chat_id: str) -> None:
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": int(time.time()), "chat": chat_id, "orig": orig, "final": final}
        with open(SIDECAR, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def log_router_usage(chat_id: str, orig: str, final: str, usage: dict,
                     mode: str, client: str = "?", status: int = 200, path: str = ""):
    if not final or final == "?":
        final = "router-internal"
    try:
        USAGE_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()), "chat": chat_id, "orig": orig or "?",
            "final": final, "mode": mode, "client": client, "status": status,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_read": int(usage.get("cache_read_input_tokens", 0) or 0),
            "cache_creation": int(usage.get("cache_creation_input_tokens", 0) or 0),
        }
        with open(USAGE_SIDECAR, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# Wire defaults after all imports (avoids circular)
_sse_events_from_message.__defaults__ = (_text_from_message,)
_send_sse_message.__defaults__ = (None, 200, _text_from_message)


# ── Path whitelist ─────────────────────────────────────────────────────────────
def _path_allowed(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    if ".." in path or "//" in path:
        return False
    if path == "/__router_health":
        return True
    if path.startswith("/v1/"):
        return True
    if path in ("/v1/images/generations", "/v1/videos/generations",
                "/v1/music/generations", "/v1/audio/speech"):
        return True
    return False


# ── handle() ─────────────────────────────────────────────────────────────────
# ponytail: quando un body ha >=2 immagini + poche parole, M3/vision tende a
# salutare invece di eseguire. Iniettiamo un preambolo di sistema che forza la
# modalita' "analisi/esegui" — fix strutturale 2026-07-21 per chat con immagini.
TASK_MODE_IMAGES_PREAMBLE_IT = (
    "\n\n[Istruzione operativa]: l'utente ha allegato piu' immagini con una richiesta.\n"
    "Comportamento richiesto: ANALIZZA le immagini e ESEGUI il task descritto nel testo utente.\n"
    "NON salutare, NON chiedere 'cosa posso fare', NON elencare opzioni generiche.\n"
    "Produci direttamente: (1) cosa vedi nelle immagini rilevante al task, (2) piano d'azione o output richiesto."
)


def _count_images_in_body(orig: dict) -> int:
    """Conta blocchi image nei content blocks dell'ULTIMO messaggio user."""
    try:
        msgs = orig.get("messages") or []
        if not msgs:
            return 0
        for m in reversed(msgs):
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, list):
                return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image")
            return 0
        return 0
    except Exception:
        return 0


def _inject_task_mode_for_images(orig: dict) -> dict:
    """Se >=2 immagini nel body, aggiunge preambolo task-mode al system prompt.
    Modifica in-place il dict orig (lo stesso che verra' passato alle pipeline)."""
    try:
        if _count_images_in_body(orig) < 2:
            return orig
        sys_val = orig.get("system")
        if isinstance(sys_val, str):
            orig["system"] = sys_val + TASK_MODE_IMAGES_PREAMBLE_IT
        elif isinstance(sys_val, list):
            sys_val.append({"type": "text", "text": TASK_MODE_IMAGES_PREAMBLE_IT})
        else:
            orig["system"] = TASK_MODE_IMAGES_PREAMBLE_IT
        return orig
    except Exception:
        return orig


async def handle(request):
    fp = _resolve_chat_fingerprint(request)
    mode = get_mode(request, fp)
    ct = (request.headers.get("Content-Type") or "").lower()
    if "multipart/form-data" in ct:
        return _err_response("multipart not supported", status=415)
    body = await request.read()

    # CTX PRE-CHECK (AQ-REF3): azione proattiva su compact/error
    ctx_check = {"action": "ok", "pct": 0.0}
    try:
        ctx_check = CTX.pre_check(fp, mode, len(body))
        if ctx_check["action"] in ("error", "compact", "warn", "warn2"):
            log(f"ctx: {ctx_check['action'].upper()} fp={fp} mode={mode} pct={ctx_check['pct']:.1%}")
        # ALERT PRE-COMPRESSIONE: avvisa l'utente (3 canali) a 80% (warn) e 88% (warn2),
        # PRIMA che la compressione lossy scatti a 90%, per dargli tempo di fare checkpoint.
        if ctx_check["action"] in ("warn", "warn2"):
            notify_context_threshold(
                fp, mode, ctx_check["pct"],
                ctx_check.get("est_tokens", 0), ctx_check.get("limit", 0),
                ctx_check["action"],
            )
        # FIX BUG-1: su compact/error, riscrivi proattivamente il body PRIMA di processare.
        # Questo libera spazio prima che upstream torni 400 context-exceeded.
        # post_check compatta_or_clear resta come safety net se la riscrittura non basta.
        if ctx_check["action"] in ("compact", "error"):
            # Modello di riferimento per la soglia di rewrite = il collo di bottiglia
            # REALE del path. Nelle modalità dove MiniMax esegue (minimax/mix-am/mix-gm)
            # il body deve stare nei 200K di MiniMax anche se il client è Opus/Sonnet.
            # Nelle modalità Anthropic/GLM pure vale il context reale (fino a 1M).
            _ctx_model_map = {
                "anthropic": "claude-opus-4-8", "minimax": "MiniMax-M2.7",
                "glm": "glm-5.2", "mix-am": "MiniMax-M2.7",
                "mix-ag": "claude-opus-4-8", "mix-gm": "MiniMax-M2.7",
            }
            ctx_model = _ctx_model_map.get(mode, "MiniMax-M2.7")
            rewrit, was_rewrit = rewrite_for_context(body, ctx_model, fp)
            if was_rewrit and len(rewrit) < len(body):
                _orig_len = len(body)
                body = rewrit
                log(f"ctx: proactive rewrite {len(rewrit)}b < {_orig_len}b fp={fp}")
            elif ctx_check["action"] == "error":
                log(f"ctx: ERROR threshold {ctx_check['pct']:.1%} fp={fp}")
                return web.json_response({
                    "type": "error",
                    "error": {"type": "context_window_exceeded",
                              "message": f"Context a {ctx_check['pct']:.0%}. Usa /compact."}
                }, status=400)
    except Exception as e:
        log(f"ctx: pre_check EXC {e} fp={fp} mode={mode}")

    # ── Early shrink per modalità dove MiniMax è collo di bottiglia ────────
    # Bug 2026-07-20: il client manda Opus/Sonnet (1M context), ma nelle modalità
    # che fanno ACT su MiniMax (200K context), il body viene relay grezzo finché
    # non torna 400. A quel punto il modello downstream ha già "visto" il body
    # pieno → si lamenta "vuoto/troncato". Fix: shrink proattivo se body >
    # limit_MiniMax, INDIPENDENTEMENTE dal ctx_check (che usa il limite client).
    _MINIMAX_BACKEND_MODES = {"minimax", "mix-am", "mix-gm"}
    if mode in _MINIMAX_BACKEND_MODES:
        try:
            from router_constants import MINIMAX_CONTEXT_BYTE_LIMIT
            _ctx_bottleneck = {
                "anthropic": "claude-opus-4-8", "minimax": "MiniMax-M2.7",
                "glm": "glm-5.2", "mix-am": "MiniMax-M2.7",
                "mix-ag": "claude-opus-4-8", "mix-gm": "MiniMax-M2.7",
            }
            _bottleneck_model = _ctx_bottleneck.get(mode, "MiniMax-M2.7")
            from model_context_map import get_safe_input_limit
            _bottleneck_safe = get_safe_input_limit(_bottleneck_model)
            if len(body) > _bottleneck_safe:
                rewrit2, was_rewrit2 = rewrite_for_context(body, _bottleneck_model, fp)
                if was_rewrit2 and len(rewrit2) < len(body):
                    _orig_len2 = len(body)
                    body = rewrit2
                    log(f"ctx: backend-bottleneck shrink {len(rewrit2)}b < {_orig_len2}b mode={mode} fp={fp}")
        except Exception as e2:
            log(f"ctx: bottleneck-shrink EXC {e2} mode={mode} fp={fp}")

    # TRIM INTERCEPT RIMOSSO (fix 2026-07-21): sostituiva il body APPENA ARRIVATO
    # con uno salvato in un turno PRECEDENTE (se più piccolo — quasi sempre vero in
    # sessioni agentiche dove il body cresce). Il body stantio NON conteneva l'ultimo
    # messaggio utente/tool_result → il modello non lo vedeva mai → "messaggio vuoto
    # o troncato", tool call ripetuti identici, step skill persi, e con fp condiviso
    # ("default") perfino contaminazione cross-chat. Un body di un turno precedente
    # non deve MAI sostituire quello corrente: l'oversize è gestito in-request da
    # rewrite/shrink/bottleneck che operano sul body del turno.

    forced = request.app.get("forced_mode")

    # health locale
    if request.path == "/__router_health":
        return web.json_response({
            "service": "ai-router-proxy", "mode": mode,
            "port_role": forced or "dynamic",
            "anthropic_upstream": ANTHROPIC_UPSTREAM,
            "minimax_upstream": MINIMAX_UPSTREAM,
            "minimax_key_present": bool(await get_minimax_key()),
        })

    # Health-check probe paths
    if request.path in _HEALTH_CHECK_PATHS:
        return web.Response(status=200, text="ok")

    # /v1/models
    if request.path in ("/v1/models", "/v1/models/") or request.path.startswith("/v1/models/"):
        try:
            _hdrs = {k: v for k, v in request.headers.items()
                      if k.lower() not in {h.lower() for h in HOP_HEADERS}}
            _hdrs.setdefault("anthropic-version", "2023-06-01")
            _reload_oauth_token()
            _tok = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
            if _tok:
                _hdrs["Authorization"] = f"Bearer {_tok}"
                _hdrs["anthropic-beta"] = "oauth-2025-04-20"
            _url = ANTHROPIC_UPSTREAM + request.path  # strip query string
            _up = await request.app["session"].request("GET", _url, headers=_hdrs)
            _body = await _up.read()
            _up.release()
            _rhdrs = {k: v for k, v in _up.headers.items()
                      if k.lower() not in {h.lower() for h in HOP_HEADERS}}
            return web.Response(body=_body, status=_up.status, headers=_rhdrs)
        except Exception as _e:
            import traceback
            log(f"[models] ERRORE: {_e}\n{traceback.format_exc()}")
            return web.Response(status=500, text=f"internal error: {_e}")

    # Generative stubs
    if request.path == "/v1/images/generations":
        return await _route_v1_images(request)
    if request.path == "/v1/videos/generations":
        return await _route_v1_videos(request)
    if request.path == "/v1/music/generations":
        return await _route_v1_music(request)
    if request.path == "/v1/audio/speech":
        return await _route_v1_audio_speech(request)

    if not _path_allowed(request.path):
        log(f"path non consentito: {request.path}")
        return web.Response(status=404, text="not found")

    # RESILIENZA: blocco traffico in modalita' DEGRADED
    if (_RESILIENCE_AVAILABLE and RESILIENCE_INST is not None
            and mode not in ("minimax", "glm")
            and not RESILIENCE_INST.state_is_ok()):
        if (request.path not in {"/", "/health", "/readyz", "/livez", "/stats",
                                 "/metrics", "/status", "/__router_health",
                                 "/__resilience", "/debug/errors", "/debug/last",
                                 "/debug/stats", "/debug/trace"}
                and not request.path.startswith("/debug/catalog")):
            log(f"DEGRADED: rifiuto {request.path}")
            return web.json_response(RESILIENCE_INST.degraded_response(), status=503)

    # Comandi in-chat + marca-chat (solo /v1/messages)
    if forced is None and request.path.endswith("/v1/messages"):
        try:
            _data = json.loads(body)
            _fp = _resolve_chat_fingerprint(request)
            _last = ""
            for _m in reversed(_data.get("messages", [])):
                if _m.get("role") == "user":
                    _c = _m.get("content", "")
                    _last = _c if isinstance(_c, str) else " ".join(
                        b.get("text", "") for b in _c if isinstance(b, dict))
                    break
            _cmd = parse_router_command(_last)
            if _cmd:
                _sid = (request.headers.get("X-Claude-Code-Session-Id")
                        or request.headers.get("x-claude-code-session-id", "")
                        or request.headers.get("X-Session-ID", "")
                        or request.headers.get("x-session-id", ""))
                _fp = f"sid:{_sid[:64]}" if _sid else conversation_fingerprint(_data)
                _fp_fallback = conversation_fingerprint(_data) if _sid else None
                _txt = _router_reply_text(_cmd, _fp, _fp_fallback)
                _msg = _synthetic_message(_txt, _data.get("model", "ai-router"))
                log(f"in-chat command {_cmd} fp={_fp}")
                if bool(_data.get("stream")):
                    return await _send_sse_message(request, _msg, "router")
                return web.json_response(_msg)
            _cm = get_chat_mode(_fp)
            if not _cm:
                _cm = get_chat_mode(conversation_fingerprint(_data))
            if _cm in VALID_MODES:
                mode = _cm
        except Exception:
            pass

    session = request.app["session"]
    orig = None

    _relay = StreamingRelay(
        request=request, body=body, mode=mode, orig=orig,
        request_orig_model=_request_orig_model,
        hop_headers=HOP_HEADERS,
        sidecar_path=SIDECAR,
        minimax_model=MINIMAX_MODEL,
        log_fn=log,
        log_router_usage_fn=log_router_usage,
        # fix 2026-07-21: no-op — il salvataggio cross-turno alimentava il TRIM
        # INTERCEPT (rimosso): body stantii sostituivano le richieste nuove.
        trim_context_fn=lambda body, fp: None,
    )
    relay = _relay.relay

    # ANTHROPIC PURA
    if mode == "anthropic":
        if not request.path.endswith("/v1/messages"):
            up = await forward_anthropic(request, body, session)
            return await relay(up)
        try:
            _am = (json.loads(body).get("model") or "").strip().lower()
        except Exception:
            _am = ""
        if _am.startswith("minimax"):
            log(f"anthropic mode: model MiniMax '{_am}' -> forward_minimax")
            return await relay(await forward_minimax(request, body, session),
                               extra_headers={"x-ai-verified": "minimax-oob"})
        try:
            up = await _retry_forward(forward_anthropic, request, body, session)
        except Exception as e:
            log(f"ERR anthropic (pure) {request.path}: {e}")
            debug_catalog.record_event(severity="error", category="anthropic",
                                        kind="forward_exception", snippet=str(e))
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)
        should_retry = str(up.headers.get("x-should-retry", "")).lower() == "true"
        if up.status == 429 and should_retry:
            ra = up.headers.get("retry-after")
            delay = min(float(ra), 3.0) if ra and ra.isdigit() else 1.5
            debug_catalog.record_event(severity="block", category="anthropic",
                                        kind="burst_limiter_429", code=429,
                                        snippet=f"retry-after={delay}s")
            up.release()
            await asyncio.sleep(delay)
            try:
                up = await _retry_forward(forward_anthropic, request, body, session)
            except Exception as e:
                log(f"ERR anthropic (pure) retry {request.path}: {e}")
                debug_catalog.record_event(severity="error", category="anthropic",
                                            kind="forward_retry_exception", snippet=str(e))
                return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)
        log(f"anthropic (pure) -> {up.status} {request.path}")
        return await relay(up, extra_headers={"x-ai-verified": "anthropic-pure"})

    # MODALITA' GLM
    if mode in ("glm", "mix-gm", "mix-ag"):
        if not GLM_AVAILABLE:
            log(f"GLM mode '{mode}' richiesto ma moduli assenti -> fallback anthropic")
            return await relay(await forward_anthropic(request, body, session),
                               extra_headers={"x-ai-verified": "glm-unavailable-fallback"})
        chat_fp = _resolve_chat_fingerprint(request)
        if not request.path.endswith("/v1/messages"):
            import glm_backend as _glm
            try:
                up = await _glm.forward_glm(request, body, session,
                                            _glm.resolve_glm_upstream_model(_glm.GLM_TIER_MID),
                                            log_fn=log, passthrough=True)
                return await relay(up)
            except Exception as e:
                # Fallback coerente con l'isolamento della coppia di provider del modo
                if mode == "mix-ag":
                    log(f"GLM non-messages EXC: {e} -> anthropic passthrough (mix-ag)")
                    return await relay(await _retry_forward(forward_anthropic, request, body, session))
                if mode == "mix-gm":
                    log(f"GLM non-messages EXC: {e} -> minimax passthrough (mix-gm)")
                    return await relay(await _retry_forward(forward_minimax, request, body, session))
                log(f"GLM non-messages EXC: {e} -> 502 (glm puro, no fallback cross-provider)")
                return web.json_response({"type": "error", "error": {
                    "type": "glm_unavailable", "message": f"glm: upstream error su {request.path}"}}, status=502)
        return await _handle_glm_mode(request, body, session, mode, chat_fp, relay)

    # MODALITA' MINIMAX
    if mode == "minimax":
        if not request.path.endswith("/v1/messages"):
            up = await forward_minimax(request, body, session)
            return await relay(up)
        if NEW_PIPELINE:
            try:
                orig = json.loads(body)
                _inject_task_mode_for_images(orig)
            except Exception:
                orig = {}
            log(f"minimax-orch pipeline attivata fp={_resolve_chat_fingerprint(request)} tools={bool(orig.get('tools'))}")
            return await _pipeline_minimax_orchestrate(request, body, session, orig, relay)
        try:
            up = await forward_minimax(request, body, session)
            log(f"minimax (pure) -> {up.status} {request.path}")
            return await relay(up)
        except Exception as e:
            log(f"ERR minimax (pure) {request.path}: {e}")
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)

    # MODALITA' MIX-AM
    chat_fp = _resolve_chat_fingerprint(request)
    anthropic_leads = mixed_anthropic_leads(chat_fp)
    is_messages = request.path.endswith("/v1/messages")
    is_t2 = is_messages and classify_t2(body)

    # ESCALATION
    if anthropic_leads and is_messages:
        try:
            orig = json.loads(body)
            _inject_task_mode_for_images(orig)
        except Exception:
            orig = {}
        if orig.get("tools"):
            try:
                return await relay(await forward_anthropic(request, body, session))
            except Exception as e:
                return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)
        question = extract_last_user_text(orig)
        wants_stream = bool(orig.get("stream"))
        try:
            gen_status, gen_json = await _call_full(_fwd_minimax_short, request, body, session)
        except Exception:
            gen_status, gen_json = 0, None
        if not gen_json or gen_status in (MINIMAX_FALLBACK_STATUSES | {400}):
            try:
                up = await forward_anthropic(request, body, session)
                return await relay(up)
            except Exception as e:
                return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)
        mixed_fail_reset(chat_fp)
        draft_v1 = _text_from_message(gen_json)
        fbody = _build_finalize_body_p(orig, question, draft_v1)
        try:
            f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
            if f_json and f_status < 400 and _text_from_message(f_json):
                final_text = _text_from_message(f_json)
                verified_flag = "escalation"
            else:
                final_text = draft_v1
                verified_flag = "m3_only_escalation"
        except Exception:
            final_text = draft_v1
            verified_flag = "m3_only_escalation"
        final_json = {
            "id": f"msg_mixed_{int(time.time()*1000)}",
            "type": "message", "role": "assistant",
            "model": "minimax-m3+claude" if verified_flag == "escalation" else "minimax-m3",
            "content": [{"type": "text", "text": final_text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)},
        }
        if wants_stream:
            return await _send_sse_message(request, final_json, verified_flag)
        # Inietta l'eventuale banner alert-context pending (non-stream): appare in-chat.
        _fj_bytes = maybe_prepend_banner(json.dumps(final_json).encode(), fp, is_stream=False)
        try:
            final_json = json.loads(_fj_bytes)
        except Exception:
            pass
        return web.json_response(final_json, headers={"x-ai-verified": verified_flag})

    # NEW PIPELINE redesign: Anthropic THINK + M3 ACT
    if mode == "mix-am" and NEW_PIPELINE and is_messages and not anthropic_leads:
        try:
            orig = json.loads(body)
            _inject_task_mode_for_images(orig)
        except Exception:
            orig = {}
        orig_model = (orig.get("model") or "").strip()
        if orig_model.lower().startswith("minimax"):
            try:
                up = await forward_minimax(request, body, session)
                if up.status == 400:
                    is_ctx, _ = await _is_context_exceed(up)
                    if is_ctx:
                        return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)
                if up.status not in FALLBACK_STATUSES:
                    mixed_fail_reset(chat_fp)
                    return await relay(up)
                log(f"mix-am FAST-PATH MiniMax {up.status} -> fallthrough pipeline fp={chat_fp}")
                try:
                    await up.release()
                except Exception:
                    pass
            except Exception as e:
                log(f"mix-am FAST-PATH MiniMax EXC: {e}")
        log(f"mix-am pipeline attivata fp={chat_fp} tools={bool(orig.get('tools'))}")
        return await _pipeline_think_act(request, body, session, orig, relay)

    # T0/T1 -> M3 diretto
    if not is_t2:
        try:
            up = await forward_minimax(request, body, session)
        except Exception as e:
            n = mixed_fail_inc(chat_fp)
            log(f"mix-am T0/T1 M3 EXC ({n}/{fail_tracker.threshold}) {request.path}: {e}")
            try:
                up = await forward_anthropic(request, body, session)
                log(f"mix-am T0/T1 fallback anthropic {up.status} {request.path}")
                return await relay(up)
            except Exception as e2:
                return web.json_response({"type": "error", "error": {"type": "router_error", "message": f"both down: {e2}"}}, status=502)
        if up.status in FALLBACK_STATUSES:
            n = mixed_fail_inc(chat_fp)
            await up.release()
            log(f"mix-am T0/T1 M3 {up.status} ({n}/{fail_tracker.threshold}) {request.path}")
            if n >= fail_tracker.threshold:
                log(f"mix-am escalation: M3 ha fallito {n}x -> Anthropic prende comando")
            try:
                up2 = await forward_anthropic(request, body, session)
                log(f"mix-am T0/T1 rescue anthropic {up2.status} {request.path}")
                if up2.status < 400:
                    mixed_fail_reset(chat_fp)
                return await relay(up2)
            except Exception as e2:
                return web.json_response({"type": "error", "error": {"type": "router_error", "message": f"rescue ko: {e2}"}}, status=502)
        if up.status < 400:
            mixed_fail_reset(chat_fp)
        log(f"mix-am T0/T1 executor M3 {up.status} {request.path}")
        return await relay(up)

    # T2: pipeline verify gerarchica
    try:
        orig = json.loads(body)
        _inject_task_mode_for_images(orig)
    except Exception:
        orig = {}
    question = extract_last_user_text(orig)
    wants_stream = bool(orig.get("stream"))
    try:
        gen_status, gen_json = await _call_full(_fwd_minimax_short, request, body, session)
    except Exception:
        gen_status, gen_json = 0, None
    if not gen_json or gen_status in (MINIMAX_FALLBACK_STATUSES | {400}):
        n = mixed_fail_inc(chat_fp)
        log(f"mix-am T2 M3 R1 ko {gen_status} ({n}/{fail_tracker.threshold}) {request.path}")
        try:
            up = await forward_anthropic(request, body, session)
            log(f"mix-am T2 fallback anthropic {up.status} {request.path}")
            return await relay(up)
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)
    mixed_fail_reset(chat_fp)
    draft_v1 = _text_from_message(gen_json)
    log(f"mix-am T2 R1 M3 draft ({len(draft_v1)} chars)")
    fbody = _build_finalize_body_p(orig, question, draft_v1)
    final_text = draft_v1
    verified_flag = "m3_only"
    try:
        f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
        if f_json and f_status < 400 and _text_from_message(f_json):
            final_text = _text_from_message(f_json)
            verified_flag = "collaborative"
    except Exception as e:
        log(f"mix-am T2 R2 anthropic EXC: {e}")
    final_json = {
        "id": f"msg_mixed_{int(time.time()*1000)}",
        "type": "message", "role": "assistant",
        "model": "minimax-m3+claude" if verified_flag == "collaborative" else "minimax-m3",
        "content": [{"type": "text", "text": final_text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)},
    }
    if wants_stream:
        return await _send_sse_message(request, final_json, verified_flag)
    return web.json_response(final_json, headers={"x-ai-verified": verified_flag})


# ── App & multiport ───────────────────────────────────────────────────────────
def _make_app(session, forced_mode):
    app = web.Application(client_max_size=1024 * 1024 * 100)
    app["session"] = session
    app["forced_mode"] = forced_mode
    if _RESILIENCE_AVAILABLE and RESILIENCE_INST is not None:
        app["RESILIENCE"] = RESILIENCE_INST

    async def healthz(request):
        _h = {"ok": True, "mode": forced_mode or _current_mode(),
              "minimax": MINIMAX_LIMITER.snapshot()}
        if GLM_AVAILABLE:
            try:
                import glm_backend as _glm
                import peak_scheduler as _peak
                _h["glm"] = {
                    "scheduling": _peak.scheduling_status(),
                    "rate_limit": _glm.GLM_LIMITER.snapshot() if _glm.GLM_LIMITER else None,
                }
            except Exception:
                pass
        return web.json_response(_h)

    async def resiliencez(request):
        if RESILIENCE_INST is None:
            return web.json_response({"resilience": "unavailable"})
        s = RESILIENCE_INST.get_status()
        s["service_state"] = RESILIENCE_INST.state()
        s["pid"] = os.getpid()
        return web.json_response(s)

    async def admin_mode_switch(request):
        mode = request.match_info.get("mode", "")
        if mode not in VALID_MODES:
            return web.json_response({"ok": False, "error": f"Modo '{mode}' non valido. Validi: {VALID_MODES}"}, status=400)
        MODE_FILE.write_text(mode + "\n")
        return web.json_response({"ok": True, "mode": mode, "msg": f"Switched to {mode}"})

    app.router.add_get("/health", healthz)
    app.router.add_get("/__resilience", resiliencez)
    app.router.add_get("/debug/errors", dl.errors_endpoint)
    app.router.add_get("/debug/last", dl.last_endpoint)
    app.router.add_get("/debug/stats", dl.stats_endpoint)
    app.router.add_get("/debug/trace", dl.trace_endpoint)
    app.router.add_get("/debug/health", dl.health_endpoint)
    app.router.add_get("/debug/catalog", dl.catalog_endpoint)
    app.router.add_get("/debug/catalog/{signature}", dl.catalog_entry_endpoint)
    app.router.add_post("/admin/mode/{mode}", admin_mode_switch)
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


async def _run_multiport():
    global RESILIENCE_INST

    # Resilience init
    if _RESILIENCE_AVAILABLE:
        RESILIENCE_INST = Resilience(
            port=LISTEN_PORT,
            log_fn=lambda m: log(f"[RES] {m}"),
            get_pid=lambda: os.getpid(),
        )
        ok = RESILIENCE_INST.boot_validate(run_self_test=False)
        if not ok:
            log("RESILIENZA: BOOT in modalita' DEGRADED")
        RESILIENCE_INST.install_signal_handlers()

    connector = TCPConnector(limit=100, limit_per_host=40, ttl_dns_cache=300)
    session = ClientSession(
        timeout=ClientTimeout(connect=30, sock_read=120, sock_connect=15),
        connector=connector,
        auto_decompress=False,
    )

    if _RESILIENCE_AVAILABLE and RESILIENCE_INST is not None:
        RESILIENCE_INST.start_periodic_self_test(session=session)
        RESILIENCE_INST.start_heartbeat()

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    runners = []
    for port in LISTEN_PORTS:
        if stop.is_set():
            log("shutdown requested during bind, exiting early")
            break
        forced = PORT_MODE.get(port)
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
        log("no ports bound (already in use?) -- exiting")
        await session.close()
        return
    try:
        await stop.wait()
        log("shutdown signal received, draining...")
    finally:
        for r in runners:
            await r.cleanup()
        await session.close()
        log("shutdown complete")


if __name__ == "__main__":
    # Self-check: _repair_message_sequence ripara coppie tool troncate
    from pipeline_anthropic import _repair_message_sequence
    broken = [
        {"role": "user", "content": [{"type": "tool_result", "id": "t1", "content": "res1"}]},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "think"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "foo"}]},
    ]
    fixed = _repair_message_sequence(broken)
    assert fixed[0]["role"] == "user" and fixed[0]["content"] == "hello", f"FAIL: {fixed[0]}"
    print("OK: _repair_message_sequence test passed")


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    if not MODE_FILE.exists():
        MODE_FILE.write_text("anthropic\n")
    log(f"START ai-router-proxy multi-port {LISTEN_PORTS}")
    asyncio.run(_run_multiport())


if __name__ == "__main__":
    main()
