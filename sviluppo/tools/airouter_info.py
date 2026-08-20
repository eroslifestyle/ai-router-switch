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




def _generazioni_ruotate():
    """Yield (path, num) di tutte le generazioni ruotate del sidecar, dalla piu'
    vecchia alla piu' recente (cosi' le entry risultano ordinate nel tempo).

    Schema rotazione: router-usage.jsonl.1 (piu' recente), .2, ...
    La numerazione crescente = generazioni piu' vecchie."""
    for n in range(99, 0, -1):
        p = SIDECAR.parent / (SIDECAR.name + "." + str(n))
        if p.exists():
            yield p, n


def _prima_entry_ts(path: Path) -> float | None:
    """Timestamp della prima riga valida di un file jsonl, o None se illeggibile."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line).get("ts")
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _ultima_entry_ts(path: Path) -> float | None:
    """Timestamp dell'ultima riga valida di un file jsonl, o None se illeggibile."""
    ts = None
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = json.loads(line).get("ts")
                except Exception:
                    continue
    except Exception:
        pass
    return ts


def _leggi_sidecar(args, files_info=None):
    """Legge il sidecar e le sue generazioni ruotate.

    ponytail: leggiamo tutti i file richiesti dalla finestra senza preoccuparci
    della loro dimensione; se un .1 da 67 MB fosse un problema siamo su un PC,
    non su un container con 256 MB. Il guadagno in correttezza supera il costo.
    """
    taglio = 0.0
    giorni, mode = args.giorni, args.mode
    if getattr(args, "ore", None):
        taglio = (dt.datetime.now() - dt.timedelta(hours=args.ore)).timestamp()
    elif giorni:
        taglio = (dt.datetime.now() - dt.timedelta(days=giorni)).timestamp()
    righe = []
    scartate = 0
    if files_info is None:
        files_info = {}
    if not SIDECAR.exists():
        return righe, scartate, files_info

    # ponytail: leggiamo .1/.2 SOLO se la finestra si estende piu' indietro della
    # prima entry del file corrente. Se i dati richiesti stanno gia' nel corrente,
    # non apriamo nemmeno il ruotato. Questo risparmia I/O su un .1 da 67 MB.
    file_da_leggere = [(SIDECAR, "corrente")]
    if giorni or getattr(args, "ore", None):
        prima_ts_corrente = _prima_entry_ts(SIDECAR)

    for gen, _ in _generazioni_ruotate():
        # Apri il ruotato se l'ULTIMA sua entry e' dentro la finestra
        # (il file contiene dati rilevanti anche se inizia prima del taglio)
        if _ultima_entry_ts(gen) >= taglio:
            file_da_leggere.append((gen, gen.name))
        elif prima_ts_corrente is None:
            file_da_leggere.append((gen, gen.name))

    for path_f, label in file_da_leggere:
        files_info[label] = {"path": str(path_f), "righe": 0, "ts_min": None, "ts_max": None}
        with path_f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                files_info[label]["righe"] += 1
                try:
                    d = json.loads(line)
                except Exception:
                    scartate += 1
                    continue
                ts = d.get("ts") or 0
                if files_info[label]["ts_min"] is None:
                    files_info[label]["ts_min"] = ts
                files_info[label]["ts_max"] = ts
                if ts < taglio:
                    continue
                if mode and d.get("mode") != mode:
                    continue
                righe.append(d)

    # ponytail: l'avviso viene emesso dal chiamante che ha la finestra richiesta.
    return righe, scartate, files_info


def _avvisa_se_finestra_supera_dati(args, files_info):
    """Se la finestra richiesta va oltre i dati disponibili, emette un avviso."""
    if not files_info:
        return
    ts_minimo = min(f["ts_min"] for f in files_info.values() if f["ts_min"])
    if ts_minimo is None:
        return
    ora = dt.datetime.now().timestamp()
    giorni_disponibili = (ora - ts_minimo) / 86400
    giorni_richiesti = args.giorni or 999999
    ore_richieste = getattr(args, 'ore', None)
    richiesta_str = f"{ore_richieste} ore" if ore_richieste else f"{giorni_richiesti} giorni"
    data_minima = dt.datetime.fromtimestamp(ts_minimo).strftime("%Y-%m-%d %H:%M")
    print(f"  dati disponibili: da {data_minima} ({giorni_disponibili:.1f} giorni fa)")
    if ore_richieste:
        ore_disponibili = giorni_disponibili * 24
        if ore_richieste > ore_disponibili:
            print(f"  ⚠ richieste {richiesta_str}, dati disponibili solo per {ore_disponibili:.0f} ore")
    elif giorni_richiesti > giorni_disponibili:
        print(f"  ⚠ richiesti {richiesta_str}, dati disponibili solo per {giorni_disponibili:.0f} giorni")


def _tab(intestazioni, righe):
    larg = [max(len(str(intestazioni[i])), *(len(str(r[i])) for r in righe)) if righe
            else len(str(intestazioni[i])) for i in range(len(intestazioni))]
    print("  " + "  ".join(str(h).rjust(larg[i]) for i, h in enumerate(intestazioni)))
    for r in righe:
        print("  " + "  ".join(str(c).rjust(larg[i]) for i, c in enumerate(r)))


def cmd_cache(args):
    """Quanto del contesto viene riletto dalla cache invece che ricreato."""
    righe, scartate, files_info = _leggi_sidecar(args)
    _avvisa_se_finestra_supera_dati(args, files_info)
    # scartate e' gia' sommato su tutti i file
    if scartate:
        print(f"  ⚠ {scartate} righe non parsabili (sidecar corrotto) — esclude dal totale")
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
    # Segnala i provider che non riportano mai cache_creation: la colonna "con cache"
    # conta solo le letture, quindi sottostima. Verificato con probe reale GLM il
    # 2026-08-20: glm-4.7 non espone il campo nemmeno quando crea la cache.
    for p, s in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        if s["cc"] == 0 and s["hit"] > 0:
            print(f"  ⚠ {p}: cache_creation mai riportata dal provider — 'con cache' conta solo le letture, e' una SOTTOSTIMA")

def cmd_costo(args):
    """Da cosa e' fatto l'input che paghiamo: definizioni tool o conversazione."""
    righe, scartate, files_info = _leggi_sidecar(args)
    _avvisa_se_finestra_supera_dati(args, files_info)
    # scartate e' gia' sommato su tutti i file
    if scartate:
        print(f"  ⚠ {scartate} righe non parsabili (sidecar corrotto) — esclude dal totale")
    agg = defaultdict(lambda: {"n": 0, "in": 0, "fresco": 0, "tb": 0, "mcp": 0, "n_tb": 0})
    for d in righe:
        p = _provider(d.get("final"))
        if not p or p == "router-internal":
            continue
        s = agg[p]
        s["n"] += 1
        # Il contesto processato e' input + cache: rapportare le definizioni tool al
        # solo `input_tokens` dava «quota tool 242878%» da quando la cache funziona,
        # perche' quel campo vale ~2 mentre il contesto vero sta in `cache_read`.
        s["in"] += ((d.get("input_tokens") or 0) + (d.get("cache_read") or 0)
                    + (d.get("cache_creation") or 0))
        s["fresco"] += d.get("input_tokens") or 0
        if d.get("tools_bytes"):
            s["tb"] += d["tools_bytes"]
            s["mcp"] += d.get("tools_mcp_bytes") or 0
            s["n_tb"] += 1
    out = []
    for p, s in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        n = s["n"] or 1
        tool_tok = (s["tb"] / max(s["n_tb"], 1)) / 4  # stima: 4 byte per token
        ctx = s["in"] / n
        out.append([p, f"{s['n']:,}", f"{ctx:,.0f}", f"{s['fresco']/n:,.0f}",
                    f"{tool_tok:,.0f}", f"{tool_tok/ctx:.0%}" if ctx else "-",
                    f"{(s['mcp']/max(s['n_tb'],1))/4:,.0f}"])
    print(f"composizione del contesto processato — {_finestra(args)} (stima tool: byte/4)")
    _tab(["provider", "richieste", "contesto/req", "di cui nuovo", "tool tok/req",
          "quota tool", "di cui MCP"], out)
    print("\n  «di cui nuovo» e' l'input NON coperto dalla cache: e' quello che si paga pieno.")
    print("  Con la cache sana le definizioni tool si pagano una volta per conversazione.")


def cmd_tool(args):
    """Quanto pesano le definizioni dei tool e quali server MCP costano."""
    righe, scartate, files_info = _leggi_sidecar(args)
    _avvisa_se_finestra_supera_dati(args, files_info)
    if scartate:
        print(f"  ⚠ {scartate} righe non parsabili (sidecar corrotto) — esclude dal totale")

    # --- A) aggregazione per provider ---
    agg = defaultdict(lambda: {
        "n": 0, "n_tool": 0,
        "tb_vals": [], "mcp_vals": [], "quota_vals": [],
        "ingenuo": 0, "reale": 0,
    })
    for d in righe:
        p = _provider(d.get("final"))
        if not p or p == "router-internal":
            continue
        s = agg[p]
        s["n"] += 1
        if not d.get("tools_bytes"):
            continue
        s["n_tool"] += 1
        tb = d["tools_bytes"]
        mcp = d.get("tools_mcp_bytes") or 0
        tok = tb / 4
        mcp_tok = mcp / 4
        s["tb_vals"].append(tok)
        s["mcp_vals"].append(mcp_tok)
        ctx = ((d.get("input_tokens") or 0) + (d.get("cache_read") or 0)
               + (d.get("cache_creation") or 0))
        if ctx:
            s["quota_vals"].append(tok / ctx)

        # costo ingenuo: peso per TUTTE le richieste
        s["ingenuo"] += tok
        # costo reale: peso solo se la cache non ha letto nulla (prefisso fresco)
        if not d.get("cache_read"):
            s["reale"] += tok

    # --- B) breakdown per server MCP ---
    server_tot = defaultdict(lambda: {"tok": 0, "n": 0})
    server_nome = {}  # rotonda_nome -> nome_originale per output
    for d in righe:
        mcp_servers = d.get("tools_mcp_servers") or {}
        for nome, info in mcp_servers.items():
            tok = (info / 4) if isinstance(info, int) else (info.get("bytes", 0) or 0) / 4
            rotonda = nome[:32]
            server_tot[rotonda]["tok"] += tok
            server_tot[rotonda]["n"] += 1
            server_nome[rotonda] = nome

    # --- output ---
    totali_n = sum(s["n"] for s in agg.values())
    totali_n_tool = sum(s["n_tool"] for s in agg.values())

    print(f"peso tool — {_finestra(args)} (stima: byte/4, mediana)")
    print(f"  righe totali: {totali_n:,}  con telemetria tool: {totali_n_tool:,}")

    out = []
    for p, s in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        med = s["tb_vals"] and sorted(s["tb_vals"])[len(s["tb_vals"]) // 2]
        med_mcp = s["mcp_vals"] and sorted(s["mcp_vals"])[len(s["mcp_vals"]) // 2]
        med_q = s["quota_vals"] and sorted(s["quota_vals"])[len(s["quota_vals"]) // 2]
        def _fmt_k(v):
            if v >= 1e6: return f"{v/1e6:.1f}M"
            if v >= 1e3: return f"{v/1e3:.0f}k"
            return f"{v:.0f}"
        out.append([
            p, f"{s['n_tool']:,}", f"{med:,.0f}" if med is not None else "-",
            f"{med_mcp:,.0f}" if med_mcp is not None else "-",
            f"{med_q:.0%}" if med_q is not None else "-",
            _fmt_k(s["ingenuo"]), _fmt_k(s["reale"]),
            f"{1 - s['reale']/max(s['ingenuo'], 1):.0%}"])
    _tab(["provider", "con tool", "tok/req", "di cui MCP", "quota ctx",
          "ingenuo", "reale", "risparmiato"], out)
    print("\n  ingenuo = peso × tutte le richieste.")
    print("  reale   = peso × solo quelle con cache_read=0 (prefisso fresco).")
    print("  risparmiato = quanto la cache ha gia' recuperato del costo tool.")

    if server_tot:
        print("\nserver MCP (byte/4 per richiesta in cui compaiono):")
        out_srv = []
        for rotonda, info in sorted(server_tot.items(), key=lambda kv: -kv[1]["tok"]):
            nome = server_nome[rotonda]
            nome_out = (nome[:28] + "...") if len(nome) > 31 else nome
            out_srv.append([nome_out, f"{info['tok']/max(info['n'], 1):,.0f}",
                            f"{info['n']:,}"])
        _tab(["server", "tok/req", "richieste"], out_srv)


def cmd_sprechi(args):
    """Contesto ri-pagato che sarebbe stato riusabile dalla cache."""
    righe, scartate, files_info = _leggi_sidecar(args)
    _avvisa_se_finestra_supera_dati(args, files_info)
    # scartate e' gia' sommato su tutti i file
    if scartate:
        print(f"  ⚠ {scartate} righe non parsabili (sidecar corrotto) — esclude dal totale")
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
        # Denominatore: TUTTO il contesto processato, non il solo input non-cachato.
        # Con la cache sana `input_tokens` vale ~2 e ogni rapporto costruito su di
        # esso esplode: la colonna dichiarava «anthropic 99% del suo input» mentre
        # il 99% del contesto veniva letto dalla cache. Stesso difetto che rende
        # illeggibile `costo` da quando la cache funziona (2026-08-16).
        s["tot"] += inp + (d.get("cache_read") or 0) + (d.get("cache_creation") or 0)
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
    _tab(["provider", "richieste", "a vuoto", "token", "% del contesto"], out)
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
    # Integrita' sidecar: riutilizza _leggi_sidecar per avere l'intervallo coperto.
    class _A:
        giorni = None; ore = None; mode = None
    _, scartate, files_info = _leggi_sidecar(_A())

    if files_info:
        # Aggrega statistiche su tutti i file
        tot_righe = sum(f["righe"] for f in files_info.values())
        tot_parsabili = tot_righe - scartate
        stato_sc = f"{tot_parsabili}/{tot_righe} parsabili"
        if scartate:
            stato_sc += f"  ⚠ {scartate} corrotte"
        print(f"  sidecar  {stato_sc}")

        # Mostra quali file sono stati letti e l'intervallo temporale
        for label, info in sorted(files_info.items(), key=lambda x: x[1]["ts_min"] or 0):
            ts_min = info["ts_min"]
            ts_max = info["ts_max"]
            if ts_min:
                data_min = dt.datetime.fromtimestamp(ts_min).strftime("%Y-%m-%d %H:%M")
                data_max = dt.datetime.fromtimestamp(ts_max).strftime("%Y-%m-%d %H:%M")
                intervallo = f"{data_min} → {data_max}"
            else:
                intervallo = "vuoto"
            print(f"    {label:<25} {info['righe']:>8} righe   {intervallo}")
    else:
        print(f"  sidecar  ASSENTE")

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
    def _mai_riferito(nome, f):
        pat = re.compile(rf"\b{re.escape(nome)}\b")
        rif = sum(len(pat.findall(t)) - (1 if p == f else 0) for p, t in testi.items())
        return rif <= 0

    orfani, totale = [], 0
    orfani_metodi, totale_metodi = [], 0
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
            if not (nome.startswith("__") or nome in ("main", "setup", "handle")):
                if _mai_riferito(nome, f):
                    orfani.append(f"{f.relative_to(REPO)}:{nodo.lineno}  {nome}")
            # I metodi di classe non erano contati, e ci si nascondeva del residuo:
            # ContextManager.post_check, .reassign e .acquire erano l'API pubblica
            # del modulo, mai chiamate da nessuno, e questo comando dichiarava
            # comunque «0 mai referenziati» (2026-08-16, rimosse).
            if isinstance(nodo, ast.ClassDef):
                for m in nodo.body:
                    if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if m.name.startswith("__") and m.name.endswith("__"):
                        continue
                    totale_metodi += 1
                    if _mai_riferito(m.name, f):
                        orfani_metodi.append(
                            f"{f.relative_to(REPO)}:{m.lineno}  {nome}.{m.name}()")

    print(f"simboli top-level in src/: {totale}   mai referenziati: {len(orfani)}")
    for o in orfani:
        print(f"    {o}")
    print(f"metodi di classe in src/:  {totale_metodi}   mai referenziati: {len(orfani_metodi)}")
    for o in orfani_metodi:
        print(f"    {o}")
    print("\n  «mai referenziato» non e' una diagnosi: puo' essere un residuo da togliere")
    print("  oppure una funzionalita' mai agganciata. Va deciso caso per caso.")
    print("  Il criterio e' il nome nudo, quindi e' prudente: un metodo che si chiama")
    print("  come qualcos'altro nel repo (close, run, acquire) risulta vivo comunque.")


def main():
    ap = argparse.ArgumentParser(prog="airouter-info", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonti", action="store_true", help="elenca le fonti dei dati ed esce")
    sub = ap.add_subparsers(dest="cmd")
    for nome, fn in (("cache", cmd_cache), ("costo", cmd_costo), ("tool", cmd_tool),
                     ("sprechi", cmd_sprechi), ("scritture", cmd_scritture),
                     ("salute", cmd_salute), ("orfani", cmd_orfani)):
        p = sub.add_parser(nome, help=(fn.__doc__ or "").strip().splitlines()[0])
        p.set_defaults(fn=fn)
        if nome in ("cache", "costo", "sprechi", "tool"):
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
