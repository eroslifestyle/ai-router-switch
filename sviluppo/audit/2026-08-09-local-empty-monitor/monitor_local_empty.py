#!/usr/bin/env python3
"""Monitor risposte vuote Local (LLM locale) - speculare a monitor_minimax_empty.py.

Fonti:
  - router-usage.jsonl  righe mode=='local' con output_tokens<=5 (segnale primario)
  - debug-events.jsonl  eventi kind=='empty_response_local' (kind futuro, oggi assente)

Un campione per esecuzione (no --report): finestra dall'ultimo campione o
DEFAULT_WINDOW_S secondi. Output: riga JSON su samples.jsonl (locale, non committato).
"""
import json, sys, time
from datetime import datetime
from pathlib import Path

ROUTER_USAGE = Path('/home/mrxxx/.claude/logs/router-usage.jsonl')
DEBUG_EVENTS = Path('/mnt/nvme2/projects/Progetti/ai-router-switch/logs/debug-events.jsonl')
OUT = Path(__file__).resolve().parent / 'samples.jsonl'
DEFAULT_WINDOW_S = 600

def _norm_model(orig):
    if not orig or orig == '?':
        return '(altro)'
    o = orig.lower()
    if 'code-max' in o or 'codemax' in o:
        return 'code-max'
    if 'coder-next' in o or 'codernext' in o:
        return 'qwen3-coder-next'
    return '(altro)'


def _check_path(p, label):
    if not p.exists():
        print('[ERRORE] {} non trovato: {}'.format(label, p), file=sys.stderr)
        sys.exit(1)


def _rows_usage_since(ts_from):
    rows = []
    by_model = {}
    with open(ROUTER_USAGE, errors='replace') as f:
        for line in f:
            if not line.startswith('{'):
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get('mode') != 'local':
                continue
            if r.get('ts', 0) < ts_from:
                continue
            rows.append(r)
            m = _norm_model(r.get('orig'))
            d = by_model.setdefault(m, {'tot': 0, 'empty': 0})
            d['tot'] += 1
            if (r.get('output_tokens') or 0) <= 5:
                d['empty'] += 1
    return rows, by_model


def _count_empty_events_since(ts_from_iso):
    n = 0
    with open(DEBUG_EVENTS, errors='replace') as f:
        for line in f:
            if not line.startswith('{'):
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get('ts', '') < ts_from_iso:
                continue
            if e.get('kind') == 'empty_response_local':
                n += 1
    return n


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


def report(max_rows=None):
    if not OUT.exists():
        print('nessun campione raccolto')
        return
    rows = []
    for line in OUT.read_text(errors='replace').splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    if not rows:
        print('nessun campione valido')
        return
    if max_rows:
        rows = rows[-max_rows:]
    hdr = "{:>2} {:>8} {:>5} {:>8} {:>8} {:>9} {:>7}".format(
        '#', 'ora', 'min', 'tot local', 'empty', 'evt empt', '%vuote')
    print(hdr)
    print('-' * 60)
    gtot = gempty = gev = 0
    for i, s in enumerate(rows, 1):
        tot = s.get('total_local_requests', 0)
        emp = s.get('empty_output_le5', 0)
        gev_s = s.get('events_empty_response_local', 0)
        pct = s.get('pct_empty_of_total', 0.0)
        print("{:>2} {:>8} {:>5.0f} {:>8} {:>8} {:>9} {:>6.1f}%".format(
            i, s.get('clock', ''), s.get('window_min', 0), tot, emp, gev_s, pct))
        gtot += tot
        gempty += emp
        gev += gev_s
        models = s.get('models', {})
        for mname in ('code-max', 'qwen3-coder-next'):
            md = models.get(mname)
            if not md or md.get('tot', 0) == 0:
                continue
            mpct = (100 * md['empty'] / md['tot']) if md['tot'] else 0.0
            print("      |__ {:<17} tot={:>5} empty={:>5} ({:.1f}%)".format(
                mname, md['tot'], md['empty'], mpct))
    print('-' * 60)
    agg_pct = (100 * gempty / gtot) if gtot else 0.0
    print("{:>3} {:>8} {:>5} {:>8} {:>8} {:>9} {:>6.1f}%".format(
        'TOT', '', '', gtot, gempty, gev, agg_pct))
    if gempty:
        print('AGGREGATO: {} empty(output<=5) / {} local ({:.1f}%) eventi={}'.format(
            gempty, gtot, agg_pct, gev))


def sample():
    now = time.time()
    ts_start = _last_sample_ts() or (now - DEFAULT_WINDOW_S)
    ts_start_iso = _fmt_ts(ts_start)
    rows, by_model = _rows_usage_since(ts_start)
    total = len(rows)
    empty = sum(1 for r in rows if (r.get('output_tokens') or 0) <= 5)
    gev = _count_empty_events_since(ts_start_iso)
    pct = (100 * empty / total) if total else 0.0
    window_min = (now - ts_start) / 60
    models_out = {m: by_model.get(m, {'tot': 0, 'empty': 0})
                  for m in ('code-max', 'qwen3-coder-next')}
    s = {
        'ts_start': ts_start, 'ts_end': now, 'ts_start_iso': ts_start_iso,
        'clock': _fmt_clock(now), 'window_min': window_min,
        'total_local_requests': total, 'empty_output_le5': empty,
        'events_empty_response_local': gev, 'models': models_out,
        'pct_empty_of_total': pct,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'a') as f:
        f.write(json.dumps(s) + '\n')
    if total == 0 and empty == 0:
        print('[{}] finestra {:.0f} min: NESSUNA richiesta local'.format(
            s['clock'], window_min))
        return
    print('[{}] {:.0f} min | local {} req | empty(ot<=5) {} ({:.1f}%) | evt {}'.format(
        s['clock'], window_min, total, empty, pct, gev))


def main():
    _check_path(ROUTER_USAGE, 'router-usage.jsonl')
    _check_path(DEBUG_EVENTS, 'debug-events.jsonl')
    args = sys.argv[1:]
    if '--report' in args:
        max_rows = None
        if '--samples' in args:
            i = args.index('--samples')
            try:
                max_rows = int(args[i + 1])
            except (IndexError, ValueError):
                pass
        report(max_rows=max_rows)
        return
    sample()


if __name__ == '__main__':
    main()
