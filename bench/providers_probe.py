import json
import statistics
import os
import time
import argparse
import urllib.request
import urllib.error
import dataclasses
import pathlib

SIGNAL_NOISE_RATIO = 2.0   # quante volte il segnale deve superare il rumore
MIN_RELEVANT_PCT = 10.0    # sotto questa quota l'effetto e' reale ma inutile in pratica
PROMPT = 'Un treno parte da A alle 9:00 a 80 km/h. Un secondo parte da B alle 9:30 a 120 km/h verso A. A e B distano 400 km. A che ora si incontrano? Spiega il ragionamento passo per passo.'

def load_env_file(path: str) -> dict[str, str]:
    vars_: dict[str, str] = {}
    if not os.path.exists(path):
        return vars_
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:]
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            k, v = k.strip(), v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            vars_[k] = v
    return vars_

def _merge(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for k, val in extra.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(val, dict):
            merged[k] = {**merged[k], **val}
        else:
            merged[k] = val
    return merged

@dataclasses.dataclass
class ProbeSpec:
    provider: str
    url: str
    headers: dict
    base_body: dict
    variants: list[tuple[str, dict]]
    response_kind: str

def extract_usage(raw: dict, kind: str) -> dict:
    res = {'input_tokens': 0, 'output_tokens': 0, 'thinking_tokens': 0, 'text_len': 0}
    if kind == 'anthropic':
        usage = raw.get('usage') or {}
        res['input_tokens'] = usage.get('input_tokens') or 0
        res['output_tokens'] = usage.get('output_tokens') or 0
        det = usage.get('output_tokens_details') or {}
        res['thinking_tokens'] = det.get('thinking_tokens') or 0
        content = raw.get('content') or []
        res['text_len'] = sum(len(b.get('text', '')) for b in content if b.get('type') == 'text')
    else:
        usage = raw.get('usage') or {}
        res['input_tokens'] = usage.get('prompt_tokens') or 0
        res['output_tokens'] = usage.get('completion_tokens') or 0
        det = usage.get('completion_tokens_details') or {}
        res['thinking_tokens'] = det.get('reasoning_tokens') or 0
        choices = raw.get('choices') or []
        if choices:
            msg = choices[0].get('message') or {}
            res['text_len'] = len(msg.get('content') or '')
    return res

def probe(spec: ProbeSpec, timeout: int = 120) -> list[dict]:
    results = []
    for name, extra in spec.variants:
        body = _merge(spec.base_body, extra)
        row = {'provider': spec.provider, 'variant': name, 'status': 0, 'ok': False,
               'latency_ms': 0, 'input_tokens': 0, 'output_tokens': 0,
               'thinking_tokens': 0, 'text_len': 0, 'error': ''}
        start = time.monotonic()
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(spec.url, data=data, headers=spec.headers, method='POST')
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read())
                row['status'] = resp.status
                usage = extract_usage(raw, spec.response_kind)
                row.update({k: usage[k] for k in ('input_tokens', 'output_tokens', 'thinking_tokens', 'text_len')})
                row['ok'] = True
        except urllib.error.HTTPError as e:
            row['status'] = e.code
            row['error'] = e.read()[:200].decode(errors='replace')
        except Exception as ex:
            row['error'] = str(ex)[:200]
        row['latency_ms'] = round((time.monotonic() - start) * 1000)
        results.append(row)
        time.sleep(1.0)
    return results

def _env_or_file(env_keys: list[str], file_path: str) -> str | None:
    for k in env_keys:
        v = os.environ.get(k)
        if v:
            return v
    secrets = load_env_file(file_path)
    for k, v in secrets.items():
        if 'KEY' in k.upper() and v.startswith('sk-'):
            return v
    return None

def build_specs() -> list[ProbeSpec]:
    specs: list[ProbeSpec] = []

    # MiniMax
    key_mm = _env_or_file(['MINIMAX_API_KEY'], os.path.expanduser('~/.secrets/minimax.env'))
    if key_mm:
        base = {'model': 'MiniMax-M2.7', 'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': PROMPT}]}
        hdrs = {'Authorization': f'Bearer {key_mm}', 'anthropic-version': '2023-06-01',
                'Content-Type': 'application/json'}
        specs.append(ProbeSpec(
            provider='MiniMax', url='https://api.minimaxi.chat/anthropic/v1/messages',
            headers=hdrs, base_body=base, response_kind='anthropic',
            variants=[
                ('baseline', {}),
                ('thinking_adaptive', {'thinking': {'type': 'adaptive'}}),
                ('thinking_disabled', {'thinking': {'type': 'disabled'}}),
                ('effort_low', {'output_config': {'effort': 'low'}}),
                ('effort_high', {'output_config': {'effort': 'high'}}),
            ]))

    # GLM
    key_glm = _env_or_file(['GLM_API_KEY', 'ZAI_API_KEY'], os.path.expanduser('~/.secrets/glm.env'))
    if key_glm:
        base = {'model': 'glm-4.7', 'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': PROMPT}]}
        hdrs = {'Authorization': f'Bearer {key_glm}', 'Content-Type': 'application/json'}
        specs.append(ProbeSpec(
            provider='GLM', url='https://api.z.ai/api/paas/v4/chat/completions',
            headers=hdrs, base_body=base, response_kind='openai',
            variants=[
                ('baseline', {}),
                ('thinking_disabled', {'thinking': {'type': 'disabled'}}),
                ('thinking_enabled', {'thinking': {'type': 'enabled'}}),
                ('reasoning_low', {'reasoning_effort': 'low'}),
                ('reasoning_high', {'reasoning_effort': 'high'}),
            ]))

    # LOCALE
    local_secrets = load_env_file(os.path.expanduser('~/.claude/secrets/local-llm.env'))
    local_key = None
    for k, v in local_secrets.items():
        if 'KEY' in k.upper() and v.startswith('sk-'):
            local_key = v
            break
    if local_key:
        base = {'model': 'code-max', 'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': PROMPT}]}
        hdrs = {'Authorization': f'Bearer {local_key}', 'Content-Type': 'application/json'}
        specs.append(ProbeSpec(
            provider='LOCALE', url='http://127.0.0.1:4000/v1/chat/completions',
            headers=hdrs, base_body=base, response_kind='openai',
            variants=[
                ('baseline', {}),
                ('reasoning_low', {'reasoning_effort': 'low'}),
                ('reasoning_medium', {'reasoning_effort': 'medium'}),
                ('reasoning_high', {'reasoning_effort': 'high'}),
            ]))

    # QWEN
    key_q = _env_or_file(['QWEN_API_KEY', 'DASHSCOPE_API_KEY'], '')
    if key_q:
        base = {'model': 'qwen3-coder-plus', 'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': PROMPT}]}
        hdrs = {'Authorization': f'Bearer {key_q}', 'Content-Type': 'application/json'}
        specs.append(ProbeSpec(
            provider='QWEN', url='https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions',
            headers=hdrs, base_body=base, response_kind='openai',
            variants=[
                ('baseline', {}),
                ('thinking_off', {'enable_thinking': False}),
                ('thinking_on', {'enable_thinking': True}),
                ('budget_small', {'enable_thinking': True, 'thinking_budget': 256}),
                ('budget_large', {'enable_thinking': True, 'thinking_budget': 4096}),
            ]))

    return specs

def verdict(rows: list[dict]) -> str:
    """Giudica se il parametro ha EFFETTO, confrontando il segnale con il rumore.

    La versione precedente dichiarava 'ONORATO' quando lo scarto fra le varianti
    superava il 15 percento, senza mai guardare la varianza interna. Su MiniMax
    avrebbe detto 'onorato' mentre il pattern era incoerente (low produceva PIU'
    token di high): quello scarto era rumore, non risposta al parametro. Un
    verdetto che non distingue le due cose e' peggio di nessun verdetto, perche'
    chi lo legge ci crede.

    Ora servono DUE condizioni insieme:
      1. lo scarto fra le medie supera SIGNAL_NOISE_RATIO volte il rumore interno;
      2. l'ordine e' coerente (se esistono un livello basso e uno alto, il basso
         deve produrre meno token dell'alto).
    Con una sola ripetizione per variante il rumore non e' stimabile e il verdetto
    resta esplicitamente INCONCLUSIVO invece di inventare una risposta.
    """
    ok = [r for r in rows if r.get("ok")]
    if not ok:
        motivo = str(rows[0].get("error", "?"))[:60] if rows else "nessun dato"
        return f"NON MISURABILE ({motivo})"

    per_variant: dict[str, list[int]] = {}
    for r in ok:
        per_variant.setdefault(r.get("variant", "?"), []).append(r.get("output_tokens", 0))
    if len(per_variant) < 2:
        return "INCONCLUSIVO (meno di due varianti riuscite)"

    means = {v: statistics.mean(x) for v, x in per_variant.items()}
    sds = [statistics.stdev(x) for x in per_variant.values() if len(x) > 1]
    if not sds:
        return (f"INCONCLUSIVO (una sola ripetizione per variante: il rumore non e' "
                f"stimabile; scarto osservato {max(means.values()) - min(means.values()):.0f} token)")

    spread = max(means.values()) - min(means.values())
    noise = statistics.mean(sds)
    ratio = spread / noise if noise else float("inf")

    coerente = None
    for lo, hi in (("effort_low", "effort_high"), ("reasoning_low", "reasoning_high"),
                   ("thinking_disabled", "thinking_enabled"), ("budget_small", "budget_large")):
        if lo in means and hi in means:
            coerente = means[lo] < means[hi]
            break

    rel_pct = spread / statistics.mean(list(means.values())) * 100

    if ratio < SIGNAL_NOISE_RATIO:
        return f"NESSUN EFFETTO (scarto {spread:.0f} vs rumore {noise:.0f}, rapporto {ratio:.1f})"
    if rel_pct < MIN_RELEVANT_PCT:
        # Distinzione che serve a chi legge: un effetto puo' essere statisticamente
        # reale e operativamente inutile. 35 token su 900 sono misurabili ma non
        # cambiano nessuna decisione di costo.
        return (f"EFFETTO TRASCURABILE (statisticamente reale, rapporto {ratio:.1f}, "
                f"ma solo {rel_pct:.1f}% di scarto: non cambia i costi)")
    if coerente is False:
        return (f"ACCETTATO MA NON ONORATO (scarto {spread:.0f} sopra il rumore ma ORDINE "
                f"INCOERENTE: il livello basso produce piu' token dell'alto)")
    if coerente is None:
        return f"EFFETTO PLAUSIBILE, ordine non verificabile (rapporto {ratio:.1f})"
    return f"ONORATO (scarto {spread:.0f}, rumore {noise:.0f}, rapporto {ratio:.1f}, ordine coerente)"

def _fmt_row(rows: list[dict]) -> list[str]:
    hdrs = ['provider', 'variant', 'status', 'out_tok', 'think_tok', 'text_len', 'lat_ms']
    widths = [max(len(h), max(len(str(r.get(h.replace('out_tok','output_tokens').replace('think_tok','thinking_tokens').replace('text_len','text_len').replace('lat_ms','latency_ms') or r.get(k,'')))) for r in rows)) for h, k in [
        ('provider', 'provider'), ('variant', 'variant'), ('status', 'status'),
        ('out_tok', 'output_tokens'), ('think_tok', 'thinking_tokens'),
        ('text_len', 'text_len'), ('lat_ms', 'latency_ms')]]
    for i, (h, w) in enumerate(zip(hdrs, widths)):
        widths[i] = max(w, len(h))
    sep = '  '
    line = sep.join(h.ljust(w) for h, w in zip(hdrs, widths))
    div = sep.join('-' * w for w in widths)
    out = [line, div]
    for r in rows:
        vals = [str(r.get('provider','')), r.get('variant',''), str(r.get('status','')),
                str(r.get('output_tokens','')), str(r.get('thinking_tokens','')),
                str(r.get('text_len','')), str(r.get('latency_ms',''))]
        out.append(sep.join(v.ljust(w) for v, w in zip(vals, widths)))
    return out

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=f'bench/results/providers-{int(time.time())}.jsonl')
    ap.add_argument('--only')
    args = ap.parse_args()

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_specs = build_specs()
    skipped: list[str] = []
    probed: list[tuple[str, list[dict]]] = []

    known = {'MiniMax', 'GLM', 'LOCALE', 'QWEN'}
    all_providers = {s.provider for s in all_specs}
    for p in known:
        if p not in all_providers:
            skipped.append(p)

    for spec in all_specs:
        if args.only and spec.provider != args.only:
            continue
        rows = probe(spec)
        probed.append((spec.provider, rows))
        with out_path.open('a') as fh:
            for r in rows:
                fh.write(json.dumps(r) + '\n')
            fh.flush()

    all_rows_flat: list[dict] = []
    for _, rows in probed:
        all_rows_flat.extend(rows)

    if all_rows_flat:
        lines = _fmt_row(all_rows_flat)
        print('\n'.join(lines))

    print()
    for provider, rows in probed:
        v = verdict(rows)
        print(f'{provider}: {v}')

    if skipped:
        print()
        print(f'Saltati (credenziali mancanti): {", ".join(skipped)}')

if __name__ == '__main__':
    main()
