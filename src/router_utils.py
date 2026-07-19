# ~320 lines
"""Router utilities: rate limiter, log, body analysis, sequence repair.
Debug centralizzato → src/router_debug.py (importato in fondo per retrocompatibilità)."""
import asyncio
import json
import os
import random
import time
import traceback
from collections import deque
from pathlib import Path

from aiohttp import web

from router_constants import (
    MINIMAX_RATE_LIMITS, MINIMAX_RATE_LIMITS_DEFAULT, MINIMAX_SAFETY,
    MINIMAX_BACKOFF_STEPS, MINIMAX_ALERTS_LOG,
    MINIMAX_RETRY_CAP_SEC,
)

# ── Analysis & tracking ──────────────────────────────────────────────────────────
SENT_ANALYSIS: deque = deque(maxlen=50)


# ── Body structure analysis ────────────────────────────────────────────────────
def _analyze_body_structure(body: "dict | bytes") -> dict:
    """Diagnostica profondo di un body request — rileva orfani, anomalie strutturali."""
    size_bytes = len(body) if isinstance(body, bytes) else len(json.dumps(body).encode())
    data = json.loads(body) if isinstance(body, bytes) else body
    msgs = data.get("messages", []) or []

    def _block_types(msg: dict) -> list:
        c = msg.get("content")
        if isinstance(c, list):
            return [b.get("type", "?") for b in c if isinstance(b, dict)]
        return []

    first = msgs[0] if msgs else {}
    last = msgs[-1] if msgs else {}

    orphan_tool_results = []
    tool_use_ids = []
    tool_result_ids = []
    for i, m in enumerate(msgs):
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    t = b.get("type", "")
                    if t == "tool_use":
                        tool_use_ids.append(b.get("id", ""))
                    elif t == "tool_result":
                        tool_result_ids.append(b.get("tool_use_id", ""))
                        tid = b.get("tool_use_id", "")
                        prev = msgs[i - 1] if i > 0 else None
                        valid = (
                            prev is not None
                            and prev.get("role") == "assistant"
                            and any(
                                isinstance(pb, dict) and pb.get("type") == "tool_use" and pb.get("id") == tid
                                for pb in (prev.get("content") or [])
                                if isinstance(pb, dict)
                            )
                        )
                        orphan_tool_results.append({
                            "msg_index": i, "tool_use_id": tid,
                            "reason": "first_message" if i == 0 else ("no_prior_tool_use" if not valid else "valid"),
                        })
    orphan_tool_results = [o for o in orphan_tool_results if o.get("reason") != "valid"]

    dangling_tool_uses = []
    for i, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tid = b.get("id", "")
                next_m = msgs[i + 1] if i + 1 < len(msgs) else None
                has_result = (
                    next_m is not None
                    and isinstance(next_m.get("content"), list)
                    and any(
                        isinstance(rb, dict) and rb.get("type") == "tool_result" and rb.get("tool_use_id") == tid
                        for rb in next_m.get("content")
                    )
                )
                if not has_result:
                    dangling_tool_uses.append({"msg_index": i, "tool_use_id": tid})

    role_system_in_messages = sum(1 for m in msgs if m.get("role") == "system")
    has_images = any(
        isinstance(b, dict) and b.get("type") == "image"
        for m in msgs
        for b in (m.get("content") or [])
        if isinstance(b, dict)
    )

    return {
        "size_bytes": size_bytes,
        "msg_count": len(msgs),
        "first_msg": {"role": first.get("role"), "block_types": _block_types(first)},
        "last_msg": {"role": last.get("role"), "block_types": _block_types(last)},
        "role_system_in_messages": role_system_in_messages,
        "orphan_tool_results": orphan_tool_results,
        "dangling_tool_uses": dangling_tool_uses,
        "has_images": has_images,
        "tool_use_ids": len(tool_use_ids),
        "tool_result_ids": len(tool_result_ids),
    }


# ── Rate limiter ───────────────────────────────────────────────────────────────
def _classify_429(raw: bytes) -> str:
    low = raw[:2000].lower()
    if b"usage limit" in low or b"resets at" in low:
        return "token_plan"
    return "rpm_tpm"


class RateLimitExhausted(Exception):
    """acquire() ha esaurito il budget di attesa senza trovare uno slot."""


class MinimaxRateLimiter:
    def __init__(self):
        self._model_locks = {}
        self._windows = {}
        self._cooldown_until = 0.0
        self._plan_exhausted_until = ""
        self._backoff_idx = 0

    def _model_lock(self, model: str) -> asyncio.Lock:
        if model not in self._model_locks:
            self._model_locks[model] = asyncio.Lock()
        return self._model_locks[model]

    def _limits(self, model: str):
        rpm, tpm = MINIMAX_RATE_LIMITS.get(model, MINIMAX_RATE_LIMITS_DEFAULT)
        return max(1, int(rpm * MINIMAX_SAFETY)), int(tpm * MINIMAX_SAFETY)

    def _prune(self, model: str, now: float):
        win = self._windows.setdefault(model, deque())
        while win and now - win[0][0] > 60.0:
            win.popleft()
        return win

    async def acquire(self, model: str, est_tokens: int, budget_sec: float):
        waited = 0.0
        while True:
            async with self._model_lock(model):
                now = time.monotonic()
                if self._cooldown_until > now:
                    wait = min(self._cooldown_until - now, 60.0)
                else:
                    win = self._prune(model, now)
                    rpm_limit, tpm_limit = self._limits(model)
                    tpm_used = sum(e[1] for e in win)
                    if len(win) < rpm_limit and tpm_used + est_tokens <= tpm_limit:
                        entry = [now, est_tokens]
                        win.append(entry)
                        return entry
                    wait = max(0.5, 60.0 - (now - win[0][0])) if win else 1.0
            wait += random.uniform(0.05, 0.5)
            if waited + wait > budget_sec:
                raise RateLimitExhausted(
                    f"minimax rate-limit: budget {budget_sec:.0f}s esaurito (waited {waited:.0f}s)")
            await asyncio.sleep(wait)
            waited += wait

    def record(self, entry: list, actual_tokens: int, success: bool):
        entry[1] = actual_tokens if success else 0

    def on_429_rpm(self):
        step = MINIMAX_BACKOFF_STEPS[min(self._backoff_idx, len(MINIMAX_BACKOFF_STEPS) - 1)]
        self._backoff_idx += 1
        until = time.monotonic() + step + random.uniform(0, 2)
        if until > self._cooldown_until:
            self._cooldown_until = until
        return step

    def on_success(self):
        self._backoff_idx = 0
        self._cooldown_until = 0.0

    def set_plan_exhausted(self, reset_hint: str):
        self._plan_exhausted_until = reset_hint[:200]

    def snapshot(self) -> dict:
        now = time.monotonic()
        per_model = {}
        for m, win in self._windows.items():
            live = [e for e in win if now - e[0] <= 60.0]
            rpm_limit, tpm_limit = self._limits(m)
            per_model[m] = {"rpm_used": len(live), "rpm_limit": rpm_limit,
                            "tpm_used": sum(e[1] for e in live), "tlimit": tpm_limit}
        return {"cooldown_sec": max(0.0, round(self._cooldown_until - now, 1)),
                "plan_exhausted": self._plan_exhausted_until, "per_model": per_model}


MINIMAX_LIMITER = MinimaxRateLimiter()
_MINIMAX_SEM = asyncio.Semaphore(int(os.environ.get("AIROUTER_MINIMAX_SEMAPHORE", "8")))


# ── Alert ──────────────────────────────────────────────────────────────────────
_last_alert_ts = 0.0
_ALERT_MIN_INTERVAL_SEC = 300


def _minimax_alert(msg: str):
    global _last_alert_ts
    try:
        with open(MINIMAX_ALERTS_LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    now = time.monotonic()
    if now - _last_alert_ts < _ALERT_MIN_INTERVAL_SEC:
        return
    _last_alert_ts = now
    try:
        import subprocess
        subprocess.Popen(["notify-send", "-u", "normal", "-t", "20000",
                          "MiniMax Token Plan", msg[:300]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg: str):
    from router_constants import LOG_FILE
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_exc(msg: str):
    log(f"{msg}\n{traceback.format_exc()}")


# ── Message sequence repair ────────────────────────────────────────────────────
def _repair_message_sequence(messages: list) -> list:
    if not messages:
        return messages
    msgs = [dict(m) for m in messages if m.get("role") != "system"]
    changed = True
    while changed and msgs:
        changed = False
        while msgs and msgs[0].get("role") != "user":
            msgs.pop(0)
            changed = True
        if not msgs:
            break
        seen = set()
        new_msgs = []
        for m in msgs:
            content = m.get("content")
            if isinstance(content, list):
                nc = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        if b.get("tool_use_id") in seen:
                            nc.append(b)
                        else:
                            changed = True
                    else:
                        nc.append(b)
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        seen.add(b.get("id"))
                if nc:
                    m["content"] = nc
                    new_msgs.append(m)
                else:
                    changed = True
            else:
                new_msgs.append(m)
        msgs = new_msgs
    if msgs and msgs[-1].get("role") == "assistant" and isinstance(msgs[-1].get("content"), list):
        clean = [c for c in msgs[-1]["content"]
                 if not (isinstance(c, dict) and c.get("type") == "tool_use")]
        if len(clean) < len(msgs[-1]["content"]):
            if clean:
                msgs[-1]["content"] = clean
            else:
                msgs.pop()
    def _first_is_clean_user(ms):
        if not ms or ms[0].get("role") != "user":
            return False
        c = ms[0].get("content")
        if isinstance(c, list):
            return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
        return True
    if not _first_is_clean_user(msgs):
        msgs.insert(0, {"role": "user", "content": "(cronologia precedente troncata)"})
    return msgs


# ── Original model tracking ────────────────────────────────────────────────────
_request_orig_model: dict = {}


# ── Retrocompatibilità: re-export debug da router_debug ─────────────────────────
# Tutti i call site esistenti importano da router_utils. Il debug è ora in
# router_debug.py, ma re-exportiamo qui per non dover cambiare centinaia di
# import nei file del progetto.
from router_debug import debug_capture, DEBUG_EVENTS, dl, _DEBUG_LAST_SENT, _DEBUG_REPAIR_TRACE, DEBUG_LAST_REQ  # noqa: E402, F401

# Retrocompat: funzioni HTTP endpoint spostate in router_debug
from router_debug import dl as _dl_global
debug_errors = _dl_global.errors_endpoint
debug_last = _dl_global.last_endpoint
debug_stats = _dl_global.stats_endpoint
debug_trace = _dl_global.trace_endpoint
debug_catalog_endpoint = _dl_global.catalog_endpoint
debug_catalog_entry = _dl_global.catalog_entry_endpoint
