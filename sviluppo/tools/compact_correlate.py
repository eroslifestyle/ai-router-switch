"""Correla i turni /compact di Claude Code col comportamento del proxy ai-router.

Sola lettura: incrocia i `compact_boundary` dei transcript locali con le righe
`ctx:` di ~/.claude/logs/ai-router.log per stabilire se una richiesta di
compattazione sia mai stata riscritta (mutilata) dal router.

Scritto per la voce G4 del TODO. Uso: python3 sviluppo/tools/compact_correlate.py
"""
import os
import sys
import glob
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bisect import bisect_left, bisect_right

def load_compacts():
    compacts = []
    pattern = os.path.expanduser("~/.claude/projects/*/*.jsonl")
    for path in glob.glob(pattern):
        try:
            with open(path, 'r', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("subtype") != "compact_boundary":
                        continue
                    session_id = d.get("sessionId")
                    if not session_id:
                        session_id = os.path.splitext(os.path.basename(path))[0]
                    ts_utc_str = d.get("timestamp")
                    if not ts_utc_str:
                        continue
                    try:
                        ts_utc = datetime.fromisoformat(ts_utc_str.replace('Z', '+00:00'))
                    except Exception:
                        continue
                    ts_local = ts_utc.astimezone(ZoneInfo("Europe/Rome"))
                    compact_meta = d.get("compactMetadata") or {}
                    trigger = compact_meta.get("trigger")
                    pre_tokens = compact_meta.get("preTokens")
                    compacts.append({
                        'session_id': session_id,
                        'ts_local': ts_local,
                        'trigger': trigger,
                        'pre_tokens': pre_tokens,
                        'raw': d
                    })
        except Exception as e:
            print(f"Impossibile leggere {path}: {e}", file=sys.stderr)
    compacts.sort(key=lambda x: x['ts_local'])
    return compacts

def load_log_index(log_path):
    pat_ts = re.compile(r'\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\] ')
    pat_bottleneck = re.compile(r'ctx: bottleneck-shrink SKIP provider=(\S+) mode=(\S+) bytes=(\d+) fp=(\S+)')
    pat_rewrite = re.compile(r'ctx: proactive rewrite (\d+)b < (\d+)b fp=(\S+)')
    pat_gate = re.compile(r'ctx: (WARN2|WARN|COMPACT|ERROR) fp=(\S+) mode=(\S+) pct=([\d.]+)%')
    log_index = {}
    coverage_start = None
    coverage_end = None
    try:
        with open(log_path, 'r', errors='replace') as f:
            for line in f:
                m = pat_ts.match(line)
                if not m:
                    continue
                ts_str = m.group(1)
                try:
                    dt_naive = datetime.fromisoformat(ts_str)
                except Exception:
                    continue
                dt_local = dt_naive.replace(tzinfo=ZoneInfo("Europe/Rome"))
                if coverage_start is None or dt_local < coverage_start:
                    coverage_start = dt_local
                if coverage_end is None or dt_local > coverage_end:
                    coverage_end = dt_local
                rest = line[m.end():].strip()
                mb = pat_bottleneck.match(rest)
                if mb:
                    provider, mode, bytes_val, fp = mb.groups()
                    sid = fp.split('sid:', 1)[-1]
                    if not sid:
                        continue
                    entry = {'ts': dt_local, 'type': 'bottleneck', 'provider': provider, 'mode': mode, 'bytes': int(bytes_val)}
                    log_index.setdefault(sid, {'times': [], 'entries': []})
                    log_index[sid]['times'].append(dt_local)
                    log_index[sid]['entries'].append(entry)
                    continue
                mr = pat_rewrite.match(rest)
                if mr:
                    bytes_after, bytes_before, fp = mr.groups()
                    sid = fp.split('sid:', 1)[-1]
                    if not sid:
                        continue
                    entry = {'ts': dt_local, 'type': 'rewrite', 'bytes_after': int(bytes_after), 'bytes_before': int(bytes_before)}
                    log_index.setdefault(sid, {'times': [], 'entries': []})
                    log_index[sid]['times'].append(dt_local)
                    log_index[sid]['entries'].append(entry)
                    continue
                mg = pat_gate.match(rest)
                if mg:
                    level, fp, mode, pct = mg.groups()
                    sid = fp.split('sid:', 1)[-1]
                    if not sid:
                        continue
                    entry = {'ts': dt_local, 'type': 'gate', 'level': level, 'mode': mode, 'pct': float(pct)}
                    log_index.setdefault(sid, {'times': [], 'entries': []})
                    log_index[sid]['times'].append(dt_local)
                    log_index[sid]['entries'].append(entry)
                    continue
    except Exception as e:
        print(f"Errore nella lettura del log: {e}", file=sys.stderr)
    return log_index, coverage_start, coverage_end

def correlate(compact, log_index, coverage):
    t_local = compact['ts_local']
    coverage_start, coverage_end = coverage
    result = {
        'verdict': None,
        'n_lines': 0,
        'provider': None,
        'mode': None,
        'max_bytes': None,
        'max_bytes_time': None,
        'rewrites': [],
        'gate_events': []
    }
    if coverage_start is None or coverage_end is None:
        result['verdict'] = "NO_LOG_COVERAGE"
        return result
    if t_local < coverage_start or t_local > coverage_end:
        result['verdict'] = "NO_LOG_COVERAGE"
        return result
    sid = compact['session_id']
    entries_data = log_index.get(sid)
    if not entries_data:
        result['verdict'] = "NO_TRACE"
        return result
    times = entries_data['times']
    entries = entries_data['entries']
    start = t_local - timedelta(seconds=600)
    end = t_local + timedelta(seconds=5)
    left = bisect_left(times, start)
    right = bisect_right(times, end)
    window_entries = entries[left:right]
    n_lines = len(window_entries)
    result['n_lines'] = n_lines
    if n_lines == 0:
        result['verdict'] = "NO_TRACE"
        return result
    max_bytes = -1
    for e in window_entries:
        if e['type'] == 'bottleneck':
            if e['bytes'] > max_bytes:
                max_bytes = e['bytes']
                result['provider'] = e['provider']
                result['mode'] = e['mode']
                result['max_bytes'] = e['bytes']
                result['max_bytes_time'] = e['ts']
        elif e['type'] == 'rewrite':
            result['rewrites'].append((e['ts'], e['bytes_after'], e['bytes_before']))
        elif e['type'] == 'gate':
            result['gate_events'].append((e['ts'], e['level'], e['pct']))
    if max_bytes == -1:
        result['max_bytes'] = None
        result['provider'] = None
        result['mode'] = None
        result['max_bytes_time'] = None
    if result['rewrites']:
        result['verdict'] = "MUTILATO"
    else:
        result['verdict'] = "INTATTO"
    return result

def format_report(compacts, results, coverage):
    coverage_start, coverage_end = coverage
    verdicts_count = {}
    total = len(compacts)
    for r in results:
        v = r['verdict']
        verdicts_count[v] = verdicts_count.get(v, 0) + 1
    print("=" * 80)
    print("RAPPORTO COMPATTEZZE COMPATTABILI")
    print("=" * 80)
    if coverage_start is not None and coverage_end is not None:
        print(f"Intervallo di copertura del log: {coverage_start.strftime('%Y-%m-%d %H:%M:%S %Z')} - {coverage_end.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    else:
        print("Intervallo di copertura del log: non disponibile")
    print(f"Numero totale di compact trovati: {total}")
    print("-" * 80)
    for comp, res in zip(compacts, results):
        sid_short = comp['session_id'][:8]
        ts_str = comp['ts_local'].strftime('%Y-%m-%d %H:%M:%S %Z')
        trigger = comp.get('trigger')
        pre_tokens = comp.get('pre_tokens')
        verdict = res['verdict']
        print(f"Sessione: {sid_short} | Orario: {ts_str} | Trigger: {trigger} | Pre-token: {pre_tokens}")
        print(f"  Verdetto: {verdict}")
        if res['provider']:
            max_time_str = res['max_bytes_time'].strftime('%H:%M:%S') if res['max_bytes_time'] else 'N/A'
            print(f"  Provider: {res['provider']} | Mode: {res['mode']} | Max bytes: {res['max_bytes']} (alle {max_time_str})")
        if res['rewrites']:
            print("  Rewrites (riscritture):")
            for rt, ba, bb in res['rewrites']:
                print(f"    - {rt.strftime('%H:%M:%S')}: {ba}b < {bb}b")
        if res['gate_events']:
            print("  Gate events:")
            for gt, gl, gp in res['gate_events']:
                print(f"    - {gt.strftime('%H:%M:%S')}: {gl} pct={gp}%")
        if not res['rewrites'] and not res['gate_events']:
            print("  Nessun evento di riscrittura o gate.")
        print("-" * 80)
    print("=" * 80)
    print("RIEPILOGO VERDETTI:")
    for v, cnt in sorted(verdicts_count.items()):
        print(f"  {v}: {cnt}")
    print("=" * 80)

def main():
    compacts = load_compacts()
    log_path = os.path.expanduser("~/.claude/logs/ai-router.log")
    log_index, coverage_start, coverage_end = load_log_index(log_path)
    results = []
    for comp in compacts:
        res = correlate(comp, log_index, (coverage_start, coverage_end))
        results.append(res)
    format_report(compacts, results, (coverage_start, coverage_end))

if __name__ == "__main__":
    main()