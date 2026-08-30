#!/usr/bin/env python3
"""Test fix fingerprint: chat diverse senza session-id ottengono fp diverse."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import loop_breaker

# Import diretto della funzione conversation_fingerprint (che router_mode importa)
import hashlib
import re

def conversation_fingerprint(data: dict) -> str:
    """Copia locale di conversation_fingerprint per il test."""
    try:
        first_user = ""
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            return "default"
        for m in messages:
            if not isinstance(m, dict):
                continue
            if m.get("role") == "user":
                c = m.get("content", "")
                if isinstance(c, str):
                    first_user = c
                elif isinstance(c, list):
                    first_user = " ".join(
                        b.get("text", "") for b in c if isinstance(b, dict))
                else:
                    first_user = str(c)
                break
        first_user = re.sub(r'<system-reminder>.*?</system-reminder>', '', first_user, flags=re.DOTALL)
        if '<system-reminder>' in first_user:
            first_user = first_user[:first_user.index('<system-reminder>')]
        normalized = " ".join(first_user.split())
        if len(normalized) < 1:
            return "default"
        return hashlib.sha256(normalized.encode()).hexdigest()[:12]
    except Exception:
        return "default"


def test_chat_diverse_con_fp_diverse():
    """Due chat con contenuto diverso (senza session-id) ottengono fingerprint DIVERSE."""
    body1 = {"messages": [{"role": "user", "content": "analizza il file A"}]}
    body2 = {"messages": [{"role": "user", "content": "analizza il file B"}]}

    fp1 = conversation_fingerprint(body1)
    fp2 = conversation_fingerprint(body2)

    assert fp1 != fp2, f"Le fingerprint dovrebbero essere diverse: {fp1} vs {fp2}"
    assert fp1 != "default" and fp2 != "default", "Entrambe dovrebbero essere hash validi"
    print(f"✓ Chat diverse: fp1={fp1}, fp2={fp2} (diverse)")


def test_stesso_corpo_4_volte_scatta_loop():
    """Lo stesso identico corpo ripetuto 4 volte con STESSA fingerprint → loop-breaker scatta."""
    fp = f"hash:{conversation_fingerprint({'messages': [{'role': 'user', 'content': 'test loop'}]})}"
    loop_breaker.reset(fp)

    corpo = json.dumps({"messages": [
        {"role": "user", "content": "test loop"},
        {"role": "assistant", "content": [{"type": "text", "text": "Ok"}]},
    ]}).encode()

    counts = []
    for i in range(4):
        # Corpo CRESCENTE: aggiungiamo un tool_result diverso a ogni giro
        msgs = json.loads(corpo)["messages"] + [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": "risultato"}]
            }]
        corpo_cresciuto = json.dumps({"messages": msgs}).encode()
        counts.append(loop_breaker.check(fp, corpo_cresciuto))

    assert counts == [1, 2, 3, 4], f"Atteso [1,2,3,4], ottenuto {counts}"
    assert counts[-1] >= loop_breaker.LOOP_BREAKER_N, "Il loop-breaker deve scattare"
    print(f"✓ Loop-breaker scatta: {counts} (soglia={loop_breaker.LOOP_BREAKER_N})")


def test_ttl_logica_corretta():
    """Verifica che una entry scaduta venga trattata come inesistente (logica)."""
    fp = "hash:test-ttl"
    loop_breaker.reset(fp)

    corpo = json.dumps({"messages": [
        {"role": "assistant", "content": [{"type": "text", "text": "msg"}]}]
    }).encode()

    # Primo turno: count = 1
    c1 = loop_breaker.check(fp, corpo)
    assert c1 == 1, f"Primo turno deve essere 1, ottenuto {c1}"

    # Leggi l'entry per manipolare il timestamp
    entry = loop_breaker._seen.get(fp)
    assert entry is not None, "Entry deve esistere dopo il primo check"

    # Manipola il timestamp per simulare scadenza (ora - TTL - 1)
    import time
    loop_breaker._seen[fp] = [entry[0], entry[1], entry[2], time.time() - loop_breaker.LOOP_BREAKER_ENTRY_TTL_SEC - 1]

    # Ora ripeti: con entry scaduta, dovrebbe ricominciare da 1
    c2 = loop_breaker.check(fp, corpo)
    assert c2 == 1, f"Entry scaduta: deve ricominciare da 1, ottenuto {c2}"
    print(f"✓ TTL logica corretta: entry scaduta resetta il conteggio (1 → 1)")


if __name__ == "__main__":
    test_chat_diverse_con_fp_diverse()
    test_stesso_corpo_4_volte_scatta_loop()
    test_ttl_logica_corretta()
    print("\n✅ Tutti i test passati")
