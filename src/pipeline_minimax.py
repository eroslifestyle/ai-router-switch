# ~160 lines
"""MiniMax pipeline (orchestrate/think/act) extracted from ai-router-proxy.py (~lines 2798-2994)."""
import json

from router_constants import (
    MINIMAX_MODEL, MINIMAX_ORCHESTRATOR_MODEL, MINIMAX_EXECUTORS,
    MINIMAX_CONTEXT_BYTE_LIMIT,
)
from router_utils import log
import debug_catalog
from pipeline_anthropic import _text_from_message  # usato dalle funzioni legacy THINK/VERIFY


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
    """mode=minimax: passthrough streaming diretto a MiniMax-M3.

    Redesign 2026-07-22 (perf/latenza): rimossa l'orchestrazione THINK/ACT/VERIFY.
    Root cause verificata dai log (giorni di "THINK: piano non valido -> executor
    diretto" al 100% dei turni): il prompt THINK chiedeva JSON mentre il parser
    _parse_think_json cercava tag [PLAN]...[/PLAN] — formati incompatibili, piano
    SEMPRE scartato. La pipeline non ha MAI orchestrato in produzione: THINK+VERIFY
    erano solo 3-8s di latenza morta prima di ogni risposta (baseline TTFB 3.8-8.4s),
    con VERIFY non-gated + up.read() bufferizzante che impediva lo streaming.

    Rimuovere l'orchestrazione morta NON cambia il comportamento osservabile
    (l'output veniva già sempre da forward_minimax diretto), elimina solo la latenza.
    Restano intatti i guard che servono davvero: context-too-large + shrink,
    immagini (vision), web-search block. Lo stream MiniMax passa al client via relay()
    senza bufferizzazione (primo byte non appena MiniMax risponde)."""
    # Lazy import to avoid circular dependencies
    from router_mode import _resolve_chat_fingerprint
    from forward_minimax import forward_minimax
    from pipeline_anthropic import (
        _is_context_too_large_for_minimax,
        _has_web_search_tool, _web_search_blocked_response,
        _body_has_images, _serve_minimax_vision,
    )

    chat_fp = _resolve_chat_fingerprint(request)

    if _is_context_too_large_for_minimax(body):
        shrunk = await _try_shrink_body(orig, MINIMAX_CONTEXT_BYTE_LIMIT)
        if shrunk is not None and shrunk != body:
            try:
                up_pre = await forward_minimax(request, shrunk, session)
                if up_pre.status < 400:
                    log(f"minimax PRE shrunk OK {up_pre.status} fp={chat_fp}")
                    return await relay(up_pre, extra_headers={"x-ai-verified": "minimax-m3-shrunk"})
                try:
                    await up_pre.release()
                except Exception:
                    pass
            except Exception as e:
                log(f"minimax PRE shrunk EXC: {e}")
        if shrunk is None:
            return web.json_response(
                {"type": "error", "error": {"type": "context_exceeded",
                 "message": f"body {len(body)}b > limit e shrink non riesce."}},
                status=400)

    if _has_web_search_tool(orig):
        return _web_search_blocked_response()

    if _body_has_images(orig):
        res = await _serve_minimax_vision(request, orig, session, chat_fp, relay)
        if res is not None:
            return res

    # Passthrough streaming diretto: primo byte appena MiniMax risponde, zero overhead.
    try:
        up = await forward_minimax(request, body, session)
    except Exception as e:
        log(f"minimax passthrough EXC: {e} fp={chat_fp}")
        debug_catalog.record_event(severity="error", category="minimax",
                                    kind="forward_exception", chat_fp=chat_fp, snippet=str(e))
        return web.json_response({"type": "error", "error": {"type": "router_error",
                                  "message": str(e)}}, status=502)
    log(f"minimax passthrough {up.status} {request.path} fp={chat_fp}")
    return await relay(up, extra_headers={"x-ai-verified": f"minimax-direct({MINIMAX_MODEL.lower()})"})


async def _try_shrink_body(orig: dict, target_bytes: int):
    """Prova a shrinkare il body per farlo stare in target_bytes."""
    from pipeline_anthropic import _repair_message_sequence, build_shrink_summary, SHRINK_KEEP_TAIL, _shrink_images_in_messages
    try:
        msgs = orig.get("messages", []) or []
        if not msgs:
            return None
        _shrink_images_in_messages(orig)
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
