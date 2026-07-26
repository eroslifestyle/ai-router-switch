#!/usr/bin/env python3
"""Test per il sistema context guardrails."""

import sys
import os
import json

sys.path.insert(0, "src")

from token_counter import estimate_tokens_body, IMAGE_TOKEN_COST
from context_manager import ContextManager
from context_rewrite import rewrite_for_context, _degrade_images_and_tools, KEEP_RECENT_IMAGES, TOOL_RESULT_MAX_CHARS


def test_g3_image_aware():
    """Verifica conteggio token con immagini."""
    # Body con immagine base64 grande
    body_dict = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Ciao"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 1_400_000}}
                ]
            }
        ]
    }
    body = json.dumps(body_dict).encode()
    tokens = estimate_tokens_body(body)
    assert tokens < 5000, f"Token stimati {tokens} troppo alto per body con immagine singola"
    assert tokens >= IMAGE_TOKEN_COST, f"Token {tokens} minori del costo immagine {IMAGE_TOKEN_COST}"
    
    # Body senza immagini
    body2 = json.dumps({"messages": [{"role": "user", "content": "X" * 936_000}]}).encode()
    tokens2 = estimate_tokens_body(body2)
    assert abs(tokens2 - len(body2) // 4) <= 1, f"Token stimati {tokens2} non matchano len/4={len(body2)//4}"
    
    # Body non JSON
    tokens3 = estimate_tokens_body(b"non-json-affatto")
    assert tokens3 >= 1, f"Token per non-JSON {tokens3} deve essere >= 1"
    
    print("  test_g3_image_aware: OK")


def test_g2_limit_per_provider():
    """Verifica limiti per provider diversi."""
    db = f"/tmp/test_ctx_guard_{os.getpid()}.db"
    try:
        os.remove(db)
    except FileNotFoundError:
        pass
    
    try:
        cm = ContextManager(db_path=db)
        body = json.dumps({"messages": [{"role": "user", "content": "X" * 936_000}]}).encode()
        
        # Provider con limite 1M
        r1 = cm.pre_check("fp-a", "mix-am", len(body), model="claude-opus-4-8", body=body)
        assert r1["limit"] == 1_000_000, f"Limite Claude {r1['limit']} != 1_000_000"
        assert r1["action"] == "ok", f"Action Claude {r1['action']} != 'ok'"
        
        # Provider con limite 200k
        r2 = cm.pre_check("fp-a", "mix-am", len(body), model="MiniMax-M2.7", body=body)
        assert r2["limit"] == 200_000, f"Limite MiniMax {r2['limit']} != 200_000"
        assert r2["action"] == "error", f"Action MiniMax {r2['action']} != 'error'"
        
        # Retrocompatibilita senza parametri nuovi
        r3 = cm.pre_check("fp-b", "mix-am", len(body))
        assert isinstance(r3, dict), "Risultato non e' dict"
        assert "action" in r3, "Manca chiave action"
        assert "est_tokens" in r3, "Manca chiave est_tokens"
        assert "limit" in r3, "Manca chiave limit"
        assert "pct" in r3, "Manca chiave pct"
    finally:
        try:
            os.remove(db)
        except FileNotFoundError:
            pass
    
    print("  test_g2_limit_per_provider: OK")


def test_g5_degradazione():
    """Verifica degradazione immagini e tool result."""
    msgs = [
        {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "B" * 10000}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "R" * 20000}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "S" * 20000}]}]},
        {"role": "assistant", "content": "recente 1"},
        {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "C" * 10000}}]}
    ]
    
    res = _degrade_images_and_tools({"messages": msgs}, msgs, KEEP_RECENT_IMAGES, TOOL_RESULT_MAX_CHARS)
    
    # Immagine vecchia degradata a text
    assert res["messages"][0]["content"][0]["type"] == "text", "Tipo non e' text"
    assert "immagine rimossa" in res["messages"][0]["content"][0]["text"], "Testo non contiene 'immagine rimossa'"
    
    # Tool result troncato
    assert len(res["messages"][1]["content"][0]["content"]) <= TOOL_RESULT_MAX_CHARS + 20, \
        f"Tool result 1 non troncato: {len(res['messages'][1]['content'][0]['content'])}"
    assert len(res["messages"][2]["content"][0]["content"][0]["text"]) <= TOOL_RESULT_MAX_CHARS + 20, \
        f"Tool result 2 non troncato: {len(res['messages'][2]['content'][0]['content'][0]['text'])}"
    
    # Ultimi KEEP_RECENT_IMAGES=2 restano intatti
    assert res["messages"][4]["content"][0]["type"] == "image", "Immagine recente non intatta"
    
    # Verifica NON mutazione input
    assert msgs[0]["content"][0]["type"] == "image", "Input mutato: tipo immagine cambiato"
    assert len(msgs[1]["content"][0]["content"]) == 20000, "Input mutato: tool result allungato"
    
    print("  test_g5_degradazione: OK")


def test_g5_rewrite_end_to_end():
    """Verifica rewrite end-to-end su testo oltre soglia + immagini.

    Il peso deve venire dal TESTO: dopo G3 i byte base64 non contano piu' nella
    stima, quindi un body fatto di sole immagini resta sotto il safe limit e non
    va riscritto (comportamento corretto, non un bug).
    """
    msgs = []
    for i in range(8):
        if i % 3 == 0:
            # Ogni terzo messaggio ha immagine (da degradare in ATTEMPT 1b)
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "D" * 400_000}}]
        else:
            content = "messaggio lungo " + "X" * 200_000
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": content})

    body = json.dumps({"model": "MiniMax-M2.7", "messages": msgs}).encode()
    new_body, changed = rewrite_for_context(body, "MiniMax-M2.7", "fp-test")
    
    assert changed is True, "Changed deve essere True"
    assert len(new_body) < len(body), f"Body non ridotto: {len(new_body)} >= {len(body)}"
    
    parsed = json.loads(new_body)
    assert "messages" in parsed, "Missing messages in result"
    assert len(parsed["messages"]) > 0, "Messages vuoto"
    
    print("  test_g5_rewrite_end_to_end: OK")


def test_g1_nessun_400_sintetico():
    """Verifica che il router non emetta 400 sintetici."""
    router_path = os.path.join("src", "ai-router-proxy.py")
    with open(router_path, "r") as f:
        contenuto = f.read()
    
    # Il router non deve proporre /compact come fallback interno
    assert "Usa /compact." not in contenuto, \
        "Router emette ancora 400 con suggerimento /compact (non deve)"
    
    # Deve esserci il gate osservatore
    assert "gate osservatore" in contenuto, \
        "Manca il gate osservatore nel router"
    
    print("  test_g1_nessun_400_sintetico: OK")


def main():
    print("=" * 40)
    test_g3_image_aware()
    test_g2_limit_per_provider()
    test_g5_degradazione()
    test_g5_rewrite_end_to_end()
    test_g1_nessun_400_sintetico()
    print("=" * 40)
    print("TUTTI I TEST PASSATI")


if __name__ == "__main__":
    main()
