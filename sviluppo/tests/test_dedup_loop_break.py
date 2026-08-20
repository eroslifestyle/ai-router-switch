"""Il loop della sessione 74070e0d (2026-08-19, mode gpt) non deve poter tornare.

Due difese indipendenti, una per test:
1. lo shrink ricongiunge i rimandi del dedup al contenuto vero (context_rewrite);
2. se un modello si impianta lo stesso, il router chiude il giro (loop_breaker).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import loop_breaker  # noqa: E402
from context_rewrite import _restore_deduped_reads  # noqa: E402

DEDUP = "Wasted call — file unchanged since your last Read. Refer to that earlier tool_result instead."
VERO = "1\t{\n2\t  \"mcpServers\": {}\n3\t}"


def _read(tool_id, path):
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": path}}]}


def _risultato(tool_id, testo):
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": testo}]}


def _storia():
    """Prima lettura reale, poi due giri in cui il dedup rimanda al messaggio iniziale."""
    msgs = [_read("t0", "/home/mrxxx/.claude/.mcp.json"), _risultato("t0", VERO)]
    for i in (1, 2):
        msgs += [_read(f"t{i}", "/home/mrxxx/.claude/.mcp.json"), _risultato(f"t{i}", DEDUP)]
    return msgs


def _testi_tool_result(msgs):
    return [b.get("content") for m in msgs for b in m.get("content") or []
            if isinstance(b, dict) and b.get("type") == "tool_result"]


def test_lo_shrink_ricongiunge_il_rimando_al_contenuto():
    msgs = _storia()
    # La coda che il modello vedrebbe: solo gli ultimi 2 messaggi, il contenuto vero e' fuori.
    tail = msgs[-2:]
    assert _testi_tool_result(tail) == [DEDUP], "premessa: nella coda c'e' solo il segnaposto"

    restored = _restore_deduped_reads(msgs, tail)

    assert _testi_tool_result(restored) == [VERO], "il contenuto va ripescato dalla storia piena"
    assert _testi_tool_result(msgs[-2:]) == [DEDUP], "la storia originale non va mutata"


def test_senza_contenuto_da_ripescare_la_coda_resta_intatta():
    msgs = [_read("t1", "/x"), _risultato("t1", DEDUP)]
    assert _restore_deduped_reads(msgs, msgs) == msgs


def test_il_router_chiude_il_giro_dopo_n_turni_identici():
    """Loop VERO: corpi CRESCENTI (ogni giro aggiunge assistant + tool_result),
    firma dell'ultimo assistant IDENTICA.

    Il test originale passava lo stesso corpo statico perche' la distinzione
    retry/loop non esisteva ancora. Con il fix 2026-08-20, il corpo deve CRESCERE
    per essere un loop; lo stesso corpo e' un retry (non raggiunge la soglia).
    """
    fp = "sid:test-loop"
    loop_breaker.reset(fp)

    # Storia di base: ogni giro aggiunge un assistant identico + tool_result
    msgs = [
        {"role": "user", "content": "vai"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Capisco. Procedo con l'analisi."},
            {"type": "tool_use", "id": "t0", "name": "Read", "input": {"file_path": "/a"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t0", "content": "contenuto"}]},
    ]

    ripetizioni = []
    for i in range(loop_breaker.LOOP_BREAKER_N):
        # Corpo CRESCE: stesso assistant (firma identica) + nuovo tool_result
        msgs += [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Capisco. Procedo con l'analisi."},
                {"type": "tool_use", "id": f"t{i+1}", "name": "Read", "input": {"file_path": "/a"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i+1}", "content": "contenuto"}]},
        ]
        corpo = json.dumps({"messages": msgs}).encode()
        ripetizioni.append(loop_breaker.check(fp, corpo))

    assert ripetizioni == list(range(1, loop_breaker.LOOP_BREAKER_N + 1))
    assert ripetizioni[-1] >= loop_breaker.LOOP_BREAKER_N, "alla soglia il proxy interrompe"
    loop_breaker.reset(fp)


def test_un_turno_diverso_azzera_il_conteggio():
    fp = "sid:test-vario"
    loop_breaker.reset(fp)
    for i in range(loop_breaker.LOOP_BREAKER_N + 2):
        corpo = json.dumps({"messages": [
            {"role": "assistant", "content": [{"type": "text", "text": f"passo {i}"}]}]}).encode()
        assert loop_breaker.check(fp, corpo) == 1, "turni diversi non sono un loop"
    loop_breaker.reset(fp)


def test_un_retry_dello_stesso_corpo_non_e_un_loop():
    """529 upstream + retry del client produce lo STESSO corpo byte per byte.

    Misurato 2026-08-20: sid:1e8be2ca ha ricevuto 6 retry su tre request-id
    distinti (179db15d10c2, 04946f, 94decd); il breaker li contava come loop
    e ha chiuso la sessione al quarto tentativo.
    """
    fp = "sid:test-retry-529"
    loop_breaker.reset(fp)
    corpo = json.dumps({"messages": [
        {"role": "user", "content": "fammi una modifica"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Procedo."},
            {"type": "tool_use", "id": "t0", "name": "Edit", "input": {"file_path": "/x", "old_string": "a", "new_string": "b"}}]},
    ]}).encode()

    # Piu' retry di LOOP_BREAKER_N: se il fix non ci fosse, reached == LOOP_BREAKER_N
    reached = 0
    for _ in range(loop_breaker.LOOP_BREAKER_N + 3):
        count = loop_breaker.check(fp, corpo)
        if count == loop_breaker.LOOP_BREAKER_N:
            reached = count
            break

    assert reached == 0, (
        f"il retry e' stato contato come loop: {reached} (deve restare 0)"
    )
    loop_breaker.reset(fp)


def test_il_loop_vero_scatta_ancora_con_firma_identica():
    """Loop VERO: a ogni giro il corpo CAMBIA (id del tool_use diverso) ma la firma
    dell'ultimo assistant RESTA IDENTICA perche' _signature() usa name+input, non id.

    Il discriminante: corpo identico = retry (no incremento), corpo diverso ma firma
    uguale = loop (incrementa). Il caso "corpo che cresce davvero" e' gia' coperto da
    test_il_router_chiude_il_giro_dopo_n_turni_identici, che usa msgs += per accumulare.
    """
    fp = "sid:test-loop-firma-identica"
    loop_breaker.reset(fp)

    base = [
        {"role": "user", "content": "vai"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Capisco."},
            {"type": "tool_use", "id": "t0", "name": "Read", "input": {"file_path": "/a"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t0", "content": "contenuto"}]},
    ]

    # LOOP_BREAKER_N giri: stesso assistant (firma identica) + corpo diverso (id diverso)
    last_count = 0
    for i in range(loop_breaker.LOOP_BREAKER_N + 2):
        msgs = list(base)  # ricostruisce da zero a ogni giro
        # L'id cambia a ogni giro ma la firma resta uguale (id non entra in _signature)
        msgs += [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Capisco."},
                {"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": "/a"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": "contenuto"}]},
        ]
        corpo = json.dumps({"messages": msgs}).encode()
        last_count = loop_breaker.check(fp, corpo)

    assert last_count >= loop_breaker.LOOP_BREAKER_N, (
        f"il loop vero non ha raggiunto la soglia: {last_count} (atteso >= {loop_breaker.LOOP_BREAKER_N})"
    )
    loop_breaker.reset(fp)


if __name__ == "__main__":
    test_lo_shrink_ricongiunge_il_rimando_al_contenuto()
    test_senza_contenuto_da_ripescare_la_coda_resta_intatta()
    test_il_router_chiude_il_giro_dopo_n_turni_identici()
    test_un_turno_diverso_azzera_il_conteggio()
    test_un_retry_dello_stesso_corpo_non_e_un_loop()
    test_il_loop_vero_scatta_ancora_con_firma_identica()
    print("OK")
