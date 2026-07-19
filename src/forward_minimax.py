# ~190 lines
"""MiniMax forwarding extracted from ai-router-proxy.py (~lines 1331-1624)."""
import asyncio
import json
import time

from aiohttp import ClientTimeout

import tool_isolation

from router_constants import (
    MINIMAX_UPSTREAM, MINIMAX_MODEL, MINIMAX_GENERATIVE_HOST,
    MINIMAX_RETRY_CAP_SEC, MINIMAX_RETRY_BUDGET_SHORT,
    MINIMAX_CONTEXT_BYTE_LIMIT, _GENERATIVE_PATHS,
)
from router_utils import (
    MINIMAX_LIMITER, _MINIMAX_SEM, RateLimitExhausted,
    _classify_429, _analyze_body_structure, SENT_ANALYSIS,
    _DEBUG_LAST_SENT, _minimax_alert, log,
    _request_orig_model,
)
from router_mode import _resolve_chat_fingerprint
from router_auth import get_minimax_key
# _log_original_model is defined in ai-router-proxy.py namespace, imported there

# Global mutable state
ANTHROPIC_OAUTH_TOKEN = ""  # will be updated by router_auth._reload_oauth_token


class _SyntheticResponse:
    """429/400 sintetico che emula la superficie ClientResponse usata dal router."""

    def __init__(self, status: int, payload: dict):
        self._body = json.dumps(payload).encode()
        self.status = status
        self.headers = {"Content-Type": "application/json", "x-ai-router": "synthetic"}

    async def read(self):
        return self._body

    async def json(self):
        return json.loads(self._body)

    async def release(self):
        return None

    @property
    def content(self):
        body = self._body
        class _OneShot:
            async def iter_any(self):
                yield body
        return _OneShot()


def _synthetic_429(msg: str) -> "_SyntheticResponse":
    return _SyntheticResponse(429, {"type": "error", "error": {"type": "rate_limit_error", "message": msg}})


def _synthetic_context_exceed(body_bytes: bytes) -> "_SyntheticResponse":
    resp = _SyntheticResponse(
        400, {"type": "error", "error": {"type": "context_exceeded",
                "message": f"body {len(body_bytes)}b > MiniMax limit {MINIMAX_CONTEXT_BYTE_LIMIT}b"}})
    resp.headers["x-ai-context-exceeded"] = "true"
    return resp


def _minimax_est_tokens(new_body: bytes) -> int:
    est = max(1, len(new_body) // 4)
    try:
        mt = int(json.loads(new_body).get("max_tokens", 0) or 0)
        est += max(0, mt)
    except Exception:
        pass
    return est


async def forward_minimax(request, body, session, retry_budget_sec: float = None,
                          act_timeout_sec: float = None):
    """Chiama MiniMax con pacing preventivo (MinimaxRateLimiter) + retry 429.

    act_timeout_sec: se settato, timeout totale (ClientTimeout.total) applicato
    a ogni tentativo HTTP verso MiniMax per questa chiamata. Pensato per i loop
    executor di mix-am: un MiniMax disconnesso/degradato ecceziona dopo
    act_timeout_sec invece di ereditare il sock_read=120s della sessione,
    evitando che un singolo tentativo blocchi il turno per minuti (retry-storm
    lato client). None = comportamento invariato (timeout di sessione)."""
    from minimax_body import remap_body_for_minimax
    from router_utils import _repair_message_sequence
    from context_rewrite import rewrite_for_context
    from router_utils import log as _log

    if retry_budget_sec is None:
        retry_budget_sec = MINIMAX_RETRY_CAP_SEC

    _orig_body = body
    try:
        if len(body) > 400_000:
            model = MINIMAX_MODEL
            rewritten, was_rewritten = rewrite_for_context(body, model, "")
            if was_rewritten:
                body = rewritten
                _log(f"[ctx-fix] rewrite {len(_orig_body)}b->{len(body)}b")
    except Exception as e:
        _log(f"[ctx-fix] rewrite failed: {e}")

    try:
        if len(body) > MINIMAX_CONTEXT_BYTE_LIMIT:
            return _synthetic_context_exceed(body)
    except Exception:
        pass

    url = MINIMAX_UPSTREAM + request.path_qs
    key = await get_minimax_key()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {
        "authorization", "x-api-key"
    }}
    headers["X-Api-Key"] = key

    # _log_original_model is in router_utils namespace, imported lazily to avoid circular
    from router_utils import _request_orig_model as _rom_store
    new_body = remap_body_for_minimax(body, request=request,
                                       orig_model_store=_rom_store,
                                       resolve_fp=_resolve_chat_fingerprint,
                                       log_model_fn=None,
                                       log_fn=_log)
    new_body = tool_isolation.filter_tools_for_backend(new_body, "minimax")

    try:
        model = json.loads(new_body).get("model", "") or MINIMAX_MODEL
    except Exception:
        model = MINIMAX_MODEL

    est = _minimax_est_tokens(new_body)
    t0 = time.monotonic()
    plan_retry_done = False

    while True:
        budget_left = retry_budget_sec - (time.monotonic() - t0)
        if budget_left <= 0:
            return _synthetic_429(f"MiniMax rate limited: retry budget {retry_budget_sec:.0f}s esaurito.")
        try:
            entry = await MINIMAX_LIMITER.acquire(model, est, budget_left)
        except RateLimitExhausted as e:
            return _synthetic_429(f"MiniMax rate limited (pacing): {e}")
        async with _MINIMAX_SEM:
            req_kwargs = dict(data=new_body, headers=headers, allow_redirects=False)
            if act_timeout_sec is not None:
                req_kwargs["timeout"] = ClientTimeout(total=act_timeout_sec)
            up = await session.request(
                request.method, url, **req_kwargs
            )
        if up.status != 429:
            MINIMAX_LIMITER.record(entry, est, success=True)
            MINIMAX_LIMITER.on_success()
            try:
                up._airouter_limiter_entry = entry
                up._airouter_limiter_est = est
            except Exception:
                pass
            return up
        try:
            raw = await up.read()
        except Exception:
            raw = b""
        try:
            await up.release()
        except Exception:
            pass
        MINIMAX_LIMITER.record(entry, 0, success=False)
        kind = _classify_429(raw)
        if kind == "token_plan":
            snippet = raw[:400].decode("utf-8", "replace")
            MINIMAX_LIMITER.set_plan_exhausted(snippet)
            _log(f"minimax 429 TOKEN-PLAN: {snippet[:200]}")
            _minimax_alert(f"Token Plan esaurito: {snippet[:200]}")
            if not plan_retry_done:
                plan_retry_done = True
                await asyncio.sleep(10)
                continue
            return _synthetic_429(f"MiniMax Token Plan esaurito. {snippet[:300]}")
        step = MINIMAX_LIMITER.on_429_rpm()
        _log(f"minimax 429 RPM/TPM: backoff {step}s (budget left {budget_left:.0f}s) model={model}")


async def _fwd_minimax_short(request, body, session):
    """forward_minimax con budget corto — da usare SOLO via _call_full."""
    return await forward_minimax(request, body, session, retry_budget_sec=MINIMAX_RETRY_BUDGET_SHORT)


async def _forward_minimax_generative(request, body: bytes, session,
                                     path: str):
    """Inoltra a MiniMax generative endpoint con retry di backoff."""
    url = MINIMAX_GENERATIVE_HOST + path
    key = await get_minimax_key()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in {"authorization", "x-api-key"}}
    headers["X-Api-Key"] = key
    try:
        json.loads(body)
    except (json.JSONDecodeError, TypeError):
        from aiohttp import web
        return web.json_response({"error": "invalid JSON body"}, status=400)
    retry_budget_sec = MINIMAX_RETRY_CAP_SEC
    t0 = time.monotonic()
    while True:
        budget_left = retry_budget_sec - (time.monotonic() - t0)
        if budget_left <= 0:
            return _synthetic_429("MiniMax generative rate limited: retry budget esaurito.")
        try:
            est_tokens = max(1, len(body) // 4)
            entry = await MINIMAX_LIMITER.acquire("generative", est_tokens, budget_left)
        except RateLimitExhausted as e:
            return _synthetic_429(f"MiniMax generative rate limited (pacing): {e}")
        async with _MINIMAX_SEM:
            up = await session.request(
                request.method, url, data=body, headers=headers, allow_redirects=False
            )
        if up.status != 429:
            MINIMAX_LIMITER.record(entry, est_tokens, success=True)
            MINIMAX_LIMITER.on_success()
            try:
                up._airouter_limiter_entry = entry
                up._airouter_limiter_est = est_tokens
            except Exception:
                pass
            raw = b""
            async for chunk in up.content.iter_chunked(65536):
                raw += chunk
            await up.release()
            from aiohttp import web
            try:
                resp_json = json.loads(raw)
                return web.json_response(resp_json, status=up.status)
            except Exception:
                return web.Response(body=raw, content_type=up.content_type or "application/octet-stream", status=up.status)
        try:
            raw = await up.read()
        except Exception:
            raw = b""
        try:
            await up.release()
        except Exception:
            pass
        MINIMAX_LIMITER.record(entry, 0, success=False)
        step = MINIMAX_LIMITER.on_429_rpm()
        log(f"minimax-generative 429: backoff {step}s")
        await asyncio.sleep(step)


async def _route_v1_images(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session, _GENERATIVE_PATHS["m3-image"])

async def _route_v1_videos(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session, _GENERATIVE_PATHS["m3-video"])

async def _route_v1_music(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session, _GENERATIVE_PATHS["m3-music"])

async def _route_v1_audio_speech(request) -> "web.Response":
    body = await request.read()
    session: "ClientSession" = request.app["session"]
    return await _forward_minimax_generative(request, body, session, _GENERATIVE_PATHS["m3-tts"])
