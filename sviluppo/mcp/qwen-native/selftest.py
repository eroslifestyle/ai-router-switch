#!/usr/bin/env python3
"""Self-test qwen-native: deriva compatible-base, poi smoke LIVE (vision + web search).

Lo smoke live gira solo se una chiave è disponibile (env o vault); altrimenti salta
quelle asserzioni e verifica solo la logica pura. Esegui con il venv del server:
    ./.venv/bin/python selftest.py
"""
import base64

import server as s

# PNG 1x1 (base64 grezzo)
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_compatible_base_pura():
    assert s._anthropic_base().endswith("/apps/anthropic")
    cb = s._compatible_base()
    assert cb.endswith("/compatible-mode/v1"), cb
    assert "/apps/anthropic" not in cb, cb
    print("OK compatible-base:", cb)


def test_image_block_da_base64():
    blk = s._image_block(TINY_PNG)
    assert blk["type"] == "image"
    assert blk["source"]["data"] == TINY_PNG
    print("OK image_block base64")


def _has_key() -> bool:
    try:
        s._api_key()
        return True
    except Exception:
        return False


def test_live_vision():
    if not _has_key():
        print("SKIP live vision (nessuna chiave)")
        return
    out = s._vision_call(TINY_PNG, "Rispondi con una sola parola: che colore domina?")
    assert isinstance(out, str) and out.strip(), "vision vuota"
    print("OK live vision:", out.strip()[:60])


def test_live_web_search():
    if not _has_key():
        print("SKIP live web search (nessuna chiave)")
        return
    out = s._web_search("In una frase: cos'è il protocollo MCP di Anthropic?")
    assert isinstance(out, str) and out.strip(), "web search vuota"
    print("OK live web search:", out.strip()[:80])


if __name__ == "__main__":
    test_compatible_base_pura()
    test_image_block_da_base64()
    test_live_vision()
    test_live_web_search()
    print("\nSELFTEST OK")
