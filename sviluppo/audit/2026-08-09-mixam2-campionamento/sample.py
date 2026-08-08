#!/usr/bin/env python3
"""Campione periodico dell'efficacia di mix-am-2 (delega verso MiniMax).

Contesto. L'hook enforce_main_exploration.py (attivo dalle ~01:35 del
2026-08-09) nega al MAIN l'esplorazione estesa in mix-am-2, per rendere lo
spawn di un subagent l'unica strada praticabile — i subagent haiku girano su
MiniMax. Prima misura: da 0/140 richieste delegate a 22/36 in sei minuti. Sei
minuti sono pochi per un verdetto, da qui il campionamento a intervalli.

Ogni esecuzione stampa UNA riga e la appende a samples.jsonl: la finestra è
quella dall'ultimo campione (o gli ultimi 10 minuti alla prima esecuzione),
così i campioni sono indipendenti e non si sovrappongono.

Uso:  python3 sample.py            # campione sulla finestra corrente
      python3 sample.py --report   # riepilogo di tutti i campioni raccolti
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

SIDECAR = Path.home() / ".claude" / "logs" / "router-usage.jsonl"
HOOKLOG = Path.home() / ".claude" / "m3" / "main-exploration.jsonl"
OUT = Path(__file__).resolve().parent / "samples.jsonl"
MODE = "mix-am-2"
DEFAULT_WINDOW_S = 600


def _rows_since(ts_from):
    out = []
    try:
        with SIDECAR.open(errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("mode") == MODE and r.get("ts", 0) >= ts_from:
                    out.append(r)
    except FileNotFoundError:
        pass
    return out


def _denies_since(ts_from):
    """Deny dell'hook esplorazione nella finestra (ts ISO, non epoch)."""
    n = 0
    try:
        cutoff = datetime.fromtimestamp(ts_from).isoformat(timespec="seconds")
        with HOOKLOG.open(errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if str(d.get("ts", "")) >= cutoff:
                    n += 1
    except FileNotFoundError:
        pass
    return n


def _weight(r):
    """Peso di fatturazione: input pieno, cache_read 10%, cache_creation 125%."""
    return ((r.get("input_tokens", 0) or 0)
            + (r.get("cache_read", 0) or 0) * 0.1
            + (r.get("cache_creation", 0) or 0) * 1.25)


def _last_sample_ts():
    if not OUT.exists():
        return None
    last = None
    for line in OUT.read_text(errors="replace").splitlines():
        if line.strip():
            try:
                last = json.loads(line)
            except Exception:
                pass
    return last.get("ts_end") if last else None


def report():
    if not OUT.exists():
        print("nessun campione raccolto")
        return
    rows = [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()]
    print(f"{'#':>2} {'ora':>8} {'min':>4} {'req':>5} {'MiniMax':>8} {'quota':>7} {'peso MM':>8} {'deny':>5}")
    tot_r = tot_m = 0
    for i, s in enumerate(rows, 1):
        print(f"{i:>2} {s['clock']:>8} {s['window_min']:>4.0f} {s['requests']:>5} "
              f"{s['minimax']:>8} {s['minimax_pct']:>6.1f}% {s['minimax_weight_pct']:>7.1f}% {s['denies']:>5}")
        tot_r += s["requests"]
        tot_m += s["minimax"]
    if tot_r:
        print(f"\nAGGREGATO: {tot_r} richieste, {tot_m} su MiniMax ({100*tot_m/tot_r:.1f}%)")


def main():
    if "--report" in sys.argv:
        report()
        return
    now = int(time.time())
    start = _last_sample_ts() or (now - DEFAULT_WINDOW_S)
    rows = _rows_since(start)
    mm = [r for r in rows if "MiniMax" in str(r.get("final"))]
    an = [r for r in rows if r.get("final") == "claude-direct"]
    w_mm = sum(_weight(r) for r in mm)
    w_an = sum(_weight(r) for r in an)
    w_tot = w_mm + w_an or 1
    s = {
        "ts_start": start,
        "ts_end": now,
        "clock": datetime.fromtimestamp(now).strftime("%H:%M:%S"),
        "window_min": (now - start) / 60,
        "requests": len(rows),
        "minimax": len(mm),
        "anthropic": len(an),
        "minimax_pct": (100 * len(mm) / len(rows)) if rows else 0.0,
        "minimax_weight_pct": 100 * w_mm / w_tot,
        "out_minimax": sum(r.get("output_tokens", 0) or 0 for r in mm),
        "out_anthropic": sum(r.get("output_tokens", 0) or 0 for r in an),
        "denies": _denies_since(start),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as f:
        f.write(json.dumps(s) + "\n")
    if not rows:
        print(f"[{s['clock']}] finestra {s['window_min']:.0f} min: NESSUNA richiesta in {MODE} "
              f"(sessione ferma o su altra modalità)")
        return
    print(f"[{s['clock']}] {s['window_min']:.0f} min: {s['requests']} req | "
          f"MiniMax {s['minimax']} ({s['minimax_pct']:.1f}%) | "
          f"peso MiniMax {s['minimax_weight_pct']:.1f}% | "
          f"out MM {s['out_minimax']:,} vs AN {s['out_anthropic']:,} | "
          f"deny esplorazione {s['denies']}")


if __name__ == "__main__":
    main()
