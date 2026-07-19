# ~170 lines
"""GLM pipeline extracted from ai-router-proxy.py (~lines 3024-3292)."""
import json

from router_constants import (
    GLM_AVAILABLE, FALLBACK_STATUSES, THINK_TIMEOUT_SEC,
    MINIMAX_FALLBACK_STATUSES,
)
from router_utils import log


async def _anthropic_glm_think_act_verify(request, body: bytes, session, chat_fp: str, relay):
    """mix-ag: Anthropic THINK -> GLM ACT -> Anthropic VERIFY."""
    from forward_anthropic import forward_anthropic, forward_anthropic_direct
    from pipeline_anthropic import _call_full, _text_from_message, _build_think_body
    orig = json.loads(body) if isinstance(body, bytes) else body
    think_body = _build_think_body(orig)
    try:
        t_status, t_json = await _call_full(forward_anthropic_direct, request, think_body, session, timeout=THINK_TIMEOUT_SEC)
    except Exception as e:
        log(f"mix-ag THINK EXC: {e}")
        think_plan = ""
    else:
        if not t_json or t_status in FALLBACK_STATUSES:
            think_plan = ""
        else:
            think_plan = _text_from_message(t_json).strip()
    log(f"mix-ag THINK: plan={len(think_plan)}c fp={chat_fp}")

    # GLM ACT
    import glm_backend as _glm
    tier = await _glm.classify_tier(body, request, session, log_fn=log)
    eff_model, capped = _glm.apply_peak_cap(tier)
    real_model = _glm.resolve_glm_upstream_model(eff_model)
    act_body = _glm.set_body_model(body, real_model)
    log(f"mix-ag ACT: GLM {real_model} (tier={eff_model}) fp={chat_fp}")
    act_resp = await _glm.forward_glm(request, act_body, session,
                                       orig.get("model") or real_model, log_fn=log)
    if act_resp.status >= 400:
        log(f"mix-ag ACT fail {act_resp.status}")
        return await relay(await forward_anthropic(request, body, session))
    act_raw = act_resp.body if isinstance(act_resp.body, (bytes, bytearray)) else b""
    log(f"mix-ag VERIFY: Anthropic fp={chat_fp}")
    try:
        verify_msgs = [
            {"role": "system", "content": "Sei un verifier AI."},
            {"role": "user", "content": f"Piano:\n{think_plan}\n\nOutput:\n{act_raw.decode(errors='ignore')[:5000]}"},
        ]
        verify_body = json.dumps({
            "model": orig.get("model", "claude-sonnet-4-6"),
            "messages": verify_msgs, "max_tokens": 500,
        }).encode()
        v_status, v_json = await _call_full(forward_anthropic_direct, request, verify_body, session, timeout=30)
        if v_status < 400 and v_json:
            verify_text = _text_from_message(v_json).strip()
            log(f"mix-ag VERIFY: {verify_text[:100]}")
    except Exception as e:
        log(f"mix-ag VERIFY EXC: {e}")
    return act_resp


async def _glm_minimax_think_act_verify(request, body: bytes, session, chat_fp: str, relay):
    """mix-gm: GLM-5.2 THINK -> MiniMax ACT -> GLM-5.2 VERIFY."""
    from forward_minimax import forward_minimax
    from pipeline_anthropic import _call_full, _build_minimax_act_body_retry, _is_context_exceed_400
    import glm_backend as _glm
    orig = json.loads(body) if isinstance(body, bytes) else body
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
        think_plan = ""
    log(f"mix-gm THINK: plan={len(think_plan)}c fp={chat_fp}")

    log(f"mix-gm ACT: MiniMax fp={chat_fp}")
    act_resp = await forward_minimax(request, body, session)
    if act_resp.status >= 400:
        is_ctx, _ = await _is_context_exceed_400(act_resp)
        await act_resp.release()
        if is_ctx:
            from pipeline_anthropic import _shrink_and_retry_minimax
            return await _shrink_and_retry_minimax(request, orig, body, session, chat_fp, relay)
        from pipeline_anthropic import _anthropic_rescue
        return await _anthropic_rescue(request, orig, session, chat_fp, relay)
    act_raw = await act_resp.read()
    await act_resp.release()

    # HHEM gate
    if len(act_raw) > 300:
        try:
            import hhem_gate as _hhem
            score = await _hhem.hhem_score("mix-gm ACT", act_raw.decode(errors="ignore")[:1000])
            if score is not None and score < _hhem.HHEM_THRESHOLD:
                log(f"mix-gm ACT HHEM score={score:.3f} -> [HHEM-WARNING] fp={chat_fp}")
                act_raw = b"[HHEM-WARNING] " + act_raw
        except Exception:
            pass

    # GLM VERIFY
    log(f"mix-gm VERIFY: GLM-5.2 fp={chat_fp}")
    verify_ok = False
    retry_note = ""
    for attempt in range(2):
        try:
            verify_body = _glm.build_glm_verify_body(orig, think_plan, act_raw.decode(errors="ignore")[:5000])
            verify_resp = await _glm.forward_glm(request, verify_body, session, "glm-5.2", log_fn=log)
            if verify_resp.status < 400:
                verify_raw = verify_resp.body if isinstance(verify_resp.body, (bytes, bytearray)) else b""
                try:
                    verify_data = json.loads(verify_raw)
                    verify_text = verify_data.get("content", [{}])[0].get("text", "") if verify_data.get("content") else ""
                    log(f"mix-gm VERIFY attempt={attempt+1}: {verify_text[:100]} fp={chat_fp}")
                    if "INCOERENTE" in verify_text:
                        retry_note = verify_text
                        correction = f"\n[NOTA CORRETTIVA]\n{verify_text}\n[/NOTA CORRETTIVA]"
                        act_body_retry = _build_minimax_act_body_retry(orig, correction)
                        act_resp = await forward_minimax(request, act_body_retry, session)
                        if act_resp.status >= 400:
                            await act_resp.release()
                            break
                        act_raw = await act_resp.read()
                        await act_resp.release()
                        continue
                    verify_ok = True
                except Exception:
                    pass
        except Exception as e:
            log(f"mix-gm VERIFY EXC: {e}")
        break

    if not verify_ok and retry_note:
        act_raw = b"[VERIFY-WARNING] " + act_raw
        log(f"mix-gm VERIFY-WARNING: {retry_note[:80]} fp={chat_fp}")
    from aiohttp import web
    return web.Response(body=act_raw, status=200, content_type="application/json")


async def _handle_glm_mode(request, body, session, mode, chat_fp, relay):
    """Dispatch delle 3 modalita' GLM."""
    import glm_backend as _glm
    if mode == "glm":
        return await _glm.glm_think_act_verify(request, body, session, log_fn=log)
    if mode == "mix-ag":
        return await _anthropic_glm_think_act_verify(request, body, session, chat_fp, relay)
    if mode == "mix-gm":
        return await _glm_minimax_think_act_verify(request, body, session, chat_fp, relay)
    from aiohttp import web
    return web.json_response({"error": f"GLM mode '{mode}' non gestita"}, status=500)
