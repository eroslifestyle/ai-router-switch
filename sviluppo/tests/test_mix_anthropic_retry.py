#!/usr/bin/env python3
"""Unit test: retry certificato Anthropic condiviso (pipeline_common) usato dalle
leg mix. Verifica: backoff/parse retry-after, x-should-retry:false=stop, retry su
429/5xx, esaurimento onesto, no-retry su 2xx/4xx-non-429. Zero rete (fake forward)."""
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


def make_forward(statuses, headers_seq=None):
    """forward_fn che ritorna gli status in sequenza; l'ultimo si ripete."""
    calls = {"n": 0}

    async def _fwd(request, body, session):
        i = min(calls["n"], len(statuses) - 1)
        st = statuses[i]
        hdr = (headers_seq[i] if headers_seq and i < len(headers_seq) else {})
        calls["n"] += 1
        return FakeResp(st, hdr)

    return _fwd, calls


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_parse_retry_after():
    assert pc.parse_retry_after("5") == 5.0
    assert pc.parse_retry_after("2.5") == 2.5
    assert pc.parse_retry_after("") is None
    assert pc.parse_retry_after("garbage") is None
    print("  parse_retry_after OK")


def test_backoff_honors_retry_after():
    # retry_after presente → usa quello (cap a MAX)
    assert pc.backoff_sleep_sec(0, 3.0) == 3.0
    assert pc.backoff_sleep_sec(5, 1000.0) == pc.ANTHROPIC_RETRY_MAX_SLEEP_SEC
    # senza retry_after → equal jitter positivo, cresce con attempt
    d0 = pc.backoff_sleep_sec(0, None)
    d3 = pc.backoff_sleep_sec(3, None)
    assert d0 > 0 and d3 > 0
    print("  backoff_sleep_sec OK")


def test_no_retry_on_success():
    fwd, calls = make_forward([200])
    up, exhausted = run(pc.anthropic_call_with_retry(fwd, None, b"", None))
    assert up.status == 200 and exhausted is False and calls["n"] == 1
    print("  no-retry on 200 OK (1 call)")


def test_no_retry_on_4xx_non429():
    fwd, calls = make_forward([400])
    up, exhausted = run(pc.anthropic_call_with_retry(fwd, None, b"", None))
    assert up.status == 400 and exhausted is False and calls["n"] == 1
    print("  no-retry on 400 OK (1 call)")


def test_retry_429_then_success():
    # 429, 429, 200 → con MAX_RETRIES=2 (default) arriva al 200
    orig = pc.ANTHROPIC_RETRY_BASE_SEC
    pc.ANTHROPIC_RETRY_BASE_SEC = 0.0  # niente sleep reale
    try:
        fwd, calls = make_forward([429, 429, 200])
        up, exhausted = run(pc.anthropic_call_with_retry(fwd, None, b"", None))
        assert up.status == 200, up.status
        assert exhausted is False
        assert calls["n"] == 3, calls["n"]
    finally:
        pc.ANTHROPIC_RETRY_BASE_SEC = orig
    print("  429,429,200 -> 200 OK (3 calls)")


def test_429_persistent_exhausted():
    orig = pc.ANTHROPIC_RETRY_BASE_SEC
    pc.ANTHROPIC_RETRY_BASE_SEC = 0.0
    try:
        fwd, calls = make_forward([429])  # sempre 429
        up, exhausted = run(pc.anthropic_call_with_retry(fwd, None, b"", None))
        assert up.status == 429 and exhausted is True
        # MAX_RETRIES=2 → 1 iniziale + 2 retry = 3 chiamate
        assert calls["n"] == pc.ANTHROPIC_MAX_RETRIES + 1, calls["n"]
    finally:
        pc.ANTHROPIC_RETRY_BASE_SEC = orig
    print("  429 persistente -> exhausted OK (3 calls, no loop)")


def test_should_retry_false_stops():
    fwd, calls = make_forward([429], [{"x-should-retry": "false"}])
    up, exhausted = run(pc.anthropic_call_with_retry(fwd, None, b"", None))
    assert up.status == 429 and exhausted is True and calls["n"] == 1
    print("  x-should-retry:false -> stop immediato OK (1 call)")


def test_5xx_retried():
    orig = pc.ANTHROPIC_RETRY_BASE_SEC
    pc.ANTHROPIC_RETRY_BASE_SEC = 0.0
    try:
        fwd, calls = make_forward([503, 200])
        up, exhausted = run(pc.anthropic_call_with_retry(fwd, None, b"", None))
        assert up.status == 200 and exhausted is False and calls["n"] == 2
    finally:
        pc.ANTHROPIC_RETRY_BASE_SEC = orig
    print("  503,200 -> 200 OK (5xx retried)")


if __name__ == "__main__":
    print("test_mix_anthropic_retry:")
    test_parse_retry_after()
    test_backoff_honors_retry_after()
    test_no_retry_on_success()
    test_no_retry_on_4xx_non429()
    test_retry_429_then_success()
    test_429_persistent_exhausted()
    test_should_retry_false_stops()
    test_5xx_retried()
    print("ALL PASS")
