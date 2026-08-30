#!/usr/bin/env python3
import json, os, sys, time
from datetime import datetime
from pathlib import Path

ROUTER_USAGE = Path('/home/mrxxx/.claude/logs/router-usage.jsonl')
DEBUG_EVENTS = Path('/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch/logs/debug-events.jsonl')
OUT = Path(__file__).resolve().parent / 'samples.jsonl'
DEFAULT_WINDOW_S = 600

def _check_path(p, label):
    if not p.exists():
        print(f'[ERRORE] {label} non trovato: {p}', file=sys.stderr)
        sys.exit(1)

def _rows_usage_since(ts_from):
    out = []
    try:
        with open(ROUTER_USAGE, errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line.startswith('{'):
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('final') == 'glm:glm-5.2' and r.get('ts', 0) >= ts_from:
                    out.append(r)
    except FileNotFoundError:
        print('[ERRORE] router-usage.jsonl non trovato', file=sys.stderr)
    return out

def _rows_events_since(ts_from_iso):
    e1, e2, ex = [], [], []
    try:
        with open(DEBUG_EVENTS, errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line.startswith('{'):
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get('ts', '') < ts_from_iso:
                    continue
                kind = e.get('kind', '')
                snippet = e.get('snippet', '') or ''
                if kind == 'glm_empty_response':
                    if 'attempt=1' in snippet:
                        e1.append(e)
                    elif 'attempt=2' in snippet:
                        e2.append(e)
                elif kind == 'glm_exhausted':
                    ex.append(e)
    except FileNotFoundError:
        print('[ERRORE] debug-events.jsonl non trovato', file=sys.stderr)
    return e1, e2, ex

def _last_sample_ts():
    if not OUT.exists():
        return None
    try:
        for line in reversed(OUT.read_text(errors='replace').splitlines()):
            if line.strip():
                try:
                    return json.loads(line).get('ts_end')
                except Exception:
                    pass
    except Exception:
        pass
    return None

def _fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%dT%H:%M:%S')

def _fmt_clock(ts):
    return datetime.fromtimestamp(ts).strftime('%H:%M:%S')

def report():
    if not OUT.exists():
        print('nessun campione raccolto')
        return
    rows = []
    try:
        for line in OUT.read_text(errors='replace').splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception as e:
        print(f'[ERRORE] {e}', file=sys.stderr)
        return
    if not rows:
        print('nessun campione valido')
        return
    hdr = "{:>2} {:>8} {:>4} {:>8} {:>7} {:>7} {:>7} {:>6} {:>7}".format('#','ora','min','tot glm','emp@1','emp@2','exhaust','recov','%vuote')
    print(hdr)
    print('-' * 72)
    tot_req = tot_e1 = tot_e2 = tot_ex = tot_rec = 0
    for i, s in enumerate(rows, 1):
        e1 = s.get('empty_attempt1', 0)
        ex = s.get('exhausted', 0)
        pct = s.get('pct_empty_of_total', 0.0)
        row = "{:>2} {:>8} {:>4.0f} {:>8} {:>7} {:>7} {:>7} {:>6} {:>6.1f}%".format(
            i, s.get('clock',''), s.get('window_min',0), s.get('total_glm5_requests',0),
            e1, s.get('empty_attempt2',0), ex, s.get('recovered',0), pct)
        print(row)
        tot_req += s.get('total_glm5_requests', 0)
        tot_e1 += e1
        tot_e2 += s.get('empty_attempt2', 0)
        tot_ex += ex
        tot_rec += s.get('recovered', 0)
    print('-' * 72)
    agg_pct = (100 * tot_e1 / tot_req) if tot_req else 0.0
    tot_row = "{:>3} {:>8} {:>4} {:>8} {:>7} {:>7} {:>7} {:>6} {:>6.1f}%".format(
        'TOT','','', tot_req, tot_e1, tot_e2, tot_ex, tot_rec, agg_pct)
    print(tot_row)
    if tot_e1:
        print('AGGREGATO: {} vuoti@1 / {} glm:glm-5.2 ({:.1f}%) exhausted={} recovered={}'.format(
            tot_e1, tot_req, agg_pct, tot_ex, tot_rec))

def main():
    _check_path(ROUTER_USAGE, 'router-usage.jsonl')
    _check_path(DEBUG_EVENTS, 'debug-events.jsonl')
    if '--report' in sys.argv:
        report()
        return
    now = time.time()
    ts_start = _last_sample_ts() or (now - DEFAULT_WINDOW_S)
    ts_start_iso = _fmt_ts(ts_start)
    rows = _rows_usage_since(ts_start)
    total_glm5 = len(rows)
    e1, e2, ex = _rows_events_since(ts_start_iso)
    empty_attempt1 = len(e1)
    empty_attempt2 = len(e2)
    exhausted = len(ex)
    recovered = max(0, empty_attempt1 - exhausted)
    pct = (100 * empty_attempt1 / total_glm5) if total_glm5 else 0.0
    window_min = (now - ts_start) / 60
    sample = {'ts_start': ts_start, 'ts_end': now, 'ts_start_iso': ts_start_iso, 'clock': _fmt_clock(now), 'window_min': window_min, 'total_glm5_requests': total_glm5, 'empty_attempt1': empty_attempt1, 'empty_attempt2': empty_attempt2, 'exhausted': exhausted, 'recovered': recovered, 'pct_empty_of_total': pct}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'a') as f:
        f.write(json.dumps(sample) + '\n')
    if total_glm5 == 0 and empty_attempt1 == 0:
        print('[{}] finestra {:.0f} min: NESSUNA richiesta glm:glm-5.2 e NESSUN evento glm_empty_response'.format(sample['clock'], window_min))
        return
    print('[{}] {:.0f} min | glm:glm-5.2 {} req | vuote@1 {} ({:.1f}%) | vuote@2 {} | exhausted {} | recovered {}'.format(
        sample['clock'], window_min, total_glm5, empty_attempt1, pct, empty_attempt2, exhausted, recovered))

if __name__ == '__main__':
    main()
