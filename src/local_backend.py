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
# Nessun fallback: il modello locale ha una sola via, llama.cpp :8083 dietro LiteLLM.
# L'alias Ollama code-max-ollama e' stato rimosso il 2026-08-04 (duplicazione da 48GB).
LOCAL_MODEL_FALLBACK = LOCAL_MODEL_CODE
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


# --- Correzione difensiva stop_reason (LiteLLM 1.95.0 non mappa finish_reason
# 'length' su stop_reason 'max_tokens': il client crede completa una risposta
# troncata). La trasformazione avviene chunk per chunk: lo streaming resta intatto.
# ELEMENTO 1
def requested_max_tokens(body: bytes) -> int | None:
    try:
        obj = json.loads(body)
        return obj.get("max_tokens")
    except Exception:
        return None


# ELEMENTO 2
class _FixedContent:
    def __init__(self, original_content, max_tokens: int, is_sse: bool, log_fn):
        self._orig = original_content
        self._max = max_tokens
        self._is_sse = is_sse
        self._log = log_fn

    def at_eof(self):
        return self._orig.at_eof()

    def __getattr__(self, name):
        return getattr(self._orig, name)

    async def iter_any(self):
        if self._is_sse:
            buffer = b""
            async for chunk in self._orig.iter_any():
                buffer += chunk
                parts = buffer.split(b"\n")
                buffer = parts.pop()
                for line in parts:
                    yield self._fix_line(line) + b"\n"
            if buffer:
                yield buffer
        else:
            chunks = []
            async for chunk in self._orig.iter_any():
                chunks.append(chunk)
            raw = b"".join(chunks)
            yield self._fix_json(raw)

    def _fix_line(self, line: bytes):
        prefix = b"data: "
        if not line.startswith(prefix):
            return line
        try:
            obj = json.loads(line[len(prefix):])
        except Exception:
            return line
        if (isinstance(obj, dict)
                and obj.get("type") == "message_delta"
                and obj.get("delta", {}).get("stop_reason") == "end_turn"
                and obj.get("usage", {}).get("output_tokens", 0) >= self._max):
            obj["delta"]["stop_reason"] = "max_tokens"
            self._log("stop_reason corretto: end_turn -> max_tokens")
            return prefix + json.dumps(obj).encode()
        return line

    def _fix_json(self, raw: bytes):
        try:
            obj = json.loads(raw)
        except Exception:
            return raw
        if (obj.get("stop_reason") == "end_turn"
                and obj.get("usage", {}).get("output_tokens", 0) >= self._max):
            obj["stop_reason"] = "max_tokens"
            self._log("stop_reason corretto: end_turn -> max_tokens")
            return json.dumps(obj).encode()
        return raw


# ELEMENTO 3
class _StopReasonFixed:
    def __init__(self, original_response, max_tokens: int, log_fn):
        ct = original_response.headers.get("content-type", "")
        is_sse = "text/event-stream" in ct.lower()
        object.__setattr__(self, "_orig", original_response)
        object.__setattr__(self, "_content", _FixedContent(
            original_response.content, max_tokens, is_sse, log_fn))

    @property
    def content(self):
        return self._content

    @property
    def headers(self):
        return {k: v for k, v in self._orig.headers.items()
                if k.lower() != "content-length"}

    def __getattr__(self, name):
        return getattr(self._orig, name)


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
                _max_tok = requested_max_tokens(body)
                if status == 200 and _max_tok:
                    return _StopReasonFixed(resp, _max_tok, log_fn)
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
