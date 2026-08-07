#!/usr/bin/env python3
"""Tool Search Probe: misura risparmio contesto tool search Anthropic."""
import json, os, sys, time, argparse, urllib.request, urllib.error
from typing import Any

CLAUDE_CODE_MARKER = "You are Claude Code, Anthropic's official CLI for Claude."
TOOL_SEARCH_TYPE = "tool_search_tool_regex_20251119"
NON_DEFERRED_KEEP = 3
DEFAULT_MODEL = "claude-opus-5"

def load_oauth_token() -> str:
    """Legge token OAuth da ~/.claude/.credentials.json."""
    path = os.path.expanduser("~/.claude/.credentials.json")
    if not os.path.isfile(path):
        raise RuntimeError(f"File credenziali non trovato: {path}")
    with open(path) as f:
        data = json.load(f)
    def _find(obj: object) -> str:
        """Cerca ricorsivamente la prima chiave accessToken con valore utile."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("accessToken", "access_token") and isinstance(v, str) and v:
                    return v
                found = _find(v)
                if found:
                    return found
        return ""
    tok = _find(data)
    if tok:
        return tok
    raise RuntimeError(f"Nessun accessToken valido in {path}")

def _idx(n: int, m: int) -> int:
    """Hashing deterministico per combinazioni."""
    return (n * 2654435761 + m) ^ (n >> 16)

def make_synthetic_tools(n: int) -> list[dict]:
    """Genera n tool realistici in modo deterministico."""
    services = ["github", "slack", "jira", "sentry", "grafana", "gmail", "drive", "notion", "linear", "datadog"]
    verbs = ["create", "get", "list", "update", "delete", "search", "archive"]
    resources = ["issue", "pull_request", "comment", "channel", "message", "user", "dashboard", "alert", "file", "page"]
    prop_combos = [
        [("id", "string", "ID univoco della risorsa"), ("query", "string", "Query di ricerca"), ("limit", "integer", "Limite risultati")],
        [("body", "string", "Corpo della richiesta"), ("labels", "array", "Etichette opzionali"), ("dry_run", "boolean", "Simula senza eseguire")],
        [("id", "string", "ID risorsa"), ("dry_run", "boolean", "Modalita' simulazione"), ("query", "string", "Filtro opzionale")],
        [("query", "string", "Testo da cercare"), ("labels", "array", "Filtra per tag"), ("limit", "integer", "Max risultati")],
        [("id", "string", "Identificatore"), ("body", "string", "Payload JSON"), ("dry_run", "boolean", "Preview only")],
    ]
    tools = []
    seen: set[str] = set()
    for i in range(n):
        svc = services[i % len(services)]
        v_i = _idx(i, 1) % len(verbs)
        r_i = _idx(i, 2) % len(resources)
        verb, res = verbs[v_i], resources[r_i]
        name = f"{svc}_{verb}_{res}"
        if name in seen:
            name = f"{name}_{i}"
        seen.add(name)
        p_i = _idx(i, 3) % len(prop_combos)
        props, required = {}, []
        for j, (pname, ptype, pdesc) in enumerate(prop_combos[p_i]):
            full_name = f"{pname}_{i}" if j > 0 else pname
            props[full_name] = {"type": ptype, "description": pdesc}
            if j < 2:
                required.append(full_name)
        desc_idx = _idx(i, 4) % 3
        desc_templates = [
            f"Permette di {verb} una {res} nel sistema {svc} con parametri configurabili e validazione inclusa.",
            f"Questa funzione {verb} l'entita' {res} su {svc} gestendo autenticazione e permessi corretti.",
            f"Endpoint per {verb} {res} tramite {svc}, supporta filtri avanzati e paginazione dei risultati."
        ]
        tools.append({
            "name": name,
            "description": desc_templates[desc_idx],
            "input_schema": {"type": "object", "properties": props, "required": required}
        })
    return tools

def build_body_full(model: str, tools: list[dict], question: str) -> dict:
    """Body completo con tutti i tool caricati."""
    return {
        "model": model,
        "max_tokens": 1024,
        "system": [{"type": "text", "text": CLAUDE_CODE_MARKER}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
        "tools": tools
    }

def build_body_search(model: str, tools: list[dict], question: str) -> dict:
    """Body con tool search e defer_loading."""
    search_tools = [{"type": TOOL_SEARCH_TYPE, "name": "tool_search_tool_regex"}]
    for i, t in enumerate(tools):
        t_copy = dict(t)
        if i >= NON_DEFERRED_KEEP:
            t_copy["defer_loading"] = True
        search_tools.append(t_copy)
    return {
        "model": model,
        "max_tokens": 1024,
        "system": [{"type": "text", "text": CLAUDE_CODE_MARKER}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
        "tools": search_tools
    }

def call(token: str, body: dict, timeout: int = 120) -> dict:
    """Chiamata POST all'API Anthropic."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "content-type": "application/json"
    }
    result = {"status": 0, "ok": False, "latency_ms": 0, "input_tokens": 0, "output_tokens": 0,
              "cache_creation": 0, "cache_read": 0, "stop_reason": None, "block_types": [], "raw": {}, "error": None}
    start = time.time()
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status"] = resp.status
            result["ok"] = True
            raw = json.loads(resp.read().decode())
            result["raw"] = raw
            result["latency_ms"] = int((time.time() - start) * 1000)
            if "usage" in raw:
                result["input_tokens"] = raw["usage"].get("input_tokens", 0)
                result["output_tokens"] = raw["usage"].get("output_tokens", 0)
                result["cache_creation"] = raw["usage"].get("cache_creation_input_tokens", 0) or 0
                result["cache_read"] = raw["usage"].get("cache_read_input_tokens", 0) or 0
            result["stop_reason"] = raw.get("stop_reason")
            if "content" in raw:
                result["block_types"] = [b.get("type") for b in raw.get("content", []) if isinstance(b, dict)]
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        try:
            result["error"] = e.read().decode()[:300]
        except:
            result["error"] = str(e)[:300]
    except Exception as e:
        result["error"] = str(e)[:300]
    return result

def run_two_turns(token: str, model: str, tools: list[dict], question: str) -> dict:
    """Verifica compatibilita' two-turn con tool search."""
    body1 = build_body_search(model, tools, question)
    resp1 = call(token, body1)
    turn2, two_turn_ok = None, False
    if not resp1.get("ok"):
        return {"turn1": resp1, "turn2": None, "two_turn_ok": False}
    has_tool_use = "tool_use" in resp1.get("block_types", [])
    if has_tool_use:
        body2 = {
            "model": model,
            "max_tokens": 1024,
            "system": [{"type": "text", "text": CLAUDE_CODE_MARKER}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": question}]},
                {"role": "assistant", "content": []}
            ]
        }
        raw_content = resp1.get("raw", {}).get("content", [])
        for block in raw_content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype in ("text", "tool_use", "server_tool_use", "tool_search_tool_result"):
                    body2["messages"][1]["content"].append(block)
        for block in raw_content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tid = block.get("id", "")
                if not tid.startswith("srvtoolu_"):
                    body2["messages"].append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tid, "content": "OK"}]
                    })
        turn2 = call(token, body2)
        two_turn_ok = turn2.get("ok", False)
    return {"turn1": resp1, "turn2": turn2, "two_turn_ok": two_turn_ok}

def main() -> None:
    parser = argparse.ArgumentParser(description="Tool Search Probe")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sizes", default="70,280")
    parser.add_argument("--out")
    args = parser.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_file = args.out or f"bench/results/toolsearch-{ts}.jsonl"
    os.makedirs(os.path.dirname(out_file) or ".", exist_ok=True)
    token = load_oauth_token()
    question = "Apri una pull request sul repository principale con il titolo Fix del parser"
    hdr = f"{'size':>6} {'variant':>8} {'status':>7} {'input_t':>9} {'cache_cr':>9} {'cache_rd':>9} {'output_t':>9} {'ms':>7} {'blocks'}"
    print(hdr)
    print("-" * 95)
    results = []
    for size in sizes:
        tools = make_synthetic_tools(size)
        full_resp = call(token, build_body_full(args.model, tools, question))
        r1 = {
            "size": size, "variant": "full", "status": full_resp.get("status"),
            "input_tokens": full_resp.get("input_tokens"), "output_tokens": full_resp.get("output_tokens"),
            "cache_creation": full_resp.get("cache_creation"), "cache_read": full_resp.get("cache_read"),
            "latency_ms": full_resp.get("latency_ms"), "block_types": full_resp.get("block_types"),
            "two_turn_ok": None
        }
        print(f"{size:>6} {'full':>8} {r1['status']:>7} {r1['input_tokens']:>9} {r1['cache_creation']:>9} "
              f"{r1['cache_read']:>9} {r1['output_tokens']:>9} {r1['latency_ms']:>7} {r1['block_types']}")
        two_turn_result = run_two_turns(token, args.model, tools, question)
        sr = two_turn_result.get("turn2")
        r2 = {"size": size}
        if sr:
            r2.update({
                "variant": "search", "status": sr.get("status"), "input_tokens": sr.get("input_tokens"),
                "output_tokens": sr.get("output_tokens"), "cache_creation": sr.get("cache_creation"),
                "cache_read": sr.get("cache_read"), "latency_ms": sr.get("latency_ms"),
                "block_types": sr.get("block_types"), "two_turn_ok": two_turn_result.get("two_turn_ok")
            })
            print(f"{size:>6} {'search':>8} {r2['status']:>7} {r2['input_tokens']:>9} {r2['cache_creation']:>9} "
                  f"{r2['cache_read']:>9} {r2['output_tokens']:>9} {r2['latency_ms']:>7} {r2['block_types']}")
        else:
            r2.update({"variant": "search", "status": None, "input_tokens": None, "output_tokens": None,
                       "cache_creation": None, "cache_read": None, "latency_ms": None,
                       "block_types": [], "two_turn_ok": False})
            turn1_err = two_turn_result.get("turn1", {}).get("error", "Unknown")
            print(f"{size:>6} {'search':>8} {'FAIL':>7} {'--':>9} {'--':>9} {'--':>9} {'--':>9} {'--':>7} []")
            print(f"       ERRORE turn1: {str(turn1_err)[:80]}")
        if r1.get("input_tokens") and r2.get("input_tokens") and r1.get("cache_creation") is not None and r2.get("cache_creation") is not None:
            full_cost = r1["input_tokens"] + (r1["cache_creation"] or 0)
            search_cost = r2["input_tokens"] + (r2["cache_creation"] or 0)
            diff = full_cost - search_cost
            pct = (diff / full_cost * 100) if full_cost else 0
            print(f"       RISPARMIO: {diff} token ({pct:.1f}%) | two_turn_ok: {r2.get('two_turn_ok')}")
        results.extend([r1, r2])
    with open(out_file, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"\nRisultati salvati in: {out_file}")

if __name__ == "__main__":
    main()
