"""Backend proxy per provider LLM locale via LiteLLM."""
import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Callable, AsyncIterator

import aiohttp
from aiohttp import web

from synthetic_response import synthetic_error

LOCAL_MODEL_CODE = 'code-max'
LOCAL_MODEL_FALLBACK = 'code-max-ollama'
LOCAL_TIMEOUT_SEC = int(os.environ.get('AIROUTER_LOCAL_TIMEOUT_SEC', 600))
LOCAL_MAX_RETRY = 2

_cache_key: Optional[str] = None
_cache_base: Optional[str] = None


async def get_local_key() -> str:
    """Legge la chiave API da env o file ~/.claude/secrets/local-llm.env."""
    global _cache_key
    if _cache_key is not None:
        return _cache_key
    key = os.environ.get('LOCAL_LLM_API_KEY', '')
    if not key:
        env_file = Path.home() / '.claude' / 'secrets' / 'local-llm.env'
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    if k.strip() == 'LOCAL_LLM_API_KEY':
                        key = v.strip().strip('"').strip("'")
                        break
    _cache_key = key
    return key


def get_local_base() -> str:
    """Legge l'URL base da env o file, default http://127.0.0.1:4000."""
    global _cache_base
    if _cache_base is not None:
        return _cache_base
    base = os.environ.get('LOCAL_LLM_API_BASE', '')
    if not base:
        env_file = Path.home() / '.claude' / 'secrets' / 'local-llm.env'
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    if k.strip() == 'LOCAL_LLM_API_BASE':
                        base = v.strip().strip('"').strip("'")
                        break
    if not base:
        base = 'http://127.0.0.1:4000'
    _cache_base = base.rstrip('/')
    return _cache_base


def set_body_model(body: bytes, model: str) -> bytes:
    """Imposta il campo 'model' nel body JSON e ritorna il body modificato."""
    try:
        data = json.loads(body)
        data['model'] = model
        return json.dumps(data).encode()
    except (json.JSONDecodeError, TypeError):
        return body


def resolve_local_model(requested: Optional[str]) -> str:
    """Restituisce il modello richiesto se consentito, altrimenti LOCAL_MODEL_CODE."""
    if requested in (LOCAL_MODEL_CODE, LOCAL_MODEL_FALLBACK):
        return requested
    return LOCAL_MODEL_CODE


async def forward_local(
    request,
    body: bytes,
    session: aiohttp.ClientSession,
    model: str,
    log_fn: Callable[..., None] = print,
    passthrough: bool = False,
    upstream_model: str = ''
) -> Optional[aiohttp.ClientResponse]:
    """Inoltra la richiesta al backend locale con retry su errori transienti."""
    key = await get_local_key()
    if not key:
        log_fn("forward_local: API key mancante")
        if passthrough:
            return synthetic_error(502, 'local_unavailable', 'Local LLM key not configured')
        return web.Response(
            text='{"type":"error","error":{"type":"local_unavailable","message":"Local LLM key not configured"}}',
            status=502,
            content_type='application/json'
        )

    base = get_local_base()
    url = base + request.path_qs
    anth_version = request.headers.get('anthropic-version', '2023-06-01')

    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'anthropic-version': anth_version,
    }

    for attempt in range(LOCAL_MAX_RETRY + 1):
        try:
            start = asyncio.get_event_loop().time()
            resp = await session.post(
                url,
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=LOCAL_TIMEOUT_SEC)
            )
            status = resp.status
            elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000
            log_fn(f"forward_local attempt {attempt+1}/{LOCAL_MAX_RETRY+1}: model={model} status={status} elapsed={elapsed_ms:.0f}ms")
            if status in (502, 503, 504):
                if attempt < LOCAL_MAX_RETRY:
                    log_fn(f"forward_local retry {attempt+1}: status {status}")
                    await resp.release()
                    await asyncio.sleep(2)
                    continue
            if passthrough:
                return resp
            body_bytes = await resp.read()
            await resp.release()
            return web.Response(
                body=body_bytes,
                status=status,
                content_type='application/json'
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000
            if attempt < LOCAL_MAX_RETRY:
                log_fn(f"forward_local retry {attempt+1}: {type(e).__name__} elapsed={elapsed_ms:.0f}ms")
                await asyncio.sleep(2)
                continue
            log_fn(f"forward_local error: {type(e).__name__} elapsed={elapsed_ms:.0f}ms")
            err_msg = f'{{"type":"error","error":{{"type":"local_unavailable","message":"Local LLM backend unreachable: {e}"}}}}'
            if passthrough:
                return synthetic_error(502, 'local_unavailable', err_msg)
            return web.Response(
                text=err_msg,
                status=502,
                content_type='application/json'
            )

    log_fn("forward_local: exhausted retries")
    err_msg = '{"type":"error","error":{"type":"local_unavailable","message":"Local LLM backend failed after retries"}}'
    if passthrough:
        return synthetic_error(502, 'local_unavailable', err_msg)
    return web.Response(text=err_msg, status=502, content_type='application/json')
