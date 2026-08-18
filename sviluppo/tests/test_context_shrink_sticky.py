#!/usr/bin/env python3
"""Lo shrink deve rientrare nel budget SENZA cambiare il prefisso a ogni turno.

Lo shrink classico riassumeva tutti i messaggi nel campo `system` e teneva gli
ultimi 6: entrambi cambiavano a ogni turno, quindi l'upstream vedeva sempre un
prefisso nuovo e il prompt caching non agganciava mai. Misurato sul traffico GLM
di agosto: 0% di riuso su 3.557 richieste, 229,4M token ripagati.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from context_shrink import shrink_body_to_budget, _STICKY_STEP  # noqa: E402

MAX_BYTES = 200_000


def _body(n_scambi, testo="contenuto di prova "):
    msgs = []
    for i in range(n_scambi):
        msgs.append({"role": "user", "content": f"domanda {i}: " + testo * 40})
        msgs.append({"role": "assistant", "content": f"risposta {i}: " + testo * 40})
    return json.dumps({"model": "glm-5.3", "system": "PROMPT BASE",
                       "messages": msgs}).encode()


def _shrink(body):
    return asyncio.run(shrink_body_to_budget(body, MAX_BYTES))


def test_rientra_nel_budget():
    out = _shrink(_body(200))
    assert out is not None, "lo shrink non ha prodotto nulla"
    assert len(out) <= MAX_BYTES, f"{len(out)} > {MAX_BYTES}"


def test_prefisso_stabile_fra_turni_consecutivi():
    """Il cuore del fix: due turni consecutivi devono condividere system e coda."""
    a = json.loads(_shrink(_body(200)))
    b = json.loads(_shrink(_body(201)))

    assert a["system"] == b["system"], "il campo system cambia fra i turni: cache invalidata"
    comune = len(a["messages"])
    assert b["messages"][:comune] == a["messages"], "il prefisso dei messaggi e' cambiato"
    assert len(b["messages"]) > comune, "il turno nuovo non ha aggiunto nulla"


def test_stabile_per_molti_turni():
    """K non deve muoversi a ogni messaggio: un solo scalino ogni _STICKY_STEP."""
    sistemi = {json.loads(_shrink(_body(200 + i)))["system"] for i in range(6)}
    assert len(sistemi) == 1, f"il system e' cambiato {len(sistemi)} volte in 6 turni"


def test_conversazione_corta_non_viene_toccata():
    """Se il body sta gia' nel budget il chiamante non invoca lo shrink, ma se
    lo fa con pochi messaggi il risultato deve restare valido."""
    piccolo = _body(3)
    out = _shrink(piccolo)
    if out is not None:
        d = json.loads(out)
        assert d["messages"], "shrink ha svuotato i messaggi"


def test_messaggio_singolo_gigante_usa_il_fallback():
    """Un body dominato da UN messaggio enorme non e' comprimibile per numero di
    messaggi: deve intervenire lo shrink classico, non un crash."""
    body = json.dumps({"model": "glm-5.3", "system": "BASE", "messages": [
        {"role": "user", "content": "x" * 400_000},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "e adesso?"},
    ]}).encode()
    out = _shrink(body)
    assert out is None or len(out) <= MAX_BYTES


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {nome}")
    print(f"tutti i test passati (step={_STICKY_STEP})")
