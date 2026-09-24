#!/usr/bin/env python3
"""Test perfmon_correlate — assert + __main__, zero framework."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import perfmon_correlate as pc


def test_percentile():
    v = list(range(1, 11))  # gia' ordinata
    assert pc._percentile(v, 50) == 5
    assert pc._percentile(v, 0) == 1
    assert pc._percentile(v, 100) == 10
    assert pc._percentile([], 50) is None
    assert pc._percentile([7], 99) == 7


def _sess(tool_calls=None, stops=None, proxy=None):
    s = {"tool_calls": tool_calls or {}, "hook_errors": [],
         "subagent_stops": stops or []}
    if proxy:
        s["proxy_requests"] = proxy
    return s


def test_fantasma_subagent_chiuso():
    now = 1_000_000.0
    s = _sess(
        tool_calls={"t1": {"tool_name": "Agent", "agent_id": "a1",
                           "agent_type": "coder", "start_ts": now - 600}},
        stops=[{"event": "subagent_stop", "agent_id": "a1", "ts": now - 590}],
    )
    # simula la logica di classificazione (stessa di _default_mode)
    stop_ts = {"a1": now - 590}
    tc = s["tool_calls"]["t1"]
    vivi, fantasmi = [], []
    elapsed = now - tc["start_ts"]
    rec = {"tool_use_id": "t1", "elapsed_sec": elapsed}
    if stop_ts.get(tc["agent_id"], 0) > tc["start_ts"]:
        rec["motivo"] = "subagent_chiuso"
        fantasmi.append(rec)
    elif elapsed > pc.RUNNING_STALE_SEC:
        rec["motivo"] = "troppo_vecchio"
        fantasmi.append(rec)
    else:
        vivi.append(rec)
    assert not vivi and fantasmi[0]["motivo"] == "subagent_chiuso"


def test_sessione_proxy_only_idle_vuoto():
    s = _sess(proxy=[{"ts": 100.0, "total_ms": 500}])
    assert pc._idle_gaps(s) == []


def test_attributione_inferenza():
    # gap [0, 100] coperto da proxy_request [10, 90] (total_ms 80000) -> tutto inferenza
    s = _sess(
        tool_calls={"a": {"tool_name": "Read", "start_ts": 0.0, "end_ts": 100.0},
                    "b": {"tool_name": "Edit", "start_ts": 200.0, "end_ts": 210.0}},
        proxy=[{"ts": 10.0, "total_ms": 80_000}],
    )
    g_tot, g_inf, g_user, g_nsp = pc._idle_attribution(s)
    # gap e' [100,200] = 100 s; inferenza copre [10,90] -> overlap 0?
    # no: il gap sta fra end del primo (100) e start del secondo (200);
    # inferenza [10,90] non copre -> ridefinisco: proxy dentro il gap
    s2 = _sess(
        tool_calls={"a": {"tool_name": "Read", "start_ts": 0.0, "end_ts": 100.0},
                    "b": {"tool_name": "Edit", "start_ts": 200.0, "end_ts": 210.0}},
        proxy=[{"ts": 120.0, "total_ms": 50_000}],  # [120,170] dentro [100,200]
    )
    _, inf, user, nsp = pc._idle_attribution(s2)
    assert inf == 50_000.0
    assert user == 0.0 and nsp == 50_000.0  # resto 50s < 120s -> non_spiegato
    # gap lungo senza copertura -> attesa_utente
    s3 = _sess(
        tool_calls={"a": {"tool_name": "Read", "start_ts": 0.0, "end_ts": 100.0},
                    "b": {"tool_name": "Edit", "start_ts": 400.0, "end_ts": 410.0}},
    )
    _, _, user3, nsp3 = pc._idle_attribution(s3)
    assert user3 == 300_000.0 and nsp3 == 0.0


def test_max_idle_gaps_lista_non_statistiche():
    # 60 gap: la lista esportata e' troncata, ma idle.dist/somma coprono tutti
    tc = {}
    for i in range(61):
        tc[f"t{i}"] = {"tool_name": "Bash", "start_ts": i * 200.0,
                       "end_ts": i * 200.0 + 100.0}
    s = _sess(tool_calls=tc)
    gaps = pc._idle_gaps(s)
    assert len(gaps) == 60
    export = gaps[-pc.MAX_IDLE_GAPS:]
    assert len(export) == pc.MAX_IDLE_GAPS
    assert sum(g["gap_ms"] for g in gaps) > sum(g["gap_ms"] for g in export)


def test_blocchi_parallelismo():
    # 3 tool_start entro 150ms (un blocco da 3) + 1 isolato (blocco da 1)
    base = 1_000_000.0
    s = _sess(tool_calls={
        "a": {"tool_name": "Read", "start_ts": base},
        "b": {"tool_name": "Edit", "start_ts": base + 0.05},
        "c": {"tool_name": "Bash", "start_ts": base + 0.12},
        "d": {"tool_name": "Read", "start_ts": base + 10.0},
    })
    par = pc._blocchi_parallelismo(s, p50_gap_ms=4000.0)
    assert par["blocchi"] == 2, par
    assert par["istogramma"] == {"3": 1, "1": 1}, par
    assert par["media_tool_per_blocco"] == 2.0, par
    # 1 blocco singolo -> 0.5 round-trip eliminati * p50 gap
    assert par["risparmio_stimato_ms"] == 2000.0, par
    # nessun tool -> nessun blocco
    assert pc._blocchi_parallelismo(_sess(), 4000.0) is None


def test_chiusura_orfani_subagent_stop():
    # orfano con stop successivo -> chiuso (stima) al PRIMO stop DOPO il start,
    # senza mutare end_ts/duration_ms osservati
    now = 1_000_000.0
    s = _sess(
        tool_calls={"t1": {"tool_name": "Agent", "agent_id": "a1",
                           "start_ts": now - 600}},
        stops=[{"event": "subagent_stop", "agent_id": "a1", "ts": now - 590},
               {"event": "subagent_stop", "agent_id": "a1", "ts": now - 580}],
    )
    n, fb = pc._chiudi_orfani_stimati(s)
    assert n == 1 and fb == 0
    tc = s["tool_calls"]["t1"]
    assert tc["closed_by"] == "subagent_stop"
    assert tc["end_ts_stimato"] == now - 590  # primo stop successivo
    assert tc["duration_ms_stimata"] == 10_000.0
    assert "end_ts" not in tc and "duration_ms" not in tc  # osservati intatti

    # stop di un agente DIVERSO -> non chiude
    s1 = _sess(
        tool_calls={"t1": {"tool_name": "Agent", "agent_id": "a1",
                           "start_ts": now - 600}},
        stops=[{"event": "subagent_stop", "agent_id": "altro", "ts": now - 590}],
    )
    assert pc._chiudi_orfani_stimati(s1) == (0, 0)
    assert s1["tool_calls"]["t1"].get("end_ts_stimato") is None

    # orfano SENZA agent_id: fallback stop di sessione, contato a parte
    s2 = _sess(
        tool_calls={"t1": {"tool_name": "Bash", "start_ts": now - 600}},
        stops=[{"event": "subagent_stop", "agent_id": "a1", "ts": now - 590}],
    )
    assert pc._chiudi_orfani_stimati(s2) == (1, 1)
    assert s2["tool_calls"]["t1"]["closed_by"] == "subagent_stop"

    # orfano senza stop successivo -> resta orfano
    s3 = _sess(
        tool_calls={"t1": {"tool_name": "Bash", "start_ts": now - 600}},
        stops=[{"event": "subagent_stop", "agent_id": "a1", "ts": now - 700}],
    )
    assert pc._chiudi_orfani_stimati(s3) == (0, 0)
    assert s3["tool_calls"]["t1"].get("end_ts") is None
    assert s3["tool_calls"]["t1"].get("end_ts_stimato") is None

    # stop precedente al tool_start -> non chiude
    s4 = _sess(
        tool_calls={"t1": {"tool_name": "Bash", "agent_id": "a1",
                           "start_ts": now - 100}},
        stops=[{"event": "subagent_stop", "agent_id": "a1", "ts": now - 200}],
    )
    assert pc._chiudi_orfani_stimati(s4) == (0, 0)
    assert s4["tool_calls"]["t1"].get("end_ts") is None

    # tool gia' chiuso normalmente -> intoccato
    s5 = _sess(
        tool_calls={"t1": {"tool_name": "Read", "start_ts": now - 50,
                           "end_ts": now - 40}},
        stops=[{"event": "subagent_stop", "agent_id": "a1", "ts": now - 30}],
    )
    assert pc._chiudi_orfani_stimati(s5) == (0, 0)
    assert "closed_by" not in s5["tool_calls"]["t1"]


if __name__ == "__main__":
    test_percentile()
    test_fantasma_subagent_chiuso()
    test_sessione_proxy_only_idle_vuoto()
    test_attributione_inferenza()
    test_max_idle_gaps_lista_non_statistiche()
    test_blocchi_parallelismo()
    test_chiusura_orfani_subagent_stop()
    print("OK: 7/7 test passati")
