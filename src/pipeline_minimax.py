# ~160 lines
"""MiniMax pipeline (orchestrate/think/act) extracted from ai-router-proxy.py (~lines 2798-2994)."""
import json

from router_constants import (
    MINIMAX_MODEL, MINIMAX_ORCHESTRATOR_MODEL, MINIMAX_EXECUTORS,
    MINIMAX_CONTEXT_BYTE_LIMIT,
)
from router_utils import log
import debug_catalog

# Dead code rimosso 2026-07-22 (audit): 5 funzioni THINK/ACT legacy MiniMax
# (_build_minimax_think_body, _pick_minimax_executor, _build_minimax_act_body,
# _build_minimax_act_body_retry, _parse_think_json) — mai chiamate da nessuno.
# L'orchestrazione minimax pura è passthrough diretto dal redesign 2026-07-22
# (il parser cercava [PLAN] ma il prompt chiedeva JSON → piano sempre scartato).
# L'import _text_from_message serviva solo a queste funzioni.


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
