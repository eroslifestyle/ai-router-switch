"""SSE (Server-Sent Events) utilities — Anthropic-compatible streaming.

_text_from_message: funzione estrattore testo dal message dict.
Deve essere passata dal chiamante (definita nel modulo proxy).
"""
import json
from aiohttp import web

# Default stub — sostituito dal chiamante se necessario.
# Il modulo proxy definisce _text_from_message nel proprio global scope.
_text_extractor = None


def _sse_events_from_message(j: dict, verified: str, _text_from_message=None) -> list:
    """Ritorna lista di eventi SSE Anthropic-compat (per invio progressivo con flush)."""
    fn = _text_from_message or _text_extractor
    text = fn(j) if fn else ""
    mid = j.get("id", "msg_router")
    model = j.get("model", "unknown")
    usage = j.get("usage", {})
    msg_start = {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": usage}}
    return [
        f"event: message_start\ndata: {json.dumps(msg_start)}\n\n",
        "event: content_block_start\ndata: " + json.dumps({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}) + "\n\n",
        "event: content_block_delta\ndata: " + json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text}}) + "\n\n",
        "event: content_block_stop\ndata: " + json.dumps({
            "type": "content_block_stop", "index": 0}) + "\n\n",
        "event: message_delta\ndata: " + json.dumps({
            "type": "message_delta",
            "delta": {"stop_reason": j.get("stop_reason", "end_turn"),
                      "stop_sequence": None},
            "usage": {"output_tokens": usage.get("output_tokens", 0)}}) + "\n\n",
        "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
    ]


async def _prepare_sse_response(request, status: int = 200, extra_headers=None):
    """Prepara una StreamResponse SSE con header anti-buffering.

    Fix per ECONNRESET in VSCode: flush immediato + no buffering downstream.
    """
    resp = web.StreamResponse(status=status)
    resp.headers["content-type"] = "text/event-stream; charset=utf-8"
    resp.headers["cache-control"] = "no-cache, no-transform"
    resp.headers["connection"] = "keep-alive"
    resp.headers["x-accel-buffering"] = "no"
    if extra_headers:
        for k, v in extra_headers.items():
            resp.headers[k] = str(v)
    resp.enable_chunked_encoding()
    await resp.prepare(request)
    return resp


async def _send_sse_message(request, final_json: dict, verified_flag: str,
                             status: int = 200, _text_from_message=None):
    """Invia un message SSE Anthropic-compat evento-per-evento con flush immediato.

    Garantisce che il PRIMO evento (message_start) raggiunga il client SUBITO,
    evitando ECONNRESET 'before first event' in VSCode.
    """
    resp = await _prepare_sse_response(request, status=status,
                                       extra_headers={"x-ai-verified": verified_flag})
    for ev in _sse_events_from_message(final_json, verified_flag, _text_from_message):
        await resp.write(ev.encode())
        try:
            await resp.drain()
        except Exception:
            pass
    await resp.write_eof()
    return resp
