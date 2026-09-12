#!/usr/bin/env python3
"""Supervisore LLM locale: legge gli aggregati perfmon, compone un prompt con i
numeri reali e lo passa a Ollama (qwen2.5:7b-instruct) per un'analisi.
Scrive report .md + .json in .claude/perfmon/reports/.

Uso:
  perfmon-report --status          # riepilogo rapido, nessuna chiamata a Ollama
  perfmon-report --last            # analizza l'ultima sessione attiva
  perfmon-report --session <id>    # analizza una sessione specifica

Kill-switch: PERFMON_DISABLE=1 -> esce subito.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGGREGATES_DIR = PROJECT_ROOT / ".claude" / "perfmon" / "aggregates"
REPORTS_DIR = PROJECT_ROOT / ".claude" / "perfmon" / "reports"
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
OLLAMA_TIMEOUT_SEC = 300
AIRouter_TIMEOUT_SEC = 15
JSON_MARKER = "---JSON---"


def _airouter_info_cmd(subcommand: str) -> list:
    """Comando per airouter-info: symlink se c'e', fallback python diretto."""
    symlink = Path.home() / ".local" / "bin" / "airouter-info"
    if symlink.exists():
        return [str(symlink), subcommand, "--giorni", "1"]
    return [sys.executable, str(PROJECT_ROOT / "sviluppo" / "tools" / "airouter_info.py"),
            subcommand, "--giorni", "1"]


def collect_airouter_info() -> dict:
    """Lancia cache/costo/sprechi e ritorna {sezione: (stdout | None, errore)}."""
    out = {}
    for section in ("cache", "costo", "sprechi"):
        try:
            proc = subprocess.run(
                _airouter_info_cmd(section),
                capture_output=True, text=True, timeout=AIRouter_TIMEOUT_SEC,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                out[section] = (proc.stdout.strip(), None)
            else:
                err = proc.stderr.strip() or f"exit code {proc.returncode}, stdout vuoto"
                out[section] = (None, err)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            out[section] = (None, str(exc))
    return out


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ATTENZIONE: impossibile leggere {path}: {exc}", file=sys.stderr)
        return None


def pick_last_session(latest: dict) -> str:
    """Sessione con priorita': running_now, altrimenti quella con piu' eventi."""
    running = latest.get("running_now") or []
    if running:
        return running[0].get("session_id", "")
    sessions = latest.get("sessions", {})
    if not sessions:
        return ""
    def event_count(data: dict) -> int:
        tools = data.get("by_tool_name", {})
        return sum(t.get("count", 0) for t in tools.values())
    return max(sessions, key=lambda sid: event_count(sessions[sid]))


def compact_baseline_rows(baseline: dict, top: int = 8) -> list:
    """Righe baseline con dati reali, ordinate per count decrescente (le altre
    sono bucket vuoti): il prompt resta piccolo, il contesto di qwen2.5 e' 32k."""
    rows = []
    for key, val in baseline.get("by_mode_model", {}).items():
        ttfb = val.get("ttfb_ms", {})
        if not val.get("count") or not ttfb.get("count"):
            continue
        total = val.get("total_ms", {})
        rows.append({
            "mode_model": key, "count": val["count"],
            "ttfb_p50": ttfb.get("p50"), "ttfb_p99": ttfb.get("p99"),
            "total_p50": total.get("p50"), "total_p99": total.get("p99"),
        })
    rows.sort(key=lambda r: -r["count"])
    return rows[:top]


def build_prompt(latest: dict, baseline, air: dict, session_id: str) -> str:
    # ponytail: elenco compatto invece del JSON integrale — i 97KB di latest.json
    # mandano qwen2.5:7b (32k ctx) in output degenere; i numeri restano identici.
    lines = []
    lines.append(
        "Analizza questi dati REALI di telemetria di una sessione Claude Code dietro "
        "un proxy router AI.\n"
        "- proxy = richieste HTTP verso i modelli AI; ttfb_ms = time-to-first-byte, "
        "total_ms = durata totale.\n"
        "- orchestrazione = le tool-call di Claude Code.\n"
        "- idle_gaps = STIMA INDIRETTA della latenza fra eventi (handoff modelli o "
        "tempo di pensiero), NON misura esatta: dichiaralo nel report.\n"
        "- baseline storica = solo proxy, senza orchestrazione.\n"
    )
    sel = latest.get("sessions", {}).get(session_id, {})
    lines.append(f"\n## Sessione analizzata: {session_id}")
    lines.append("Tool-call (count, durate in ms):")
    for tool, t in sel.get("by_tool_name", {}).items():
        lines.append(f"- {tool}: n={t['count']}, p50={t['p50']} p90={t['p90']} "
                     f"p99={t['p99']} max={t['max']}")
    lines.append("Richieste proxy per mode|model (ms):")
    for key, v in sel.get("by_mode_model", {}).items():
        ttfb, total = v.get("ttfb_ms", {}), v.get("total_ms", {})
        lines.append(f"- {key}: n={v['count']}, ttfb p50={ttfb.get('p50')} "
                     f"p90={ttfb.get('p90')} p99={ttfb.get('p99')} max={ttfb.get('max')}; "
                     f"total p50={total.get('p50')} p90={total.get('p90')} "
                     f"p99={total.get('p99')} max={total.get('max')}")
    lines.append(f"conteggi totali: tool_calls={sel.get('n_tool_calls', 0)}, "
                 f"proxy_requests={sel.get('n_proxy_requests', 0)}")
    gaps = sel.get("idle_gaps", [])
    top_gaps = sorted(gaps, key=lambda g: -g.get("gap_ms", 0))[:5]
    lines.append(f"idle_gaps (stima indiretta): n={len(gaps)}, "
                 f"top 5 (ms): {[round(g['gap_ms']) for g in top_gaps]}")
    lines.append(f"hook_errors: {json.dumps(sel.get('hook_errors', []))}")
    lines.append(f"subagent_stops: {json.dumps(sel.get('subagent_stops', []))}")
    lines.append(f"running_now (sessioni con tool ancora in corso): "
                 f"{json.dumps(latest.get('running_now', []))}")
    lines.append("Altre sessioni nella finestra (tool/proxy):")
    for s, d in latest.get("sessions", {}).items():
        if s != session_id:
            lines.append(f"- {s[:12]}: {d.get('n_tool_calls', 0)} tool / "
                         f"{d.get('n_proxy_requests', 0)} proxy")
    if baseline is not None:
        lines.append("\n## Baseline storica solo-proxy (top 8 per count, ms)")
        for row in compact_baseline_rows(baseline):
            lines.append(f"- {row['mode_model']}: n={row['count']}, "
                         f"ttfb p50={row['ttfb_p50']} p99={row['ttfb_p99']}; "
                         f"total p50={row['total_p50']} p99={row['total_p99']}")
    else:
        lines.append("\n## Baseline storica\nbaseline storica non disponibile, "
                     "eseguire prima `perfmon_correlate.py --backfill`.")
    lines.append("\n## Diagnostica router (ultime 24h, airouter-info)")
    for section in ("cache", "costo", "sprechi"):
        stdout, err = air.get(section, (None, "sezione non raccolta"))
        if stdout:
            lines.append(f"### {section}\n```\n{stdout}\n```")
        else:
            lines.append(f"### {section}\n[AVVISO: comando fallito/non disponibile: {err}]")
    lines.append(
        "\n## Cosa devi fare\n"
        "1. Identifica dove si perde piu' tempo: percentili alti, sessioni con "
        "'running_now' da molto tempo, gap ampi fra eventi.\n"
        "2. Distingui i pattern plausibili (rete lenta vs retry vs modello "
        "sovraccarico vs orchestrazione bloccata) SOLO se i numeri li supportano.\n"
        "3. Dichiara esplicitamente quando un dato e' una stima indiretta (i gap) "
        "e non una misura esatta.\n"
        "4. NON suggerire tagli o cambi di modalita' specifici: e' un report "
        "generico, solo diagnosi.\n\n"
        "Output in DUE parti, separate da una riga contenente solo " + JSON_MARKER + ":\n"
        "(a) prima parte: markdown leggibile con sezioni;\n"
        "(b) seconda parte: UN blocco JSON con la chiave 'findings', lista di oggetti "
        '{"title", "severity": "low"|"medium"|"high", "category", "evidence", '
        '"session_id_or_mode"}.\n'
        f"Se non hai finding, scrivi 'findings': [] dopo il marcatore {JSON_MARKER}."
    )
    return "\n".join(lines)


def call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": "Sei un supervisore che analizza telemetria di performance di un proxy AI e scrive report tecnici in italiano."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        # ponytail: temperatura 0 — a default 0.8 il 7B degenera su prompt densi
        # di numeri; greedy e' deterministico e ha prodotto report corretti.
        "options": {"temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("message", {}).get("content", "")
    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        print(
            f"ERRORE: Ollama non risponde su :11434, e' attivo? il modello e' "
            f"scaricato? `ollama list` per controllare. Dettaglio: {reason}",
            file=sys.stderr,
        )
        sys.exit(1)


def split_response(response: str) -> tuple:
    """(markdown, findings, avviso). Marcatore mancante -> fallback sul fence
    ```json (qwen a temp 0 tende a chiudere il JSON in un code fence)."""
    json_part = None
    if JSON_MARKER in response:
        md, _, rest = response.partition(JSON_MARKER)
        json_part = rest
    else:
        md = response
        fence_start = response.rfind("```json")
        if fence_start != -1:
            md = response[:fence_start]
            json_part = response[fence_start:].strip("`").lstrip("json")
    if json_part is None:
        return response.strip(), [], "il modello non ha prodotto il blocco JSON atteso"
    try:
        txt = json_part.strip()
        if txt.startswith("```"):
            txt = txt.strip("`").lstrip("json").strip()
        findings = json.loads(txt).get("findings", [])
        return md.strip(), findings, None
    except (json.JSONDecodeError, AttributeError) as exc:
        return (md.strip(), [],
                f"blocco JSON dopo il marcatore non parsabile: {exc}")


def main() -> int:
    if os.environ.get("PERFMON_DISABLE") == "1":
        return 0

    parser = argparse.ArgumentParser(description="Supervisore perfmon via LLM locale")
    parser.add_argument("--session", help="session_id da analizzare")
    parser.add_argument("--last", action="store_true",
                        help="usa l'ultima sessione attiva in latest.json")
    parser.add_argument("--status", action="store_true",
                        help="riepilogo rapido, nessuna chiamata a Ollama")
    args = parser.parse_args()

    latest = load_json(AGGREGATES_DIR / "latest.json")
    if latest is None:
        print("ERRORE: latest.json mancante o illeggibile in "
              f"{AGGREGATES_DIR}", file=sys.stderr)
        return 1

    if args.status:
        sessions = latest.get("sessions", {})
        running = latest.get("running_now", [])
        errors = sum(len(s.get("hook_errors", [])) for s in sessions.values())
        print(f"sessioni: {len(sessions)}")
        print(f"running_now: {len(running)}")
        for r in running:
            print(f"  {r.get('session_id', '?')[:12]} {r.get('tool_name')} "
                  f"{r.get('elapsed_sec', 0):.0f}s")
        print(f"hook_errors: {errors}")
        print(f"generato: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest.get('generated_at', 0)))}")
        return 0

    if not args.session and not args.last:
        parser.error("specificare --session <id> oppure --last (o --status)")

    session_id = args.session or pick_last_session(latest)
    if not session_id or session_id not in latest.get("sessions", {}):
        print(f"ERRORE: sessione '{session_id}' non trovata in latest.json",
              file=sys.stderr)
        return 1

    baseline = load_json(AGGREGATES_DIR / "baseline_proxy_only.json")
    air = collect_airouter_info()
    prompt = build_prompt(latest, baseline, air, session_id)

    response = call_ollama(prompt)
    markdown, findings, warn = split_response(response)
    if len(markdown.strip()) < 100:
        print(
            "ERRORE: risposta del modello troppo corta o degenere, report non scritto. "
            f"Contenuto: {markdown.strip()[:200]!r}",
            file=sys.stderr,
        )
        return 1
    if warn:
        findings.append({
            "title": warn,
            "severity": "low",
            "category": "parser",
            "evidence": "risposta del modello priva di blocco JSON parsabile",
            "session_id_or_mode": session_id,
        })

    generated_at = time.time()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{int(generated_at)}_{session_id[:12] or 'ALL'}"
    md_path = REPORTS_DIR / f"{stem}.md"
    json_path = REPORTS_DIR / f"{stem}.json"

    header = (
        f"# Report perfmon supervisor\n\n"
        f"- generato: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(generated_at))}\n"
        f"- sessione: {session_id}\n"
        f"- modello Ollama: {OLLAMA_MODEL}\n\n"
    )
    md_path.write_text(header + markdown + "\n", encoding="utf-8")
    json_path.write_text(json.dumps({
        "generated_at": generated_at,
        "session_id": session_id,
        "model": OLLAMA_MODEL,
        "findings": findings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(md_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
