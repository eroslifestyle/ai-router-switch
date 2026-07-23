"""Dispatch modalita' GLM (glm puro, mix-ag, mix-gm).

Refactor agent-sdk 2026-07-23: le pipeline classiche _anthropic_glm_think_act_verify
e _glm_minimax_think_act_verify sono state rimosse — mix-ag/mix-gm passano SEMPRE
dal run loop tipizzato (agent_loop_glm), che ora gestisce anche i path stream
(_mix_ag_stream/_mix_gm_stream). Zero duplicazioni.
"""
from router_utils import log


async def _handle_glm_mode(request, body, session, mode, chat_fp, relay):
    """Dispatch delle 3 modalita' GLM."""
    import glm_backend as _glm
    if mode == "glm":
        return await _glm.glm_think_act_verify(request, body, session, log_fn=log, relay=relay)
    if mode == "mix-ag":
        from agent_loop_glm import run_mix_ag_via_agent_loop
        return await run_mix_ag_via_agent_loop(request, body, session, chat_fp, relay)
    if mode == "mix-gm":
        from agent_loop_glm import run_mix_gm_via_agent_loop
        return await run_mix_gm_via_agent_loop(request, body, session, chat_fp, relay)
    from aiohttp import web
    return web.json_response({"error": f"GLM mode '{mode}' non gestita"}, status=500)
