#!/usr/bin/env python3
"""Stress di VOLUME sul router vivo: tiene il carico, e a che prezzo in token.

Complementare a stress_fix_20260816.py, che verifica i singoli fix. Qui si guarda
il comportamento aggregato: conversazioni che crescono turno dopo turno su piu'
modalita' in parallelo, contando errori, token e tenuta della cache.

Tutto il traffico e' marcato ``x-airouter-synthetic: 1`` e passa dalle porte di
modalita': la modalita' globale non viene toccata.

Uso: python3 stress_volume_20260816.py [--turni 6] [--paralleli 3]
"""
import argparse
import concurrent.futures as futures
import gzip
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict

PORTE = {"anthropic": 8771, "glm": 8775, "mix-am-2": 8781, "mix-gm-2": 8784}
# Il modello dichiara il RUOLO: opus/haiku scelgono THINK o ACT dentro la modalita'.
MODELLO = {"anthropic": "claude-haiku-4-5-20251001", "glm": "claude-opus-5",
           "mix-am-2": "claude-haiku-4-5-20251001", "mix-gm-2": "claude-opus-5"}

FIRMA_ESTRANEA = "9be54cbc3de7463699fb38fa"  # firma GLM, misurata il 2026-08-16


def chiama(porta, payload, timeout=240):
    req = urllib.request.Request(
        f"http://127.0.0.1:{porta}/v1/messages", data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "accept-encoding": "identity", "x-airouter-synthetic": "1"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, st, enc = r.read(), r.status, (r.headers.get("content-encoding") or "")
    except urllib.error.HTTPError as e:
        raw, st, enc = e.read(), e.code, (e.headers.get("content-encoding") or "")
    except Exception as e:
        return {"status": 0, "errore": f"{type(e).__name__}", "ms": (time.monotonic()-t0)*1000}
    if "gzip" in enc.lower():
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"status": st, "errore": "corpo non JSON", "ms": (time.monotonic()-t0)*1000}
    u = d.get("usage") or {}
    testo = "".join(b.get("text", "") for b in (d.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "text")
    return {"status": st, "ms": (time.monotonic() - t0) * 1000,
            "in": int(u.get("input_tokens", 0) or 0),
            "out": int(u.get("output_tokens", 0) or 0),
            "read": int(u.get("cache_read_input_tokens", 0) or 0),
            "creation": int(u.get("cache_creation_input_tokens", 0) or 0),
            "stop": d.get("stop_reason"), "vuota": not testo,
            "errore": (d.get("error") or {}).get("type") if st != 200 else None}


def conversazione(nome, turni):
    """Una conversazione che cresce, con un thinking ESTRANEO nella cronologia:
    e' la forma che prima faceva fallire ogni turno THINK verso Anthropic."""
    zavorra = "contesto di lavoro. " * 1200          # ~24 KB, stabile fra i turni
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": f"[{nome}] {zavorra}", "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "ragiono sul compito",
             "signature": FIRMA_ESTRANEA},
            {"type": "text", "text": "Pronto."}]}]
    for t in range(turni):
        yield msgs + [{"role": "user", "content": f"turno {t}: rispondi con una riga."}]
        msgs = msgs + [{"role": "user", "content": f"turno {t}: rispondi con una riga."},
                       {"role": "assistant", "content": f"riga {t}. " + "dettaglio. " * 200}]


def giro(nome, turni):
    esiti = []
    for payload_msgs in conversazione(nome, turni):
        esiti.append(chiama(PORTE[nome], {"model": MODELLO[nome], "max_tokens": 128,
                                          "messages": payload_msgs}))
        time.sleep(0.4)
    return nome, esiti


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turni", type=int, default=6)
    ap.add_argument("--paralleli", type=int, default=3)
    ap.add_argument("--modi", default="anthropic,glm,mix-am-2,mix-gm-2")
    a = ap.parse_args()
    modi = [m for m in a.modi.split(",") if m in PORTE]

    print("=" * 82)
    print(f"STRESS DI VOLUME — {len(modi)} modalita' x {a.turni} turni x {a.paralleli} conversazioni")
    print("cronologia con thinking di firma estranea: prima ogni turno THINK dava 400")
    print("=" * 82)

    lavori = [(m, f"{m}-{k}") for m in modi for k in range(a.paralleli)]
    t0 = time.monotonic()
    risultati = defaultdict(list)
    with futures.ThreadPoolExecutor(max_workers=min(8, len(lavori))) as ex:
        for fut in futures.as_completed([ex.submit(giro, m, a.turni) for m, _ in lavori]):
            try:
                nome, esiti = fut.result()
                risultati[nome].extend(esiti)
            except Exception as e:
                print(f"  giro fallito: {type(e).__name__}: {e}")
    durata = time.monotonic() - t0

    print(f"\n{'mode':<11}{'req':>5}{'ok':>5}{'err':>5}{'vuote':>7}{'input':>10}"
          f"{'output':>9}{'read':>11}{'creat':>9}{'c/r':>7}{'p50 ms':>9}")
    print("-" * 88)
    tot = defaultdict(int)
    problemi = []
    for m in modi:
        e = risultati[m]
        if not e:
            continue
        ok = sum(1 for x in e if x["status"] == 200)
        err = len(e) - ok
        vuote = sum(1 for x in e if x.get("vuota"))
        s = {k: sum(x.get(k, 0) for x in e) for k in ("in", "out", "read", "creation")}
        ms = sorted(x["ms"] for x in e)
        cr = s["creation"] / s["read"] if s["read"] else float("nan")
        stop = defaultdict(int)
        for x in e:
            stop[str(x.get("stop"))] += 1
        print(f"{m:<11}{len(e):>5}{ok:>5}{err:>5}{vuote:>7}{s['in']:>10,}{s['out']:>9,}"
              f"{s['read']:>11,}{s['creation']:>9,}{cr:>7.3f}{ms[len(ms)//2]:>9,.0f}")
        print(f"{'':<11}stop_reason: {dict(sorted(stop.items(), key=lambda kv: -kv[1]))}")
        for k in ("in", "out", "read", "creation"):
            tot[k] += s[k]
        tot["req"] += len(e)
        tot["ok"] += ok
        for x in e:
            if x["status"] != 200:
                problemi.append(f"{m}: status={x['status']} {x.get('errore')}")
            elif x.get("vuota"):
                # Distinzione che conta: senza testo E fermata dal tetto di
                # max_tokens = il modello ha speso tutto il budget nel thinking e
                # al client non arriva nulla di utile — turno perso. Senza testo ma
                # con end_turn e' una risposta legittimamente senza blocchi text
                # (di solito un tool_use), non una perdita.
                if x.get("stop") == "max_tokens":
                    problemi.append(f"{m}: nessun testo, troncata a max_tokens "
                                    f"(out={x.get('out')}) — budget speso nel thinking")
                elif x.get("out", 0) == 0:
                    problemi.append(f"{m}: risposta vuota, stop={x.get('stop')}")
    print("-" * 88)
    print(f"{'TOT':<11}{tot['req']:>5}{tot['ok']:>5}{tot['req']-tot['ok']:>5}"
          f"{'':>7}{tot['in']:>10,}{tot['out']:>9,}{tot['read']:>11,}{tot['creation']:>9,}")
    print(f"\ndurata {durata:.1f}s · richieste riuscite {tot['ok']}/{tot['req']}"
          f" ({100*tot['ok']/max(1,tot['req']):.1f}%)")
    if problemi:
        print(f"\nPROBLEMI ({len(problemi)}):")
        for p in problemi[:15]:
            print(f"  {p}")
    else:
        print("\nnessun errore, nessuna risposta vuota.")
    return 0 if not problemi else 1


if __name__ == "__main__":
    raise SystemExit(main())
