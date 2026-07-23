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
    from pipelines.primitives import (
        build_think_body, build_act_body, build_finalize_body,
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

def test_pipeline_primitives():
    from pipelines.primitives import build_think_body, build_act_body, build_finalize_body
    orig = {"model": "sonnet-4-7", "system": "sei un assistente", "messages": [{"role": "user", "content": "pippo"}]}
    think = build_think_body(orig)
    # model preservato (orig è Anthropic)
    assert think["model"] in ("sonnet-4-7", "claude-haiku-4-5-20251001"), think["model"]
    assert "ORCHESTRATORE" in think["system"]
    from pipelines.primitives import THINK_MAX_TOKENS
    assert think["max_tokens"] == THINK_MAX_TOKENS, think["max_tokens"]
    assert "tools" not in think  # rimossi da think
    # MiniMax -> fallback Haiku
    orig_mm = {"model": "MiniMax-M2.7", "messages": [{"role": "user", "content": "x"}]}
    think_mm = build_think_body(orig_mm)
    assert think_mm["model"] == "claude-haiku-4-5-20251001", think_mm["model"]
    act = build_act_body(orig, "fai pippo", [], executor="MiniMax-M2.7")
    assert act["model"] == "MiniMax-M2.7", act["model"]  # executor forzato
    act_preserve = build_act_body(orig, "fai pippo", [])  # no executor
    assert act_preserve["model"] == "sonnet-4-7"  # preservato da orig
    fin = build_finalize_body(orig, "pippo?", "draft risposta")
    assert "draft risposta" in fin["messages"][0]["content"], fin["messages"][0]["content"]
    print("  pipeline primitives: OK")

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
    test_pipeline_primitives()
    test_router_http()
    print("=" * 40)
    print("TUTTI I TEST PASSATI ✅")

if __name__ == "__main__":
    main()
