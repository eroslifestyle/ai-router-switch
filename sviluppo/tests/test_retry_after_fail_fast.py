#!/usr/bin/env python3
"""429 fail-fast: retry-after oltre la soglia (MAX_RETRY_AFTER_SEC) → il 429
torna SUBITO al client, senza sleep né retry. Fallisce se il fail-fast salta
(il forward viene richiamato o il delay supera la soglia)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pipeline_common as pc


class FakeResp:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}
        self.released = False

    def release(self):
        self.released = True


def test_fail_fast_on_huge_retry_after():
    calls = {"n": 0}
    logs = []

    async def fwd(request, body, session):
        calls["n"] += 1
        return FakeResp(429, {"retry-after": "144777"})

    async def main():
        return await pc.anthropic_call_with_retry(
            fwd, None, None, None,
            log_fn=logs.append, tag="anthropic")

    up, exhausted = asyncio.run(main())
    assert up.status == 429, "il 429 deve tornare al client invariato"
    assert exhausted is True
    assert calls["n"] == 1, f"forward chiamato {calls['n']} volte, attesa 1 (no retry)"
    assert any("fail-fast" in line for line in logs), logs
    print("  fail-fast 429 OK")


def test_small_retry_after_still_retries():
    calls = {"n": 0}

    async def fwd(request, body, session):
        calls["n"] += 1
        return FakeResp(429, {"retry-after": "1"})

    async def main():
        return await pc.anthropic_call_with_retry(
            fwd, None, None, None, log_fn=lambda *_: None, tag="anthropic")

    up, exhausted = asyncio.run(main())
    assert up.status == 429 and exhausted is True
    assert calls["n"] == pc.ANTHROPIC_MAX_RETRIES + 1, calls["n"]
    print("  pacing 429 con retry OK")
