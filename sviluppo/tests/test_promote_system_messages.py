#!/usr/bin/env python3
"""Contratto di promote_system_messages: il contenuto dei messaggi role=system
non deve andare perso E il campo `system` non deve essere toccato.

Il secondo vincolo e' quello che mancava: fino al 2026-08-16 la funzione
spostava il testo in coda al campo `system`, che sta a monte di tutti i
messaggi. Il prefisso della conversazione cambiava a ogni turno e il prompt
caching veniva invalidato per intero — 670M token-equivalenti in 8 giorni,
il 69% del consumo di mix-am e mix-am-2.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from router_utils import promote_system_messages, _repair_message_sequence  # noqa: E402


def test_system_diventa_user_in_loco():
    body = {
        "system": [{"type": "text", "text": "PROMPT BASE",
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "user", "content": "ciao"},
            {"role": "system", "content": "reminder numero uno"},
            {"role": "assistant", "content": "ok"},
        ],
    }
    prima = json.dumps(body["system"], sort_keys=True)
    n = promote_system_messages(body)

    assert n == 1, n
    assert json.dumps(body["system"], sort_keys=True) == prima, "il campo system e' stato toccato"
    assert [m["role"] for m in body["messages"]] == ["user", "user", "assistant"]
    assert body["messages"][1]["content"] == "reminder numero uno", "contenuto perso"
    assert promote_system_messages.ultimi_caratteri == len("reminder numero uno")


def test_prefisso_stabile_turno_dopo_turno():
    """Il cuore del bug: due turni consecutivi devono condividere il prefisso.

    Turno 2 = turno 1 + un nuovo scambio + un nuovo reminder. Tutto cio' che
    precede il nuovo materiale deve restare IDENTICO dopo la trasformazione.
    """
    def turno(n_reminder):
        msgs = [{"role": "user", "content": "domanda 0"}]
        for i in range(n_reminder):
            msgs.append({"role": "system", "content": f"reminder {i}"})
            msgs.append({"role": "assistant", "content": f"risposta {i}"})
            msgs.append({"role": "user", "content": f"domanda {i + 1}"})
        return {"system": [{"type": "text", "text": "PROMPT BASE"}], "messages": msgs}

    t1, t2 = turno(3), turno(4)
    promote_system_messages(t1)
    promote_system_messages(t2)

    assert t1["system"] == t2["system"], "il system diverge fra i turni: cache invalidata"
    comune = len(t1["messages"])
    assert t2["messages"][:comune] == t1["messages"], "il prefisso dei messaggi e' cambiato"


def test_blocchi_testo_e_contenuto_vuoto():
    body = {"messages": [
        {"role": "user", "content": "ciao"},
        {"role": "system", "content": [{"type": "text", "text": "a"},
                                       {"type": "text", "text": "b"}]},
        {"role": "system", "content": "   "},
    ]}
    n = promote_system_messages(body)

    assert n == 1, n
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == [{"type": "text", "text": "a"},
                                              {"type": "text", "text": "b"}]
    # Il messaggio vuoto resta system: non c'e' nulla da salvare e _repair lo scarta.
    assert body["messages"][2]["role"] == "system"
    assert "system" not in body


def test_repair_non_scarta_i_convertiti():
    """A valle gira _repair_message_sequence: i convertiti devono sopravvivere."""
    body = {"messages": [
        {"role": "user", "content": "ciao"},
        {"role": "system", "content": "reminder che deve sopravvivere"},
        {"role": "assistant", "content": "ok"},
    ]}
    promote_system_messages(body)
    out = _repair_message_sequence(body["messages"])

    testi = [m["content"] for m in out if isinstance(m.get("content"), str)]
    assert "reminder che deve sopravvivere" in testi, out
    assert all(m["role"] != "system" for m in out)


def test_body_senza_messaggi_o_malformato():
    assert promote_system_messages({}) == 0
    assert promote_system_messages({"messages": []}) == 0
    assert promote_system_messages({"messages": [{"role": "user", "content": "x"}]}) == 0
    assert promote_system_messages({"messages": "non-una-lista"}) == 0


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {nome}")
    print("tutti i test passati")
