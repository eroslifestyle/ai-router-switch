#!/usr/bin/env python3
"""Harness di benchmark per proxy LLM Anthropic."""

import json
import sys
import time
import argparse
import urllib.request
import urllib.error
import statistics
import dataclasses
import typing
import pathlib
import concurrent.futures
import threading


CLAUDE_CODE_MARKER = "You are Claude Code, Anthropic's official CLI for Claude."


def load_oauth_token() -> str:
    """Legge il token OAuth da ~/.claude/.credentials.json con ricerca ricorsiva."""
    cred_path = pathlib.Path.home() / ".claude" / ".credentials.json"
    if not cred_path.exists():
        raise RuntimeError("token OAuth non trovato")
    with open(cred_path) as f:
        data = json.load(f)

    def _search(obj: typing.Any) -> typing.Optional[str]:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ("accessToken", "access_token") and isinstance(value, str) and value:
                    return value
                found = _search(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = _search(item)
                if found:
                    return found
        return None

    token = _search(data)
    if not token:
        raise RuntimeError("token OAuth non trovato")
    return token


@dataclasses.dataclass
class Scenario:
    """Scenario di test per il benchmark."""
    name: str
    model: str
    messages: list
    tools: typing.Optional[list] = None
    max_tokens: int = 4000
    system_extra: typing.Optional[str] = None


@dataclasses.dataclass
class Config:
    """Configurazione per una chiamata API."""
    name: str
    extra_body: dict
    endpoint: str = "https://api.anthropic.com"


@dataclasses.dataclass
class Result:
    """Risultato di una singola esecuzione."""
    scenario: str
    config: str
    run: int
    status: int
    ok: bool
    latency_ms: float
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cache_read: int
    cache_creation: int
    stop_reason: str
    error: str
    ts: float

    def to_dict(self) -> dict:
        """Converte il risultato in dizionario."""
        return {
            "scenario": self.scenario,
            "config": self.config,
            "run": self.run,
            "status": self.status,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 2),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cache_read": self.cache_read,
            "cache_creation": self.cache_creation,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "ts": self.ts,
        }


def build_body(scenario: Scenario, config: Config) -> dict:
    """Costruisce il body della richiesta API fondendo scenario e config."""
    system_texts = [CLAUDE_CODE_MARKER]
    if scenario.system_extra:
        system_texts.append(scenario.system_extra)

    body = {
        "model": scenario.model,
        "max_tokens": scenario.max_tokens,
        "system": [{"type": "text", "text": t} for t in system_texts],
        "messages": list(scenario.messages),
    }

    if scenario.tools is not None:
        body["tools"] = list(scenario.tools)

    if config.extra_body:
        merged = dict(body)
        merged.update(config.extra_body)
        return merged

    return body


def call_once(
    token: str,
    scenario: Scenario,
    config: Config,
    timeout: int = 180,
) -> Result:
    """Esegue una singola chiamata API e restituisce il risultato."""
    body = build_body(scenario, config)
    body_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        f"{config.endpoint}/v1/messages",
        data=body_bytes,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    t0 = time.perf_counter()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = (time.perf_counter() - t0) * 1000
            raw = json.loads(resp.read().decode("utf-8"))
            usage = raw.get("usage", {})

            return Result(
                scenario=scenario.name,
                config=config.name,
                run=0,
                status=resp.status,
                ok=True,
                latency_ms=latency_ms,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                thinking_tokens=usage.get("output_tokens_details", {}).get("thinking_tokens", 0),
                cache_read=usage.get("cache_read_input_tokens", 0),
                cache_creation=usage.get("cache_creation_input_tokens", 0),
                stop_reason=raw.get("stop_reason", ""),
                error="",
                ts=time.time(),
            )

    except urllib.error.HTTPError as e:
        latency_ms = (time.perf_counter() - t0) * 1000
        body_err = e.read().decode("utf-8", errors="replace")[:300]
        return Result(
            scenario=scenario.name,
            config=config.name,
            run=0,
            status=e.code,
            ok=False,
            latency_ms=latency_ms,
            input_tokens=0,
            output_tokens=0,
            thinking_tokens=0,
            cache_read=0,
            cache_creation=0,
            stop_reason="",
            error=body_err,
            ts=time.time(),
        )

    except Exception as e:
        latency_ms = (time.perf_counter() - t0) * 1000
        return Result(
            scenario=scenario.name,
            config=config.name,
            run=0,
            status=0,
            ok=False,
            latency_ms=latency_ms,
            input_tokens=0,
            output_tokens=0,
            thinking_tokens=0,
            cache_read=0,
            cache_creation=0,
            stop_reason="",
            error=f"{type(e).__name__}: {e}",
            ts=time.time(),
        )


def run_matrix(
    token: str,
    scenarios: dict[str, Scenario],
    configs: dict[str, Config],
    repeat: int = 3,
    sleep_s: float = 1.0,
    out_path: typing.Optional[pathlib.Path] = None,
    parallel: int = 1,
) -> list[Result]:
    """Esegue la matrice scenario x config x repeat e scrive risultati su file."""
    results: list[Result] = []
    lock = threading.Lock()

    def _write(r: Result) -> None:
        if out_path:
            with lock:
                with open(out_path, "a") as fh:
                    fh.write(json.dumps(r.to_dict()) + "\n")
                    fh.flush()

    if parallel > 1:
        sem = threading.Semaphore(parallel)

        def worker(s_id: str, c_id: str, run_idx: int) -> Result:
            sem.acquire()
            try:
                r = call_once(token, scenarios[s_id], configs[c_id])
                r.run = run_idx
                _write(r)
                print(
                    f"[{r.status}] {r.scenario}/{r.config} run-{r.run}: "
                    f"{r.error[:50] if r.error else 'OK'}",
                    file=sys.stderr,
                )
                return r
            finally:
                sem.release()

        combos = [(s_id, c_id, r) for s_id in scenarios for c_id in configs for r in range(repeat)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = [
                executor.submit(worker, s_id, c_id, r)
                for s_id, c_id, r in combos
            ]
            for i, (s_id, c_id, r) in enumerate(combos):
                if i > 0:
                    time.sleep(sleep_s)
                results.append(futures[i].result())

    else:
        for s_id in scenarios:
            for c_id in configs:
                for r in range(repeat):
                    result = call_once(token, scenarios[s_id], configs[c_id])
                    result.run = r
                    results.append(result)
                    _write(result)
                    print(
                        f"[{result.status}] {result.scenario}/{result.config} run-{result.run}: "
                        f"{result.error[:50] if result.error else 'OK'}",
                        file=sys.stderr,
                    )
                    time.sleep(sleep_s)

    return results


def summarize(results: list[Result]) -> dict:
    """Raggruppa i risultati e calcola statistiche di sintesi."""
    groups: dict[tuple, list[Result]] = {}
    for r in results:
        key = (r.scenario, r.config)
        groups.setdefault(key, []).append(r)

    summary: dict = {}

    for (scenario, config), grp in groups.items():
        latencies = [r.latency_ms for r in grp]
        out_tokens = [r.output_tokens for r in grp]
        think_tokens = [r.thinking_tokens for r in grp]
        in_tokens = [r.input_tokens for r in grp]

        n = len(grp)
        n_ok = sum(1 for r in grp if r.ok)
        err_rate = (n - n_ok) / n if n > 0 else 0.0

        lat_sorted = sorted(latencies) if latencies else [0.0]
        p95_idx = min(int(len(lat_sorted) * 0.95), len(lat_sorted) - 1)

        summary[(scenario, config)] = {
            "scenario": scenario,
            "config": config,
            "n": n,
            "n_ok": n_ok,
            "error_rate": err_rate,
            "latency_p50": statistics.median(latencies) if latencies else 0.0,
            "latency_p95": lat_sorted[p95_idx],
            "output_tokens_p50": statistics.median(out_tokens) if out_tokens else 0.0,
            "thinking_tokens_p50": statistics.median(think_tokens) if think_tokens else 0.0,
            "input_tokens_p50": statistics.median(in_tokens) if in_tokens else 0.0,
            "cache_read_sum": sum(r.cache_read for r in grp),
            "cache_creation_sum": sum(r.cache_creation for r in grp),
        }

    return summary


def print_table(summary: dict) -> None:
    """Stampa una tabella formattata delle statistiche."""
    headers = ["scenario", "config", "n", "err%", "lat_p50", "lat_p95", "out_p50", "think_p50", "cache_read"]
    rows: list[list[str]] = []

    for key, s in summary.items():
        rows.append([
            s["scenario"],
            s["config"],
            str(s["n"]),
            f"{s['error_rate'] * 100:.1f}",
            f"{s['latency_p50']:.0f}",
            f"{s['latency_p95']:.0f}",
            str(int(s["output_tokens_p50"])),
            str(int(s["thinking_tokens_p50"])),
            str(s["cache_read_sum"]),
        ])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    total = sum(widths) + len(widths) * 3 + 1
    print("-" * total)
    print("  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("-" * total)
    for row in rows:
        print("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    print("-" * total)


def main() -> None:
    """Entry point principale del benchmark."""
    parser = argparse.ArgumentParser(description="Benchmark harness per proxy LLM Anthropic")
    parser.add_argument("--out", default=f"bench/results/run-{int(time.time())}.jsonl")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--endpoint", default="https://api.anthropic.com")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--only-scenario")
    parser.add_argument("--only-config")
    args = parser.parse_args()

    token = load_oauth_token()

    scenarios: dict[str, Scenario] = {
        "reasoning": Scenario(
            name="reasoning",
            model=args.model,
            messages=[{
                "role": "user",
                "content": (
                    "Due treni distano 200 km e viaggiano uno verso l'altro a 80 km/h e 120 km/h. "
                    "A che distanza dal punto di partenza del primo treno si incontreranno?"
                ),
            }],
        ),
        "trivial": Scenario(
            name="trivial",
            model=args.model,
            messages=[{"role": "user", "content": "Che colore ha il cielo in una giornata serena?"}],
        ),
        "codegen": Scenario(
            name="codegen",
            model=args.model,
            messages=[{
                "role": "user",
                "content": (
                    "Scrivi una funzione Python che data una stringa restituisce True se e' un "
                    "anagramma valido (composto solo da lettere alfabetiche), False altrimenti. "
                    "Gestisci edge case: stringa vuota, spazi, numeri, maiuscole/minuscole."
                ),
            }],
        ),
    }

    configs: dict[str, Config] = {
        "baseline": Config(name="baseline", extra_body={}),
        "effort_low": Config(name="effort_low", extra_body={"output_config": {"effort": "low"}}),
        "effort_medium": Config(name="effort_medium", extra_body={"output_config": {"effort": "medium"}}),
        "effort_high": Config(name="effort_high", extra_body={"output_config": {"effort": "high"}}),
        "effort_xhigh": Config(name="effort_xhigh", extra_body={"output_config": {"effort": "xhigh"}}),
    }

    if args.only_scenario:
        scenarios = {k: v for k, v in scenarios.items() if args.only_scenario in k}
    if args.only_config:
        configs = {k: v for k, v in configs.items() if args.only_config in k}

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = run_matrix(
        token,
        scenarios,
        configs,
        repeat=args.repeat,
        sleep_s=1.0,
        out_path=out_path,
        parallel=args.parallel,
    )

    summary = summarize(results)
    print_table(summary)

    print(f"\nRisultati: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
