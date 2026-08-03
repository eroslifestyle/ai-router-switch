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
import random
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
from providers.base import (
    FALLBACK_STATUSES, MINIMAX_FALLBACK_STATUSES,
    extract_last_user_text, _text_from_message as _pmsg_text,
    _is_context_too_large_for_minimax, _is_context_exceed_400,
    strip_images_body, call_full,
    T2_KEYWORDS, trim_old_messages,
    _body_has_images,
    classify_t2,
)
from sse_utils import _sse_events_from_message, _prepare_sse_response, _send_sse_message

# ── Router modules (this refactoring) ─────────────────────────────────────────
from router_constants import (
    LISTEN_HOST, LISTEN_PORT, LISTEN_PORTS,
    ANTHROPIC_UPSTREAM, MINIMAX_UPSTREAM, MINIMAX_MODEL,
    MINIMAX_ORCHESTRATOR_MODEL, MINIMAX_EXECUTORS, MIXED_EXECUTOR_MODEL,
    NEW_PIPELINE, VALID_MODES, GLM_AVAILABLE, QWEN_AVAILABLE,
    MODE_FILE, KEY_FILE, LOG_FILE, SIDECAR, USAGE_SIDECAR,
    CHAT_STORE, TRIM_STATE_DIR,
    HOP_HEADERS, FALLBACK_STATUSES as _FS, MINIMAX_FALLBACK_STATUSES as _MFS,
    MINIMAX_CONTEXT_BYTE_LIMIT, ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT,
    SUMMARY_BUDGET, TRIM_TARGET_BYTES, TRIM_MIN_MESSAGES,
    PORT_MODE, _GENERATIVE_PATHS, THINK_TIMEOUT_SEC,
    THINK_MAX_TOKENS, CLAUDE_CODE_MARKER,
    _HEALTH_CHECK_PATHS,
    trim_locks,
)
from router_utils import (
    log, log_exc, debug_capture, debug_errors, debug_last,
    debug_stats, debug_trace, debug_catalog_endpoint, debug_catalog_entry,
    MINIMAX_LIMITER, _MINIMAX_SEM,
    _request_orig_model, log_router_usage,
)
from router_debug import dl
from router_mode import (
    get_file_mode, _current_mode, _err_response, get_mode,
    conversation_fingerprint, _resolve_chat_fingerprint,
    get_chat_mode, set_chat_mode, clear_chat_mode,
    _LEGACY_MODE_MAP,
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
    _text_from_message,
    _retry_forward,
    _is_context_too_large_for_minimax as _is_ctx_too_large,
    _is_context_exceed_400 as _is_ctx_exceed,
    _repair_message_sequence,
)
from pipeline_minimax import (
    _pipeline_minimax_orchestrate, _try_shrink_body,
)

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
    # log() non è ancora disponibile qui (import successivo): scrittura diretta
    # su stderr. Era una lambda usata una sola volta, subito sotto la sua
    # definizione (finding audit 2026-07-17, BASSA).
    print(f"[{time.strftime('%H:%M:%S')}] WARN: resilience module non disponibile: {_rexc}",
          file=sys.stderr)

RESILIENCE_INST = None

# Heartbeat del gate di contesto: {chat_fp: ts_ultimo_evento}. Serve a registrare
# telemetria anche SOTTO soglia (dopo il fix 2026-07-26 le azioni compact/error
# non si verificano quasi più e il gate era diventato cieco), senza inondare il
# catalogo: un evento per chat ogni CTX_GATE_HEARTBEAT_SEC.
_CTX_GATE_HEARTBEAT: dict = {}
# Entrambe le costanti accettano un override da env, così una taratura mirata
# (o un'indagine temporanea) non richiede di modificare il codice.
CTX_GATE_HEARTBEAT_SEC = int(os.environ.get("AIROUTER_CTX_HEARTBEAT_SEC", "300"))
# 0.30 e non 0.50 (taratura 2026-07-26 sui dati reali): col limite Anthropic da 1M
# una soglia 0.50 richiede un body da ~2 MB e sul traffico osservato (max 548 KB)
# non sarebbe scattata mai. Il costo di abbassarla è nullo: il catalogo deduplica
# per (modalità, azione) e il throttle resta a 300 s per chat.
CTX_GATE_HEARTBEAT_PCT = float(os.environ.get("AIROUTER_CTX_HEARTBEAT_PCT", "0.30"))

# Aliases for backward compat with pipeline modules
def _log_original_model(orig: str, final: str, chat_id: str) -> None:
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": int(time.time()), "chat": chat_id, "orig": orig, "final": final}
        with open(SIDECAR, "a") as f:
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


# ── Anthropic 429 retry — certificato da doc ufficiale Anthropic ────────────────
# Spec: platform.claude.com/docs/en/api/errors
#   "The official SDKs automatically retry transient failures (rate limits, 5xx)
#    with exponential backoff, twice by default, honoring the retry-after header
#    when present."
# Spec: platform.claude.com/docs/en/api/rate-limits
#   retry-after = "number of seconds to wait until you can retry. Earlier retries
#    will fail." → onorare il valore reale, non un cap arbitrario.
# max_retries=2 = default SDK ufficiale. Backoff esponenziale con jitter (equal
# jitter, come i backoff SDK). Se il 429 persiste dopo i retry, il 429 originale
# torna al chiamante (che decide il fallback) — NON si rilancia in loop.
ANTHROPIC_MAX_RETRIES = int(os.environ.get("AIROUTER_ANTHROPIC_MAX_RETRIES", "2"))
ANTHROPIC_RETRY_BASE_SEC = float(os.environ.get("AIROUTER_ANTHROPIC_RETRY_BASE_SEC", "0.5"))
ANTHROPIC_RETRY_MAX_SLEEP_SEC = float(os.environ.get("AIROUTER_ANTHROPIC_RETRY_MAX_SLEEP_SEC", "60"))


# Helper certificato SDK: la logica vive ora in pipeline_common (condivisa con le
# leg Anthropic delle modalità MIX — riuso, non duplicazione). Questi thin-wrapper
# restano per compatibilità con i call site anthropic puri.
from pipeline_common import (
    parse_retry_after as _parse_retry_after,
    backoff_sleep_sec as _backoff_sleep_sec,
    anthropic_call_with_retry as _anthropic_call_with_retry,
)


async def _anthropic_forward_with_retry(request, body, session, relay):
    """Forward ad Anthropic con retry certificato SDK sui 429/5xx transienti.

    Ritorna (up, exhausted): `up` è la ClientResponse finale (aperta, da relayare);
    `exhausted` True se il 429/5xx è persistito dopo tutti i retry. Il chiamante
    decide cosa fare del 429 persistente (relay grezzo o fallback), NON si rimbalza
    in loop verso il client. Solleva su eccezione di rete dopo l'ultimo tentativo.

    Delega a pipeline_common.anthropic_call_with_retry (via _retry_forward per il
    retry sulle sole eccezioni di rete, come prima), emettendo l'evento di debug
    e il log 'anthropic 429' storici per non alterare i test/log del path puro."""
    def _log_and_event(msg: str):
        log(f"{msg} {request.path}")
        debug_catalog.record_event(
            severity="block", category="anthropic", kind="rate_limit_429",
            snippet=msg)
    return await _anthropic_call_with_retry(
        forward_anthropic, request, body, session,
        log_fn=_log_and_event, tag="anthropic")


import re as _re

# Un content-hash fingerprint è esattamente 12 hex (conversation_fingerprint ->
# hexdigest()[:12]). È STABILE e UNIVOCO per conversazione, quindi pinnabile come
# un sid:. Restano NON pinnabili "default" e gli IP (request.remote): sono
# condivisi tra chat diverse, pinnarli le legherebbe insieme (bug originale).
_CONTENT_HASH_FP = _re.compile(r"^[0-9a-f]{12}$")


def _is_pinnable_fp(fp: str) -> bool:
    """True se il fingerprint identifica UNA sola conversazione (sid: o
    content-hash), False per i fp condivisi (default / IP remoto)."""
    if not fp:
        return False
    if fp.startswith("sid:"):
        return True
    return bool(_CONTENT_HASH_FP.match(fp))


async def handle(request):
    fp = _resolve_chat_fingerprint(request)
    ct = (request.headers.get("Content-Type") or "").lower()
    if "multipart/form-data" in ct:
        return _err_response("multipart not supported", status=415)
    body = await request.read()
    # FIX BUG-COMPRESSIONE: preserva il body ORIGINALE prima di ogni riscrittura.
    # rewrite_for_context (linee 334-374) può riscrivere `body` con tail+summary,
    # ma le pipeline (THINK/ACT) devono poter accedere ai messaggi COMPLETI.
    _original_body = body

    # FIX ISOLAMENTO-CROSS-SESSIONE (2026-07-26): il content-hash fingerprint va
    # calcolato PRIMA di get_mode(). Bug precedente: get_mode() girava con
    # fp="default"/IP (header X-Claude-Code-Session-Id assente su CLI/SDK locale),
    # quindi ~115k richieste collassavano su "default" e rileggevano il MODE_FILE
    # GLOBALE ad ogni turno. Risultato: cambiare modello in UNA sessione (o un
    # `ai-mode X` da terminale) spostava TUTTE le chat aperte. Ora una chat senza
    # header ottiene un fp stabile per-conversazione (hash system+primo msg) e il
    # pin-on-first-use la isola come una chat sid:. request.remote/"default" restano
    # NON pinnabili (condivisi tra chat diverse) e ricadono sul default globale.
    # Audit fp 2026-07-21: la cache su request['chat_fp'] fa risolvere la stessa fp
    # a valle (pipeline, remap MiniMax, GLM).
    if not fp.startswith("sid:") and request.path.endswith("/v1/messages"):
        try:
            request["chat_fp"] = conversation_fingerprint(json.loads(body))
            fp = request["chat_fp"]
        except Exception:
            pass

    mode = get_mode(request, fp)

    # FIX 2026-07-26: risoluzione anticipata del provider effettivo per evitare
    # shrink inutile su richieste THINK (es. mix-am con Claude Opus inoltrato
    # ad Anthropic). Il provider dipende dal modello richiesto, non dalla modalità.
    _early_provider = None
    _early_override = None
    _early_model = ""
    try:
        from role_routing import resolve_route as _resolve_route
        _early_model = (json.loads(body).get("model") or "").strip()
        if _early_model:
            _early_provider, _early_override = _resolve_route(mode, _early_model)
    except Exception:
        pass

    # CTX PRE-CHECK (AQ-REF3): azione proattiva su compact/error
    # Il limite del gate e del rewrite devono essere lo stesso, misurato sul
    # modello destinatario reale, non su un modello generico per provider
    # (fix 2026-07-26, esteso F8). Esempio del bug risolto: verso anthropic
    # con claude-haiku-4-5-20251001 (limite 200k) il gate usava ctx_model
    # claude-opus-4-8 (limite 1M), lasciando passare corpi da 208k token che
    # Anthropic rifiuta con 400 "prompt is too long".
    _ctx_model_map = {
        "anthropic": "claude-opus-4-8", "minimax": "MiniMax-M2.7",
        "glm": "glm-5.2", "mix-am": "MiniMax-M2.7",
        "mix-ag": "claude-opus-4-8", "mix-gm": "MiniMax-M2.7",
        "qwen": "qwen3.7-max",
    }
    _provider_ctx_model_map = {
        "anthropic": "claude-opus-4-8",
        "minimax": "MiniMax-M2.7",
        "glm": "glm-5.2",
        "qwen": "qwen3.7-max",
    }
    if _early_model:
        ctx_model = _early_override or _early_model
    elif _early_provider and _early_provider in _provider_ctx_model_map:
        ctx_model = _provider_ctx_model_map[_early_provider]
    else:
        ctx_model = _ctx_model_map.get(mode, "MiniMax-M2.7")

    ctx_check = {"action": "ok", "pct": 0.0}
    try:
        ctx_check = CTX.pre_check(fp, mode, len(body), model=ctx_model, body=body)
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

        # Nuovo: calcola safe_limit e determina se serve rewrite.
        # Lo spazio da riservare all'output e' il max_tokens che il client ha
        # chiesto davvero, non una percentuale fissa: il 20% sprecava 72k di
        # contesto sui modelli da 1M e non bastava (mancavano 88k) su glm-4.7
        # e glm-5-turbo, che hanno 200k di finestra e 128k di output massimo.
        try:
            _req_max_tokens = None
            try:
                _req_max_tokens = json.loads(body).get("max_tokens")
            except Exception:
                pass
            _safe_limit = get_safe_input_limit(ctx_model, _req_max_tokens)
        except Exception:
            _safe_limit = 0

        _est = ctx_check.get("est_tokens", 0)

        # pre_check misura sul context limit pieno mentre rewrite_for_context sul safe input limit
        # (context - 20% riservato all'output). Questo gap lasciava una zona morta dove il buffer
        # di output poteva essere consumato senza intervento, anche quando l'input superava la soglia sicura.
        _needs_rewrite = ctx_check["action"] in ("compact", "error") or (_safe_limit and _est > _safe_limit)

        _orig_body_len = len(body)
        if _needs_rewrite:
            rewrit, was_rewrit = rewrite_for_context(body, ctx_model, fp)
            if was_rewrit and len(rewrit) < len(body):
                body = rewrit
                log(f"ctx: proactive rewrite {len(rewrit)}b < {_orig_body_len}b fp={fp}")
            elif ctx_check["action"] == "error":
                # Mantengo il gate in modalita' osservatore per preservare la via di recupero /compact
                log(f"ctx: ERROR threshold {ctx_check['pct']:.1%} fp={fp} model={ctx_model} — inoltro comunque (gate osservatore)")

        # Helper inline per estrarre stats dal body JSON.
        # Serve a identificare il marker del turno /compact dai dati reali invece di inventarlo.
        def _get_body_stats(b: bytes):
            try:
                _data = json.loads(b)
                # Il body è un dict Anthropic Messages API: {"messages": [...]},
                # non una lista. Leggerlo come lista azzerava ogni statistica.
                if not isinstance(_data, dict):
                    return 0, 0, ""
                _msgs = _data.get("messages", [])
                if not isinstance(_msgs, list):
                    return 0, 0, ""
            except Exception:
                return 0, 0, ""

            _msgs_count = len(_msgs)
            _largest_chars = 0
            _last_user_text = ""

            for msg in reversed(_msgs):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "")
                content = msg.get("content", "")

                # Estrai testo dal content (stringa o lista di blocchi)
                text_parts = []
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            block_text = block.get("text", "")
                            if isinstance(block_text, str):
                                text_parts.append(block_text)

                full_text = "".join(text_parts)
                text_len = len(full_text)

                if text_len > _largest_chars:
                    _largest_chars = text_len

                if role == "user" and not _last_user_text:
                    _last_user_text = full_text[:80]

            return _msgs_count, _largest_chars, _last_user_text

        _rewritten_len = len(body)

        # Determina se registrare heartbeat
        _heartbeat = False
        _should_record = ctx_check["action"] in ("compact", "error")

        if not _should_record:
            if ctx_check["pct"] >= CTX_GATE_HEARTBEAT_PCT:
                _last_heartbeat = _CTX_GATE_HEARTBEAT.get(fp, 0)
                if time.time() - _last_heartbeat >= CTX_GATE_HEARTBEAT_SEC:
                    _should_record = True
                    _heartbeat = True
                    _CTX_GATE_HEARTBEAT[fp] = time.time()

        if _should_record:
            # Parsing del body solo quando l'evento viene davvero registrato, per non pesare sull'hot path.
            _msgs_count, _largest_chars, _last_user_text = _get_body_stats(body)

            detail = {
                "mode": mode,
                "model": ctx_model,
                "provider": _early_provider,
                "action": ctx_check["action"],
                "est_tokens": ctx_check.get("est_tokens", 0),
                "limit": ctx_check.get("limit", 0),
                "safe_limit": _safe_limit,
                "pct": round(ctx_check["pct"], 4),
                "body_bytes": _orig_body_len,
                "rewritten_bytes": _rewritten_len,
                "msgs": _msgs_count,
                "largest_msg_chars": _largest_chars,
                "last_user_prefix": _last_user_text,
                "heartbeat": _heartbeat,
            }

            try:
                if ctx_check["action"] == "error":
                    _severity = "error"
                elif ctx_check["action"] == "compact":
                    _severity = "block"
                else:
                    _severity = "info"

                debug_catalog.record_event(
                    severity=_severity,
                    category=mode,
                    kind="ctx_gate",
                    # code=action: la signature del catalogo è (category, kind, code,
                    # snippet). Senza l'azione, heartbeat e error collassavano nella
                    # STESSA entry e ogni heartbeat successivo sovrascriveva
                    # example_detail — cancellando proprio il last_user_prefix del
                    # turno /compact che serve a chiudere G4.
                    code=ctx_check["action"],
                    chat_fp=fp,
                    detail=detail
                )
            except Exception:
                pass

    except Exception as e:
        log(f"ctx: pre_check EXC {e} fp={fp} mode={mode}")

    # ── Early shrink per modalità dove MiniMax è collo di bottiglia ────────
    # Bug 2026-07-20: il client manda Opus/Sonnet (1M context), ma nelle modalità
    # che fanno ACT su MiniMax (200K context), il body viene relay grezzo finché
    # non torna 400. A quel punto il modello downstream ha già "visto" il body
    # pieno → si lamenta "vuoto/troncato". Fix: shrink proattivo se body >
    # limit_MiniMax, INDIPENDENTEMENTE dal ctx_check (che usa il limite client).
    # FIX 2026-07-26: gate sul provider effettivo per non shrinkare richieste
    # THINK inoltrate ad Anthropic (es. mix-am con claude-opus-*).
    _MINIMAX_BACKEND_MODES = {"minimax", "mix-am", "mix-gm"}
    _shrink_for_minimax = mode in _MINIMAX_BACKEND_MODES and (_early_provider == "minimax" or _early_provider is None)
    if mode in _MINIMAX_BACKEND_MODES and not _shrink_for_minimax:
        log(f"ctx: bottleneck-shrink SKIP provider={_early_provider} mode={mode} bytes={len(body)} fp={fp}")
    if _shrink_for_minimax:
        try:
            _ctx_bottleneck = {
                "anthropic": "claude-opus-4-8", "minimax": "MiniMax-M2.7",
                "glm": "glm-5.2", "mix-am": "MiniMax-M2.7",
                "mix-ag": "claude-opus-4-8", "mix-gm": "MiniMax-M2.7",
            }
            _bottleneck_model = _ctx_bottleneck.get(mode, "MiniMax-M2.7")
            # NIENTE import locale qui: get_safe_input_limit è già importata a livello
            # modulo (riga 51). Re-importarla dentro handle() la rendeva LOCALE all'intera
            # funzione, quindi l'uso precedente nel gate di contesto sollevava
            # UnboundLocalError → _safe_limit=0 → il rewrite proattivo non scattava mai.
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

    # Generative stubs. In modalita' qwen vanno su DashScope, in tutte le altre
    # restano sul percorso MiniMax di sempre (nessuna regressione).
    _GEN_ROUTES = {
        "/v1/images/generations": (_route_v1_images, "forward_qwen_image"),
        "/v1/videos/generations": (_route_v1_videos, "forward_qwen_video"),
        "/v1/music/generations": (_route_v1_music, "forward_qwen_music"),
        "/v1/audio/speech": (_route_v1_audio_speech, "forward_qwen_tts"),
        "/v1/audio/transcriptions": (None, "forward_qwen_asr"),  # mancava: 6 su 7 mappate
        "/v1/embeddings": (None, "forward_qwen_embedding"),
        "/v1/rerank": (None, "forward_qwen_rerank"),
    }
    if request.path in _GEN_ROUTES:
        _minimax_route, _qwen_fn_name = _GEN_ROUTES[request.path]
        if mode == "qwen" and QWEN_AVAILABLE:
            try:
                import qwen_generative as _qgen
                log(f"generative {mode}: {request.path} -> DashScope ({_qwen_fn_name})")
                return await getattr(_qgen, _qwen_fn_name)(
                    request, body, request.app["session"], log_fn=log)
            except Exception as e:
                log(f"generative qwen EXC su {request.path}: {e} -> 502")
                debug_catalog.record_event(severity="error", category="qwen",
                                           kind="generative_exception", snippet=str(e))
                return web.json_response({"type": "error", "error": {
                    "type": "qwen_unavailable", "message": str(e)}}, status=502)
        if _minimax_route is not None:
            return await _minimax_route(request)
        return web.Response(status=404, text="not found")

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
            _cm = _LEGACY_MODE_MAP.get(_cm, _cm)
            if _cm in VALID_MODES:
                mode = _cm
            elif _is_pinnable_fp(_fp):
                # PIN-ON-FIRST-USE (2026-07-26, decisione utente): una chat nuova
                # EREDITA il default globale nel momento in cui nasce, poi la
                # modalita' e' SUA. Prima il default era dinamico: ogni richiesta
                # rileggeva ai-router-mode, quindi un solo `ai-mode X` da terminale
                # spostava di colpo TUTTE le chat aperte prive di override esplicito
                # ("ai-mode travolge tutto"). Con il pin, `ai-mode` governa solo le
                # chat che nascono DOPO. `!router reset` cancella il pin: la chat
                # torna al default globale corrente e si ri-pinna a quello.
                # ESTESO 2026-07-26: pin anche per i fp content-hash (12 hex), non
                # solo sid:. Senza header X-Claude-Code-Session-Id (CLI/SDK locale)
                # la chat ha un fp content-hash stabile e univoco -> isolabile.
                # Restano fuori solo default/IP (_is_pinnable_fp -> False): condivisi
                # tra chat, pinnarli le legherebbe insieme.
                _pin = get_file_mode()
                set_chat_mode(_fp, _pin)
                mode = _pin
                log(f"chat {_fp}: pin-on-first-use -> {_pin}")
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

    # ─────────────────────────────────────────────────────────────────────────────
    # TUNNEL TRASPARENTE: dispatch universale per tutte le modalità (righe 486-550)
    # ─────────────────────────────────────────────────────────────────────────────
    # Nuovo dispatch basato su role_routing.resolve_route(): determina il provider
    # e il model_override per la richiesta, poi la inoltra al provider appropriato.
    # Questo blocco ritorna SEMPRE e rende irraggiungibile il codice sottostante.
    try:
        _req_model = (json.loads(body).get("model") or "").strip()
    except Exception:
        _req_model = ""

    from role_routing import resolve_route
    try:
        _provider, _model_override = resolve_route(mode, _req_model)
    except ValueError as e:
        log(f"routing: {e} -> fallback anthropic")
        _provider, _model_override = "anthropic", None

    # Fase 3 ACTUATOR: consulta router-policy.json (hot-reload). Se il modello target e'
    # degradato per questo task, logga (telemetria); la deviazione effettiva avviene
    # nell'orchestratore lato agente (chain fallback). Best-effort, mai blocca il dispatch.
    try:
        from router_policy import is_degraded
        from self_healing.sensor import classify_task
        _tc = classify_task(body)
        _tgt = _model_override or _req_model
        if _tgt and is_degraded(_tgt, _tc):
            log(f"policy: model={_tgt} DEGRADATO per task_class={_tc} mode={mode} (consulta fallback chain)")
    except Exception:
        pass

    log(f"tunnel {mode}: model={_req_model or '?'} -> provider={_provider} override={_model_override or '-'}")

    # Smistamento per provider
    if _provider == "anthropic":
        # Anthropic valida il nome del modello e risponde 404 se non lo riconosce:
        # l'override calcolato da resolve_route va SCRITTO nel body, altrimenti resta
        # decorativo (i rami minimax/glm lo applicano gia', questo no — bug 2026-08-01).
        if _model_override:
            try:
                _ab = json.loads(body)
                _ab["model"] = _model_override
                body = json.dumps(_ab).encode()
                log(f"tunnel {mode}: body model riscritto {_req_model or '?'} -> {_model_override} (nativizzazione)")
            except Exception as _e:
                log(f"tunnel {mode}: rewrite model FALLITO ({_e}) — inoltro body originale")
        # Case 1: mode == "anthropic" (pure)
        #   Lascia cadere nel codice sottostante (ramo "ANTHROPIC PURA" esistente).
        # Case 2: mode != "anthropic" (mix-am, mix-ag, mix-gm con THINK su Anthropic)
        #   Inoltra direttamente ad Anthropic dal blocco tunnel.
        if mode != "anthropic":
            # Inoltro diretto ad Anthropic per modalità miste
            if not request.path.endswith("/v1/messages"):
                up = await forward_anthropic(request, body, session)
                return await relay(up, extra_headers={"x-ai-verified": f"tunnel-{mode}-anthropic"},
                                   final_override="claude-direct")
            # Per /v1/messages, applica il retry certificato SDK
            try:
                up, exhausted = await _anthropic_forward_with_retry(request, body, session, relay)
            except Exception as e:
                log(f"ERR tunnel {mode} anthropic {request.path}: {e}")
                debug_catalog.record_event(severity="error", category="anthropic",
                                            kind="forward_exception", snippet=str(e))
                return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)
            if exhausted and up is not None and up.status == 429:
                log(f"tunnel {mode} anthropic -> 429 PERSISTENTE dopo {ANTHROPIC_MAX_RETRIES} retry {request.path}")
                debug_catalog.record_event(severity="error", category="anthropic",
                                            kind="rate_limit_429_exhausted", code=429,
                                            snippet=f"retry-after={up.headers.get('retry-after','?')}")
                return await relay(up, extra_headers={"x-ai-verified": f"tunnel-{mode}-anthropic-ratelimit"})
            log(f"tunnel {mode} anthropic -> {up.status} {request.path}")
            # final_override esplicito: senza, `_final` veniva dedotto dall'indice di
            # remap (che per le miste risponde col modello dell'ACT anche quando ha
            # eseguito Anthropic) e la guardia response-side si asteneva sul provider
            # sbagliato. Qui l'esecutore e' noto con certezza: e' Anthropic diretto.
            return await relay(up, extra_headers={"x-ai-verified": f"tunnel-{mode}-anthropic"},
                               final_override="claude-direct")
        # Altrimenti: mode == "anthropic" → cadi nel ramo "ANTHROPIC PURA" sotto

    elif _provider == "minimax":
        if not request.path.endswith("/v1/messages"):
            up = await forward_minimax(request, body, session, model_override=_model_override)
            return await relay(up, final_override=_model_override)
        try:
            _orig = json.loads(body)
            _inject_task_mode_for_images(_orig)
        except Exception:
            _orig = {}
        return await _pipeline_minimax_orchestrate(request, body, session, _orig, relay, model_override=_model_override)

    elif _provider == "glm":
        if not GLM_AVAILABLE:
            log(f"tunnel {mode}: GLM non disponibile -> 502")
            return web.json_response({"type": "error", "error": {"type": "glm_unavailable",
                                      "message": f"{mode}: modulo GLM assente"}}, status=502)
        try:
            import glm_backend as _glm_mod
            _glm_model = _model_override or _glm_mod.resolve_glm_upstream_model(_glm_mod.GLM_TIER_MID)
            # Dal 2026-07-25 il tiering dinamico non esiste piu: il modello GLM
            # effettivo arriva da role_routing ed e' noto solo qui. In fascia
            # peak (14-18 Asia/Shanghai) glm-5.2 e glm-5-turbo costano 3x e
            # vengono declassati a glm-4.7 per evitare costi eccessivi.
            _glm_model_pre = _glm_model
            _glm_model, _peak_capped = _glm_mod.apply_peak_cap(_glm_model)
            if _peak_capped:
                log(f"tunnel {mode} GLM peak-cap: {_glm_model_pre} -> {_glm_model} (fascia peak 14-18 Asia/Shanghai, costo 3x)")
            # z.ai onora il campo "model" del BODY: senza set_body_model ignora
            # il modello scelto qui e usa il proprio default (vedi docstring di
            # set_body_model). forward_glm non riscrive il body: lo fa il caller.
            _glm_body = _glm_mod.set_body_model(body, _glm_model)
            up = await _glm_mod.forward_glm(request, _glm_body, session,
                                            _req_model or _glm_model, log_fn=log,
                                            passthrough=True, upstream_model=_glm_model)
            return await relay(up, extra_headers={"x-ai-verified": f"tunnel-{mode}-glm({_glm_model})"}, final_override=f"glm:{_glm_model}")
        except Exception as e:
            log(f"tunnel {mode} GLM EXC: {e} -> 502")
            debug_catalog.record_event(severity="error", category="glm", kind="forward_exception", snippet=str(e))
            return web.json_response({"type": "error", "error": {"type": "glm_unavailable",
                                      "message": f"{mode}: {e}"}}, status=502)

    elif _provider == "qwen":
        if not QWEN_AVAILABLE:
            log(f"tunnel {mode}: Qwen non disponibile -> 502")
            return web.json_response({"type": "error", "error": {"type": "qwen_unavailable",
                                      "message": f"{mode}: modulo Qwen assente"}}, status=502)
        try:
            import qwen_backend as _qwen_mod
            _qwen_model = _model_override or _qwen_mod.resolve_qwen_upstream_model(_qwen_mod.QWEN_TIER_CODER)
            # L'upstream Model Studio onora il campo "model" del BODY (come z.ai):
            # senza set_body_model userebbe il proprio default ignorando la rotta.
            _qwen_body = _qwen_mod.set_body_model(body, _qwen_model)
            up = await _qwen_mod.forward_qwen(request, _qwen_body, session,
                                              _req_model or _qwen_model, log_fn=log,
                                              passthrough=True, upstream_model=_qwen_model)
            return await relay(up, extra_headers={"x-ai-verified": f"tunnel-{mode}-qwen({_qwen_model})"}, final_override=f"qwen:{_qwen_model}")
        except Exception as e:
            log(f"tunnel {mode} QWEN EXC: {e} -> 502")
            debug_catalog.record_event(severity="error", category="qwen", kind="forward_exception", snippet=str(e))
            return web.json_response({"type": "error", "error": {"type": "qwen_unavailable",
                                      "message": f"{mode}: {e}"}}, status=502)

    # ANTHROPIC PURA
    if mode == "anthropic":
        if not request.path.endswith("/v1/messages"):
            up = await forward_anthropic(request, body, session)
            return await relay(up)
        # Retry 429/5xx certificato SDK ufficiale (exponential backoff + retry-after).
        try:
            up, exhausted = await _anthropic_forward_with_retry(request, body, session, relay)
        except Exception as e:
            log(f"ERR anthropic (pure) {request.path}: {e}")
            debug_catalog.record_event(severity="error", category="anthropic",
                                        kind="forward_exception", snippet=str(e))
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": str(e)}}, status=502)
        if exhausted and up is not None and up.status == 429:
            # 429 persistito dopo i retry certificati = rate-limit REALE del piano
            # (non burst). Relay del 429 con retry-after al client + header diagnostico:
            # il 429 è la risposta corretta e onesta, NON un errore del router. Claude
            # Code lato client applica il suo backoff onorando retry-after.
            log(f"anthropic (pure) -> 429 PERSISTENTE dopo {ANTHROPIC_MAX_RETRIES} retry {request.path}")
            debug_catalog.record_event(severity="error", category="anthropic",
                                        kind="rate_limit_429_exhausted", code=429,
                                        snippet=f"retry-after={up.headers.get('retry-after','?')}")
            return await relay(up, extra_headers={"x-ai-verified": "anthropic-ratelimit-exhausted"})
        log(f"anthropic (pure) -> {up.status} {request.path}")
        return await relay(up, extra_headers={"x-ai-verified": "anthropic-pure"})

    # Fallback di sicurezza: ogni percorso sopra ritorna (il tunnel per
    # minimax/glm, il ramo ANTHROPIC PURA per mode=anthropic). Se si arriva
    # qui c'è un buco nel dispatch: meglio un 502 esplicito che un None
    # restituito ad aiohttp, che si manifesterebbe come errore oscuro.
    log(f"dispatch: nessun ramo ha gestito mode={mode} path={request.path} -> 502")
    return web.json_response(
        {"type": "error", "error": {"type": "router_error",
         "message": f"dispatch incompleto per mode={mode}"}}, status=502)


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

        if QWEN_AVAILABLE:
            try:
                import qwen_backend as _qwen
                _h["qwen"] = {
                    "upstream": await _qwen.qwen_upstream(),
                    "has_key": bool(await _qwen.get_qwen_key()),
                    "rate_limit": _qwen.QWEN_LIMITER.snapshot() if _qwen.QWEN_LIMITER else None,
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
        # auto_decompress=False BY DESIGN: il relay passa i byte compressi (br/gzip)
        # al client che decodifica. NON rimuovere: la lib Brotli locale è rotta
        # (TypeError in aiohttp compression_utils) → True romperebbe ogni risposta br.
        auto_decompress=False,
    )

    if _RESILIENCE_AVAILABLE and RESILIENCE_INST is not None:
        RESILIENCE_INST.start_periodic_self_test(session=session)
        RESILIENCE_INST.start_heartbeat()
        if hasattr(RESILIENCE_INST, "attach_loop"):
            RESILIENCE_INST.attach_loop(asyncio.get_running_loop())

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


# Il self-check di _repair_message_sequence che stava qui è stato spostato in
# sviluppo/tests/test_repair_message_sequence.py: era un assert dentro il blocco
# __main__ del processo di produzione, quindi una sua rottura non faceva fallire
# un test ma impediva al router di avviarsi.


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    if not MODE_FILE.exists():
        MODE_FILE.write_text("anthropic\n")
    log(f"START ai-router-proxy multi-port {LISTEN_PORTS}")
    asyncio.run(_run_multiport())


if __name__ == "__main__":
    main()
