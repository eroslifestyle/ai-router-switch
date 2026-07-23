"""Adapter: collega agent_loop.run_agent_loop alle pipeline GLM reali (mix-ag/mix-gm).

agent_loop è l'unico path (flag AIROUTER_AGENT_LOOP rimosso). Le pipeline classiche
in pipeline_glm.py sono state eliminate; i comportamenti stream/non-stream sono
portati qui come helper:
  - _mix_ag_stream: streaming passthrough GLM (relay diretto, bypassa THINK/VERIFY)
  - _mix_gm_stream: streaming passthrough MiniMax (relay diretto, bypassa THINK/VERIFY)
  - run_mix_ag_via_agent_loop: ciclo non-stream mix-ag
  - run_mix_gm_via_agent_loop: ciclo non-stream mix-gm con HHEM gate e retry MiniMax
"""
import json

from router_utils import log
import debug_catalog


def _should_verify_wrapper(fp, raw):
    """Wrapper bool per should_verify (ritorna solo il bool, non la tupla)."""
    from pipeline_common import should_verify
    return should_verify(fp, raw)[0]


def _extract_act_text(raw, sse):
    """Estrae il testo dall'ACT response (SSE data: or JSON content)."""
    if sse:
        parts = []
        for line in raw.decode(errors='ignore').splitlines():
            if line.startswith('data: '):
                try:
                    obj = json.loads(line[6:])
                    if obj.get('type') == 'content_block_delta' and \
                            obj.get('delta', {}).get('type') == 'text_delta':
                        parts.append(obj['delta']['text'])
                except Exception:
                    continue
        return ''.join(parts)
    else:
        try:
            data = json.loads(raw)
            return ''.join(c.get('text', '') for c in data.get('content', [])
                           if c.get('type') == 'text')
        except Exception:
            return ''


async def _mix_ag_stream(request, body, session, chat_fp, relay):
    """Streaming passthrough mix-ag: relay diretto GLM, bypassa THINK/VERIFY.

    Fix 2026-07-22: ACT GLM relayato diretto (passthrough=True, GLM_STREAM_ACQUIRE_CAP
    + niente timeout totale) invece di bufferizzare in memoria; rescue fallback su Haiku.
    """
    import aiohttp
    import glm_backend as _glm

    orig = json.loads(body) if isinstance(body, bytes) else body
    tier = await _glm.classify_tier(body, request, session, log_fn=log)
    eff_model, _capped = _glm.apply_peak_cap(tier)
    real_model = _glm.resolve_glm_upstream_model(eff_model)
    act_body = _glm.set_body_model(body, real_model)
    log(f"mix-ag STREAM ACT: GLM {real_model} (tier={eff_model}) fp={chat_fp}")
    act_resp = await _glm.forward_glm(request, act_body, session,
                                      orig.get("model") or real_model, log_fn=log,
                                      passthrough=True, upstream_model=real_model)
    if isinstance(act_resp, aiohttp.web.Response):
        return act_resp
    if act_resp.status < 400:
        log(f"mix-ag STREAM passthrough GLM {act_resp.status} fp={chat_fp}")
        return await relay(act_resp,
                           extra_headers={"x-ai-verified": f"mix-ag-stream({real_model})"})
    log(f"mix-ag STREAM ACT fail {act_resp.status} -> rescue (user model -> Haiku) "
        f"fp={chat_fp}")
    try:
        await act_resp.release()
    except Exception:
        pass
    debug_catalog.record_event(severity="error", category="mix-ag",
                               kind="glm_act_fail", chat_fp=chat_fp,
                               code=act_resp.status)
    from pipeline_anthropic import _anthropic_rescue
    return await _anthropic_rescue(request, orig, session, chat_fp, relay)


async def _mix_gm_stream(request, body, session, chat_fp, relay):
    """Streaming passthrough mix-gm: relay diretto MiniMax, MAI Anthropic fallback.

    Fix 2026-07-22: ACT MiniMax relayato diretto invece di bufferizzare per intero;
    retry singolo, poi 502 (MAI rimbalzo su Anthropic).
    """
    from forward_minimax import forward_minimax
    from router_constants import FALLBACK_STATUSES as _FALLBACK

    orig = json.loads(body) if isinstance(body, bytes) else body
    act_resp = await forward_minimax(request, body, session)
    if act_resp.status not in _FALLBACK:
        log(f"mix-gm STREAM passthrough MiniMax {act_resp.status} fp={chat_fp}")
        return await relay(act_resp,
                           extra_headers={"x-ai-verified": "mix-gm-stream"})
    log(f"mix-gm STREAM ACT {act_resp.status} -> retry fp={chat_fp}")
    try:
        await act_resp.release()
    except Exception:
        pass
    act_resp = await forward_minimax(request, body, session)
    if act_resp.status not in _FALLBACK:
        return await relay(act_resp,
                           extra_headers={"x-ai-verified": "mix-gm-stream-retry"})
    try:
        await act_resp.release()
    except Exception:
        pass
    from aiohttp import web
    debug_catalog.record_event(severity="error", category="mix-gm",
                               kind="minimax_act_fail", chat_fp=chat_fp,
                               code=act_resp.status, detail={"stream": True})
    return web.json_response({"error": {"type": "minimax_unavailable",
        "message": "mix-gm: MiniMax ACT unavailable (stream path)"}}, status=502)


async def run_mix_ag_via_agent_loop(request, body, session, chat_fp, relay):
    """mix-ag attraverso agent_loop. Path stream -> _mix_ag_stream.

    Callback:
    - think_fn: Anthropic THINK (forward_anthropic_direct + _call_full, retry certificato)
    - act_fn:   GLM ACT (tiering + forward_glm), piano THINK in body via build_executor_body
    - rescue_fn: _anthropic_rescue (user model -> Haiku)
    - verify_fn: Anthropic VERIFY (best-effort, non blocca)
    """
    import agent_loop as al
    import mode_spec as ms

    orig = json.loads(body) if isinstance(body, bytes) else body
    want_stream = bool(orig.get("stream")) if isinstance(orig, dict) else False
    if want_stream and relay is not None:
        return await _mix_ag_stream(request, body, session, chat_fp, relay)

    from forward_anthropic import forward_anthropic_direct
    from pipeline_anthropic import (
        _call_full, _text_from_message, _build_think_body, _anthropic_rescue,
    )
    from pipeline_common import build_executor_body
    from router_constants import THINK_TIMEOUT_SEC, FALLBACK_STATUSES, THINK_MODEL
    import glm_backend as _glm

    state = {"plan": "", "real_model": None}

    async def think_fn(ctx):
        think_body = _build_think_body(orig)
        try:
            t_status, t_json = await _call_full(
                forward_anthropic_direct, request, think_body, session,
                timeout=THINK_TIMEOUT_SEC)
        except Exception as e:
            debug_catalog.record_event(severity="error", category="mix-ag",
                                       kind="think_exception", chat_fp=chat_fp,
                                       snippet=str(e))
            return (500, "")
        if not t_json or t_status in FALLBACK_STATUSES:
            debug_catalog.record_event(severity="block", category="mix-ag",
                                       kind="think_failed", chat_fp=chat_fp,
                                       code=t_status)
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
                                       kind="glm_act_fail", chat_fp=chat_fp,
                                       code=act_resp.status)
        return act_resp

    async def verify_fn(ctx, act_resp):
        act_raw = act_resp.body if isinstance(act_resp.body, (bytes, bytearray)) else b""
        orig_model = (orig.get("model") or "").strip()
        try:
            verify_body = json.dumps({
                "model": orig_model or THINK_MODEL,
                "system": "Sei un verifier AI. Rispondi SOLO con: VERIFIED, "
                          "oppure INCOERENTE: [motivo breve].",
                "messages": [{"role": "user", "content":
                    f"Piano:\n{state['plan']}\n\n"
                    f"Output:\n{act_raw.decode(errors='ignore')[:5000]}"}],
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
                                       kind="verify_exception", chat_fp=chat_fp,
                                       snippet=str(e))
        return True

    async def rescue_fn(ctx):
        return await _anthropic_rescue(request, orig, session, chat_fp, relay)

    def should_verify_fn(fp, act_output):
        # Il loop passa l'OGGETTO risposta, non i bytes: estrai il body.
        raw = getattr(act_output, "body", None)
        return _should_verify_wrapper(fp, raw if isinstance(raw, (bytes, bytearray)) else b"")

    ctx = al.LoopContext(
        spec=ms.get_mode_spec("mix-ag"), request=request, body=body, session=session,
        orig=orig, chat_fp=chat_fp, relay=relay,
        think_fn=think_fn, act_fn=act_fn, verify_fn=verify_fn, rescue_fn=rescue_fn,
        should_verify_fn=should_verify_fn,
    )
    result = await al.run_agent_loop(ctx)
    if result.payload is not None and hasattr(result.payload, "status"):
        payload = result.payload
        verified = f"mix-ag-agent_loop({state.get('real_model') or '?'})"
        if not hasattr(payload, "content"):
            if not getattr(payload, "prepared", False):
                payload.headers["x-ai-verified"] = verified
            return payload
        return await relay(payload, extra_headers={"x-ai-verified": verified})
    return await _anthropic_rescue(request, orig, session, chat_fp, relay)


async def run_mix_gm_via_agent_loop(request, body, session, chat_fp, relay):
    """mix-gm attraverso agent_loop. Path stream -> _mix_gm_stream.

    REGOLA INVIOLABILE: mix-gm NON fa MAI rescue Anthropic (spec.rescue_backend=None).
    HHEM gate sempre dopo ACT; retry MiniMax singolo su ACT fallito.
    """
    import agent_loop as al
    import mode_spec as ms

    orig = json.loads(body) if isinstance(body, bytes) else body
    want_stream = bool(orig.get("stream")) if isinstance(orig, dict) else False
    if want_stream and relay is not None:
        return await _mix_gm_stream(request, body, session, chat_fp, relay)

    from forward_minimax import forward_minimax
    from pipeline_anthropic import _is_context_exceed_400
    from pipeline_common import build_executor_body_bytes
    from router_constants import FALLBACK_STATUSES
    import glm_backend as _glm

    state = {"plan": "", "act_raw": b"", "is_sse": False,
             "eval_text": "", "warn_parts": []}

    async def think_fn(ctx):
        think_body = _glm.build_glm_think_body(orig, "")
        try:
            think_resp = await _glm.forward_glm(request, think_body, session,
                                                "glm-5.2", log_fn=log)
        except Exception as e:
            debug_catalog.record_event(severity="error", category="mix-gm",
                                       kind="think_exception", chat_fp=chat_fp,
                                       snippet=str(e))
            return (500, "")
        if think_resp.status >= 400:
            return (think_resp.status, "")
        think_raw = think_resp.body if isinstance(think_resp.body,
                                                   (bytes, bytearray)) else b""
        try:
            think_data = json.loads(think_raw)
            from pipeline_anthropic import _text_from_message
            state["plan"] = _text_from_message(think_data).strip()
        except Exception:
            state["plan"] = ""
        return (think_resp.status, state["plan"])

    async def act_fn(ctx, model, plan):
        if state["plan"]:
            act_body = build_executor_body_bytes(orig, state["plan"], executor="")
        else:
            act_body = body
        act_resp = await forward_minimax(request, act_body, session)
        if act_resp.status >= 400:
            is_ctx, _ = await _is_context_exceed_400(act_resp)
            await act_resp.release()
            debug_catalog.record_event(severity="error", category="mix-gm",
                                       kind="minimax_act_fail", chat_fp=chat_fp,
                                       code=act_resp.status,
                                       detail={"context_exceed": is_ctx})
            if is_ctx:
                retry_orig = {k: v for k, v in orig.items() if k != "thinking"}
                retry_orig["messages"] = orig.get("messages", [])[-3:]
                retry_body = json.dumps(retry_orig).encode()
            else:
                retry_body = body
            log(f"mix-gm ACT retry fp={chat_fp}")
            act_resp = await forward_minimax(request, retry_body, session)
            if act_resp.status >= 400:
                await act_resp.release()
                from aiohttp import web
                return web.json_response(
                    {"error": {"type": "minimax_unavailable",
                     "message": "mix-gm: MiniMax ACT unavailable after retry"}},
                    status=502)
        act_raw = await act_resp.read()
        await act_resp.release()
        state["act_raw"] = act_raw
        act_ct = act_resp.headers.get('Content-Type', 'application/json')
        state["is_sse"] = (act_ct.startswith('text/event-stream') or
                           act_raw.lstrip()[:6] == b'event:' or
                           act_raw.lstrip()[:5] == b'data:')
        state["eval_text"] = _extract_act_text(act_raw, state["is_sse"])

        if len(state["eval_text"]) > 300:
            try:
                import hhem_gate as _hhem
                score = await _hhem.hhem_score("mix-gm ACT", state["eval_text"][:1000])
                if score is not None and score < _hhem.HHEM_THRESHOLD:
                    log(f"mix-gm ACT HHEM score={score:.3f} -> [HHEM-WARNING] "
                        f"fp={chat_fp}")
                    debug_catalog.record_event(severity="block", category="mix-gm",
                                               kind="hhem_warning", chat_fp=chat_fp,
                                               snippet=f"score={score:.3f} < "
                                                       f"{_hhem.HHEM_THRESHOLD}")
                    state["warn_parts"].append(f'hhem={score:.3f}')
            except Exception:
                pass

        from aiohttp import web
        resp_headers = {}
        if state["warn_parts"]:
            resp_headers['x-ai-verify'] = ','.join(state["warn_parts"])
        return web.Response(
            body=act_raw, status=200,
            content_type=('text/event-stream' if state["is_sse"]
                          else 'application/json'),
            headers=resp_headers)

    async def verify_fn(ctx, act_resp):
        try:
            verify_body = _glm.build_glm_verify_body(
                orig, state["plan"], state["eval_text"][:5000])
            verify_resp = await _glm.forward_glm(request, verify_body, session,
                                                 "glm-5.2", log_fn=log)
            if verify_resp.status < 400:
                verify_raw = verify_resp.body if isinstance(
                    verify_resp.body, (bytes, bytearray)) else b""
                try:
                    verify_data = json.loads(verify_raw)
                    verify_text = (verify_data.get("content", [{}])[0].get("text", "")
                                   if verify_data.get("content") else "")
                    log(f"mix-gm VERIFY: {verify_text[:100]} fp={chat_fp}")
                    if "INCOERENTE" in verify_text:
                        state["warn_parts"].append('verify=incoherent')
                        debug_catalog.record_event(
                            severity="block", category="mix-gm",
                            kind="verify_incoherent", chat_fp=chat_fp,
                            snippet=verify_text[:80])
                        act_resp.headers['x-ai-verify'] = ','.join(
                            state["warn_parts"])
                except Exception:
                    pass
        except Exception as e:
            log(f"mix-gm VERIFY EXC: {e}")
            debug_catalog.record_event(severity="block", category="mix-gm",
                                       kind="verify_exception", chat_fp=chat_fp,
                                       snippet=str(e))
        return True

    def should_verify_fn(fp, act_output):
        if state["is_sse"]:
            blocks = [{"type": "text", "text": state["eval_text"]}]
            if b'"tool_use"' in state["act_raw"]:
                blocks.append({"type": "tool_use"})
            gate_raw = json.dumps({"type": "message", "content": blocks}).encode()
        else:
            gate_raw = state["act_raw"]
        return _should_verify_wrapper(fp, gate_raw)

    ctx = al.LoopContext(
        spec=ms.get_mode_spec("mix-gm"), request=request, body=body, session=session,
        orig=orig, chat_fp=chat_fp, relay=relay,
        think_fn=think_fn, act_fn=act_fn, verify_fn=verify_fn,
        rescue_fn=None,
        should_verify_fn=should_verify_fn,
    )
    result = await al.run_agent_loop(ctx)
    if result.payload is not None and hasattr(result.payload, "status"):
        if result.payload.status in FALLBACK_STATUSES:
            try:
                await result.payload.release()
            except Exception:
                pass
            from aiohttp import web
            return web.json_response(
                {"error": {"type": "minimax_unavailable",
                 "message": "mix-gm: MiniMax ACT unavailable (agent_loop)"}},
                status=502)
        payload = result.payload
        if not hasattr(payload, "content"):
            if not getattr(payload, "prepared", False):
                payload.headers["x-ai-verified"] = "mix-gm-agent_loop"
            return payload
        return await relay(payload,
                           extra_headers={"x-ai-verified": "mix-gm-agent_loop"})
    from aiohttp import web
    return web.json_response(
        {"error": {"type": "minimax_unavailable",
         "message": "mix-gm: no payload (agent_loop)"}}, status=502)
