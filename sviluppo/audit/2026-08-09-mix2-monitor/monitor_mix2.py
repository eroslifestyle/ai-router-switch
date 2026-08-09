#!/usr/bin/env python3
"""
Monitor per le modalità mix-am-2, mix-gm-2, mix-ag-2.
Legge router-usage.jsonl e main-exploration.jsonl, calcola metriche di delega.
"""
import json
import sys
import time
import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SAMPLES_FILE = SCRIPT_DIR / "samples.jsonl"
USAGE_LOG = Path.home() / ".claude" / "logs" / "router-usage.jsonl"
DENY_LOG = Path.home() / ".claude" / "m3" / "main-exploration.jsonl"
MODES = ["mix-am-2", "mix-gm-2", "mix-ag-2"]
WINDOW_SECONDS = 600


def load_samples() -> dict:
    """Carica ultimi ts_end per ogni mode da samples.jsonl."""
    samples = {m: 0 for m in MODES}
    if SAMPLES_FILE.exists():
        for line in SAMPLES_FILE.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
                mode = rec.get("mode", "")
                if mode in MODES:
                    samples[mode] = max(samples[mode], rec.get("ts_end", 0))
            except json.JSONDecodeError:
                continue
    return samples


def save_samples(records: list[dict]) -> None:
    """Appende i record dei campioni a samples.jsonl."""
    with SAMPLES_FILE.open("a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def read_usage_log(cutoff_ts: int) -> list[dict]:
    """Legge router-usage.jsonl, restituisce richieste dopo cutoff_ts."""
    if not USAGE_LOG.exists():
        return []
    records = []
    for line in USAGE_LOG.read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("ts", 0) >= cutoff_ts and rec.get("mode") in MODES:
                records.append(rec)
        except json.JSONDecodeError:
            continue
    return records


def read_deny_log(cutoff_iso: str) -> int:
    """Conta deny log entry con ts ISO dopo cutoff_iso."""
    if not DENY_LOG.exists():
        return 0
    count = 0
    for line in DENY_LOG.read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
            ts_iso = rec.get("ts", "")
            if ts_iso >= cutoff_iso:
                count += 1
        except json.JSONDecodeError:
            continue
    return count


def calc_weight(rec: dict) -> float:
    """Peso = input + cache_read*0.1 + cache_creation*1.25."""
    return (rec.get("input_tokens", 0)
            + rec.get("cache_read", 0) * 0.1
            + rec.get("cache_creation", 0) * 1.25)


def is_delegated(rec: dict) -> bool:
    """Delega: orig contiene haiku (case-insensitive)."""
    return "haiku" in rec.get("orig", "").lower()


def analyze_window(records: list[dict]) -> dict:
    """Aggrega metriche per una finestra."""
    del_recs = [r for r in records if is_delegated(r)]
    think_recs = [r for r in records if not is_delegated(r)]

    peso_del = sum(calc_weight(r) for r in del_recs)
    peso_th = sum(calc_weight(r) for r in think_recs)
    peso_tot = peso_del + peso_th

    peso_exec_pct = (100 * peso_del / peso_tot) if peso_tot > 0 else 0

    out_del = sum(r.get("output_tokens", 0) for r in del_recs)
    out_th = sum(r.get("output_tokens", 0) for r in think_recs)

    return {
        "requests": len(records),
        "delegated": len(del_recs),
        "think": len(think_recs),
        "delegate_pct": (100 * len(del_recs) / len(records)) if records else 0,
        "peso_exec_pct": peso_exec_pct,
        "out_exec": out_del,
        "out_think": out_th,
    }


def format_time(ts: int) -> str:
    """HH:MM:SS da epoch."""
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def format_report(all_samples: list[dict]) -> str:
    """Genera tabella di tutti i campioni."""
    if not all_samples:
        return "Nessun campione disponibile.\n"

    lines = ["mode           ora        min  req  del  del%  peso%  deny"]
    lines.append("-" * 65)

    for s in all_samples:
        clock = datetime.datetime.fromtimestamp(s["clock"]).strftime("%H:%M:%S")
        lines.append(
            f"{s['mode']:<14} {clock}  "
            f"{s['window_min']:<4} {s['requests']:<4} {s['delegated']:<4} "
            f"{s['delegate_pct']:<5.0f} {s['peso_exec_pct']:<6.1f} {s['denies']}"
        )

    tot_req = sum(s["requests"] for s in all_samples)
    tot_del = sum(s["delegated"] for s in all_samples)
    agg_pct = (100 * tot_del / tot_req) if tot_req > 0 else 0
    lines.append("-" * 65)
    lines.append(f"AGGREGATO: {tot_req} richieste, {tot_del} delegate ({agg_pct:.0f}%) su 3 mode")

    return "\n".join(lines)


def main() -> None:
    report_mode = "--report" in sys.argv

    last_ts = load_samples()
    now_ts = int(time.time())
    clock_now = time.time()

    all_samples = []
    new_records = []

    for mode in MODES:
        ts_start = last_ts[mode] if last_ts[mode] > 0 else now_ts - WINDOW_SECONDS
        cutoff_iso = datetime.datetime.fromtimestamp(ts_start).isoformat(timespec="seconds")
        window_sec = now_ts - ts_start
        window_min = max(1, round(window_sec / 60))

        records = read_usage_log(ts_start)
        mode_recs = [r for r in records if r.get("mode") == mode]

        stats = analyze_window(mode_recs)
        denies = read_deny_log(cutoff_iso)

        sample = {
            "ts_start": ts_start,
            "ts_end": now_ts,
            "clock": clock_now,
            "mode": mode,
            "window_min": window_min,
            "requests": stats["requests"],
            "delegated": stats["delegated"],
            "think": stats["think"],
            "delegate_pct": stats["delegate_pct"],
            "peso_exec_pct": stats["peso_exec_pct"],
            "out_exec": stats["out_exec"],
            "out_think": stats["out_think"],
            "denies": denies,
        }
        new_records.append(sample)

        if report_mode:
            all_samples.append(sample)
        else:
            time_str = format_time(now_ts)
            if stats["requests"] == 0:
                print(f"[{time_str}] {mode}: inattiva (nessuna richiesta in {window_min} min)")
            else:
                print(
                    f"[{time_str}] {mode}: {window_min}min | "
                    f"{stats['requests']}req delegate {stats['delegated']} ({stats['delegate_pct']:.0f}%) | "
                    f"peso exec {stats['peso_exec_pct']:.1f}% | "
                    f"out exec {stats['out_exec']} vs think {stats['out_think']} | "
                    f"deny {denies}"
                )

    if not report_mode:
        tot_req = sum(r["requests"] for r in new_records)
        tot_del = sum(r["delegated"] for r in new_records)
        agg_pct = (100 * tot_del / tot_req) if tot_req > 0 else 0
        print(f"AGGREGATO: {tot_req} richieste, {tot_del} delegate ({agg_pct:.0f}%) su 3 mode")

    save_samples(new_records)

    if report_mode:
        print(format_report(all_samples))

    sys.exit(0)


if __name__ == "__main__":
    main()
