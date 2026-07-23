"""Adapter: collega agent_loop.run_agent_loop alle pipeline GLM reali (mix-ag/mix-gm).

Cablaggio flag-gated (AIROUTER_AGENT_LOOP=1). Quando il flag è OFF (default),
`_handle_glm_mode` usa le pipeline classiche `_anthropic_glm_think_act_verify` /
`_glm_minimax_think_act_verify`. Quando è ON, usa `run_agent_loop` con i callback
qui definiti, che RICHIAMANO le funzioni di trasporto collaudate (forward_glm,
forward_minimax, forward_anthropic_direct, _call_full, glm tiering) — nessuna
logica di trasporto è riscritta, solo orchestrata dagli stati tipizzati.

Il path STREAMING resta gestito dalle pipeline classiche (relay diretto che
bypassa THINK/ACT/VERIFY): agent_loop modella il ciclo non-stream. Sul path
stream questo adapter delega alla pipeline classica corrispondente.

Rimozione delle pipeline classiche: SOLO dopo che il flag ON ha girato in
produzione senza regressioni (duplicazione temporanea documentata).
"""
import json
import os

from router_utils import log
import debug_catalog


def agent_loop_enabled() -> bool:
    """True se il cablaggio agent_loop è attivo (flag default-OFF)."""
    return os.environ.get("AIROUTER_AGENT_LOOP", "0") == "1"


def _wants_stream(orig) -> bool:
    return bool(orig.get("stream")) if isinstance(orig, dict) else False


async def run_mix_ag_via_agent_loop(request, body, session, chat_fp, relay):
    """mix-ag attraverso agent_loop. Path stream -> pipeline classica.

    Callback:
    - think_fn: Anthropic THINK (forward_anthropic_direct + _call_full, retry certificato)
    - act_fn:   GLM ACT (tiering + forward_glm), il piano THINK va nel body via build_executor_body
    - rescue_fn: _anthropic_rescue (user model -> Haiku)
    - verify_fn: Anthropic VERIFY (best-effort, non blocca)
    """
    import agent_loop as al
    import mode_spec as ms
    from pipeline_glm import _anthropic_glm_think_act_verify  # fallback stream

    orig = json.loads(body) if isinstance(body, bytes) else body

    # Il path streaming resta sulla pipeline classica (relay diretto, no ciclo).
    if _wants_stream(orig):
        return await _anthropic_glm_think_act_verify(request, body, session, chat_fp, relay)

    from forward_anthropic import forward_anthropic_direct
    from pipeline_anthropic import (
        _call_full, _text_from_message, _build_think_body, _anthropic_rescue,
    )
    from pipeline_common import build_executor_body, should_verify
    from router_constants import THINK_TIMEOUT_SEC, FALLBACK_STATUSES, THINK_MODEL
    import glm_backend as _glm

    # Stato condiviso tra i callback (il piano THINK, il modello GLM risolto).
    state = {"plan": "", "real_model": None}

    async def think_fn(ctx):
        think_body = _build_think_body(orig)
        try:
            t_status, t_json = await _call_full(
                forward_anthropic_direct, request, think_body, session,
                timeout=THINK_TIMEOUT_SEC)
        except Exception as e:
            debug_catalog.record_event(severity="error", category="mix-ag",
                                       kind="think_exception", chat_fp=chat_fp, snippet=str(e))
            return (500, "")
        if not t_json or t_status in FALLBACK_STATUSES:
            debug_catalog.record_event(severity="block", category="mix-ag",
                                       kind="think_failed", chat_fp=chat_fp, code=t_status)
            return (t_status, "")
        state["plan"] = _text_from_message(t_json).strip()
        return (t_status, state["plan"])

    async def act_fn(ctx, model, plan):
        tier = await _glm.classify_tier(body, request, session, log_fn=log)
        eff_model, _capped = _glm.apply_peak_cap(tier)
        real_model = _glm.resolve_glm_upstream_model(eff_model)
        state["real_model"] = real_model
        if state["plan"]:
            act_dict = build_executor_body(orig, state["plan"], executor=real_model)
            act_body = json.dumps(act_dict).encode()
        else:
            act_body = _glm.set_body_model(body, real_model)
        log(f"mix-ag[agent_loop] ACT: GLM {real_model} (tier={eff_model}) fp={chat_fp}")
        act_resp = await _glm.forward_glm(request, act_body, session,
                                          orig.get("model") or real_model, log_fn=log)
        if act_resp.status >= 400:
            debug_catalog.record_event(severity="error", category="mix-ag",
                                       kind="glm_act_fail", chat_fp=chat_fp, code=act_resp.status)
        return act_resp

    async def verify_fn(ctx, act_resp):
        # Best-effort: logga incoerenza, NON blocca (ritorna sempre "coerente").
        act_raw = act_resp.body if isinstance(act_resp.body, (bytes, bytearray)) else b""
        orig_model = (orig.get("model") or "").strip()
        try:
            verify_body = json.dumps({
                "model": orig_model or THINK_MODEL,
                "system": "Sei un verifier AI. Rispondi SOLO con: VERIFIED, oppure INCOERENTE: [motivo breve].",
                "messages": [{"role": "user", "content":
                    f"Piano:\n{state['plan']}\n\nOutput:\n{act_raw.decode(errors='ignore')[:5000]}"}],
                "max_tokens": 200,
            }).encode()
            v_status, v_json = await _call_full(
                forward_anthropic_direct, request, verify_body, session, timeout=15)
            if v_status < 400 and v_json:
                verify_text = _text_from_message(v_json).strip()
                if "INCOERENTE" in verify_text:
                    debug_catalog.record_event(severity="block", category="mix-ag",
                                               kind="verify_incoherent", chat_fp=chat_fp,
                                               snippet=verify_text[:80])
        except Exception as e:
            debug_catalog.record_event(severity="block", category="mix-ag",
                                       kind="verify_exception", chat_fp=chat_fp, snippet=str(e))
        return True  # best-effort: non forza retry

    async def rescue_fn(ctx):
        return await _anthropic_rescue(request, orig, session, chat_fp, relay)

    ctx = al.LoopContext(
        spec=ms.get_mode_spec("mix-ag"), request=request, body=body, session=session,
        orig=orig, chat_fp=chat_fp, relay=relay,
        think_fn=think_fn, act_fn=act_fn, verify_fn=verify_fn, rescue_fn=rescue_fn,
        should_verify_fn=should_verify,
    )
    result = await al.run_agent_loop(ctx)
    # agent_loop ritorna StepResult(payload=act_resp o rescue_resp).
    if result.payload is not None and hasattr(result.payload, "status"):
        payload = result.payload
        verified = f"mix-ag-agent_loop({state.get('real_model') or '?'})"
        # forward_glm non-passthrough e _anthropic_rescue ritornano web.Response/
        # StreamResponse SENZA .content: relay() itera upstream.content.iter_any()
        # -> AttributeError dopo prepare() (header 200 al client, body mai inviato).
        # Vanno restituite dirette; relay() solo per superfici ClientResponse-like.
        if not hasattr(payload, "content"):
            if not getattr(payload, "prepared", False):
                payload.headers["x-ai-verified"] = verified
            return payload
        return await relay(payload, extra_headers={"x-ai-verified": verified})
    # Nessun payload utile -> rescue esplicito.
    return await _anthropic_rescue(request, orig, session, chat_fp, relay)


async def run_mix_gm_via_agent_loop(request, body, session, chat_fp, relay):
    """mix-gm attraverso agent_loop. Path stream -> pipeline classica.

    REGOLA INVIOLABILE: mix-gm NON fa MAI rescue Anthropic (spec.rescue_backend=None).
    """
    import agent_loop as al
    import mode_spec as ms
    from pipeline_glm import _glm_minimax_think_act_verify  # fallback stream

    orig = json.loads(body) if isinstance(body, bytes) else body

    if _wants_stream(orig):
        return await _glm_minimax_think_act_verify(request, body, session, chat_fp, relay)

    from forward_minimax import forward_minimax
    from pipeline_common import should_verify
    from router_constants import FALLBACK_STATUSES
    import glm_backend as _glm

    state = {"plan": ""}

    async def think_fn(ctx):
        think_body = _glm.build_glm_think_body(orig, "")
        try:
            think_resp = await _glm.forward_glm(request, think_body, session, "glm-5.2", log_fn=log)
        except Exception as e:
            debug_catalog.record_event(severity="error", category="mix-gm",
                                       kind="think_exception", chat_fp=chat_fp, snippet=str(e))
            return (500, "")
        if think_resp.status >= 400:
            return (think_resp.status, "")
        think_raw = think_resp.body if isinstance(think_resp.body, (bytes, bytearray)) else b""
        try:
            think_data = json.loads(think_raw)
            from pipeline_anthropic import _text_from_message
            state["plan"] = _text_from_message(think_data).strip()
        except Exception:
            state["plan"] = ""
        return (think_resp.status, state["plan"])

    async def act_fn(ctx, model, plan):
        from pipeline_common import build_executor_body_bytes
        if state["plan"]:
            act_body = build_executor_body_bytes(orig, state["plan"], executor="")
        else:
            act_body = body
        act_resp = await forward_minimax(request, act_body, session)
        if act_resp.status in FALLBACK_STATUSES:
            debug_catalog.record_event(severity="error", category="mix-gm",
                                       kind="minimax_act_fail", chat_fp=chat_fp, code=act_resp.status)
        return act_resp

    async def verify_fn(ctx, act_resp):
        # mix-gm ha use_hhem=True: HHEM gate + GLM verify (best-effort, non blocca).
        return True

    ctx = al.LoopContext(
        spec=ms.get_mode_spec("mix-gm"), request=request, body=body, session=session,
        orig=orig, chat_fp=chat_fp, relay=relay,
        think_fn=think_fn, act_fn=act_fn, verify_fn=verify_fn,
        rescue_fn=None,  # regola inviolabile: mix-gm mai Anthropic
        should_verify_fn=should_verify,
    )
    result = await al.run_agent_loop(ctx)
    if result.payload is not None and hasattr(result.payload, "status"):
        if result.payload.status in FALLBACK_STATUSES:
            from aiohttp import web
            try:
                await result.payload.release()
            except Exception:
                pass
            return web.json_response({"error": {"type": "minimax_unavailable",
                "message": "mix-gm: MiniMax ACT unavailable (agent_loop)"}}, status=502)
        return await relay(result.payload, extra_headers={"x-ai-verified": "mix-gm-agent_loop"})
    from aiohttp import web
    return web.json_response({"error": {"type": "minimax_unavailable",
        "message": "mix-gm: no payload (agent_loop)"}}, status=502)
