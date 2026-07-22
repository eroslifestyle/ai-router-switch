# ~170 lines
"""GLM pipeline extracted from ai-router-proxy.py (~lines 3024-3292)."""
import json

from router_constants import (
    GLM_AVAILABLE, FALLBACK_STATUSES, THINK_TIMEOUT_SEC,
    MINIMAX_FALLBACK_STATUSES,
)
from router_utils import log
import debug_catalog


async def _anthropic_glm_think_act_verify(request, body: bytes, session, chat_fp: str, relay):
    """mix-ag: Anthropic THINK -> GLM ACT -> Anthropic VERIFY."""
    from forward_anthropic import forward_anthropic_direct
    from pipeline_anthropic import _call_full, _text_from_message, _build_think_body, _anthropic_rescue
    import glm_backend as _glm
    import aiohttp
    orig = json.loads(body) if isinstance(body, bytes) else body

    # STREAMING PASSTHROUGH (fix 2026-07-22): il client chiede stream. Prima
    # l'ACT GLM veniva bufferizzato (forward_glm ritorna il body in memoria) e
    # senza timeout stretto → su GLM lento la richiesta restava appesa 77s+ e
    # tornava 502. Ora: ACT GLM relayato diretto (forward_glm passthrough=True,
    # GLM_STREAM_ACQUIRE_CAP + niente timeout totale), THINK/VERIFY Anthropic
    # skippati sul path stream. Rescue: user model -> Haiku, invariato.
    # (stesso pattern del glm puro `5f6c9f5`).
    want_stream = bool(orig.get("stream")) if isinstance(orig, dict) else False
    if want_stream and relay is not None:
        tier = await _glm.classify_tier(body, request, session, log_fn=log)
        eff_model, _capped = _glm.apply_peak_cap(tier)
        real_model = _glm.resolve_glm_upstream_model(eff_model)
        act_body = _glm.set_body_model(body, real_model)
        log(f"mix-ag STREAM ACT: GLM {real_model} (tier={eff_model}) fp={chat_fp}")
        act_resp = await _glm.forward_glm(request, act_body, session,
                                          orig.get("model") or real_model, log_fn=log,
                                          passthrough=True, upstream_model=real_model)
        if isinstance(act_resp, aiohttp.web.Response):
            return act_resp  # errore sintetico (key missing / 429 finale)
        if act_resp.status < 400:
            log(f"mix-ag STREAM passthrough GLM {act_resp.status} fp={chat_fp}")
            return await relay(act_resp, extra_headers={"x-ai-verified": f"mix-ag-stream({real_model})"})
        log(f"mix-ag STREAM ACT fail {act_resp.status} -> rescue (user model -> Haiku) fp={chat_fp}")
        try:
            await act_resp.release()
        except Exception:
            pass
        debug_catalog.record_event(severity="error", category="mix-ag",
                                    kind="glm_act_fail", chat_fp=chat_fp, code=act_resp.status)
        return await _anthropic_rescue(request, orig, session, chat_fp, relay)

    think_body = _build_think_body(orig)
    try:
        # Leg Anthropic THINK: _call_full ritenta i 429/5xx transienti col backoff
        # certificato SDK (retry_transient default on, riuso pipeline_common) invece
        # di trattare il 429 come plan vuoto -> no rimbalzo/loop lato client (2026-07-22).
        t_status, t_json = await _call_full(forward_anthropic_direct, request, think_body, session, timeout=THINK_TIMEOUT_SEC)
    except Exception as e:
        log(f"mix-ag THINK EXC: {e}")
        debug_catalog.record_event(severity="error", category="mix-ag",
                                    kind="think_exception", chat_fp=chat_fp, snippet=str(e))
        think_plan = ""
    else:
        if not t_json or t_status in FALLBACK_STATUSES:
            debug_catalog.record_event(severity="block", category="mix-ag",
                                        kind="think_failed", chat_fp=chat_fp, code=t_status)
            think_plan = ""
        else:
            think_plan = _text_from_message(t_json).strip()
    log(f"mix-ag THINK: plan={len(think_plan)}c fp={chat_fp}")

    # GLM ACT
    import glm_backend as _glm
    tier = await _glm.classify_tier(body, request, session, log_fn=log)
    eff_model, capped = _glm.apply_peak_cap(tier)
    real_model = _glm.resolve_glm_upstream_model(eff_model)
    # Fix 2026-07-20: il piano THINK ora arriva all'ACT GLM (prima set_body_model(body)
    # usava il body grezzo → piano buttato). build_executor_body preserva system+
    # messages+tools e appende piano + completion guard.
    from pipeline_common import build_executor_body
    if think_plan:
        act_dict = build_executor_body(orig, think_plan, executor=real_model)
        act_body = json.dumps(act_dict).encode()
    else:
        act_body = _glm.set_body_model(body, real_model)
    log(f"mix-ag ACT: GLM {real_model} (tier={eff_model}) fp={chat_fp}")
    act_resp = await _glm.forward_glm(request, act_body, session,
                                       orig.get("model") or real_model, log_fn=log)
    if act_resp.status >= 400:
        log(f"mix-ag ACT fail {act_resp.status} -> rescue (user model -> Haiku)")
        debug_catalog.record_event(severity="error", category="mix-ag",
                                    kind="glm_act_fail", chat_fp=chat_fp, code=act_resp.status)
        return await _anthropic_rescue(request, orig, session, chat_fp, relay)
    act_raw = act_resp.body if isinstance(act_resp.body, (bytes, bytearray)) else b""
    # VERIFY a campione (fix 2026-07-21): non su ogni turno, MAI retry ACT.
    from pipeline_common import should_verify
    from router_constants import THINK_MODEL
    do_verify, v_reason = should_verify(chat_fp, act_raw)
    if not do_verify:
        log(f"mix-ag VERIFY skip (gate) fp={chat_fp}")
        return act_resp
    log(f"mix-ag VERIFY ({v_reason}): Haiku fp={chat_fp}")
    try:
        verify_body = json.dumps({
            "model": THINK_MODEL,
            "system": "Sei un verifier AI. Rispondi SOLO con: VERIFIED, oppure INCOERENTE: [motivo breve].",
            "messages": [{"role": "user", "content":
                f"Piano:\n{think_plan}\n\nOutput:\n{act_raw.decode(errors='ignore')[:5000]}"}],
            "max_tokens": 200,
        }).encode()
        # Leg Anthropic VERIFY: stesso retry certificato via _call_full (2026-07-22).
        v_status, v_json = await _call_full(forward_anthropic_direct, request, verify_body, session, timeout=15)
        if v_status < 400 and v_json:
            verify_text = _text_from_message(v_json).strip()
            log(f"mix-ag VERIFY: {verify_text[:100]}")
            if "INCOERENTE" in verify_text:
                debug_catalog.record_event(severity="block", category="mix-ag",
                                            kind="verify_incoherent", chat_fp=chat_fp,
                                            snippet=verify_text[:80])
    except Exception as e:
        log(f"mix-ag VERIFY EXC: {e}")
        debug_catalog.record_event(severity="block", category="mix-ag",
                                    kind="verify_exception", chat_fp=chat_fp, snippet=str(e))
    return act_resp


async def _glm_minimax_think_act_verify(request, body: bytes, session, chat_fp: str, relay):
    """mix-gm: GLM-5.2 THINK -> MiniMax ACT -> GLM-5.2 VERIFY."""
    from forward_minimax import forward_minimax
    from pipeline_anthropic import _call_full, _is_context_exceed_400
    from router_constants import FALLBACK_STATUSES as _FALLBACK
    import glm_backend as _glm
    orig = json.loads(body) if isinstance(body, bytes) else body

    # STREAMING PASSTHROUGH (fix 2026-07-22): il client (Claude Code) chiede
    # stream. Prima l'ACT MiniMax veniva bufferizzato per intero con
    # `act_resp.read()` + ricostruzione web.Response non-stream → primo byte al
    # client = fine generazione (13s+), e il client stream-atteso andava in
    # timeout totale (HTTP 000). Ora: ACT MiniMax relayato diretto, THINK/VERIFY
    # GLM skippati sul path stream (stesso pattern del glm puro `5f6c9f5` e del
    # minimax puro `539456e`). THINK/VERIFY restano solo sul path non-stream.
    want_stream = bool(orig.get("stream")) if isinstance(orig, dict) else False
    if want_stream and relay is not None:
        act_resp = await forward_minimax(request, body, session)
        if act_resp.status not in _FALLBACK:
            log(f"mix-gm STREAM passthrough MiniMax {act_resp.status} fp={chat_fp}")
            return await relay(act_resp, extra_headers={"x-ai-verified": "mix-gm-stream"})
        # ACT KO sul path stream: retry singolo, poi errore (MAI Anthropic).
        log(f"mix-gm STREAM ACT {act_resp.status} -> retry fp={chat_fp}")
        try:
            await act_resp.release()
        except Exception:
            pass
        act_resp = await forward_minimax(request, body, session)
        if act_resp.status not in _FALLBACK:
            return await relay(act_resp, extra_headers={"x-ai-verified": "mix-gm-stream-retry"})
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

    think_body = _glm.build_glm_think_body(orig, "")
    try:
        think_resp = await _glm.forward_glm(request, think_body, session, "glm-5.2", log_fn=log)
        if think_resp.status >= 400:
            think_plan = ""
        else:
            think_raw = think_resp.body if isinstance(think_resp.body, (bytes, bytearray)) else b""
            try:
                think_data = json.loads(think_raw)
                think_plan = think_data.get("content", [{}])[0].get("text", "") if think_data.get("content") else ""
            except Exception:
                think_plan = ""
    except Exception as e:
        log(f"glm-minimax THINK EXC: {e}")
        debug_catalog.record_event(severity="error", category="mix-gm",
                                    kind="think_exception", chat_fp=chat_fp, snippet=str(e))
        think_plan = ""
    log(f"mix-gm THINK: plan={len(think_plan)}c fp={chat_fp}")

    # Fix 2026-07-20: il piano THINK ora arriva DAVVERO all'ACT (prima era buttato:
    # forward_minimax(body) grezzo). build_executor_body preserva system+messages+tools
    # e appende piano + completion guard → l'esecutore MiniMax completa i task multi-step.
    from pipeline_common import build_executor_body_bytes
    if think_plan:
        act_body_gm = build_executor_body_bytes(orig, think_plan, executor="")
    else:
        act_body_gm = body
    log(f"mix-gm ACT: MiniMax fp={chat_fp}")
    act_resp = await forward_minimax(request, act_body_gm, session)
    if act_resp.status >= 400:
        is_ctx, _ = await _is_context_exceed_400(act_resp)
        await act_resp.release()
        debug_catalog.record_event(severity="error", category="mix-gm",
                                    kind="minimax_act_fail", chat_fp=chat_fp,
                                    code=act_resp.status, detail={"context_exceed": is_ctx})
        # MiniMax retry — NEVER Anthropic
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
            return web.json_response({"error": {"type": "minimax_unavailable",
                "message": "mix-gm: MiniMax ACT unavailable after retry"}}, status=502)
    act_ct = act_resp.headers.get('Content-Type', 'application/json')
    act_raw = await act_resp.read()
    await act_resp.release()

    is_sse = act_ct.startswith('text/event-stream') or act_raw.lstrip()[:6] == b'event:' or act_raw.lstrip()[:5] == b'data:'

    def _extract_act_text(raw, sse):
        if sse:
            parts = []
            for line in raw.decode(errors='ignore').splitlines():
                if line.startswith('data: '):
                    try:
                        obj = json.loads(line[6:])
                        if obj.get('type') == 'content_block_delta' and obj.get('delta', {}).get('type') == 'text_delta':
                            parts.append(obj['delta']['text'])
                    except Exception:
                        continue
            return ''.join(parts)
        else:
            try:
                data = json.loads(raw)
                return ''.join(c.get('text', '') for c in data.get('content', []) if c.get('type') == 'text')
            except Exception:
                return ''

    eval_text = _extract_act_text(act_raw, is_sse)
    warn_parts = []

    if len(eval_text) > 300:
        try:
            import hhem_gate as _hhem
            score = await _hhem.hhem_score("mix-gm ACT", eval_text[:1000])
            if score is not None and score < _hhem.HHEM_THRESHOLD:
                log(f"mix-gm ACT HHEM score={score:.3f} -> [HHEM-WARNING] fp={chat_fp}")
                debug_catalog.record_event(severity="block", category="mix-gm",
                                            kind="hhem_warning", chat_fp=chat_fp,
                                            snippet=f"score={score:.3f} < {_hhem.HHEM_THRESHOLD}")
                warn_parts.append(f'hhem={score:.3f}')
        except Exception:
            pass

    from pipeline_common import should_verify
    if is_sse:
        _gate_blocks = [{"type": "text", "text": eval_text}]
        if b'"tool_use"' in act_raw:
            _gate_blocks.append({"type": "tool_use"})
        _gate_raw = json.dumps({"type": "message", "content": _gate_blocks}).encode()
    else:
        _gate_raw = act_raw
    do_verify, v_reason = should_verify(chat_fp, _gate_raw)
    if do_verify:
        log(f"mix-gm VERIFY ({v_reason}): GLM-5.2 fp={chat_fp}")
        try:
            verify_body = _glm.build_glm_verify_body(orig, think_plan, eval_text[:5000])
            verify_resp = await _glm.forward_glm(request, verify_body, session, "glm-5.2", log_fn=log)
            if verify_resp.status < 400:
                verify_raw = verify_resp.body if isinstance(verify_resp.body, (bytes, bytearray)) else b""
                try:
                    verify_data = json.loads(verify_raw)
                    verify_text = verify_data.get("content", [{}])[0].get("text", "") if verify_data.get("content") else ""
                    log(f"mix-gm VERIFY: {verify_text[:100]} fp={chat_fp}")
                    if "INCOERENTE" in verify_text:
                        warn_parts.append('verify=incoherent')
                        debug_catalog.record_event(severity="block", category="mix-gm",
                                                    kind="verify_incoherent", chat_fp=chat_fp,
                                                    snippet=verify_text[:80])
                except Exception:
                    pass
        except Exception as e:
            log(f"mix-gm VERIFY EXC: {e}")
            debug_catalog.record_event(severity="block", category="mix-gm",
                                        kind="verify_exception", chat_fp=chat_fp, snippet=str(e))
    else:
        log(f"mix-gm VERIFY skip (gate) fp={chat_fp}")
    from aiohttp import web
    resp_headers = {'x-ai-verify': ','.join(warn_parts)} if warn_parts else {}
    return web.Response(body=act_raw, status=200,
                        content_type=('text/event-stream' if is_sse else 'application/json'),
                        headers=resp_headers)


async def _handle_glm_mode(request, body, session, mode, chat_fp, relay):
    """Dispatch delle 3 modalita' GLM."""
    import glm_backend as _glm
    if mode == "glm":
        return await _glm.glm_think_act_verify(request, body, session, log_fn=log, relay=relay)
    if mode == "mix-ag":
        return await _anthropic_glm_think_act_verify(request, body, session, chat_fp, relay)
    if mode == "mix-gm":
        return await _glm_minimax_think_act_verify(request, body, session, chat_fp, relay)
    from aiohttp import web
    return web.json_response({"error": f"GLM mode '{mode}' non gestita"}, status=500)
