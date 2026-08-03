#!/usr/bin/env python3
"""Servizi nativi Alibaba Cloud Model Studio (DashScope) per la modalità `qwen`.

Separato da `qwen_backend.py` (che gestisce il solo endpoint Anthropic-compatible
`/apps/anthropic/v1/messages`): qui vivono i servizi che parlano il protocollo
DashScope nativo — immagini, video, TTS, ASR, musica — più gli endpoint
OpenAI-compatible per embeddings e rerank.

ATTENZIONE — nomi modello da verificare col probe live: i due mirror della doc
ufficiale divergono sul catalogo (alibabacloud.com/help/en dà qwen-image-2.0-pro
e qwen3-tts-flash, help.aliyun.com/en dà qwen-image-3.0-pro e
qwen-audio-3.0-tts-plus). Ogni nome è sovrascrivibile da variabile d'ambiente.
"""
import json

import aiohttp
import aiohttp.web

from qwen_backend import (
    QWEN_COMPATIBLE_PATH,
    QWEN_DASHSCOPE_HOST,
    QWEN_MODEL_ASR,
    QWEN_MODEL_EMBED,
    QWEN_MODEL_IMAGE,
    QWEN_MODEL_MUSIC,
    QWEN_MODEL_RERANK,
    QWEN_MODEL_TTS,
    QWEN_MODEL_VIDEO,
    QWEN_MULTIMODAL_PATH,
    _non_stream_timeout,
    get_qwen_key,
)



async def _forward_dashscope(request, payload: dict, session, path: str, log_fn=print,
                             sse: bool = False) -> aiohttp.web.Response:
    """Helper comune per inoltrare richieste ai servizi nativi DashScope.

    URL: QWEN_DASHSCOPE_HOST + path
    Auth: Authorization Bearer
    """
    key = await get_qwen_key()
    if not key:
        return aiohttp.web.Response(status=502, text="qwen key missing")

    url = QWEN_DASHSCOPE_HOST + path
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    if sse:
        headers["X-DashScope-SSE"] = "enable"

    try:
        timeout = _non_stream_timeout()
        async with _QWEN_SEM:
            resp = await session.request(
                method="POST",
                url=url,
                headers=headers,
                json=payload,
                timeout=timeout,
                ssl=True
            )

        raw = await resp.read()
        return aiohttp.web.Response(
            status=resp.status,
            body=raw,
            content_type=resp.content_type or "application/json"
        )

    except Exception as e:
        log_fn(f"DashScope error ({path}): {e}")
        return aiohttp.web.Response(
            status=502,
            json={"error": str(e)},
            content_type="application/json"
        )


async def forward_qwen_image(request, body: bytes, session, log_fn=print):
    """Generazione immagini via DashScope.

    Path: /api/v1/services/aigc/text2image/image-generation
    Modello: QWEN_MODEL_IMAGE (da verificare col probe live)
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return aiohttp.web.Response(status=400, text="invalid JSON")

    prompt = data.get("prompt", "")
    if not prompt:
        return aiohttp.web.Response(status=400, text="prompt required")

    payload = {
        "model": QWEN_MODEL_IMAGE,
        "input": {
            "messages": [
                {"role": "user", "content": [{"text": prompt}]}
            ]
        },
        "parameters": {}
    }

    # Parametri opzionali
    params = payload["parameters"]
    if "size" in data:
        params["size"] = data["size"]
    if "n" in data:
        params["n"] = data["n"]
    if "negative_prompt" in data:
        params["negative_prompt"] = data["negative_prompt"]
    if "seed" in data:
        params["seed"] = data["seed"]
    if "watermark" in data:
        params["watermark"] = data["watermark"]

    return await _forward_dashscope(
        request, payload, session,
        "/api/v1/services/aigc/text2image/image-generation",
        log_fn=log_fn
    )


async def forward_qwen_video(request, body: bytes, session, log_fn=print):
    """Generazione video via DashScope.

    Path: /api/v1/services/aigc/video generation/text2video
    Modello: QWEN_MODEL_VIDEO (da verificare col probe live)
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return aiohttp.web.Response(status=400, text="invalid JSON")

    prompt = data.get("prompt", "")
    if not prompt:
        return aiohttp.web.Response(status=400, text="prompt required")

    payload = {
        "model": QWEN_MODEL_VIDEO,
        "input": {
            "messages": [
                {"role": "user", "content": [{"text": prompt}]}
            ]
        },
        "parameters": {}
    }

    params = payload["parameters"]
    if "duration" in data:
        params["duration"] = data["duration"]
    if "aspect_ratio" in data:
        params["aspect_ratio"] = data["aspect_ratio"]
    if "resolution" in data:
        params["resolution"] = data["resolution"]

    return await _forward_dashscope(
        request, payload, session,
        "/api/v1/services/aigc/video-generation/text2video",
        log_fn=log_fn
    )


async def forward_qwen_tts(request, body: bytes, session, log_fn=print):
    """Sintesi vocale via DashScope.

    Path: /api/v1/services/aigc/speech-generation/text2audio
    Modello: QWEN_MODEL_TTS (da verificare col probe live)
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return aiohttp.web.Response(status=400, text="invalid JSON")

    text = data.get("input", {}).get("text") or data.get("text", "")
    if not text:
        return aiohttp.web.Response(status=400, text="text required")

    payload = {
        "model": QWEN_MODEL_TTS,
        "input": {
            "text": text
        },
        "parameters": {}
    }

    if "voice" in data:
        payload["input"]["voice"] = data["voice"]
    if "response_format" in data:
        payload["parameters"]["response_format"] = data["response_format"]
    if "sample_rate" in data:
        payload["parameters"]["sample_rate"] = data["sample_rate"]
    if "speed" in data:
        payload["parameters"]["speed"] = data["speed"]

    return await _forward_dashscope(
        request, payload, session,
        "/api/v1/services/aigc/speech-generation/text2audio",
        log_fn=log_fn
    )


async def forward_qwen_asr(request, body: bytes, session, log_fn=print):
    """Riconoscimento vocale via DashScope.

    Path: /api/v1/services/aigc/speech2text/voice-generation
    Modello: QWEN_MODEL_ASR (da verificare col probe live)
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return aiohttp.web.Response(status=400, text="invalid JSON")

    audio = data.get("input", {}).get("audio") or data.get("audio")
    if not audio:
        return aiohttp.web.Response(status=400, text="audio required")

    payload = {
        "model": QWEN_MODEL_ASR,
        "input": {
            "audio": audio
        },
        "parameters": {}
    }

    if "language" in data:
        payload["parameters"]["language"] = data["language"]
    if "task" in data:
        payload["parameters"]["task"] = data["task"]

    return await _forward_dashscope(
        request, payload, session,
        "/api/v1/services/aigc/speech2text/voice-generation",
        log_fn=log_fn
    )


async def forward_qwen_music(request, body: bytes, session, log_fn=print):
    """Generazione musica via DashScope.

    Path: /api/v1/services/aigc/music-generation/music-creation
    Modello: QWEN_MODEL_MUSIC (da verificare col probe live)
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return aiohttp.web.Response(status=400, text="invalid JSON")

    prompt = data.get("prompt", "")
    if not prompt:
        return aiohttp.web.Response(status=400, text="prompt required")

    payload = {
        "model": QWEN_MODEL_MUSIC,
        "input": {
            "messages": [
                {"role": "user", "content": [{"text": prompt}]}
            ]
        },
        "parameters": {}
    }

    if "instrumental" in data:
        payload["parameters"]["instrumental"] = data["instrumental"]
    if "duration" in data:
        payload["parameters"]["duration"] = data["duration"]

    return await _forward_dashscope(
        request, payload, session,
        "/api/v1/services/aigc/music-generation/music-creation",
        log_fn=log_fn
    )


async def forward_qwen_embedding(request, body: bytes, session, log_fn=print):
    """Embedding via DashScope compatible-mode.

    Path: /compatible-mode/v1/embeddings (passa il body OpenAI-like cosi' com'e',
    forzando solo il model se assente)
    Modello: QWEN_MODEL_EMBED (da verificare col probe live)
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return aiohttp.web.Response(status=400, text="invalid JSON")

    if "model" not in data:
        data["model"] = QWEN_MODEL_EMBED

    key = await get_qwen_key()
    if not key:
        return aiohttp.web.Response(status=502, text="qwen key missing")

    url = QWEN_DASHSCOPE_HOST + QWEN_COMPATIBLE_PATH + "/embeddings"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    try:
        timeout = _non_stream_timeout()
        async with _QWEN_SEM:
            resp = await session.request(
                method="POST",
                url=url,
                headers=headers,
                json=data,
                timeout=timeout,
                ssl=True
            )

        raw = await resp.read()
        return aiohttp.web.Response(
            status=resp.status,
            body=raw,
            content_type=resp.content_type or "application/json"
        )

    except Exception as e:
        log_fn(f"DashScope embedding error: {e}")
        return aiohttp.web.Response(
            status=502,
            json={"error": str(e)},
            content_type="application/json"
        )


async def forward_qwen_rerank(request, body: bytes, session, log_fn=print):
    """Reranking via DashScope.

    Path: /compatible-mode/v1/rerank
    Modello: QWEN_MODEL_RERANK (da verificare col probe live)
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return aiohttp.web.Response(status=400, text="invalid JSON")

    if "model" not in data:
        data["model"] = QWEN_MODEL_RERANK

    key = await get_qwen_key()
    if not key:
        return aiohttp.web.Response(status=502, text="qwen key missing")

    url = QWEN_DASHSCOPE_HOST + QWEN_COMPATIBLE_PATH + "/rerank"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    try:
        timeout = _non_stream_timeout()
        async with _QWEN_SEM:
            resp = await session.request(
                method="POST",
                url=url,
                headers=headers,
                json=data,
                timeout=timeout,
                ssl=True
            )

        raw = await resp.read()
        return aiohttp.web.Response(
            status=resp.status,
            body=raw,
            content_type=resp.content_type or "application/json"
        )

    except Exception as e:
        log_fn(f"DashScope rerank error: {e}")
        return aiohttp.web.Response(
            status=502,
            json={"error": str(e)},
            content_type="application/json"
        )
