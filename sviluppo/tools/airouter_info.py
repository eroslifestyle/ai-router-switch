#!/usr/bin/env python3
"""airouter-info — interrogazione ON-DEMAND dei dati del router.

Perche' esiste: le analisi sul consumo si facevano scrivendo ogni volta uno
script usa-e-getta (11 in una sola sessione, il 2026-08-16), sparsi fuori dal
progetto e senza contratto comune. Ognuno rileggeva le stesse fonti con criteri
leggermente diversi, e il risultato dipendeva da quale script avevi sottomano.

Principi:
1. UNA fonte per ogni dato, dichiarata (`--fonti` le elenca).
2. Output DIGEST: poche righe, mai il dato grezzo. Il contesto e' il budget.
3. On-demand: nessun processo, nessun cron, nessuno stato. Si legge quando serve.
4. Ogni numero e' riproducibile: `--come` stampa il criterio usato.

Uso:
    airouter-info cache [--giorni N|--ore N] [--mode M]   efficienza del prompt caching
    airouter-info costo [--giorni N|--ore N]              da cosa e' fatto l'input pagato
    airouter-info sprechi [--giorni N|--ore N]            contesto ri-pagato invece che riusato
    airouter-info scritture                       Write vs Edit per modalita'
    airouter-info salute                          router, porte, modalita' attiva
    airouter-info orfani                          simboli src/ mai referenziati
"""
import argparse
import json
import os
import re
import subprocess
import sys
import datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

CONFIG = Path(os.environ.get("AIROUTER_HOME", Path.home() / ".claude"))
SIDECAR = CONFIG / "logs" / "router-usage.jsonl"
LOG_ROUTER = CONFIG / "logs" / "ai-router.log"
LOG_HOOK = CONFIG / "m3" / "hierarchy-violations.jsonl"
REPO = Path(__file__).resolve().parents[2]

# Prezzi relativi Anthropic-style: scrivere cache costa 1.25x l'input, leggerla 0.1x.
PESO_CACHE_WRITE, PESO_CACHE_READ = 1.25, 0.1

FONTI = {
    "sidecar": (SIDECAR, "una riga per richiesta inoltrata: token, cache, modalita', provider"),
    "log router": (LOG_ROUTER, "righe 'cache:' con i breakpoint, conversioni role=system, shrink"),
    "log hook": (LOG_HOOK, "ogni Edit/Write/Bash osservato: tool, file, esito, motivo"),
}


def _provider(final: str) -> str | None:
    f = (final or "").lower()
    for k in ("minimax", "glm", "qwen"):
        if k in f:
            return k
    if "code-max" in f or f.startswith("local"):
        return "local"
    if any(k in f for k in ("claude", "opus", "sonnet", "haiku", "fable")):
        return "anthropic"
    if "router-internal" in f:
        return "router-internal"
    return None


def _finestra(args) -> str:
    """Etichetta leggibile della finestra temporale richiesta."""
    ore = getattr(args, "ore", None)
    if ore:
        return f"ultime {ore} ore"
    return f"ultimi {args.giorni} giorni" if args.giorni else "tutto lo storico"


def _leggi_sidecar(args):
    # --ore vince su --giorni: serve a isolare una finestra stretta (es. il traffico
    # dopo un restart), dove 24h di granularita' mescolano il prima e il dopo.
    taglio = 0.0
    giorni, mode = args.giorni, args.mode
    if getattr(args, "ore", None):
        taglio = (dt.datetime.now() - dt.timedelta(hours=args.ore)).timestamp()
    elif giorni:
        taglio = (dt.datetime.now() - dt.timedelta(days=giorni)).timestamp()
    righe = []
    if not SIDECAR.exists():
        return righe
    with SIDECAR.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if (d.get("ts") or 0) < taglio:
                continue
            if mode and d.get("mode") != mode:
                continue
            righe.append(d)
    return righe


def _tab(intestazioni, righe):
    larg = [max(len(str(intestazioni[i])), *(len(str(r[i])) for r in righe)) if righe
            else len(str(intestazioni[i])) for i in range(len(intestazioni))]
    print("  " + "  ".join(str(h).rjust(larg[i]) for i, h in enumerate(intestazioni)))
    for r in righe:
        print("  " + "  ".join(str(c).rjust(larg[i]) for i, c in enumerate(r)))


def cmd_cache(args):
    """Quanto del contesto viene riletto dalla cache invece che ricreato."""
    righe = _leggi_sidecar(args)
    agg = defaultdict(lambda: {"n": 0, "cr": 0, "cc": 0, "in": 0, "hit": 0})
    for d in righe:
        p = _provider(d.get("final"))
        if not p or p == "router-internal":
            continue
        s = agg[p]
        s["n"] += 1
        s["cr"] += d.get("cache_read") or 0
        s["cc"] += d.get("cache_creation") or 0
        s["in"] += d.get("input_tokens") or 0
        if (d.get("cache_read") or 0) > 0:
            s["hit"] += 1
    out = []
    for p, s in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        n = s["n"] or 1
        out.append([p, f"{s['n']:,}", f"{s['hit']/n:.0%}", f"{s['cr']/n:,.0f}",
                    f"{s['cc']/n:,.0f}", f"{s['cc']/s['cr']:.3f}" if s["cr"] else "-"])
    print(f"efficienza cache — {_finestra(args)}")
    _tab(["provider", "richieste", "con cache", "read/req", "creation/req", "creation/read"], out)
    print("\n  creation/read alto = il prefisso cambia a ogni turno: qualcosa a monte cresce.")


def cmd_costo(args):
    """Da cosa e' fatto l'input che paghiamo: definizioni tool o conversazione."""
    righe = _leggi_sidecar(args)
    agg = defaultdict(lambda: {"n": 0, "in": 0, "tb": 0, "mcp": 0, "n_tb": 0})
    for d in righe:
        p = _provider(d.get("final"))
        if not p or p == "router-internal":
            continue
        s = agg[p]
        s["n"] += 1
        s["in"] += d.get("input_tokens") or 0
        if d.get("tools_bytes"):
            s["tb"] += d["tools_bytes"]
            s["mcp"] += d.get("tools_mcp_bytes") or 0
            s["n_tb"] += 1
    out = []
    for p, s in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        n = s["n"] or 1
        tool_tok = (s["tb"] / max(s["n_tb"], 1)) / 4  # stima: 4 byte per token
        inp = s["in"] / n
        out.append([p, f"{s['n']:,}", f"{inp:,.0f}", f"{tool_tok:,.0f}",
                    f"{tool_tok/inp:.0%}" if inp else "-",
                    f"{(s['mcp']/max(s['n_tb'],1))/4:,.0f}"])
    print(f"composizione dell'input pagato — {_finestra(args)} (stima tool: byte/4)")
    _tab(["provider", "richieste", "input/req", "tool tok/req", "quota tool", "di cui MCP"], out)
    print("\n  senza caching le definizioni tool si ripagano a ogni richiesta.")


def cmd_sprechi(args):
    """Contesto ri-pagato che sarebbe stato riusabile dalla cache."""
    righe = _leggi_sidecar(args)
    righe.sort(key=lambda d: d.get("ts") or 0)
    SOGLIA, FINESTRA = 5000, 300
    ultimo, agg = {}, defaultdict(lambda: {"n": 0, "sprec": 0, "tok": 0, "tot": 0})
    for d in righe:
        p = _provider(d.get("final"))
        if not p or p == "router-internal":
            continue
        s = agg[p]
        s["n"] += 1
        inp = d.get("input_tokens") or 0
        s["tot"] += inp
        ts = d.get("ts") or 0
        prec = ultimo.get(p)
        if inp > SOGLIA and not (d.get("cache_read") or 0) and prec and (ts - prec) <= FINESTRA:
            s["sprec"] += 1
            s["tok"] += inp
        ultimo[p] = ts
    out, tot = [], 0
    for p, s in sorted(agg.items(), key=lambda kv: -kv[1]["tok"]):
        tot += s["tok"]
        out.append([p, f"{s['n']:,}", f"{s['sprec']:,}", f"{s['tok']/1e6:,.1f}M",
                    f"{s['tok']/max(s['tot'],1):.0%}"])
    print(f"contesto ri-pagato — {_finestra(args)} (input>{SOGLIA:,}, cache_read=0, entro {FINESTRA}s "
          f"da una richiesta dello stesso provider)")
    _tab(["provider", "richieste", "a vuoto", "token", "% del suo input"], out)
    print(f"\n  totale: {tot/1e6:,.0f}M token che la cache avrebbe coperto.")


def cmd_scritture(args):
    """Write (riscrive tutto) contro Edit (modifica chirurgica), per modalita'."""
    MODI = ("mix-am-2", "mix-gm-2", "mix-ag-2", "mix-am", "mix-gm", "mix-ag", "mix-al",
            "minimax", "anthropic", "glm", "qwen")
    per_mode, denied = defaultdict(Counter), Counter()
    if not LOG_HOOK.exists():
        print("log hook assente:", LOG_HOOK)
        return
    with LOG_HOOK.open(errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            r = str(d.get("reason") or "")
            m = next((x for x in MODI if r.startswith(x + "-")), None)
            if not m:
                continue
            per_mode[m][d.get("tool")] += 1
            if d.get("action") == "denied":
                denied[m] += 1
    out = []
    for m, c in sorted(per_mode.items(), key=lambda kv: -sum(kv[1].values())):
        w, e, b = c.get("Write", 0), c.get("Edit", 0), c.get("Bash", 0)
        out.append([m, w, e, b, f"{w/max(e,1):.2f}", denied[m]])
    print("Write vs Edit per modalita' (dal log dell'hook di gerarchia)")
    _tab(["mode", "Write", "Edit", "Bash", "W/E", "denied"], out)
    print("\n  W/E alto = si rigenera il file invece di modificarlo: cio' che non e' nella")
    print("  spec sparisce, e cio' che non serve piu' resta. E' l'origine del codice morto.")


def cmd_salute(args):
    """Stato del servizio, modalita' attiva, porte in ascolto."""
    def sh(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except Exception:
            return "?"
    stato = sh("systemctl --user is-active ai-router")
    pid = sh("systemctl --user show -p MainPID --value ai-router")
    porte = sh(f"ss -ltnp 2>/dev/null | grep -c 'pid={pid},'") if pid.isdigit() else "?"
    mode = (CONFIG / "ai-router-mode").read_text().strip() if (CONFIG / "ai-router-mode").exists() else "?"
    print(f"  servizio       {stato}   pid={pid}")
    print(f"  modalita'      {mode}")
    print(f"  porte in ascolto del processo: {porte}")
    err = sh(f"tail -400 '{LOG_ROUTER}' | grep -ciE 'ERR listen|Traceback'")
    print(f"  errori nelle ultime 400 righe di log: {err}")
    ultime = sh(f"grep 'cache: ' '{LOG_ROUTER}' | tail -3 | sed -E 's/.*\\] cache: //'")
    if ultime:
        print("  ultime righe cache (read deve CRESCERE, creation restare bassa):")
        for r in ultime.splitlines():
            print(f"     {r}")


def cmd_orfani(args):
    """Simboli top-level di src/ mai referenziati altrove."""
    import ast
    src = REPO / "src"
    testi = {}
    for base in (REPO, CONFIG / "scripts", CONFIG / "hooks"):
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if (p.is_file() and p.suffix in {".py", ".sh", ".md", ".json", ".in", ".service"}
                    and not any(x in p.parts for x in (".git", "__pycache__", "graphify-out"))):
                try:
                    testi[p] = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
    orfani, totale = [], 0
    for f in sorted(src.glob("*.py")):
        try:
            albero = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for nodo in albero.body:
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            totale += 1
            nome = nodo.name
            if nome.startswith("__") or nome in ("main", "setup", "handle"):
                continue
            pat = re.compile(rf"\b{re.escape(nome)}\b")
            rif = sum(len(pat.findall(t)) - (1 if p == f else 0) for p, t in testi.items())
            if rif <= 0:
                orfani.append(f"{f.relative_to(REPO)}:{nodo.lineno}  {nome}")
    print(f"simboli top-level in src/: {totale}   mai referenziati: {len(orfani)}")
    for o in orfani:
        print(f"    {o}")
    print("\n  «mai referenziato» non e' una diagnosi: puo' essere un residuo da togliere")
    print("  oppure una funzionalita' mai agganciata. Va deciso caso per caso.")


def main():
    ap = argparse.ArgumentParser(prog="airouter-info", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonti", action="store_true", help="elenca le fonti dei dati ed esce")
    sub = ap.add_subparsers(dest="cmd")
    for nome, fn in (("cache", cmd_cache), ("costo", cmd_costo), ("sprechi", cmd_sprechi),
                     ("scritture", cmd_scritture), ("salute", cmd_salute), ("orfani", cmd_orfani)):
        p = sub.add_parser(nome, help=(fn.__doc__ or "").strip().splitlines()[0])
        p.set_defaults(fn=fn)
        if nome in ("cache", "costo", "sprechi"):
            p.add_argument("--giorni", type=int, default=None, help="finestra in giorni")
            p.add_argument("--ore", type=int, default=None, help="finestra in ore (vince su --giorni)")
            p.add_argument("--mode", default=None, help="filtra per modalita' del router")
    args = ap.parse_args()
    if args.fonti:
        for nome, (path, descr) in FONTI.items():
            stato = f"{path.stat().st_size/1e6:.1f}MB" if path.exists() else "ASSENTE"
            print(f"  {nome:12s} {stato:>10s}  {path}\n               {descr}")
        return 0
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
