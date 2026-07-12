#!/usr/bin/env python3
"""Context shrink adattivo con HHEM e learn."""

import json
import os
import time
from pathlib import Path

POLICY_PATH = Path.home() / ".claude" / "state" / "shrink_policy.json"
HHEM_URL = "http://100.71.178.53:4002/score"

# Model name aliases → JSON key
MODEL_MAP = {
    "haiku-4.5": "haiku-4.5",
    "sonnet-5": "sonnet-5",
    "opus-4.8": "opus-4.8",
    "opus": "opus-4.8",
    "m2.7-hs": "m2.7-hs",
    "minimax-m2.7-hs": "m2.7-hs",
    "m3": "m3",
    "minimax-m3": "m3",
    "glm-4.7": "glm-4.7",
    "glm-5-turbo": "glm-5-turbo",
    "glm-5.2": "glm-5.2",
    "glm-5": "glm-5-turbo",
}

_policy_cache = None
_last_save_ts = 0.0
SAVE_COOLDOWN = 30.0


def _load_policy() -> dict:
    global _policy_cache
    if _policy_cache is None:
        if POLICY_PATH.exists():
            _policy_cache = json.loads(POLICY_PATH.read_text())
        else:
            raise FileNotFoundError(f"Policy not found: {POLICY_PATH}")
    return _policy_cache


def _persist(policy: dict) -> None:
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    POLICY_PATH.write_text(json.dumps(policy, indent=2))
    global _policy_cache, _last_save_ts
    _policy_cache = policy
    _last_save_ts = time.time()


def _resolve_key(model: str) -> str:
    key = MODEL_MAP.get(model, model)
    policy = _load_policy()
    if key not in policy["models"]:
        raise ValueError(f"Unknown model: {model!r} (tried {key!r})")
    return key


def threshold_for(model: str) -> float:
    """Soglia attuale per il modello, clamp con floor/ceiling."""
    policy = _load_policy()
    key = _resolve_key(model)
    cfg = policy["models"][key]
    raw = cfg["threshold"]
    floor = policy["_guardrails"]["floor"]
    ceiling = policy["_guardrails"]["ceiling"]
    return max(floor, min(ceiling, raw))


def should_shrink(history_tokens: int, model: str) -> bool:
    """True se history ha superato la soglia."""
    policy = _load_policy()
    key = _resolve_key(model)
    ctx = policy["models"][key]["ctx"]
    return history_tokens >= threshold_for(model) * ctx


def _find_open_tool_calls(messages: list) -> list:
    """Raccoglie msg con tool_calls/tool_use_id non chiusi da un tool_result."""
    open_ids = set()
    result_ids = set()
    for m in messages:
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                open_ids.add(tc.get("id") or tc.get("tool_use_id", ""))
        if m.get("role") == "tool":
            result_ids.add(m.get("tool_call_id") or m.get("tool_use_id", ""))
    return [m for m in messages if m.get("tool_calls") and
            any(tc.get("id") or tc.get("tool_use_id", "") not in result_ids
                for tc in m.get("tool_calls", []))]


def _find_handoff_summary(messages: list) -> dict | None:
    """Ritorna il msg system con handoff_summary se presente."""
    for m in messages:
        if m.get("role") == "system" and m.get("handoff_summary"):
            return m
        if m.get("handoff_summary"):
            return m
    return None


def shrink(history: list, model: str, orchestrator_fn) -> list:
    """
    Comprime history preservando: system, ultimi N turni, tool_use aperti, riassunto handoff.
    Il riassunto è prodotto da orchestrator_fn(original_messages → str).
    """
    policy = _load_policy()
    min_turns = policy["_guardrails"]["min_turns_preserved"]
    guardrails = policy["_guardrails"]
    floor = guardrails["floor"]
    ceiling = guardrails["ceiling"]
    min_preserved = guardrails["min_turns_preserved"]

    if not history:
        return history

    system_msgs = [m for m in history if m.get("role") == "system"]
    non_system = [m for m in history if m.get("role") != "system"]

    # Ultimi N turni (coppie user/assistant, minimi min_turns_preserved)
    turns = []
    buffer = []
    for msg in reversed(non_system):
        buffer.insert(0, msg)
        if msg.get("role") == "user" and len(buffer) >= 2:
            turns = buffer + turns
            buffer = []
    if buffer:
        turns = buffer + turns
    # Sempre almeno min_turns_preserved
    if len(turns) < min_preserved * 2:
        turns = non_system[-min_preserved * 2:] if len(non_system) >= min_preserved * 2 else non_system[:]

    open_tools = _find_open_tool_calls(non_system)
    handoff = _find_handoff_summary(history)

    # Costruisci cosa riassumere: tutto ciò che verrà scartato
    preserved_keys = {id(m) for m in system_msgs + turns + open_tools}
    if handoff:
        preserved_keys.add(id(handoff))
    to_summarize = [m for m in non_system if id(m) not in preserved_keys]

    if not to_summarize:
        result = system_msgs + turns + open_tools
        if handoff and handoff not in result:
            result.append(handoff)
        return result

    # Riassunto tramite orchestrator_fn
    original_text = "\n".join(
        f"[{m.get('role', '?')}]: {m.get('content', '')}" for m in to_summarize
    )
    summary_text = orchestrator_fn(original_text)
    summary_msg = {
        "role": "system",
        "content": f"[Riassunto conversazione precedente]\n{summary_text}",
        "is_summary": True,
    }

    result = system_msgs[:1] if system_msgs else []
    if handoff and handoff.get("role") != "system":
        result.append(handoff)
    result.append(summary_msg)
    result.extend(turns)
    result.extend(open_tools)

    return result


def learn(event: dict) -> None:
    """
    Aggiorna la soglia per-modello sul segnale.
    event = {"model": str, "type": "overflow"|"hhem_low"|"latency"|"relabor", ...}
    """
    policy = _load_policy()
    model = event.get("model")
    if not model:
        return
    try:
        key = _resolve_key(model)
    except ValueError:
        return

    cfg = policy["models"][key]
    floor = policy["_guardrails"]["floor"]
    ceiling = policy["_guardrails"]["ceiling"]
    step = 0.02

    # Segnali negativi → alza soglia (shrink prima)
    positive = {"overflow", "hhem_low", "latency", "relabor"}
    delta = step if event.get("type") in positive else -step

    new_threshold = max(floor, min(ceiling, cfg["threshold"] + delta))
    cfg["threshold"] = new_threshold
    cfg["events"] += 1
    cfg["adjustments"].append({
        "ts": time.time(),
        "type": event.get("type"),
        "delta": delta,
        "new_threshold": new_threshold,
    })
    _persist(policy)


def save_policy() -> None:
    """Write JSON, throttled: non riscrive se < 30s dall'ultimo save."""
    global _last_save_ts
    now = time.time()
    if now - _last_save_ts < SAVE_COOLDOWN:
        return
    policy = _load_policy()
    policy["_meta"]["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
    _persist(policy)


def _hhem_score(original: str, summary: str) -> float:
    """
    Chiama HHEM locale (:4002) per valutare qualità del riassunto.
    Ritorna float 0-1 (1 = perfetto, 0 = allucinato).
    """
    import subprocess
    try:
        result = subprocess.run(
            [
                "curl", "-s", HHEM_URL,
                "--data-urlencode", f"source={original}",
                "--data-urlencode", f"claim={summary}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.strip()
        # HHEM ritorna un float o JSON con campo score
        try:
            parsed = json.loads(output)
            return float(parsed.get("score", parsed.get("hhem_score", 0.5)))
        except (json.JSONDecodeError, ValueError):
            return float(output)
    except Exception:
        return 0.5  # fallback neutro


def reload_policy() -> None:
    """Forza ricarica del policy file dal disco."""
    global _policy_cache
    _policy_cache = None


if __name__ == "__main__":
    # Demo / self-check
    print(f"Policy path: {POLICY_PATH}")
    print(f"Exists: {POLICY_PATH.exists()}")
    if POLICY_PATH.exists():
        p = _load_policy()
        print(f"Models: {list(p['models'].keys())}")
        for m in ["m2.7-hs", "sonnet-5", "glm-5.2"]:
            try:
                print(f"  threshold_for({m!r}) = {threshold_for(m)}")
            except Exception as e:
                print(f"  threshold_for({m!r}) ERROR: {e}")
