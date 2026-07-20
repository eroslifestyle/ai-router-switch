# ~160 lines
"""MiniMax pipeline (orchestrate/think/act) extracted from ai-router-proxy.py (~lines 2798-2994)."""
import json

from router_constants import (
    MINIMAX_MODEL, MINIMAX_ORCHESTRATOR_MODEL, MINIMAX_EXECUTORS,
    MINIMAX_CONTEXT_BYTE_LIMIT,
)
from router_utils import log, _request_orig_model
import debug_catalog


def _build_minimax_think_body(orig: dict) -> bytes:
    """M3 orchestra e produce SOLO JSON con piano + executor scelto."""
    executors = ", ".join(sorted(MINIMAX_EXECUTORS)) or MINIMAX_MODEL
    sys_msg = (
        "Sei M3, il META-ORCHESTRATORE. Il tuo compito è PIANIFICARE, non eseguire. "
        "Ricevi una richiesta utente (con possibili tools), produci un PIANO ragionato, "
        "scegli quali tool chiamare e QUALE modello esecutore inferiore deve eseguire. "
        "Rispondi SOLO con JSON valido, nessun testo fuori.\n\n"
        'Schema esatto:\n'
        '{"plan": "<ragionamento in italiano, max 800 char>",'
        ' "tools_to_call": [{"name": "<tool_name>", "input": <object>}, ...],'
        f' "executor_model": "<uno tra: {executors}>",'
        ' "self_review_ok": <bool>,'
        ' "self_review_notes": ["<criticita risolta>", ...]}\n\n'
        "Regole: scegli executor_model in base al task (coding pesante -> il piu' capace). "
        "Fai auto-review del piano PRIMA di emettere JSON; se resta incoerente metti "
        "self_review_ok=false e tools_to_call=[]. Tu NON esegui mai: solo pianifichi."
    )
    body = dict(orig)
    body["model"] = MINIMAX_ORCHESTRATOR_MODEL
    body["system"] = sys_msg
    body["stream"] = False
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _pick_minimax_executor(plan_json: dict) -> str:
    """Executor scelto da M3, validato contro la whitelist dei modelli inferiori."""
    em = (plan_json.get("executor_model") or "").strip()
    if em in MINIMAX_EXECUTORS and em != MINIMAX_ORCHESTRATOR_MODEL:
        return em
    return MINIMAX_MODEL


def _build_minimax_act_body(orig: dict, plan: str, tools_to_call: list, executor: str) -> bytes:
    """L'executor inferiore esegue il piano prodotto da M3.

    Fix 2026-07-20: costruzione UNIFICATA via pipeline_common — preserva il system
    originale (istruzioni skill, CLAUDE.md) invece di sovrascriverlo. Prima
    body['system']=sys_msg distruggeva la disciplina del task → premature termination."""
    from pipeline_common import build_executor_body_bytes
    note = ""
    if tools_to_call:
        note = f"TOOLS suggeriti da M3: {json.dumps(tools_to_call, ensure_ascii=False)}"
    return build_executor_body_bytes(orig, plan, executor, extra_note=note)


def _build_minimax_act_body_retry(orig: dict, correction: str) -> bytes:
    """Re-esegue l'ACT con nota correttiva iniettata nel system message."""
    body = dict(orig)
    orig_sys = orig.get("system", "")
    body["system"] = orig_sys + f"\n\n[NOTA CORRETTIVA dal verifier]\n{correction}\n[/NOTA CORRETTIVA]" \
        if orig_sys else f"[NOTA CORRETTIVA dal verifier]\n{correction}\n[/NOTA CORRETTIVA]"
    return json.dumps(body).encode()


def _parse_think_json(text: str) -> dict | None:
    """Parsa l'output della fase THINK con formato [PLAN]...[/PLAN]."""
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

    return {
        "plan": plan,
        "tools_to_call": tools,
        "self_review_ok": self_review_ok,
        "self_review_notes": self_review_notes,
    }


async def _pipeline_minimax_orchestrate(request, body, session, orig: dict, relay):
    """mode=minimax redesign: M3 THINK/orchestra -> executor inferiore ACT."""
    # Lazy import to avoid circular dependencies
    from router_mode import _resolve_chat_fingerprint
    from forward_minimax import forward_minimax, _fwd_minimax_short
    from pipeline_anthropic import (
        _call_full, _text_from_message, _is_context_too_large_for_minimax,
        _is_context_exceed_400, _has_web_search_tool, _web_search_blocked_response,
        _has_server_tools, _body_has_images, _serve_minimax_vision,
        _shrink_and_retry_minimax,
    )
    from router_utils import _repair_message_sequence

    chat_fp = _resolve_chat_fingerprint(request)

    if _is_context_too_large_for_minimax(body):
        shrunk = await _try_shrink_body(orig, MINIMAX_CONTEXT_BYTE_LIMIT)
        if shrunk is not None and shrunk != body:
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
        if shrunk is None:
            return web.json_response(
                {"type": "error", "error": {"type": "context_exceeded",
                 "message": f"body {len(body)}b > limit e shrink non riesce."}},
                status=400)

    if _has_web_search_tool(orig):
        return _web_search_blocked_response()

    async def _executor_direct():
        return await relay(
            await forward_minimax(request, body, session),
            extra_headers={"x-ai-verified": f"minimax-direct-fallback({MINIMAX_MODEL.lower()})"}
        )

    if _body_has_images(orig):
        res = await _serve_minimax_vision(request, orig, session, chat_fp, relay)
        if res is not None:
            return res
        return await _executor_direct()

    think_body = _build_minimax_think_body(orig)
    try:
        t_status, t_json = await _call_full(_fwd_minimax_short, request, think_body, session)
    except Exception as e:
        log(f"minimax-orch THINK EXC: {e} -> executor diretto")
        debug_catalog.record_event(severity="error", category="minimax",
                                    kind="think_exception", chat_fp=chat_fp, snippet=str(e))
        return await _executor_direct()

    if not t_json or t_status in {500, 502, 503, 504, 529}:
        log(f"minimax-orch THINK ko {t_status} -> executor diretto")
        debug_catalog.record_event(severity="error", category="minimax",
                                    kind="think_failed", chat_fp=chat_fp, code=t_status)
        return await _executor_direct()

    plan_json = _parse_think_json(_text_from_message(t_json))
    if not plan_json or not plan_json.get("self_review_ok", False):
        log(f"minimax-orch THINK: piano non valido -> executor diretto")
        debug_catalog.record_event(severity="block", category="minimax",
                                    kind="think_plan_invalid", chat_fp=chat_fp)
        return await _executor_direct()

    plan = plan_json.get("plan", "")
    tools_to_call = plan_json.get("tools_to_call", []) or []
    executor = _pick_minimax_executor(plan_json)
    log(f"minimax-orch THINK OK plan={len(plan)}c tools={len(tools_to_call)} executor={executor} fp={chat_fp}")

    act_body = _build_minimax_act_body(orig, plan, tools_to_call, executor)
    try:
        up = await forward_minimax(request, act_body, session)
    except Exception as e:
        log(f"minimax-orch ACT EXC: {e} -> executor diretto")
        debug_catalog.record_event(severity="error", category="minimax",
                                    kind="act_exception", chat_fp=chat_fp, snippet=str(e))
        return await _executor_direct()

    if up.status == 400:
        is_ctx_pre = up.headers.get("x-ai-context-exceeded") == "true" if hasattr(up, "headers") else False
        is_ctx_real, _ = await _is_context_exceed_400(up)
        if is_ctx_pre or is_ctx_real:
            log(f"minimax-orch ACT 400 context-exceed -> shrink/retry fp={chat_fp}")
            try:
                await up.release()
            except Exception:
                pass
            return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay,
                                                   allow_anthropic_rescue=False)
        try:
            await up.release()
        except Exception:
            pass

    if up.status in {401, 403, 408, 409, 413, 429, 500, 502, 503, 504, 529}:
        log(f"minimax-orch ACT {up.status} -> executor diretto")
        debug_catalog.record_event(severity="error", category="minimax",
                                    kind="act_fail", chat_fp=chat_fp, code=up.status)
        try:
            await up.release()
        except Exception:
            pass
        return await _executor_direct()

    log(f"minimax-orch ACT {executor} {up.status} {request.path} fp={chat_fp}")

    # VERIFY: M3 verifica output ACT
    log(f"minimax-orch VERIFY: M3 verifica fp={chat_fp}")
    try:
        act_raw = await up.read()
        await up.release()
        verify_messages = [
            {"role": "system", "content": "Sei un verifier AI. Verifica che l'output sia corretto e completo."},
            {"role": "user", "content": f"Piano:\n{plan}\n\nOutput:\n{act_raw.decode(errors='ignore')[:5000]}"},
        ]
        verify_body = dict(orig)
        verify_body["model"] = MINIMAX_ORCHESTRATOR_MODEL
        verify_body["messages"] = verify_messages
        verify_body["max_tokens"] = 500
        verify_body["stream"] = False
        v_status, v_json = await _call_full(_fwd_minimax_short, request, json.dumps(verify_body).encode(), session)
        if v_status < 400 and v_json:
            verify_text = _text_from_message(v_json).strip()
            log(f"minimax-orch VERIFY: {verify_text[:100]} fp={chat_fp}")
    except Exception as e:
        log(f"minimax-orch VERIFY EXC: {e} fp={chat_fp}")
        debug_catalog.record_event(severity="block", category="minimax",
                                    kind="verify_exception", chat_fp=chat_fp, snippet=str(e))

    return web.Response(body=act_raw, status=200, content_type="application/json")


async def _try_shrink_body(orig: dict, target_bytes: int):
    """Prova a shrinkare il body per farlo stare in target_bytes."""
    from pipeline_anthropic import _repair_message_sequence, build_shrink_summary, SHRINK_KEEP_TAIL
    try:
        msgs = orig.get("messages", []) or []
        if not msgs:
            return None
        budget = 560_000  # SUMMARY_BUDGET equivalent
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
        shrunk["messages"] = tail_msgs
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
        log(f"try_shrink_body EXC: {e}")
        return None
