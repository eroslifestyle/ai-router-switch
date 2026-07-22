# ~440 lines
"""Anthropic pipeline and body builders extracted from ai-router-proxy.py (~lines 1705-2796)."""
import asyncio
import gzip
import json
import os
import time

import debug_catalog

from router_constants import (
    THINK_MAX_TOKENS, THINK_MODEL, THINK_MODEL_ANTHROPIC, THINK_TIMEOUT_SEC,
    MINIMAX_CONTEXT_BYTE_LIMIT, ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT,
    SUMMARY_BUDGET, TRIM_TARGET_BYTES, TRIM_MIN_MESSAGES,
    CLAUDE_CODE_MARKER, FALLBACK_STATUSES, MINIMAX_FALLBACK_STATUSES,
    MIXED_EXECUTOR_MODEL, MINIMAX_MODEL,
)
from router_utils import (
    log, log_exc, _request_orig_model, _analyze_body_structure,
    SENT_ANALYSIS, _DEBUG_LAST_SENT, _DEBUG_REPAIR_TRACE,
    debug_capture, _repair_message_sequence,
)
from router_mode import _resolve_chat_fingerprint
from trim_smart import SHRINK_KEEP_HEAD, SHRINK_KEEP_TAIL, build_shrink_summary
from providers.base import (
    strip_images_body, FALLBACK_STATUSES as _FBS,
    _is_context_too_large_for_minimax as _is_ctx_large,
    _is_context_exceed_400 as _is_ctx_400,
    _body_has_images,
)
from forward_minimax import forward_minimax
from forward_anthropic import _log_original_model
from pipelines.primitives import build_finalize_body, build_act_body, to_json_bytes

# THINK su Sonnet (budget aumentato da 4s Haiku → 15s Sonnet).
# ACT_MINIMAX_TIMEOUT_SEC resta 12s per evitare retry-storm lato client.
MIX_AM_THINK_FAST_SEC = float(os.environ.get("AIROUTER_MIX_AM_THINK_FAST_SEC", "15"))
MIX_AM_ACT_TIMEOUT_SEC = float(os.environ.get("AIROUTER_MIX_AM_ACT_TIMEOUT_SEC", "12"))

# ── THINK backoff state per chat_fp ──────────────────────────────────────────
_think_lock = __import__("threading").Lock()
_think_count: dict[str, int] = {}
_THINK_TIMEOUT_SEQUENCE = [15, 20, 25]   # Sonnet ci sta in 15s sulla maggior parte
_THINK_SKIP_AFTER = 2

def _think_timeout_budget(chat_fp: str) -> float:
    with _think_lock:
        cnt = _think_count.get(chat_fp, 0)
    return _THINK_TIMEOUT_SEQUENCE[min(cnt, len(_THINK_TIMEOUT_SEQUENCE) - 1)]

def _think_timeout_record(chat_fp: str) -> None:
    with _think_lock:
        _think_count[chat_fp] = _think_count.get(chat_fp, 0) + 1

def _think_timeout_reset(chat_fp: str) -> None:
    with _think_lock:
        _think_count[chat_fp] = 0


# ── Body builders ──────────────────────────────────────────────────────────────
def _anthropic_system(instruction: str) -> list:
    """System array per Anthropic OAuth: marker Claude Code + istruzione."""
    return [{"type": "text", "text": CLAUDE_CODE_MARKER}, {"type": "text", "text": instruction}]


def _build_think_body(orig: dict) -> bytes:
    """THINK sintetico su MiniMax-M2.7 — redesign gerarchia 2026-07-22.

    Catena normale: main Anthropic → THINK MiniMax-M2.7 (genera piano) → ACT MiniMax (esegue).
    anthropic_leads=True → THINK passa a _build_think_body_haiku (catena Anthropic).
    """
    sys_msg = (
        "Sei un ORCHESTRATORE. Leggi la richiesta utente e scrivi un PIANO D'AZIONE "
        "BREVE (2-3 frasi) in italiano: cosa va fatto e in che ordine. "
        "Scrivi SOLO il piano come testo semplice. NON eseguire nulla.\n\n"
        "OPIANIFICATORE:\n"
        "OBIETTIVO: <cosa vuole ottenere l'utente>\n"
        "VINCOLI: <requisiti, limiti>\n"
        "NON FARE: <cose da evitare>"
    )
    from pipeline_common import build_think_digest
    # Immagini NON ridimensionate: inviate al THINK senza resize (fix 2026-07-22)
    digest, images = build_think_digest(orig)
    max_tokens = THINK_MAX_TOKENS
    if images:
        # L'esecutore NON riceve le immagini: la descrizione nel piano è la sua
        # unica fonte. Senza questa sezione l'esecutore rispondeva "nessuna
        # immagine allegata" e la richiedeva all'utente.
        sys_msg += (
            "\n\nIMMAGINI ALLEGATE: l'esecutore a valle NON vedrà le immagini. "
            "Aggiungi al piano una sezione:\n"
            "IMMAGINI: <per ciascuna: descrizione dettagliata di ciò che mostra — "
            "testo visibile, messaggi di errore verbatim, elementi UI, dati rilevanti>"
        )
        max_tokens = max(THINK_MAX_TOKENS, 1024)
    orig_model = (orig.get("model") or "").strip()
    content = [{"type": "text", "text": digest}] + images
    body = {
        "model": orig_model or THINK_MODEL,
        "system": _anthropic_system(sys_msg),
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "max_tokens": max_tokens,
    }
    return json.dumps(body).encode()


def _build_finalize_body_p(orig: dict, question: str, draft_v2: str) -> bytes:
    return to_json_bytes(build_finalize_body(orig, question, draft_v2))


def _build_act_body_p(orig: dict, plan: str, tools_to_call: list, executor: str = "") -> bytes:
    return to_json_bytes(build_act_body(orig, plan, tools_to_call, executor))


# ── Text extraction ────────────────────────────────────────────────────────────
def _text_from_message(j: dict) -> str:
    out = []
    for b in (j or {}).get("content", []):
        if isinstance(b, dict):
            t = b.get("type", "")
            if t == "text":
                out.append(b.get("text", ""))
            elif t == "thinking":
                inner = b.get("thinking", {})
                if isinstance(inner, dict):
                    out.append(inner.get("thinking", ""))
                elif isinstance(inner, str):
                    out.append(inner)
    return "".join(out)


def _parse_plan_text(text: str) -> dict | None:
    if not text:
        return None
    import re
    def extract_section(tag: str) -> str:
        pattern = rf'\[{re.escape(tag)}\](.*?)\[/{re.escape(tag)}\]'
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""
    plan = extract_section("PLAN")
    tools_raw = extract_section("TOOLS")
    review_raw = extract_section("SELF_REVIEW")
    if not plan:
        return None
    tools = []
    if tools_raw:
        try:
            tools = json.loads(tools_raw)
        except Exception:
            pass
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
    return {"plan": plan, "tools_to_call": tools, "self_review_ok": self_review_ok,
            "self_review_notes": self_review_notes}


def _parse_think_json(text: str) -> dict | None:
    return _parse_plan_text(text)


# ── Helpers ─────────────────────────────────────────────────────────────────────
def _force_no_stream(body: bytes):
    try:
        d = json.loads(body)
        d["stream"] = False
        return json.dumps(d).encode(), d
    except Exception:
        return body, {}


async def _retry_forward(forward_fn, request, body, session, attempts: int = 2):
    last_exc = None
    for i in range(attempts):
        try:
            return await forward_fn(request, body, session)
        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                log(f"[_retry] attempt {i+1}/{attempts} EXC={e}, retrying...")
    log(f"[_retry] all {attempts} attempts failed: {last_exc}")
    raise last_exc


async def _call_full(forward_fn, request, body, session, timeout: float = 90,
                     retry_transient: bool = True):
    """Chiamata non-streaming (THINK/VERIFY). Con retry_transient=True (default),
    le leg Anthropic ritentano i 429/5xx con backoff certificato SDK (riuso
    pipeline_common.anthropic_call_with_retry) invece di rimbalzare subito il 429
    come 'plan vuoto' → così le mix (mix-am THINK, mix-ag THINK/VERIFY) non fanno
    girare la chat all'infinito lato client durante un rate-limit reale del piano.
    Il retry avvolge SOLO il forward+status; la wait_for/read del body resta invariata."""
    nb, _ = _force_no_stream(body)
    up = None
    try:
        if retry_transient:
            from pipeline_common import anthropic_call_with_retry
            up, _exhausted = await asyncio.wait_for(
                anthropic_call_with_retry(forward_fn, request, nb, session,
                                          log_fn=log, tag="mix anthropic-leg"),
                timeout=timeout)
        else:
            up = await asyncio.wait_for(forward_fn(request, nb, session), timeout=timeout)
    except asyncio.TimeoutError:
        log(f"_call_full TIMEOUT {timeout}s req {getattr(request, 'path', '?')}")
        return 0, None
    except Exception as e:
        log_exc(f"_call_full EXC req {getattr(request, 'path', '?')}: {e}")
        if up is not None:
            try:
                up.release()
            except Exception:
                pass
        return 0, None
    status = up.status
    try:
        raw = await asyncio.wait_for(up.read(), timeout=timeout)
    except asyncio.TimeoutError:
        log(f"_call_full TIMEOUT {timeout}s read {getattr(request, 'path', '?')}")
        try:
            up.release()
        except Exception:
            pass
        return status, None
    try:
        up.release()
    except Exception:
        pass
    ce = (up.headers.get("Content-Encoding") or "").lower().strip()
    if ce and raw:
        try:
            if "gzip" in ce:
                raw = gzip.decompress(raw)
            elif "br" in ce or "brotli" in ce:
                try:
                    import brotli
                    raw = brotli.decompress(raw)
                except Exception:
                    pass
            elif "deflate" in ce:
                import zlib
                try:
                    raw = zlib.decompress(raw, -zlib.MAXWBITS)
                except Exception:
                    raw = zlib.decompress(raw)
        except Exception as e:
            log(f"_call_full: decompress {ce} fail: {e}")
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


# ── Context helpers ─────────────────────────────────────────────────────────────
def _is_context_too_large_for_minimax(body_bytes: bytes) -> bool:
    return len(body_bytes) > MINIMAX_CONTEXT_BYTE_LIMIT


async def _is_context_exceed_400(up) -> tuple:
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
              or b"context_exceeded" in low)
    return (is_ctx, raw)


def _has_server_tools(orig: dict) -> bool:
    return any(isinstance(t, dict) and "input_schema" not in t
               for t in orig.get("tools") or [])


def _has_web_search_tool(orig: dict) -> bool:
    return any(isinstance(t, dict) and (str(t.get("type", "")).startswith("web_search")
               or t.get("name") == "web_search") for t in orig.get("tools") or [])


def _web_search_blocked_response():
    from aiohttp import web
    return web.json_response(
        {"type": "error", "error": {"type": "invalid_request_error",
         "message": "web_search Anthropic disabilitato in mixed/minimax: usa MCP MiniMax."}},
        status=400)


# ── Message repair ──────────────────────────────────────────────────────────────

# ── Trim ──────────────────────────────────────────────────────────────────────
# _trim_context_after_response RIMOSSA (fix 2026-07-21): salvava un body "trimmato"
# in TRIM_STATE_DIR che il TRIM INTERCEPT (rimosso da handle()) usava per SOSTITUIRE
# la richiesta del turno successivo → il modello perdeva l'ultimo messaggio/tool_result.
# Inoltre lo slice msgs[:-k]+msgs[-k:] era un no-op (stesso pattern del bug 284322a).


# ── Shrink ─────────────────────────────────────────────────────────────────────
async def _shrink_and_retry_minimax(request, orig: dict, body: bytes,
                                   session, chat_fp: str, relay,
                                   allow_anthropic_rescue: bool = True):
    """Pipeline shrink dinamico: comprime e ritenta MiniMax.

    allow_anthropic_rescue=False (solo-minimax): mai cross-provider, 502 al client."""
    from forward_minimax import forward_minimax
    from summarizer import summarize_old_messages
    from router_constants import MINIMAX_UPSTREAM
    from router_auth import get_minimax_key
    from aiohttp import web

    async def _rescue():
        if allow_anthropic_rescue:
            return await _escalate_anthropic(request, orig, session, chat_fp, relay)
        log(f"shrink: rescue Anthropic OFF (isolamento minimax) -> 502 fp={chat_fp}")
        return web.json_response({"type": "error", "error": {
            "type": "minimax_unavailable",
            "message": "minimax solo: shrink fallito, nessun fallback cross-provider"}}, status=502)

    log(f"shrink: inizio body={len(body)}b fp={chat_fp}")
    try:
        orig_dict = json.loads(body) if isinstance(body, bytes) else body
    except Exception:
        return await _rescue()
    messages = orig_dict.get("messages", [])
    if not messages:
        return await _rescue()
    # Immagini NON ridimensionate per shrink (fix 2026-07-22)
    budget = MINIMAX_CONTEXT_BYTE_LIMIT // 2  # ponytail: summary <= 50% del limite
    summary_content = build_shrink_summary(messages, budget)
    shrunk = dict(orig_dict)
    tail_msgs = messages[-SHRINK_KEEP_TAIL:] if messages else []
    system_val = orig_dict.get("system", "")
    if isinstance(system_val, list):
        system_str = "\n\n".join(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for v in system_val)
    else:
        system_str = system_val or ""
    system_content = system_str + "\n\n" + summary_content if "system" in orig_dict else summary_content
    shrunk["messages"] = tail_msgs
    tail_msgs = _repair_message_sequence(tail_msgs)
    if system_content:
        shrunk["system"] = system_content
    shrunk.pop("thinking", None)
    shrunk_bytes = json.dumps(shrunk).encode()
    log(f"shrink: {len(body)}b -> {len(shrunk_bytes)}b fp={chat_fp}")
    if len(shrunk_bytes) > MINIMAX_CONTEXT_BYTE_LIMIT:
        summary_msgs = await summarize_old_messages(messages, MINIMAX_MODEL, chat_fp, MINIMAX_UPSTREAM, await get_minimax_key())
        if summary_msgs is not None:
            summ_shrunk = dict(orig_dict)
            summ_shrunk["messages"] = summary_msgs
            summ_shrunk.pop("thinking", None)
            summ_bytes = json.dumps(summ_shrunk).encode()
            log(f"shrink: LLM summary {len(messages)} msgs -> {len(summ_bytes)}b fp={chat_fp}")
            if len(summ_bytes) <= MINIMAX_CONTEXT_BYTE_LIMIT:
                try:
                    up = await forward_minimax(request, summ_bytes, session)
                    if up.status < 400:
                        log(f"shrink: LLM summary SUCCESS fp={chat_fp}")
                        return await relay(up, extra_headers={"x-ai-verified": "m3-llm-summary"})
                    await up.release()
                except Exception as e:
                    log(f"shrink: LLM summary MiniMax EXC {e} fp={chat_fp}")
        return await _rescue()
    try:
        up = await forward_minimax(request, shrunk_bytes, session)
        if up.status < 400:
            log(f"shrink: SUCCESS {up.status} fp={chat_fp}")
            return await relay(up, extra_headers={"x-ai-verified": "m3-shrunk-act"})
        is_ctx, _ = await _is_context_exceed_400(up)
        try:
            await up.release()
        except Exception:
            pass
        if is_ctx:
            log(f"shrink: anche compresso -> 400 context-exceed fp={chat_fp}")
            return await _rescue()
        log(f"shrink: MiniMax {up.status} -> rescue fp={chat_fp}")
        return await _rescue()
    except Exception as e:
        log(f"shrink: MiniMax EXC {e} -> rescue fp={chat_fp}")
        return await _rescue()


# ── Mixed rescue ───────────────────────────────────────────────────────────────
async def _escalate_anthropic(request, orig: dict, session, chat_fp: str, relay,
                              anthropic_leads: bool = False):
    """Catena escalation Anthropic dopo fallimento MiniMax.

    anthropic_leads=True: MiniMax down da 2+ turni → catena Anthropic completa.
      Haiku (digest compatto) → VERIFY Sonnet (se gated) → 502 se tutto fallisce.
    anthropic_leads=False (default): fallback dopo singolo fail MiniMax nel turno.
      Prova modello utente → Haiku → 502.
    """
    from forward_anthropic import forward_anthropic, forward_anthropic_direct
    from router_utils import log as _log, _analyze_body_structure
    from router_constants import THINK_MODEL, ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT
    tr = getattr(request, "transport", None)
    if tr is None or tr.is_closing():
        # Relay al client già iniziato e rotto (o client sparito): ogni rescue
        # scriverebbe su un transport chiuso -> "Cannot write to closing
        # transport". Inutile spendere chiamate user-model + Haiku.
        _log(f"mix-am ACT rescue SKIP: transport client chiuso fp={chat_fp}")
        raise ConnectionResetError("client transport closing, rescue impossibile")
    _log(f"mix-am ACT: Haiku rescue fp={chat_fp}")
    body_bytes_rescue = json.dumps(dict(orig)).encode()
    if len(body_bytes_rescue) > MINIMAX_CONTEXT_BYTE_LIMIT:
        shrunk = await _try_shrink_body_haiku(orig, MINIMAX_CONTEXT_BYTE_LIMIT)
        if shrunk is not None and shrunk != body_bytes_rescue:
            body_bytes_rescue = shrunk
            _log(f"mix-am ACT rescue: shrink OK -> {len(body_bytes_rescue)}b fp={chat_fp}")
    user_status = None
    user_raw = b""
    haiku_status = None
    haiku_raw = b""
    try:
        up = await forward_anthropic_direct(request, body_bytes_rescue, session)
        user_status = up.status
        if up.status < 400:
            from fail_tracker import mixed_fail_reset
            mixed_fail_reset(chat_fp)
            _log(f"mix-am ACT rescue: modello utente {up.status} OK fp={chat_fp}")
            return await relay(up)
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
            should_retry = str(up.headers.get("x-should-retry", "")).lower() == "true"
            if not should_retry:
                _log(f"mix-am ACT rescue: modello utente 429 Rate Limit -> relay subito fp={chat_fp}")
                return await relay(up)
            # Retry certificato SDK (2026-07-22): stessa logica del path anthropic
            # puro (backoff esponenziale + jitter, onora retry-after) invece dei
            # delay hardcoded [1.5, 3.0] che ignoravano retry-after. Esauriti i
            # retry → Haiku. Riuso pipeline_common.
            from pipeline_common import (parse_retry_after as _parse_ra,
                                         backoff_sleep_sec as _backoff,
                                         ANTHROPIC_MAX_RETRIES as _MAXR)
            last_exc = None
            for i in range(_MAXR):
                retry_after = _parse_ra(up.headers.get("retry-after", ""))
                delay = _backoff(i, retry_after)
                try:
                    await up.release()
                except Exception:
                    pass
                _log(f"mix-am ACT rescue: modello utente 429 retry {i+1}/{_MAXR} "
                     f"retry-after={retry_after} sleep={delay:.2f}s fp={chat_fp}")
                await asyncio.sleep(delay)
                try:
                    up = await forward_anthropic_direct(request, body_bytes_rescue, session)
                    user_status = up.status
                    if up.status < 400:
                        from fail_tracker import mixed_fail_reset
                        mixed_fail_reset(chat_fp)
                        _log(f"mix-am ACT rescue: modello utente retry {i+1} {up.status} OK fp={chat_fp}")
                        return await relay(up)
                    if up.status == 429:
                        if str(up.headers.get("x-should-retry", "")).lower() == "false":
                            _log(f"mix-am ACT rescue: modello utente 429 x-should-retry=false -> Haiku fp={chat_fp}")
                            break
                        _log(f"mix-am ACT rescue: modello utente retry {i+1} ancora 429 -> continua fp={chat_fp}")
                        last_exc = None
                        continue
                    # non-429, non-2xx: esci dal retry loop e vai a Haiku
                    _log(f"mix-am ACT rescue: modello utente retry {i+1} {up.status} -> Haiku fp={chat_fp}")
                    break
                except Exception as e:
                    last_exc = e
                    _log(f"mix-am ACT rescue modello utente retry {i+1} EXC: {e}")
            # tutti i retry esauriti o eccezione: prosegue a Haiku
            if last_exc:
                user_status = None
                _log(f"mix-am ACT rescue: modello utente retry esauriti EXC -> Haiku fp={chat_fp}")
        else:
            try:
                await up.release()
            except Exception:
                pass
    except Exception as e:
        user_status = None
        _log(f"mix-am ACT rescue modello utente EXC: {e} -> Haiku")

    # Haiku fallback
    try:
        haiku_body_dict = dict(orig)
        haiku_body_dict["model"] = THINK_MODEL
        haiku_body_bytes = json.dumps(haiku_body_dict).encode()
        if len(haiku_body_bytes) > ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT:
            shrunk_h = await _try_shrink_body_haiku(haiku_body_dict, MINIMAX_CONTEXT_BYTE_LIMIT)
            if shrunk_h is None:
                from aiohttp import web
                _log(f"mix-am ACT rescue: body > Haiku limit, skip fp={chat_fp}")
                return web.json_response(
                    {"type": "error", "error": {"type": "context_exceeded",
                     "message": f"body troppo grande anche per shrink."}},
                    status=400)
            haiku_body_bytes = shrunk_h
        # Retry certificato SDK anche sulla leg Haiku (bucket separato ma 429/5xx
        # transienti possibili): backoff + retry-after invece di rimbalzo immediato.
        from pipeline_common import anthropic_call_with_retry as _acr
        up_h, _h_exhausted = await _acr(forward_anthropic, request, haiku_body_bytes,
                                        session, log_fn=_log, tag="mix-am Haiku-rescue")
        haiku_status = up_h.status
        if up_h.status < 400:
            from fail_tracker import mixed_fail_reset
            mixed_fail_reset(chat_fp)
            _log(f"mix-am ACT rescue Haiku OK fp={chat_fp}")
            return await relay(up_h, extra_headers={"x-ai-verified": "haiku-rescue-act"})
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
            # 429 persistito dopo i retry certificati = rate-limit reale del piano.
            # Relay onesto del 429 (con retry-after) al client, NON loop.
            _log(f"mix-am ACT rescue Haiku 429 PERSISTENTE dopo retry -> relay onesto fp={chat_fp}")
            return await relay(up_h, extra_headers={"x-ai-verified": "mix-am-ratelimit-exhausted"})
        elif up_h.status >= 500:
            _log(f"mix-am ACT rescue: Haiku {up_h.status}, relay upstream body fp={chat_fp}")
            return await relay(up_h)
        else:
            try:
                await up_h.release()
            except Exception:
                pass
        haiku_status = up_h.status
    except Exception as e:
        _log(f"mix-am ACT rescue Haiku EXC: {e} -> 502")

    # Debug capture
    orig_analysis = _analyze_body_structure(orig)
    user_sent_analysis = _analyze_body_structure(body_bytes_rescue)
    haiku_sent_analysis = _analyze_body_structure(haiku_body_bytes)
    debug_capture(kind="mixed_rescue_502", request=request, fp=chat_fp,
                  client_model=orig.get("model", ""), status=502, stage="user_model",
                  upstream_status=user_status or 0, upstream_raw=user_raw,
                  sent_bytes=len(body_bytes_rescue), orig=orig,
                  sent_analysis={"orig": orig_analysis, "sent": user_sent_analysis},
                  note=f"haiku_stage={haiku_status}")
    err_parts = [f"Haiku rescue failed: user_model={user_status}, Haiku={haiku_status}."]
    if user_raw:
        from router_utils import _decompress_upstream
        err_parts.append("user_model: " + _decompress_upstream(user_raw)[:300])
    if haiku_raw:
        err_parts.append("haiku: " + _decompress_upstream(haiku_raw)[:300])
    err_parts.append("Dettagli: /debug/last")
    from aiohttp import web
    return web.json_response({"type": "error", "error": {"type": "router_error",
             "message": " | ".join(err_parts)}}, status=502)


async def _anthropic_rescue(request, orig: dict, session, chat_fp: str, relay):
    return await _escalate_anthropic(request, orig, session, chat_fp, relay)


async def _try_shrink_body_haiku(orig: dict, target_bytes: int):
    """Shrink body per stare in target_bytes (versione inline per rescue)."""
    try:
        msgs = orig.get("messages", []) or []
        if not msgs:
            return None
        # Immagini NON ridimensionate per shrink (fix 2026-07-22)
        budget = SUMMARY_BUDGET
        summary_content = build_shrink_summary(msgs, budget)
        tail_msgs = msgs[-SHRINK_KEEP_TAIL:] if msgs else []
        system_val = orig.get("system", "")
        if isinstance(system_val, list):
            system_str = "\n\n".join(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for v in system_val)
        else:
            system_str = system_val or ""
        system_content = system_str + "\n\n" + summary_content if system_str else summary_content
        shrunk = dict(orig)
        shrunk["messages"] = tail_msgs
        tail_msgs = _repair_message_sequence(tail_msgs)
        if system_content:
            shrunk["system"] = system_content
        shrunk.pop("thinking", None)
        shrunk_bytes = json.dumps(shrunk).encode()
        if len(shrunk_bytes) <= target_bytes:
            return shrunk_bytes
        tail2 = msgs[-2:] if len(msgs) >= 2 else msgs
        tail2 = _repair_message_sequence(tail2)
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
        log(f"try_shrink_body_haiku EXC: {e}")
        return None


def _shrink_images_in_messages(orig: dict, max_side: int = 1024, jpeg_quality: int = 70):
    """Ridimensiona immagini base64 nei content blocks per ridurre body. ponytail:
    solo Anthropic image blocks (source.type=base64), lazy import PIL, fallback
    silenzioso se non installato. max_side=1024 + JPEG q70 -> tipicamente 5-10x
    riduzione su PNG foto-realistiche. Non applica a tool_result immagini
    ricevute dal modello (il flusso non riutilizza immagini ricevute)."""
    try:
        from PIL import Image
        import io, base64
    except Exception:
        return
    try:
        for msg in orig.get("messages", []) or []:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for blk in content:
                if not isinstance(blk, dict) or blk.get("type") != "image":
                    continue
                src = blk.get("source") or {}
                if src.get("type") != "base64":
                    continue
                data_b64 = src.get("data")
                if not data_b64 or len(data_b64) < 4000:  # ponytail: skip icone
                    continue
                try:
                    raw = base64.b64decode(data_b64)
                    img = Image.open(io.BytesIO(raw))
                    img.load()
                    if max(img.size) > max_side:
                        img.thumbnail((max_side, max_side), Image.LANCZOS)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=jpeg_quality)
                    new_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    if len(new_b64) < len(data_b64):
                        src["data"] = new_b64
                        src["media_type"] = "image/jpeg"
                except Exception:
                    continue
    except Exception:
        return


# ── Main mixed pipeline ─────────────────────────────────────────────────────────
async def _pipeline_think_act(request, body, session, orig: dict, relay):
    """Redesign 2026-07-01 mixed: Anthropic THINK+self-review -> M3 ACT.
    Scatta per TUTTE le /v1/messages (incluso agentico con tools)."""
    from forward_anthropic import forward_anthropic, forward_anthropic_direct
    from forward_minimax import forward_minimax
    from fail_tracker import mixed_fail_inc, mixed_fail_reset, fail_tracker, mixed_anthropic_leads

    chat_fp = _resolve_chat_fingerprint(request)
    mixed_fail_last_status = None
    wants_stream = bool(orig.get("stream"))

    if _is_context_too_large_for_minimax(body):
        log(f"mix-am PRE: body {len(body)}b > limit -> shrink/retry fp={chat_fp}")
        return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)

    if _has_web_search_tool(orig):
        log(f"mix-am: web_search Anthropic bloccato -> usa MCP MiniMax fp={chat_fp}")
        return _web_search_blocked_response()

    if _has_server_tools(orig):
        log(f"mix-am: server tools -> pipeline bypass fp={chat_fp}")
        return await _escalate_anthropic(request, orig, session, chat_fp, relay)

    # Bypass vision rimosso (fix 2026-07-21): con immagini attraverso normale
    # pipeline THINK→ACT→VERIFY (Anthropic THINK legge immagini, M3 ACT riceve
    # piano testuale, no immagini a M3 che allucinava).

    # BYPASS-THINK rimosso (fix 2026-07-23): ogni messaggio passa SEMPRE per
    # l'orchestratore Anthropic (THINK→ACT→VERIFY), anche i task leggeri.
    # Prima: task <200c senza tool andavano direttamente a MiniMax (bypassava
    # orchestratore). Ora: anche i task leggeri vengono analizzati dall'orchestratore
    # prima di delegare all'esecutore.

    think_body = _build_think_body(orig)
    # FIX BUG-3: backoff esponenziale sul timeout THINK + skip dopo 2 KO consecutivi.
    # Budget: 4s → 6s → 8s (max). Dopo 2 timeout: ACT diretto senza THINK.
    plan = ""
    think_budget = _think_timeout_budget(chat_fp)
    if _think_count.get(chat_fp, 0) >= _THINK_SKIP_AFTER:
        log(f"mix-am THINK skip (>= {_THINK_SKIP_AFTER} timeout consec.) -> ACT diretto fp={chat_fp}")
        plan = ""
    else:
        try:
            t_status, t_json = await asyncio.wait_for(
                _call_full(forward_anthropic_direct, request, think_body, session, timeout=THINK_TIMEOUT_SEC),
                timeout=think_budget,
            )
            if t_json and t_status not in FALLBACK_STATUSES:
                _think_timeout_reset(chat_fp)
                plan = _text_from_message(t_json).strip()
                if not plan:
                    log(f"mix-am THINK {t_status} OK ma testo VUOTO -> ACT senza piano fp={chat_fp}")
            else:
                log(f"mix-am THINK KO status={t_status} json={bool(t_json)} -> ACT senza piano fp={chat_fp}")
                debug_catalog.record_event(severity="block", category="mix-am",
                                            kind="think_status_ko", chat_fp=chat_fp,
                                            snippet=f"status={t_status}")
        except asyncio.TimeoutError:
            _think_timeout_record(chat_fp)
            think_budget = _think_timeout_budget(chat_fp)
            log(f"mix-am THINK timeout ({think_budget}s) -> ACT senza piano fp={chat_fp}")
            debug_catalog.record_event(severity="block", category="mix-am",
                                        kind="think_timeout", chat_fp=chat_fp,
                                        snippet=f"budget={think_budget}s, seq={_THINK_TIMEOUT_SEQUENCE}")
        except Exception as e:
            _think_timeout_reset(chat_fp)
            log(f"mix-am THINK EXC: {e} -> ACT senza piano fp={chat_fp}")
            debug_catalog.record_event(severity="error", category="mix-am",
                                        kind="think_exception", chat_fp=chat_fp, snippet=str(e))

    if not plan:
        # THINK assente/scaduto/fallito: ACT diretto con timeout stretto + rescue.
        # (precedentemente 3 path duplicati: EXC / status-ko / piano-vuoto — unificati)
        log(f"mix-am senza piano THINK -> ACT diretto fp={chat_fp}")
        try:
            up = await forward_minimax(request, body, session, act_timeout_sec=MIX_AM_ACT_TIMEOUT_SEC)
            if up.status not in FALLBACK_STATUSES:
                log(f"mix-am ACT diretto (no plan) {up.status} OK {request.path} fp={chat_fp}")
                return await relay(up)
            log(f"mix-am ACT diretto (no plan) {up.status} -> rescue fp={chat_fp}")
            try:
                await up.release()
            except Exception:
                pass
        except Exception as e:
            log(f"mix-am ACT diretto (no plan) EXC: {type(e).__name__}: {e} -> rescue fp={chat_fp}")
        anthropic_leads = mixed_anthropic_leads(chat_fp)
        return await _escalate_anthropic(request, orig, session, chat_fp, relay,
                                        anthropic_leads=anthropic_leads)
    tools_to_call = []
    log(f"mix-am THINK OK plan={len(plan)}c fp={chat_fp}")

    executors = [MIXED_EXECUTOR_MODEL]
    if MINIMAX_MODEL not in executors:
        executors.append(MINIMAX_MODEL)
    orig_model = (orig.get("model") or "").strip()
    if orig_model and not orig_model.startswith("MiniMax"):
        if _is_context_too_large_for_minimax(body):
            return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)

    up = None
    used_exe = ""
    for exe in executors:
        act_body = _build_act_body_p(orig, plan, tools_to_call, executor=exe)
        if orig_model and not orig_model.startswith("MiniMax"):
            try:
                from router_constants import SIDECAR
                SIDECAR.parent.mkdir(parents=True, exist_ok=True)
                entry = {"ts": int(time.time()), "chat": chat_fp, "orig": orig_model, "final": exe}
                with open(SIDECAR, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception:
                pass
            _request_orig_model[chat_fp] = orig_model
        try:
            up = await forward_minimax(request, act_body, session, act_timeout_sec=MIX_AM_ACT_TIMEOUT_SEC)
        except Exception as e:
            mixed_fail_last_status = None
            n = mixed_fail_inc(chat_fp)
            log(f"mix-am ACT {exe} EXC ({n}/{fail_tracker.threshold}): {type(e).__name__}: {e} | repr={repr(e)[:200]}")
            up = None
            continue
        mixed_fail_last_status = up.status
        if up.status in MINIMAX_FALLBACK_STATUSES:
            n = mixed_fail_inc(chat_fp)
            log(f"mix-am ACT {exe} {up.status} ({n}/{fail_tracker.threshold})")
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
        is_ctx, _ctx_raw = await _is_context_exceed_400(up)
        if is_ctx:
            log(f"mix-am ACT {exe} 400 context-exceed -> rescue fp={chat_fp}")
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
        log(f"mix-am ACT {used_exe} {up.status} {request.path} fp={chat_fp}")
        mixed_fail_reset(chat_fp)
        return await relay(up, extra_headers={"x-ai-verified": f"anthropic-think+{used_exe.lower()}-act"})

    if mixed_fail_last_status == 429:
        return web.json_response(
            {"type": "error", "error": {"type": "rate_limit_error",
             "message": "MiniMax rate limited (429). Retry-After rispettato dal client."}},
            status=429)

    # Gerarchia: anthropic_leads=True (MiniMax fallito 2x) → escalation Anthropic
    # Il mixed_fail_tracker si attiva dopo fail_threshold=2 chiamate consecutive.
    anthropic_leads = mixed_anthropic_leads(chat_fp)
    if anthropic_leads:
        log(f"mix-am ACT: anthropic_leads=True (MiniMax 2x KO) -> catena Anthropic fp={chat_fp}")
        return await _escalate_anthropic(request, orig, session, chat_fp, relay,
                                         anthropic_leads=True)
    log(f"mix-am ACT: tutti executor falliti (non-429) -> rescue fp={chat_fp}")
    return await _escalate_anthropic(request, orig, session, chat_fp, relay)


# _serve_minimax_vision RIMOSSO (fix 2026-07-21):
# - bypass diretto a M3 saltava l'intera catena THINK→ACT→VERIFY
# - ora mix-am gestisce immagini tramite pipeline normale: THINK legge le immagini,
#   ACT riceve solo testo+piano (images strippate da build_executor_body), VERIFY chiude


async def _serve_minimax_vision(request, orig: dict, session, chat_fp: str, relay):
    """Stub: la logica vision è ora nel flusso THINK→ACT di mix-am/mix-gm.
    Le immagini arrivano a THINK (Anthropic/GLM) che le analizza e produce un piano.
    L'ACT (MiniMax) riceve solo testo+piano, mai immagini raw.
    Ritorna sempre None (nessun bypass diretto a M3)."""
    return None


# Alias back-compat: _mixed_haiku_rescue rinominata in _escalate_anthropic
# (redesign gerarchia 2026-07-22). ai-router-proxy.py e altri moduli importano
# ancora il vecchio nome.
_mixed_haiku_rescue = _escalate_anthropic
