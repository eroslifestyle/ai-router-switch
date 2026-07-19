# ~440 lines
"""Anthropic pipeline and body builders extracted from ai-router-proxy.py (~lines 1705-2796)."""
import asyncio
import gzip
import json
import time

from router_constants import (
    THINK_MAX_TOKENS, THINK_MODEL, THINK_TIMEOUT_SEC,
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
from pipelines.primitives import build_finalize_body, build_act_body, to_json_bytes


# ── Body builders ──────────────────────────────────────────────────────────────
def _anthropic_system(instruction: str) -> list:
    """System array per Anthropic OAuth: marker Claude Code + istruzione."""
    return [{"type": "text", "text": CLAUDE_CODE_MARKER}, {"type": "text", "text": instruction}]


def _build_think_body(orig: dict) -> bytes:
    """THINK gira sul MODELLO SELEZIONATO DALL'UTENTE."""
    sys_msg = (
        "Sei un ORCHESTRATORE. Leggi la richiesta utente e scrivi un PIANO D'AZIONE "
        "BREVE (2-3 frasi) in italiano: cosa va fatto e in che ordine. "
        "Scrivi SOLO il piano come testo semplice. NON eseguire nulla.\n\n"
        "OPIANIFICATORE:\n"
        "OBIETTIVO: <cosa vuole ottenere l'utente>\n"
        "VINCOLI: <requisiti, limiti>\n"
        "NON FARE: <cose da evitare>"
    )
    body = dict(orig)
    body["system"] = _anthropic_system(sys_msg)
    body["stream"] = False
    body["max_tokens"] = THINK_MAX_TOKENS
    _m = (orig.get("model") or "").strip()
    body["model"] = _m if _m and not _m.startswith("MiniMax") else THINK_MODEL
    body.pop("tools", None)
    body.pop("thinking", None)
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


async def _call_full(forward_fn, request, body, session, timeout: float = 90):
    nb, _ = _force_no_stream(body)
    up = None
    try:
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
def _trim_context_after_response(req_body: bytes, fp: str) -> None:
    """Taglia proattivamente il context DOPO ogni risposta."""
    import os, tempfile
    from router_constants import TRIM_STATE_DIR, TRIM_TARGET_BYTES, TRIM_MIN_MESSAGES
    from router_utils import trim_locks
    try:
        stripped = strip_images_body(req_body)
        data = json.loads(stripped)
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
        lock = trim_locks.setdefault(fp, __import__('threading').Lock())
        with lock:
            tmp = tempfile.NamedTemporaryFile(dir=TRIM_STATE_DIR, delete=False, suffix=".tmp")
            try:
                tmp.write(trimmed_bytes)
                tmp.close()
                os.replace(tmp.name, str(TRIM_STATE_DIR / f"{fp}.json"))
            except Exception:
                __import__('pathlib').Path(tmp.name).unlink(missing_ok=True)
                raise
        log(f"trim: {len(req_body)}b->{len(trimmed_bytes)}b ({n}->{len(trimmed['messages'])} msg) fp={fp}")
    except Exception as e:
        log(f"trim: write fail {e} fp={fp}")


# ── Shrink ─────────────────────────────────────────────────────────────────────
async def _shrink_and_retry_minimax(request, orig: dict, body: bytes,
                                   session, chat_fp: str, relay):
    """Pipeline shrink dinamico: comprime e ritenta MiniMax."""
    from forward_minimax import forward_minimax
    from summarizer import summarize_old_messages
    from router_constants import MINIMAX_UPSTREAM
    from router_auth import get_minimax_key
    log(f"shrink: inizio body={len(body)}b fp={chat_fp}")
    try:
        orig_dict = json.loads(body) if isinstance(body, bytes) else body
    except Exception:
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)
    messages = orig_dict.get("messages", [])
    if not messages:
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)
    budget = SUMMARY_BUDGET
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
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)
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
            return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)
        log(f"shrink: MiniMax {up.status} -> fallback Haiku fp={chat_fp}")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)
    except Exception as e:
        log(f"shrink: MiniMax EXC {e} -> fallback Haiku fp={chat_fp}")
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)


# ── Vision ─────────────────────────────────────────────────────────────────────
async def _serve_minimax_vision(request, orig: dict, session, chat_fp: str, relay):
    from forward_minimax import forward_minimax
    if _has_server_tools(orig):
        return None
    orig2 = dict(orig)
    orig_model = (orig2.get("model") or "").strip()
    orig2["model"] = "MiniMax-M3"
    body2 = json.dumps(orig2).encode()
    if _is_context_too_large_for_minimax(body2):
        return None
    if orig_model and not orig_model.startswith("MiniMax"):
        # inline _log_original_model to avoid circular import
        try:
            from router_constants import SIDECAR
            SIDECAR.parent.mkdir(parents=True, exist_ok=True)
            entry = {"ts": int(time.time()), "chat": chat_fp, "orig": orig_model, "final": "MiniMax-M3"}
            with open(SIDECAR, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
        _request_orig_model[chat_fp] = orig_model
    try:
        up = await forward_minimax(request, body2, session)
    except Exception as e:
        log(f"minimax-vision EXC: {e} -> fallback caller fp={chat_fp}")
        return None
    if up.status in MINIMAX_FALLBACK_STATUSES:
        log(f"minimax-vision {up.status} -> fallback caller fp={chat_fp}")
        try:
            raw = await up.read()
        except Exception:
            raw = b""
        try:
            await up.release()
        except Exception:
            pass
        debug_capture(kind="minimax_vision_fallback", request=request, fp=chat_fp,
                      client_model=orig.get("model", ""), upstream_model="MiniMax-M3",
                      status=up.status, stage="minimax_vision", upstream_status=up.status,
                      upstream_raw=raw, upstream_encoding=up.headers.get("Content-Encoding", ""),
                      orig=orig, note=f"status {up.status} -> caller fallback")
        return None
    log(f"minimax-vision M3 OK {up.status} fp={chat_fp}")
    return await relay(up, extra_headers={"x-ai-verified": "minimax-m3-vision"})


# ── Mixed rescue ───────────────────────────────────────────────────────────────
async def _mixed_haiku_rescue(request, orig: dict, session, chat_fp: str, relay):
    """Fallback: user model -> Haiku -> 502."""
    from forward_anthropic import forward_anthropic, forward_anthropic_direct
    from router_utils import log as _log, _analyze_body_structure
    from router_constants import THINK_MODEL, ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT
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
            ra = up.headers.get("retry-after")
            delay = min(float(ra), 3.0) if ra and ra.isdigit() else 1.5
            try:
                await up.release()
            except Exception:
                pass
            _log(f"mix-am ACT rescue: modello utente 429 retry, {delay}s fp={chat_fp}")
            await asyncio.sleep(delay)
            try:
                up = await forward_anthropic_direct(request, body_bytes_rescue, session)
                user_status = up.status
                if up.status < 400:
                    from fail_tracker import mixed_fail_reset
                    mixed_fail_reset(chat_fp)
                    _log(f"mix-am ACT rescue: modello utente retry {up.status} OK fp={chat_fp}")
                return await relay(up)
            except Exception as e:
                _log(f"mix-am ACT rescue modello utente retry EXC: {e}")
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
        up_h = await forward_anthropic(request, haiku_body_bytes, session)
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
            _log(f"mix-am ACT rescue Haiku 429 Rate Limit -> relay subito fp={chat_fp}")
            return await relay(up_h)
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
    return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)


async def _try_shrink_body_haiku(orig: dict, target_bytes: int):
    """Shrink body per stare in target_bytes (versione inline per rescue)."""
    try:
        msgs = orig.get("messages", []) or []
        if not msgs:
            return None
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


# ── Main mixed pipeline ─────────────────────────────────────────────────────────
async def _pipeline_think_act(request, body, session, orig: dict, relay):
    """Redesign 2026-07-01 mixed: Anthropic THINK+self-review -> M3 ACT.
    Scatta per TUTTE le /v1/messages (incluso agentico con tools)."""
    from forward_anthropic import forward_anthropic, forward_anthropic_direct
    from forward_minimax import forward_minimax
    from fail_tracker import mixed_fail_inc, mixed_fail_reset, fail_tracker

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
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

    if _body_has_images(orig):
        res = await _serve_minimax_vision(request, orig, session, chat_fp, relay)
        if res is not None:
            return res
        return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)

    # D45 BYPASS-THINK: task leggeri
    LIGHT_MSG_THRESHOLD = 200
    msgs = orig.get('messages') or []
    user_msgs = [m for m in msgs if m.get('role') == 'user']
    content_len = 0
    if user_msgs:
        last = user_msgs[-1].get('content', '')
        content_len = len(last) if isinstance(last, str) else len(str(last))
    is_light = (not orig.get('tools') and len(user_msgs) == 1 and content_len < LIGHT_MSG_THRESHOLD)
    if is_light:
        orig_model = (orig.get('model') or '').strip()
        try:
            if orig_model.lower().startswith('minimax'):
                up = await forward_minimax(request, body, session)
            else:
                up = await forward_anthropic(request, body, session)
            mixed_fail_reset(chat_fp)
            log(f'mix-am BYPASS-THINK direct (light, {content_len}c) fp={chat_fp}')
            return await relay(up)
        except Exception as e:
            log(f'mix-am BYPASS-THINK EXC: {e} -> fallthrough')

    think_body = _build_think_body(orig)
    try:
        t_status, t_json = await _call_full(forward_anthropic_direct, request, think_body, session, timeout=THINK_TIMEOUT_SEC)
    except Exception as e:
        log(f"mix-am THINK EXC: {e} -> fallback M3 diretto")
        try:
            return await relay(await _retry_forward(forward_minimax, request, body, session))
        except Exception as e2:
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": f"think+fallback ko: {e2}"}}, status=502)
    if not t_json or t_status in FALLBACK_STATUSES:
        log(f"mix-am THINK ko {t_status} -> fallback M3 diretto")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": f"think ko + fallback ko: {e}"}}, status=502)
    plan = _text_from_message(t_json).strip()
    if not plan:
        log(f"mix-am THINK: piano vuoto -> fallback M3 diretto fp={chat_fp}")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": f"think vuoto + fallback ko: {e}"}}, status=502)
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
            up = await forward_minimax(request, act_body, session)
        except Exception as e:
            mixed_fail_last_status = None
            n = mixed_fail_inc(chat_fp)
            log(f"mix-am ACT {exe} EXC ({n}/{fail_tracker.threshold}): {e}")
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

    log(f"mix-am ACT: tutti executor falliti (non-429) -> rescue fp={chat_fp}")
    return await _mixed_haiku_rescue(request, orig, session, chat_fp, relay)
