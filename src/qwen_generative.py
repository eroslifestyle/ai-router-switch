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
import os

import aiohttp
import aiohttp.web

from qwen_backend import (
    QWEN_COMPATIBLE_PATH,
    qwen_dashscope_base,
    QWEN_MODEL_ASR,
    QWEN_MODEL_EMBED,
    QWEN_MODEL_IMAGE,
    QWEN_MODEL_MUSIC,
    QWEN_MODEL_RERANK,
    QWEN_MODEL_TTS,
    QWEN_MODEL_VIDEO,
    QWEN_MULTIMODAL_PATH,
    _QWEN_SEM,
    _non_stream_timeout,
    get_qwen_key,
)


def _decomprimi(raw: bytes, encoding: str) -> bytes:
    """Decomprime il corpo della risposta upstream.

    La ClientSession del proxy usa auto_decompress=False PER SCELTA: il relay
    streaming inoltra i byte compressi al client conservando Content-Encoding.
    Qui pero' si costruisce una web.Response nuova, e l'header non viene
    inoltrato: restituire i byte gzip dichiarandoli JSON fa arrivare al client
    spazzatura binaria (verificato dal vivo su /v1/images/generations).
    Quindi qui si decomprime, e l'header non serve piu'.
    """
    if not raw:
        return raw
    enc = (encoding or "").lower()
    try:
        if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
            import gzip as _gz
            return _gz.decompress(raw)
        if "deflate" in enc:
            import zlib as _zl
            return _zl.decompress(raw)
        if "br" in enc:
            try:
                import brotli as _br
            except ImportError:
                return raw
            return _br.decompress(raw)
    except Exception:
        # Meglio i byte grezzi di un'eccezione: il chiamante li inoltra e il
        # problema resta visibile invece di diventare un 502 opaco.
        return raw
    return raw


# sovrascrivibile da env, cosi' il probe live li corregge senza toccare il codice.
QWEN_PATH_IMAGE = os.environ.get("QWEN_PATH_IMAGE", QWEN_MULTIMODAL_PATH)
QWEN_PATH_TTS = os.environ.get("QWEN_PATH_TTS", QWEN_MULTIMODAL_PATH)
QWEN_PATH_VIDEO = os.environ.get(  # VERIFICATO: con model valido nel body e header X-DashScope-Async
    "QWEN_PATH_VIDEO", "/api/v1/services/aigc/video-generation/video-synthesis")
QWEN_PATH_ASR = os.environ.get(  # CORRETTO /api/v1/services/audio/asr/transcription; era SBAGLIATO
    # puntava a multimodal-generation che con fun-asr risponde "url error" come la controprova.
    # video e ASR richiedono X-DashScope-Async: senza header 403 AccessDenied, con header 200 e task_id.
    "QWEN_PATH_ASR", "/api/v1/services/audio/asr/transcription")
QWEN_PATH_MUSIC = os.environ.get(  # NON DETERMINABILE: model fun-music-v1 non esiste su questo account,
    "QWEN_PATH_MUSIC", QWEN_MULTIMODAL_PATH)  # risponde 404 "Model not exist" identico alla controprova.


async def _forward_dashscope(request, payload: dict, session, path: str, log_fn=print,
                             sse: bool = False, async_task: bool = False) -> aiohttp.web.Response:
    """Helper comune per inoltrare richieste ai servizi nativi DashScope.

    URL: host DEDICATO del workspace + path (vedi qwen_dashscope_base)
    Auth: Authorization Bearer
    Nota: i servizi video e ASR di questo gateway accettano SOLO chiamate asincrone.
    Verificato il 2026-08-03, senza l'header rispondono 403 AccessDenied con messaggio
    "current user api does not support synchronous calls", mentre con l'header rispondono
    200 restituendo output.task_id e task_status PENDING. Il chiamante riceve quindi un
    task da interrogare, non il risultato finale.
    """
    key = await get_qwen_key()
    if not key:
        return aiohttp.web.Response(status=502, text="qwen key missing")

    url = (await qwen_dashscope_base()) + path
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    if sse:
        headers["X-DashScope-SSE"] = "enable"

    if async_task:
        headers["X-DashScope-Async"] = "enable"

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

        raw = _decomprimi(await resp.read(),
                          resp.headers.get("Content-Encoding", ""))
        return aiohttp.web.Response(
            status=resp.status,
            body=raw,
            content_type=resp.content_type or "application/json"
        )

    except Exception as e:
        log_fn(f"DashScope error ({path}): {e}")
        return aiohttp.web.json_response({"error": str(e)}, status=502)


async def forward_qwen_image(request, body: bytes, session, log_fn=print):
    """Generazione immagini via DashScope.

    Path: QWEN_PATH_IMAGE (doc ufficiale: multimodal-generation/generation)
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
        QWEN_PATH_IMAGE,
        log_fn=log_fn
    )


async def forward_qwen_video(request, body: bytes, session, log_fn=print):
    """Generazione video via DashScope.

    Path: QWEN_PATH_VIDEO (VERIFICATO 2026-08-03; task ASINCRONO, vedi async_task)
    Modello: QWEN_MODEL_VIDEO (VERIFICATO esistente sull'account)
    Ritorna output.task_id con task_status PENDING, non il video finito.
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
        QWEN_PATH_VIDEO,
        log_fn=log_fn,
        async_task=True  # il gateway rifiuta le chiamate sincrone con 403
    )


async def forward_qwen_tts(request, body: bytes, session, log_fn=print):
    """Sintesi vocale via DashScope.

    Path: QWEN_PATH_TTS (doc ufficiale: multimodal-generation/generation)
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
        QWEN_PATH_TTS,
        log_fn=log_fn
    )


async def forward_qwen_asr(request, body: bytes, session, log_fn=print):
    """Riconoscimento vocale via DashScope.

    Path: QWEN_PATH_ASR (CORRETTO 2026-08-03: era multimodal, sbagliato; task ASINCRONO)
    Modello: QWEN_MODEL_ASR (VERIFICATO esistente sull'account)
    Ritorna output.task_id con task_status PENDING, non la trascrizione finita.
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
        QWEN_PATH_ASR,
        log_fn=log_fn,
        async_task=True  # il gateway rifiuta le chiamate sincrone con 403
    )


async def forward_qwen_music(request, body: bytes, session, log_fn=print):
    """Generazione musica via DashScope.

    Path: QWEN_PATH_MUSIC (NON DETERMINABILE finche' manca un modello valido)
    Modello: QWEN_MODEL_MUSIC (fun-music-v1 NON ESISTE sull'account: 404 Model not exist)
    Questa rotta non puo' funzionare oggi: il gateway valida il modello prima del path.
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
        QWEN_PATH_MUSIC,
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

    url = (await qwen_dashscope_base()) + QWEN_COMPATIBLE_PATH + "/embeddings"
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

        raw = _decomprimi(await resp.read(),
                          resp.headers.get("Content-Encoding", ""))
        return aiohttp.web.Response(
            status=resp.status,
            body=raw,
            content_type=resp.content_type or "application/json"
        )

    except Exception as e:
        log_fn(f"DashScope embedding error: {e}")
        return aiohttp.web.json_response({"error": str(e)}, status=502)


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

    url = (await qwen_dashscope_base()) + QWEN_COMPATIBLE_PATH + "/rerank"
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

        raw = _decomprimi(await resp.read(),
                          resp.headers.get("Content-Encoding", ""))
        return aiohttp.web.Response(
            status=resp.status,
            body=raw,
            content_type=resp.content_type or "application/json"
        )

    except Exception as e:
        log_fn(f"DashScope rerank error: {e}")
        return aiohttp.web.json_response({"error": str(e)}, status=502)
