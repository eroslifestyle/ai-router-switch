#!/usr/bin/env python3
"""Dashboard web perfmon (componente d). Read-only su .claude/perfmon/.

Bind 127.0.0.1 esplicito, porta libera da 8790 in su.
PERFMON_DISABLE=1 → avviso ed exit, niente server.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from html import escape
from pathlib import Path

from aiohttp import web

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGG_DIR = PROJECT_ROOT / ".claude" / "perfmon" / "aggregates"
LATEST = AGG_DIR / "latest.json"
REPORTS_DIR = PROJECT_ROOT / ".claude" / "perfmon" / "reports"
PORT_BASE = 8790
POLL_SEC = int(os.environ.get("PERFMON_POLL_SEC", "45"))
CORRELATE_SCRIPT = PROJECT_ROOT / "sviluppo" / "tools" / "perfmon_correlate.py"
ROTATE_SCRIPT = PROJECT_ROOT / ".claude" / "perfmon" / "hooks" / "rotate.py"
POLL_TIMEOUT_SEC = 30
MAX_PORT_TRIES = 20

PCT_KEYS = ("min", "p50", "p90", "p99", "max")


def load_latest():
    try:
        return json.loads(LATEST.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def sessions_with_running(data):
    return {r.get("session_id") for r in data.get("running_now", []) if r.get("session_id")}


def pick_default_session(data):
    running = sessions_with_running(data)
    sessions = data.get("sessions", {})
    for sid in sessions:
        if sid in running:
            return sid
    return max(sessions, key=lambda s: sessions[s].get("n_tool_calls", 0), default=None)


def fmt_pct(p):
    if not p:
        return "-"
    return " / ".join(str(p.get(k, "-")) for k in PCT_KEYS)


def render_page(data, selected):
    sessions = data.get("sessions", {})
    running = data.get("running_now", [])
    running_for_sel = [r for r in running if r.get("session_id") == selected]
    sess = sessions.get(selected, {})
    by_tool = sess.get("by_tool_name", {})
    by_mode = sess.get("by_mode_model", {})

    parts = []
    parts.append("<!DOCTYPE html><html lang='it'><head><meta charset='utf-8'>")
    parts.append("<title>perfmon dashboard</title><style>")
    parts.append("body{font-family:monospace;margin:1.5em;background:#111;color:#ddd}")
    parts.append("table{border-collapse:collapse;margin:1em 0}th,td{border:1px solid #444;padding:4px 10px;text-align:right}")
    parts.append("th{background:#222;text-align:center}td:first-child,th:first-child{text-align:left}")
    parts.append(".running{background:#5a1515;border:2px solid #c0392b;padding:10px 14px;margin:1em 0}")
    parts.append(".running .badge{background:#c0392b;color:#fff;padding:2px 8px;font-weight:bold}")
    parts.append(".hookerr{background:#5a4a15;border:1px solid #c0a020;padding:8px 12px;margin:1em 0;display:inline-block}")
    parts.append(".muted{color:#777}.num{color:#7ec97e}a{color:#6cb2ff}")
    parts.append("select{font-family:inherit;font-size:1em;background:#222;color:#ddd;padding:4px}")
    parts.append("</style></head><body>")
    parts.append("<h1>perfmon dashboard</h1>")
    parts.append(f"<p class='muted'>aggregato: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get('generated_at', 0)))} &middot; eventi scartati: {escape(str(data.get('eventi_scartati', 0)))}</p>")

    parts.append("<form method='get' action='/'><label>Sessione: <select name='session' onchange='this.form.submit()'>")
    for sid in sessions:
        sel = " selected" if sid == selected else ""
        parts.append(f"<option value='{escape(sid)}'{sel}>{escape(sid)}</option>")
    parts.append("</select></label><noscript><button type='submit'>vai</button></noscript></form>")

    if running:
        parts.append("<div class='running'><span class='badge'>IN ESECUZIONE ORA</span><ul>")
        for r in running:
            parts.append(f"<li>{escape(str(r.get('tool_name', '?')))} &mdash; {escape(str(r.get('elapsed_sec', '?')))}s"
                         f" <span class='muted'>(session {escape(str(r.get('session_id', '?'))[:12])}...)</span></li>")
        parts.append("</ul></div>")

    if running_for_sel:
        parts.append("<div class='running'><span class='badge'>IN ESECUZIONE (questa sessione)</span><ul>")
        for r in running_for_sel:
            parts.append(f"<li>{escape(str(r.get('tool_name', '?')))} &mdash; {escape(str(r.get('elapsed_sec', '?')))}s</li>")
        parts.append("</ul></div>")

    hook_errors = sess.get("hook_errors", [])
    if hook_errors:
        parts.append(f"<div class='hookerr'>&#9888; {len(hook_errors)} errori hook, vedi orchestration.jsonl</div>")

    parts.append(f"<h2>Tool calls ({sess.get('n_tool_calls', 0)})</h2>")
    parts.append("<table><tr><th>tool</th><th>count</th><th>durata ms: minimo / tipico / lento(10%) / molto lento(1%) / peggiore</th></tr>")
    for tool in sorted(by_tool, key=lambda t: -by_tool[t].get("count", 0)):
        s = by_tool[tool]
        parts.append(f"<tr><td>{escape(tool)}</td><td>{s.get('count', 0)}</td><td class='num'>{escape(fmt_pct(s))}</td></tr>")
    if not by_tool:
        parts.append("<tr><td colspan='3' class='muted'>nessun tool call</td></tr>")
    parts.append("</table>")

    parts.append(f"<h2>Proxy requests ({sess.get('n_proxy_requests', 0)}) per modalit&agrave;|modello</h2>")
    parts.append("<table><tr><th>mode|model</th><th>count</th><th>tempo alla prima risposta ms: minimo/tipico/lento(10%)/molto lento(1%)/peggiore</th><th>tempo totale ms: stesso ordine</th><th>errori 429/5xx (%)</th></tr>")
    for key in sorted(by_mode, key=lambda k: -by_mode[k].get("count", 0)):
        m = by_mode[key]
        err = m.get("errors", {})
        parts.append(f"<tr><td>{escape(key)}</td><td>{m.get('count', 0)}</td>"
                     f"<td class='num'>{escape(fmt_pct(m.get('ttfb_ms')))}</td>"
                     f"<td class='num'>{escape(fmt_pct(m.get('total_ms')))}</td>"
                     f"<td class='num'>{err.get('n_429', 0)}/{err.get('n_5xx', 0)} ({err.get('error_rate_pct', 0.0)}%)</td></tr>")
    if not by_mode:
        parts.append("<tr><td colspan='5' class='muted'>nessuna richiesta proxy</td></tr>")
    parts.append("</table>")

    gaps = sess.get("idle_gaps", [])
    parts.append(f"<h2>Idle gaps ({len(gaps)}) <span class='muted'>(stima indiretta, non misura esatta)</span></h2>")
    if gaps:
        parts.append("<table><tr><th>from</th><th>to</th><th>gap ms</th></tr>")
        for g in gaps[:30]:
            parts.append(f"<tr><td>{time.strftime('%H:%M:%S', time.localtime(g.get('from_ts', 0)))}</td>"
                         f"<td>{time.strftime('%H:%M:%S', time.localtime(g.get('to_ts', 0)))}</td>"
                         f"<td class='num'>{g.get('gap_ms', 0)}</td></tr>")
        parts.append("</table>")

    reports = list_reports()
    parts.append(f"<h2>Report ({len(reports)})</h2><ul>")
    for name, mtime in reports:
        parts.append(f"<li><a href='/api/reports/{escape(name)}'>{escape(name)}</a>"
                     f" <span class='muted'>{time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}</span></li>")
    if not reports:
        parts.append("<li class='muted'>nessun report ancora</li>")
    parts.append("</ul>")

    parts.append(f"<script>setInterval(()=>fetch('/api/status').then(()=>location.reload()),{POLL_SEC * 1000});</script>")
    parts.append("</body></html>")
    return "".join(parts)


def list_reports():
    if not REPORTS_DIR.is_dir():
        return []
    out = []
    for p in REPORTS_DIR.glob("*.md"):
        try:
            out.append((p.name, p.stat().st_mtime))
        except OSError:
            continue
    return sorted(out, key=lambda t: -t[1])


def report_path_safe(name):
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        return None
    p = (REPORTS_DIR / name).resolve()
    if p.parent != REPORTS_DIR.resolve() or p.suffix != ".md" or not p.is_file():
        return None
    return p


async def index(request):
    data = load_latest()
    if data is None:
        return web.Response(text=(
            "<!DOCTYPE html><html lang='it'><head><meta charset='utf-8'><title>perfmon</title></head><body>"
            "<h1>perfmon dashboard</h1><p>Nessun dato ancora: esegui prima <code>perfmon_correlate.py</code>.</p>"
            "</body></html>"), content_type="text/html")
    sessions = data.get("sessions", {})
    selected = request.query.get("session")
    if selected not in sessions:
        selected = pick_default_session(data)
    if selected is None:
        return web.Response(text="<html><body><p>Nessuna sessione nei dati.</p></body></html>", content_type="text/html")
    return web.Response(text=render_page(data, selected), content_type="text/html")


async def api_status(request):
    data = load_latest()
    if data is None:
        return web.json_response({"error": "latest.json non esiste ancora", "dashboard_ts": time.time()}, status=404)
    data["dashboard_ts"] = time.time()
    return web.json_response(data)


async def api_sessions(request):
    data = load_latest() or {}
    running = sessions_with_running(data)
    out = {sid: {"n_tool_calls": s.get("n_tool_calls", 0),
                 "running_now": sid in running}
           for sid, s in data.get("sessions", {}).items()}
    return web.json_response(out)


async def api_reports(request):
    return web.json_response([{"name": n, "mtime": m} for n, m in list_reports()])


async def api_report_content(request):
    name = request.match_info.get("name", "")
    p = report_path_safe(name)
    if p is None:
        return web.json_response({"error": "report non valido o non trovato"}, status=400)
    return web.Response(text=p.read_text(errors="replace"), content_type="text/plain")


def find_port():
    import socket
    for offset in range(MAX_PORT_TRIES):
        port = PORT_BASE + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                print(f"porta {port} occupata, provo la successiva", flush=True)
                continue
        return port
    raise SystemExit(f"nessuna porta libera in {PORT_BASE}-{PORT_BASE + MAX_PORT_TRIES - 1}")


async def poll_loop(app):
    """Rigenera latest.json (correlate + rotate) nel server, senza cron esterno."""
    while True:
        try:
            if os.environ.get("PERFMON_DISABLE") == "1":
                await asyncio.sleep(POLL_SEC)
                continue
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: subprocess.run([sys.executable, str(CORRELATE_SCRIPT)], timeout=POLL_TIMEOUT_SEC))
            await loop.run_in_executor(
                None, lambda: subprocess.run([sys.executable, str(ROTATE_SCRIPT)], timeout=POLL_TIMEOUT_SEC))
        except Exception as exc:
            print(f"perfmon poll error: {exc}", flush=True)
        await asyncio.sleep(POLL_SEC)


async def start_poll(app):
    app["poll_task"] = asyncio.create_task(poll_loop(app))


async def stop_poll(app):
    task = app.get("poll_task")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main():
    if os.environ.get("PERFMON_DISABLE") == "1":
        print("perfmon_dashboard: PERFMON_DISABLE=1 attivo, server non avviato", flush=True)
        return
    port = find_port()
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/sessions", api_sessions)
    app.router.add_get("/api/reports", api_reports)
    app.router.add_get("/api/reports/{name}", api_report_content)
    app.on_startup.append(start_poll)
    app.on_cleanup.append(stop_poll)
    print(f"perfmon_dashboard: http://127.0.0.1:{port}/ (bind 127.0.0.1)", flush=True)
    print(f"perfmon_dashboard: poll ogni {POLL_SEC}s", flush=True)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
