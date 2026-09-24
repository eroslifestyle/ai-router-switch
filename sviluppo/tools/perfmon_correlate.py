#!/usr/bin/env python3
"""perfmon correlate — componente (b) aggregazione/correlazione.

Default: legge .claude/perfmon/events/orchestration.jsonl (hook (a)) + sidecar
proxy via airouter_info._leggi_sidecar, correla per session_id (tool_use_id e'
gia' la chiave nativa dei tool-call), scrive .claude/perfmon/aggregates/latest.json.

--backfill: passata separata SOLO sullo storico proxy -> baseline_proxy_only.json.

ponytail: riuso deliberato delle funzioni "private" di airouter_info (stessa
cartella tools/) per non duplicare ~100 righe di gestione rotazioni sidecar.
"""
import json
import math
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / ".claude" / "perfmon" / "events" / "orchestration.jsonl"
AGG_DIR = ROOT / ".claude" / "perfmon" / "aggregates"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import airouter_info  # noqa: E402  (riuso lettura sidecar con rotazioni)

RUNNING_THRESHOLD_SEC = 5
MAX_IDLE_GAPS = 50
RUNNING_STALE_SEC = 900
IDLE_USER_SEC = 120
MAX_CALLS = 3000

CALLS_SCHEMA = ["ts", "tool", "dur_ms", "agent_type", "session8", "status"]

IDLE_GAPS_NOTE = (
    "stima indiretta della latenza fra eventi (handoff fra modelli diversi O "
    "tempo di 'pensiero' del modello THINK prima del tool-call successivo) — "
    "nessun hook misura l'inferenza pura, questo e' un limite noto, non una "
    "misura esatta"
)


def _percentile(values_sorted, p):
    """Nearest-rank: ceil(p/100*n)-1, clampato."""
    if not values_sorted:
        return None
    if p <= 0:
        return values_sorted[0]
    idx = math.ceil(p / 100 * len(values_sorted)) - 1
    return values_sorted[max(0, min(len(values_sorted) - 1, idx))]


def _dist(values):
    """{count, min, p50, p90, p99, max} sui valori non-None, ordinati."""
    v = sorted(x for x in values if x is not None)
    return {
        "count": len(v),
        "min": _percentile(v, 0),
        "p50": _percentile(v, 50),
        "p90": _percentile(v, 90),
        "p99": _percentile(v, 99),
        "max": _percentile(v, 100),
    }


def _error_stats(items):
    """Conta risposte 429 e 5xx sul totale (status HTTP gia' nel sidecar/proxy_requests)."""
    statuses = [it.get("status") for it in items]
    n_429 = sum(1 for s in statuses if s == 429)
    n_5xx = sum(1 for s in statuses if isinstance(s, int) and 500 <= s < 600)
    total = len(statuses)
    return {
        "n_429": n_429,
        "n_5xx": n_5xx,
        "error_rate_pct": round(100 * (n_429 + n_5xx) / total, 1) if total else 0.0,
    }


def _leggi_eventi():
    """Tutte le righe valide di orchestration.jsonl + numero righe scartate."""
    eventi, scartate = [], 0
    if not EVENTS.exists():
        return eventi, scartate
    with EVENTS.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                eventi.append(json.loads(line))
            except Exception:
                scartate += 1
    return eventi, scartate


def _raggruppa_sessioni(eventi):
    """sessions[session_id] = {tool_calls, hook_errors, subagent_stops}."""
    sessions = {}
    for ev in eventi:
        sid = ev.get("session_id")
        if not sid:
            continue
        s = sessions.setdefault(
            sid, {"tool_calls": {}, "hook_errors": [], "subagent_stops": []}
        )
        kind = ev.get("event")
        if kind == "tool_start":
            tc = s["tool_calls"].setdefault(
                ev.get("tool_use_id"),
                {"tool_name": ev.get("tool_name"), "agent_id": ev.get("agent_id"),
                 "agent_type": ev.get("agent_type")},
            )
            tc["start_ts"] = ev.get("ts")
            tc.setdefault("tool_name", ev.get("tool_name"))
        elif kind == "tool_end":
            # entry creata anche se manca il tool_start (eventi tagliati da rotazione)
            tc = s["tool_calls"].setdefault(
                ev.get("tool_use_id"),
                {"tool_name": ev.get("tool_name"), "agent_id": ev.get("agent_id"),
                 "agent_type": ev.get("agent_type")},
            )
            tc["end_ts"] = ev.get("ts")
            tc["duration_ms"] = ev.get("duration_ms")
            tc["status"] = ev.get("status")
            tc.setdefault("tool_name", ev.get("tool_name"))
        elif kind == "hook_error":
            s["hook_errors"].append(ev)
        elif kind == "subagent_stop":
            s["subagent_stops"].append(ev)
    return sessions


def _aggiungi_proxy(sessions, righe_proxy):
    for riga in righe_proxy:
        chat = riga.get("chat") or ""
        if not chat.startswith("sid:"):
            continue
        sid = chat[4:]
        s = sessions.setdefault(
            sid, {"tool_calls": {}, "hook_errors": [], "subagent_stops": []}
        )
        s.setdefault("proxy_requests", []).append({
            "ts": riga.get("ts"),
            "mode": riga.get("mode"),
            "model": riga.get("final"),
            "status": riga.get("status"),
            "outcome": riga.get("outcome"),
            "ttfb_ms": riga.get("ttfb_ms"),
            "total_ms": riga.get("total_ms"),
            "cache_read": riga.get("cache_read"),
            "cache_creation": riga.get("cache_creation"),
            "input_tokens": riga.get("input_tokens"),
            "output_tokens": riga.get("output_tokens"),
        })


def _idle_gaps(s):
    """Span SOLO dai tool_call (i proxy_requests vengono dal sidecar a 24h e
    mescolano finestre incompatibili: causavano gap di ore in sessioni di minuti)."""
    spans = [(tc.get("start_ts"), tc.get("end_ts"))
             for tc in s["tool_calls"].values()]
    spans = [sp for sp in spans if sp[0] is not None]
    if not spans:
        return []
    spans.sort(key=lambda sp: sp[0])
    gaps = []
    for (_, e1), (s2, _e2) in zip(spans, spans[1:]):
        if e1 is None:
            continue
        gap_ms = (s2 - e1) * 1000.0
        if gap_ms > 0:
            gaps.append({"from_ts": e1, "to_ts": s2, "gap_ms": round(gap_ms, 1)})
    return gaps


def _idle_attribution(s):
    """Sovrappone ogni gap con gli intervalli di inferenza delle proxy_requests
    della STESSA sessione: gap = [t0,t1], inferenza = [pr.ts, pr.ts + total_ms/1000]."""
    gaps = _idle_gaps(s)
    inferenze = []
    for pr in s.get("proxy_requests", []):
        ts, total = pr.get("ts"), pr.get("total_ms")
        if ts is not None and total is not None:
            inferenze.append((ts, ts + total / 1000.0))
    tot_gap_ms = tot_inf = tot_user = tot_nspieg = 0.0
    for g in gaps:
        t0, t1, g_ms = g["from_ts"], g["to_ts"], g["gap_ms"]
        inf_ms = 0.0
        for i0, i1 in inferenze:
            ov = min(t1, i1) - max(t0, i0)
            if ov > 0:
                inf_ms += ov * 1000.0
        inf_ms = min(inf_ms, g_ms)  # sovrappizioni multiple non superano il gap
        resto = g_ms - inf_ms
        tot_gap_ms += g_ms
        tot_inf += inf_ms
        if resto > IDLE_USER_SEC * 1000:
            tot_user += resto
        else:
            tot_nspieg += resto
    return tot_gap_ms, tot_inf, tot_user, tot_nspieg


def _by_tool_name(s):
    out = {}
    for tc in s["tool_calls"].values():
        if tc.get("end_ts") is None or tc.get("duration_ms") is None:
            continue
        out.setdefault(tc["tool_name"], []).append(tc["duration_ms"])
    return {name: _dist(v) for name, v in sorted(out.items())}


def _by_mode_model(requests):
    gruppi = {}
    for pr in requests:
        chiave = (pr.get("mode"), pr.get("model"))
        gruppi.setdefault(chiave, []).append(pr)
    out = {}
    for (mode, model), grp in gruppi.items():
        out[f"{mode}|{model}"] = {
            "count": len(grp),
            "ttfb_ms": _dist([p.get("ttfb_ms") for p in grp]),
            "total_ms": _dist([p.get("total_ms") for p in grp]),
            "errors": _error_stats(grp),
        }
    return dict(sorted(out.items()))


def _scrivi_atomico(path, dati):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dati, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _default_mode():
    eventi, scartate = _leggi_eventi()
    sessions = _raggruppa_sessioni(eventi)

    righe_proxy, _scart, _info = airouter_info._leggi_sidecar(
        SimpleNamespace(giorni=1, mode=None, ore=None)
    )
    _aggiungi_proxy(sessions, righe_proxy)

    now = time.time()
    running_now, stale_calls = [], []
    orphans = {}
    out_sessions = {}
    all_gaps_ms, tot_inf, tot_user, tot_nspieg = [], 0.0, 0.0, 0.0
    calls = []
    by_tool_g, by_agent_g = {}, {}
    ts_min, ts_max = None, None
    for sid, s in sessions.items():
        # agent_id chiusi: subagent_stop successivo al tool_start => fantasmi
        stop_ts = {}
        for ev in s["subagent_stops"]:
            aid = ev.get("agent_id")
            if aid:
                stop_ts[aid] = max(stop_ts.get(aid, 0), ev.get("ts") or 0)
        gaps = _idle_gaps(s)
        for g in gaps:
            all_gaps_ms.append(g["gap_ms"])
        g_tot, g_inf, g_user, g_nsp = _idle_attribution(s)
        tot_inf += g_inf
        tot_user += g_user
        tot_nspieg += g_nsp
        for ev in s["tool_calls"].values():
            if ev.get("ts") is not None:
                lo = ev["ts"] if ts_min is None else min(ts_min, ev["ts"])
                ts_min = lo
                ts_max = ev["ts"] if ts_max is None else max(ts_max, ev["ts"])
        proxy = s.get("proxy_requests", [])
        out_sessions[sid] = {
            "by_tool_name": _by_tool_name(s),
            "by_mode_model": _by_mode_model(proxy),
            "n_tool_calls": len(s["tool_calls"]),
            "n_proxy_requests": len(proxy),
            "idle_gaps": gaps[-MAX_IDLE_GAPS:],
            "idle_gaps_note": IDLE_GAPS_NOTE,
            "hook_errors": s["hook_errors"],
            "subagent_stops": s["subagent_stops"],
        }
        for tuid, tc in s["tool_calls"].items():
            # orfani: tool-call senza tool_end (bias di sopravvivenza nelle stats)
            if tc.get("end_ts") is None and tc.get("start_ts") is not None:
                name = tc.get("tool_name") or "?"
                orphans[name] = orphans.get(name, 0) + 1
            if tc.get("end_ts") is None or tc.get("duration_ms") is None:
                continue
            # calls grezze per filtri UI + dist globali
            atype = tc.get("agent_type") or "main"
            calls.append([tc["start_ts"], tc.get("tool_name"), tc.get("duration_ms"),
                          atype, sid[:8], tc.get("status")])
            by_tool_g.setdefault(tc.get("tool_name"), []).append(tc["duration_ms"])
            by_agent_g.setdefault(atype, []).append(tc["duration_ms"])
            ts_max = tc["end_ts"] if (tc["end_ts"] or 0) > (ts_max or 0) else ts_max
        for tuid, tc in s["tool_calls"].items():
            if tc.get("start_ts") is None or tc.get("end_ts") is not None:
                continue
            elapsed = now - tc["start_ts"]
            if elapsed <= RUNNING_THRESHOLD_SEC:
                continue
            rec = {
                "session_id": sid,
                "tool_use_id": tuid,
                "tool_name": tc.get("tool_name"),
                "agent_type": tc.get("agent_type"),
                "elapsed_sec": round(elapsed, 1),
            }
            aid = tc.get("agent_id")
            if aid and aid in stop_ts and stop_ts[aid] > tc["start_ts"]:
                rec["motivo"] = "subagent_chiuso"
                stale_calls.append(rec)
            elif elapsed > RUNNING_STALE_SEC:
                rec["motivo"] = "troppo_vecchio"
                stale_calls.append(rec)
            else:
                running_now.append(rec)
    running_now.sort(key=lambda r: -r["elapsed_sec"])
    stale_calls.sort(key=lambda r: -r["elapsed_sec"])
    calls.sort(key=lambda row: row[0])
    calls = calls[-MAX_CALLS:]

    aggregato = {
        "generated_at": now,
        "schema": 2,
        "window": {"from_ts": ts_min, "to_ts": ts_max},
        "running_now": running_now,
        "stale_calls": stale_calls,
        "orphans": {"by_tool": orphans, "total": sum(orphans.values())},
        "calls_schema": CALLS_SCHEMA,
        "calls": calls,
        "by_tool_global": {k: _dist(v) for k, v in sorted(by_tool_g.items())},
        "by_agent_global": {k: _dist(v) for k, v in sorted(by_agent_g.items())},
        "idle": {
            "dist": _dist(all_gaps_ms),
            "somma_ms": round(sum(all_gaps_ms), 1),
            "inferenza_ms": round(tot_inf, 1),
            "attesa_utente_ms": round(tot_user, 1),
            "non_spiegato_ms": round(tot_nspieg, 1),
        },
        "eventi_scartati": scartate,
        "sessions": out_sessions,
    }
    _scrivi_atomico(AGG_DIR / "latest.json", aggregato)
    print(f"latest.json: {len(out_sessions)} sessioni, "
          f"{len(running_now)} running_now, {len(stale_calls)} stale, "
          f"{aggregato['orphans']['total']} orfani, {scartate} eventi scartati")


def _backfill_mode():
    righe, _scart, _info = airouter_info._leggi_sidecar(
        SimpleNamespace(giorni=None, mode=None, ore=None)
    )
    gruppi = {}
    for riga in righe:
        chiave = (riga.get("mode"), riga.get("final"))
        gruppi.setdefault(chiave, []).append(riga)

    by_mode_model = {}
    for (mode, final), grp in gruppi.items():
        by_mode_model[f"{mode}|{final}"] = {
            "count": len(grp),
            "ttfb_ms": _dist([r.get("ttfb_ms") for r in grp]),
            "total_ms": _dist([r.get("total_ms") for r in grp]),
            "cache_read_media": round(
                sum(r.get("cache_read") or 0 for r in grp) / len(grp), 1),
            "cache_creation_media": round(
                sum(r.get("cache_creation") or 0 for r in grp) / len(grp), 1),
            "errors": _error_stats(grp),
        }
    ts_validi = [r.get("ts") for r in righe if r.get("ts") is not None]
    baseline = {
        "generated_at": time.time(),
        "window_from_ts": min(ts_validi) if ts_validi else None,
        "window_to_ts": max(ts_validi) if ts_validi else None,
        "by_mode_model": dict(sorted(by_mode_model.items())),
    }
    _scrivi_atomico(AGG_DIR / "baseline_proxy_only.json", baseline)
    print(f"baseline_proxy_only.json: {len(by_mode_model)} combinazioni "
          f"mode|model su {len(righe)} righe sidecar")


def main():
    if os.environ.get("PERFMON_DISABLE") == "1":
        sys.exit(0)
    if "--backfill" in sys.argv[1:]:
        _backfill_mode()
    else:
        _default_mode()


if __name__ == "__main__":
    main()
