#!/usr/bin/env python3
"""Il prefisso (system + messages) deve restare byte-identico tra turni.

Quando la conversazione supera il budget, il taglio sticky avanzava di pochi
messaggi a ogni turno (finestra scorrevole) e il summary veniva ricalcolato
sull'intera storia: prefisso nuovo a ogni richiesta, prompt cache distrutta.
Con il headroom, il punto di taglio avanza a scatti e il summary e' in cache
per (fp, drop_count): il prefisso cambia solo quando cambia drop_count.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import context_rewrite  # noqa: E402
from context_rewrite import rewrite_for_context  # noqa: E402
REWRITE_HEADROOM_FRACTION = getattr(context_rewrite, "REWRITE_HEADROOM_FRACTION", 0.25)

MODEL = "claude-haiku-4-5"  # 200k finestra, tokenizer 3.5 b/t: budget noto e stretto
TURNO = "contenuto di prova " * 3000  # ~60 KB a turno: supera il budget in pochi turni


def _body(n_scambi):
    msgs = []
    for i in range(n_scambi):
        msgs.append({"role": "user", "content": f"domanda {i}: " + TURNO})
        msgs.append({"role": "assistant", "content": f"risposta {i}: " + TURNO})
    return json.dumps({"model": MODEL, "system": "PROMPT BASE",
                       "max_tokens": 1024, "messages": msgs}).encode()


def setup_module(module):
    context_rewrite._STICKY_DROP_COUNT.clear()
    if hasattr(context_rewrite, "_STICKY_SUMMARY"):
        context_rewrite._STICKY_SUMMARY.clear()


def test_prefisso_stabile_su_30_turni():
    fp = "test-fp-stabile"
    prev = None
    prev_drop_reale = None
    cambi_system_su_turno_stabile = 0
    turni_stabili = 0
    rewrite_visti = 0

    for turno in range(20, 50):  # cresce di un turno (2 messaggi) alla volta
        out, rewritten = rewrite_for_context(_body(turno), MODEL, fp)
        data = json.loads(out)
        assert rewritten, f"turno {turno}: nessun rewrite"

        # (a) il body riscritto sta nel budget: ricaduta su safe limit del modello
        from model_context_map import get_safe_input_limit
        from token_counter import estimate_tokens_body
        assert estimate_tokens_body(out, MODEL) <= get_safe_input_limit(MODEL, 1024)

        drop = context_rewrite._STICKY_DROP_COUNT.get(fp, 0)
        # il taglio salvato deve coincidere col primo messaggio realmente
        # emesso (la riparazione della sequenza puo' scartare un assistant
        # orfano in testa, quindi si cerca l'indice vero nella storia piena)
        full = json.loads(_body(turno))["messages"]
        first = data["messages"][0]["content"]
        drop_reale = next(i for i, m in enumerate(full) if m["content"] == first)

        if prev is not None and rewritten:
            rewrite_visti += 1
            if drop_reale == prev_drop_reale:
                turni_stabili += 1
                # (b) prefisso byte-identico quando drop_count non cambia
                if data["system"] != prev["system"]:
                    cambi_system_su_turno_stabile += 1
                comune = len(prev["messages"])
                assert data["messages"][:comune] == prev["messages"], \
                    f"turno {turno}: prefisso messaggi cambiato a drop invariato ({drop_reale})"
        prev, prev_drop_reale = data, drop_reale

    assert rewrite_visti > 10, "mai entrati in regime di rewrite"
    stabilita = turni_stabili / max(1, rewrite_visti - 1)
    # Il taglio avanza a scatti (finestra con margine REWRITE_HEADROOM_FRACTION):
    # con turni grandi rispetto al margine il ciclo stabile e' di 2-3 turni,
    # quindi la soglia conta il comportamento a scatti, non la perfezione.
    assert stabilita >= 0.6, f"prefisso stabile solo nel {stabilita:.0%} dei turni"
    assert turni_stabili >= 10, "mai stabilizzato per almeno 10 turni"
    assert cambi_system_su_turno_stabile == 0, \
        f"system cambiato in {cambi_system_su_turno_stabile} turni con drop invariato"


def test_headroom_costante():
    assert 0.0 < REWRITE_HEADROOM_FRACTION < 0.5


def test_recall_block_stabile_con_drop_invariato(monkeypatch):
    """Il recall block dipende dall'ultimo messaggio (query): senza cache cambia
    a ogni turno e rompe il system anche a drop_count invariato."""
    import context_recall

    fp = "test-fp-recall"
    chiamate = []

    def fake_recall(fp_, msgs, dropped, budget_chars):
        chiamate.append(len(chiamate))
        ultimo = msgs[-1].get("content", "")
        return f"ESTRATTI PER: {ultimo[-40:]}"  # dipende dalla richiesta corrente

    monkeypatch.setattr(context_recall, "RECALL_ENABLED", True)
    monkeypatch.setattr(context_recall, "build_recall_block", fake_recall)
    monkeypatch.setattr(context_recall, "archive", lambda fp_, dropped: None)

    prev = None
    prev_drop = None
    for turno in range(20, 40):
        out, rewritten = rewrite_for_context(_body(turno), MODEL, fp)
        data = json.loads(out)
        assert rewritten
        drop = context_rewrite._STICKY_DROP_COUNT.get(fp, 0)
        if prev is not None and drop == prev_drop:
            assert data["system"] == prev["system"], \
                f"turno {turno}: system cambiato con recall a drop invariato ({drop})"
        prev, prev_drop = data, drop
    assert len(chiamate) < 20, "build_recall_block chiamato a ogni turno: cache non usata"
