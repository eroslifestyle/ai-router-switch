#!/usr/bin/env python3
"""Fake upstream Anthropic che risponde 429 con retry-after su /v1/messages.
Usato per testare il backoff certificato delle leg Anthropic mix in ISOLAMENTO,
senza consumare quota reale né toccare :8787. Configurabile via env:
  FAKE_PORT (default 9429), FAKE_RETRY_AFTER (sec, default 1),
  FAKE_STATUS (default 429), FAKE_SHOULD_RETRY (true/false, default true),
  FAKE_SUCCEED_AFTER (N: dopo N richieste risponde 200; default 0 = sempre errore)."""
import json
import os
from aiohttp import web

RETRY_AFTER = os.environ.get("FAKE_RETRY_AFTER", "1")
STATUS = int(os.environ.get("FAKE_STATUS", "429"))
SHOULD_RETRY = os.environ.get("FAKE_SHOULD_RETRY", "true")
SUCCEED_AFTER = int(os.environ.get("FAKE_SUCCEED_AFTER", "0"))
PORT = int(os.environ.get("FAKE_PORT", "9429"))

_count = {"n": 0}


async def handle(request):
    _count["n"] += 1
    n = _count["n"]
    if SUCCEED_AFTER and n > SUCCEED_AFTER:
        body = json.dumps({"id": "msg_fake", "type": "message", "role": "assistant",
                           "model": "claude-fake", "content": [{"type": "text",
                           "text": "OK dal fake dopo backoff"}],
                           "stop_reason": "end_turn",
                           "usage": {"input_tokens": 1, "output_tokens": 3}})
        print(f"[fake] req#{n} -> 200", flush=True)
        return web.Response(status=200, body=body.encode(),
                            content_type="application/json")
    print(f"[fake] req#{n} -> {STATUS} retry-after={RETRY_AFTER} "
          f"x-should-retry={SHOULD_RETRY}", flush=True)
    return web.Response(
        status=STATUS,
        body=json.dumps({"type": "error", "error": {"type": "rate_limit_error",
                         "message": "fake rate limit"}}).encode(),
        content_type="application/json",
        headers={"retry-after": RETRY_AFTER, "x-should-retry": SHOULD_RETRY},
    )


app = web.Application()
app.router.add_route("*", "/{tail:.*}", handle)
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
