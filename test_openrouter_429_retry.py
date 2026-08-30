#!/usr/bin/env python3
"""Test retry OpenRouter 429 upstream pool (ox-alpha)."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import ClientResponse, web
from multidict import CIMultiDict

import sys
sys.path.insert(0, 'src')

from local_backend import forward_local
from router_constants import OPENROUTER_BACKOFF_STEPS_SEC


async def test_openrouter_429_upstream_retry():
    """Verifica che forward_local ritenti OpenRouter 429 upstream pool."""
    print(f"Testing OpenRouter 429 retry with backoff {OPENROUTER_BACKOFF_STEPS_SEC}")

    # Mock request
    request = MagicMock()
    request.path_qs = "/v1/messages"
    request.headers = {"anthropic-version": "2023-06-01"}

    body = json.dumps({
        "model": "claude-opus-5",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "test"}]
    }).encode()

    # Mock session che restituisce 429 upstream pool
    mock_session = AsyncMock()
    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        # Primi 2 tentativi: 429 upstream pool
        if call_count <= 2:
            mock_resp = MagicMock(spec=ClientResponse)
            mock_resp.status = 429
            mock_resp.headers = CIMultiDict({"content-type": "application/json"})

            error_body = json.dumps({
                "error": {
                    "message": "Provider returned error",
                    "code": 429,
                    "metadata": {
                        "raw": "stealth/ox-alpha is temporarily rate-limited upstream. Please retry shortly.",
                        "provider_name": "Stealth",
                        "limit_source": "upstream_provider_shared_pool"
                    }
                }
            }).encode()

            mock_resp.read = AsyncMock(return_value=error_body)
            mock_resp.release = AsyncMock()
            return mock_resp
        else:
            # Terzo tentativo: successo 200
            mock_resp = MagicMock(spec=ClientResponse)
            mock_resp.status = 200
            mock_resp.headers = CIMultiDict({"content-type": "application/json"})

            success_body = json.dumps({
                "id": "test-id",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}]
            }).encode()

            mock_resp.read = AsyncMock(return_value=success_body)
            mock_resp.release = AsyncMock()
            return mock_resp

    mock_session.post = mock_post

    # Mock log_fn
    log_entries = []
    def mock_log(msg):
        log_entries.append(msg)
        print(f"[LOG] {msg}")

    # Mock secrets_provider
    with patch('local_backend.get_local_key', return_value="test-key"):
        with patch('local_backend.get_local_base', return_value="http://localhost:4000"):
            with patch('local_backend.debug_catalog'):
                # Patch tool_isolation per evitare side effects
                with patch('local_backend.tool_isolation.filter_tools_for_backend', side_effect=lambda b, p: b):
                    result = await forward_local(
                        request, body, mock_session,
                        model="claude-opus-5",
                        log_fn=mock_log,
                        passthrough=False,
                        upstream_model="ox-alpha"
                    )

    print(f"\nCall count: {call_count}")
    print(f"Result status: {result.status if hasattr(result, 'status') else 'N/A'}")
    print(f"Log entries ({len(log_entries)}):")
    for entry in log_entries:
        print(f"  {entry}")

    # Verifica
    assert call_count == 3, f"Expected 3 calls (2 retries + 1 final), got {call_count}"

    retry_logs = [e for e in log_entries if "OpenRouter 429 upstream pool" in e and "retry" in e]
    assert len(retry_logs) == 2, f"Expected 2 retry logs, got {len(retry_logs)}"
    assert "retry in 3s" in retry_logs[0], f"First retry should be 3s, got: {retry_logs[0]}"
    assert "retry in 8s" in retry_logs[1], f"Second retry should be 8s, got: {retry_logs[1]}"

    assert result.status == 200, f"Expected status 200, got {result.status}"
    # Verifica body response (web.Response ha body attribute, non text())
    response_text = result.body.decode() if hasattr(result, 'body') else ""
    assert "Hello" in response_text, f"Response should contain 'Hello', got: {response_text[:100]}"

    print("\n✓ OpenRouter 429 retry test PASSED")
    print(f"  - {call_count} total calls (2 retries + success)")
    print(f"  - Backoff sequence: 3s, 8s (from {OPENROUTER_BACKOFF_STEPS_SEC})")
    print(f"  - Final status: {result.status}")
    return True


async def test_openrouter_429_exhausted_retries():
    """Verifica che dopo retry esauriti, l'errore venga propagato."""
    print(f"\nTesting OpenRouter 429 exhausted retries")

    request = MagicMock()
    request.path_qs = "/v1/messages"
    request.headers = {"anthropic-version": "2023-06-01"}

    body = json.dumps({
        "model": "claude-opus-5",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "test"}]
    }).encode()

    mock_session = AsyncMock()
    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        # Sempre 429 upstream pool
        mock_resp = MagicMock(spec=ClientResponse)
        mock_resp.status = 429
        mock_resp.headers = CIMultiDict({"content-type": "application/json"})

        error_body = json.dumps({
            "error": {
                "message": "Provider returned error",
                "code": 429,
                "metadata": {
                    "raw": "rate-limited upstream",
                    "limit_source": "upstream_provider_shared_pool"
                }
            }
        }).encode()

        mock_resp.read = AsyncMock(return_value=error_body)
        mock_resp.release = AsyncMock()
        return mock_resp

    mock_session.post = mock_post

    log_entries = []
    def mock_log(msg):
        log_entries.append(msg)
        print(f"[LOG] {msg}")

    with patch('local_backend.get_local_key', return_value="test-key"):
        with patch('local_backend.get_local_base', return_value="http://localhost:4000"):
            with patch('local_backend.debug_catalog'):
                with patch('local_backend.tool_isolation.filter_tools_for_backend', side_effect=lambda b, p: b):
                    result = await forward_local(
                        request, body, mock_session,
                        model="claude-opus-5",
                        log_fn=mock_log,
                        passthrough=False,
                        upstream_model="ox-alpha"
                    )

    print(f"Call count: {call_count}")
    print(f"Result status: {result.status}")

    # Con 3 backoff steps ci sono 4 tentativi (0,1,2,3), poi esaurisce
    expected_calls = len(OPENROUTER_BACKOFF_STEPS_SEC) + 1  # 4
    assert call_count == expected_calls, f"Expected {expected_calls} calls (all retries exhausted), got {call_count}"
    assert result.status == 429, f"Expected status 429 (error propagated), got {result.status}"

    propagate_log = [e for e in log_entries if "propagating error" in e and "exhausted" in e]
    assert len(propagate_log) == 1, f"Expected 1 exhausted propagation log, got {len(propagate_log)}"

    print("✓ OpenRouter 429 exhausted retries test PASSED")
    print(f"  - {call_count} total calls (all {len(OPENROUTER_BACKOFF_STEPS_SEC)} backoff steps exhausted)")
    print(f"  - Error propagated: 429")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("OpenRouter 429 Upstream Pool Retry Test Suite")
    print("=" * 60)

    try:
        asyncio.run(test_openrouter_429_upstream_retry())
        asyncio.run(test_openrouter_429_exhausted_retries())
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
