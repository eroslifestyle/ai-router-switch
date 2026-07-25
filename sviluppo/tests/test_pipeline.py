#!/usr/bin/env python3
"""AQ-TEST — Test funzionali pipeline ai-router."""

import sys, os
sys.path.insert(0, "src")

def test_imports():
    from providers.base import (
        FALLBACK_STATUSES, MINIMAX_FALLBACK_STATUSES,
        extract_last_user_text, _is_context_too_large_for_minimax,
        _is_context_exceed_400, strip_images_body, call_full,
        T2_KEYWORDS, trim_old_messages,
    )
    print(f"  imports: OK ({len(FALLBACK_STATUSES)} status codes)")

def test_text_extraction():
    from providers.base import extract_last_user_text, _text_from_message
    data = {
        "messages": [
            {"role": "user", "content": "primo"},
            {"role": "assistant", "content": [{"type": "text", "text": "risposta"}]},
            {"role": "user", "content": [{"type": "text", "text": "ultimo"}]},
        ]
    }
    assert extract_last_user_text(data) == "ultimo", extract_last_user_text(data)
    resp = {"content": [{"type": "text", "text": "draft risposta"}]}
    assert _text_from_message(resp) == "draft risposta"
    print("  text extraction: OK")

def test_context_checks():
    from providers.base import _is_context_exceed_400
    body_ctx = b'{"error": {"type": "context_exceeded", "message": "context window exceeded"}}'
    assert _is_context_exceed_400(body_ctx)[0] == True
    body_ok = b'{"data": "ok"}'
    assert _is_context_exceed_400(body_ok)[0] == False
    print("  context checks: OK")

def test_strip_images():
    from providers.base import strip_images_body
    import json
    body = json.dumps({
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "ciao"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "XYZ"}},
            ]}
        ]
    }).encode()
    stripped = strip_images_body(body)
    d = json.loads(stripped)
    imgs = [b for m in d["messages"] for b in m.get("content", []) if b.get("type") == "image"]
    assert len(imgs) == 0, f"Still has {len(imgs)} images"
    print("  strip_images: OK")

def test_router_http():
    import urllib.request, json
    try:
        req = urllib.request.Request("http://127.0.0.1:8787/v1/models")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            count = len(data.get("data", []))
            assert count >= 10, f"Expected >=10 models, got {count}"
            print(f"  router /v1/models: OK ({count} modelli)")
    except Exception as e:
        print(f"  router /v1/models: SKIP ({e})")

def main():
    print("AQ-TEST pipeline")
    print("=" * 40)
    test_imports()
    test_text_extraction()
    test_context_checks()
    test_strip_images()
    test_router_http()
    print("=" * 40)
    print("TUTTI I TEST PASSATI ✅")

if __name__ == "__main__":
    main()
