import json
import os
import sys
import time
import argparse
import urllib.request
import urllib.error
import statistics
import threading
import concurrent.futures
import http.client
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple
from socket import socket, AF_INET, SOCK_STREAM

HEALTH_PATH = '/health'
MESSAGES_PATH = '/v1/messages'
DEFAULT_PORT = 8771
CLAUDE_CODE_MARKER = "You are Claude Code, Anthropic's official CLI for Claude."


def health_probe(port: int, timeout: float = 3.0) -> Tuple[bool, float]:
    """GET su /health, ritorna (ok, latenza_ms). Non solleva mai."""
    try:
        url = f"http://127.0.0.1:{port}{HEALTH_PATH}"
        req = urllib.request.Request(url, method='GET')
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            latency_ms = (time.perf_counter() - start) * 1000
            return (resp.status == 200, latency_ms)
    except Exception:
        return (False, 0.0)


def post_messages(port: int, body: dict, timeout: float = 120) -> dict:
    """POST JSON su /v1/messages, ritorna dict con status, ok, latency_ms, output_tokens, error."""
    result = {"status": 0, "ok": False, "latency_ms": 0.0, "output_tokens": 0, "error": ""}
    try:
        url = f"http://127.0.0.1:{port}{MESSAGES_PATH}"
        data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('anthropic-version', '2023-06-01')
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read()
                latency_ms = (time.perf_counter() - start) * 1000
                result["status"] = resp.status
                result["ok"] = resp.status == 200
                result["latency_ms"] = latency_ms
                if content:
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            result["output_tokens"] = parsed.get("usage", {}).get("output_tokens", 0)
                    except Exception:
                        pass
        except urllib.error.HTTPError as e:
            latency_ms = (time.perf_counter() - start) * 1000
            result["status"] = e.code
            result["latency_ms"] = latency_ms
            body_err = e.read()
            result["error"] = body_err[:200].decode('utf-8', errors='replace')
    except Exception as e:
        result["error"] = str(e)[:120]
    return result


def tiny_body(model: str = 'claude-haiku-4-5-20251001', max_tokens: int = 32) -> dict:
    """Body minimo valido con system marker e messages."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": CLAUDE_CODE_MARKER}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "ok"}]}]
    }


def test_concurrency(port: int, levels: Tuple[int, ...] = (1, 2, 4, 8), per_level: int = 8) -> List[dict]:
    """Test concorrenza con health monitoring parallelo."""
    results = []
    for level in levels:
        health_lats = []
        health_ok_count = 0
        health_lock = threading.Lock()

        def health_sampler():
            nonlocal health_ok_count
            end_time = time.time() + (per_level * 2)
            while time.time() < end_time:
                ok, lat = health_probe(port, 3.0)
                with health_lock:
                    health_lats.append(lat)
                    if ok:
                        health_ok_count += 1
                time.sleep(0.5)

        req_lats = []
        req_ok = 0
        req_lock = threading.Lock()

        def make_request(idx: int):
            nonlocal req_ok
            r = post_messages(port, tiny_body())
            with req_lock:
                req_lats.append(r["latency_ms"])
                if r["ok"]:
                    req_ok += 1

        sampler = threading.Thread(target=health_sampler, daemon=True)
        sampler.start()
        time.sleep(0.1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=level) as ex:
            futures = [ex.submit(make_request, i) for i in range(per_level)]
            concurrent.futures.wait(futures)

        time.sleep(0.3)
        sampler.join(timeout=1.0)

        with health_lock:
            all_health = list(health_lats)

        n = per_level
        ok_rate = req_ok / n if n > 0 else 0
        err_rate = 1 - ok_rate
        req_p50 = statistics.median(req_lats) if req_lats else 0.0
        req_p95 = 0.0
        if req_lats:
            sorted_lats = sorted(req_lats)
            idx = int(len(sorted_lats) * 0.95)
            req_p95 = sorted_lats[min(idx, len(sorted_lats) - 1)]

        health_n = len(all_health)
        health_ok_rate = health_ok_count / health_n if health_n > 0 else 0.0
        health_p50 = statistics.median(all_health) if all_health else 0.0
        health_max = max(all_health) if all_health else 0.0

        results.append({
            "level": level,
            "n": n,
            "ok": req_ok,
            "error_rate": err_rate,
            "req_p50": round(req_p50, 1),
            "req_p95": round(req_p95, 1),
            "health_n": health_n,
            "health_ok_rate": round(health_ok_rate, 2),
            "health_p50": round(health_p50, 1),
            "health_max": round(health_max, 1)
        })
    return results


def test_fault_injection(port: int) -> List[dict]:
    """Test con input malformati, verifica risposte pulite."""
    def make_req(body_bytes: bytes, name: str) -> dict:
        url = f"http://127.0.0.1:{port}{MESSAGES_PATH}"
        req = urllib.request.Request(url, data=body_bytes, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('anthropic-version', '2023-06-01')
        status = 0
        ok = False
        error = ""
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                ok = resp.status == 200
                resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            error = e.read()[:120].decode('utf-8', errors='replace')
        except Exception as e:
            status = 0
            error = str(e)[:120]
        gestito = "gestito" if (status in range(400, 500) or ok) else "NON GESTITO"
        return {"nome": name, "status": status, "ok": ok, "error": error[:120], "gestito": gestito}

    valid = tiny_body()
    cases = []

    cases.append(make_req(b'{"model":"claude-haiku-4-5","messages":[', 'json_troncato'))
    cases.append(make_req(b'', 'json_vuoto'))
    cases.append(make_req(b'[1,2,3]', 'array_al_posto_di_oggetto'))

    no_model = {k: v for k, v in valid.items() if k != 'model'}
    cases.append(make_req(json.dumps(no_model).encode(), 'model_assente'))

    no_msg = {k: v for k, v in valid.items() if k != 'messages'}
    cases.append(make_req(json.dumps(no_msg).encode(), 'messages_assente'))

    tool_body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 32,
        "messages": [
            {"role": "assistant", "content": [
                {"type": "server_tool_use", "id": "BAD-ID-123", "name": "bash", "input": {}},
                {"type": "text", "text": "Running command"}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "BAD-ID-123", "content": "done"}
            ]}
        ]
    }
    cases.append(make_req(json.dumps(tool_body).encode(), 'srvtoolu_id_non_conforme'))

    giant_system = "x" * 200000
    sys_body = dict(tiny_body())
    sys_body["system"] = [{"type": "text", "text": giant_system}]
    cases.append(make_req(json.dumps(sys_body).encode(), 'system_gigante'))

    unknown_body = dict(tiny_body())
    unknown_body["campo_inventato_xyz"] = "valore"
    cases.append(make_req(json.dumps(unknown_body).encode(), 'campo_sconosciuto'))

    effort_body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "ok"}]}],
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "output_config": {"type": "text", "effort": "low"}
    }
    cases.append(make_req(json.dumps(effort_body).encode(), 'effort_su_haiku'))

    return cases


def test_health_under_stop(port: int) -> dict:
    """Test resilienza a connessione chiusa bruscamente."""
    result = {"socket_ok": False, "health_after_ok": False, "health_after_ms": 0.0}
    try:
        s = socket(AF_INET, SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(("127.0.0.1", port))
        s.sendall(b"GET /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\n")
        s.close()
        result["socket_ok"] = True
        time.sleep(1.0)
        ok, lat = health_probe(port, 3.0)
        result["health_after_ok"] = ok
        result["health_after_ms"] = lat
    except Exception:
        pass
    return result


def main():
    parser = argparse.ArgumentParser(description='Stress test proxy LLM locale')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--levels', default='1,2,4,8')
    parser.add_argument('--per-level', type=int, default=8)
    parser.add_argument('--out', default='')
    parser.add_argument('--skip-concurrency', action='store_true')
    args = parser.parse_args()

    levels = tuple(int(x) for x in args.levels.split(','))
    ts = int(time.time())
    out_path = args.out or f"bench/results/stress-{ts}.jsonl"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    def write_result(res: dict):
        with open(out_path, 'a') as f:
            f.write(json.dumps(res) + '\n')
            f.flush()

    print(f"[STRESS TEST] port={args.port} levels={levels} per_level={args.per_level}")
    print(f"[OUTPUT] {out_path}\n")

    ok0, lat0 = health_probe(args.port)
    print(f"[INIT] health={'OK' if ok0 else 'FAIL'} {lat0:.1f}ms")
    write_result({"type": "init_health", "ok": ok0, "latency_ms": lat0})

    print("\n=== FAULT INJECTION ===")
    fault_results = test_fault_injection(args.port)
    write_result({"type": "fault_injection", "results": fault_results})
    for fr in fault_results:
        print(f"  {fr['nome']:<30} status={fr['status']:<5} {fr['gestito']}")

    print("\n=== SOCKET ABORT ===")
    abort_res = test_health_under_stop(args.port)
    write_result({"type": "socket_abort", "result": abort_res})
    print(f"  socket_ok={abort_res['socket_ok']} health_after_ok={abort_res['health_after_ok']} latency={abort_res['health_after_ms']:.1f}ms")

    conc_results = []
    if not args.skip_concurrency:
        print("\n=== CONCURRENCY ===")
        conc_results = test_concurrency(args.port, levels, args.per_level)
        write_result({"type": "concurrency", "results": conc_results})
        for cr in conc_results:
            print(f"  level={cr['level']} ok={cr['ok']}/{cr['n']} err={cr['error_rate']:.2f} req_p50={cr['req_p50']:.1f}ms req_p95={cr['req_p95']:.1f}ms health_ok={cr['health_ok_rate']:.2f} health_p50={cr['health_p50']:.1f}ms health_max={cr['health_max']:.1f}ms")

    print("\n" + "=" * 70)
    print("VERDETTO")
    print("=" * 70)

    total_errors = sum(1 for fr in fault_results if fr['gestito'] == 'NON GESTITO')
    print(f"  Errori fault injection non gestiti: {total_errors}")

    if conc_results:
        health_max = max(cr['health_max'] for cr in conc_results)
        print(f"  Health max sotto carico: {health_max:.1f}ms")
        total_req = sum(cr['n'] for cr in conc_results)
        total_ok = sum(cr['ok'] for cr in conc_results)
        overall_err_rate = 1 - (total_ok / total_req) if total_req > 0 else 1.0
        print(f"  Error rate complessivo richieste: {overall_err_rate:.2%}")
        if health_max > 3000:
            print("\n  AVVISO: health_max supera 3000ms - watchdog esterno potrebbe considerare il proxy morto!")
    else:
        print("  Test concorrenza saltato")

    print("=" * 70)


if __name__ == '__main__':
    main()
