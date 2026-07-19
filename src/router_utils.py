# ~270 lines
"""Debug, logging, and rate limiter extracted from ai-router-proxy.py (~lines 74-627)."""
import asyncio
import gzip
import json
import os
import random
import threading
import time
from collections import deque
from pathlib import Path

from aiohttp import web

from router_constants import (
    MINIMAX_RATE_LIMITS, MINIMAX_RATE_LIMITS_DEFAULT, MINIMAX_SAFETY,
    MINIMAX_BACKOFF_STEPS, MINIMAX_ALERTS_LOG,
    MINIMAX_RETRY_CAP_SEC,
)

# ── Debug directories ──────────────────────────────────────────────────────────
_DEBUG_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEBUG_LOGS_DIR = _DEBUG_PROJECT_ROOT / "logs"
_DEBUG_LOGS_DIR.mkdir(exist_ok=True)
_DEBUG_JSONL = _DEBUG_LOGS_DIR / "debug-errors.jsonl"
_DEBUG_LAST_REQ = _DEBUG_LOGS_DIR / "debug-last-request.json"
_DEBUG_LAST_SENT = _DEBUG_LOGS_DIR / "debug-last-sent.json"
_DEBUG_REPAIR_TRACE = _DEBUG_LOGS_DIR / "debug-repair-trace.json"

DEBUG_EVENTS: deque = deque(maxlen=100)
SENT_ANALYSIS: deque = deque(maxlen=50)

# ── Decompression helpers ──────────────────────────────────────────────────────
def _decompress_upstream(raw: bytes, content_encoding: str = "") -> str:
    """Decomprime gzip/brotli/deflate un body upstream in testo leggibile UTF-8."""
    if not raw:
        return ""
    try:
        enc = (content_encoding or "").lower()
        if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "br" in enc or "brotli" in enc:
            try:
                import brotli
                raw = brotli.decompress(raw)
            except Exception:
                pass
        elif "deflate" in enc:
            import zlib
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                raw = zlib.decompress(raw)
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")


def _orig_flags(orig: dict | None) -> dict:
    """Estrae flags diagnostici dal body richiesta originale."""
    if not orig:
        return {}
    msgs = orig.get("messages", [])
    img_count = 0
    for m in msgs:
        c = m.get("content", [])
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "image":
                    img_count += 1
    return {
        "msg_count": len(msgs),
        "has_tools": bool(orig.get("tools")),
        "has_images": img_count > 0,
        "has_thinking": bool(orig.get("thinking")),
        "cache_control_count": img_count,
        "system_is_list": isinstance(orig.get("system"), list),
    }


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


def _rotated_jsonl_path() -> Path:
    """Ritorna il path del JSONL: .1 se .0 supera 10MB."""
    p = _DEBUG_JSONL
    try:
        if p.exists() and p.stat().st_size > 10 * 1024 * 1024:
            rot = p.with_suffix(".jsonl.1")
            try:
                rot.unlink()
            except Exception:
                pass
            p.rename(rot)
    except Exception:
        pass
    return p


# ── Debug capture ──────────────────────────────────────────────────────────────
def debug_capture(*, kind: str, request=None, fp: str = "", client_model: str = "",
                  upstream_model: str = "", status: int | None = None, stage: str = "",
                  upstream_status: int | None = None, upstream_raw: bytes = b"",
                  upstream_encoding: str = "", sent_bytes: int = 0, orig: dict | None = None,
                  sent_analysis: dict | None = None, note: str = "", mode: str = None,
                  severity: str = "error") -> None:
    """Registra un evento di errore in RAM + JSONL. Decomprime il body upstream.

    FIX 2026-07-19: 'mode' era sempre get_file_mode() (il file globale), MAI il
    mode realmente risolto per la richiesta -> attribuzione fuorviante quando il
    file globale differiva dall'override per-chat. Ora usa get_mode(request, fp)
    (stessa risoluzione canonica forced->per-chat->file), a meno che il chiamante
    passi esplicitamente il mode gia' risolto (es. StreamingRelay.mode)."""
    try:
        from router_mode import get_mode
        resolved_mode = mode or get_mode(request, fp)
        err_text = _decompress_upstream(upstream_raw, upstream_encoding)
        flags = _orig_flags(orig)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": kind, "fp": fp,
            "mode": resolved_mode,
            "path": getattr(request, "path", "") if request else "",
            "client_model": client_model, "upstream_model": upstream_model,
            "status": status, "stage": stage,
            "upstream_status": upstream_status,
            "upstream_error": err_text[:2000],
            "sent_bytes": sent_bytes,
            "sent_analysis": sent_analysis, "flags": flags, "note": note,
        }
        DEBUG_EVENTS.append(record)
        try:
            import debug_catalog
            debug_catalog.record_event(
                severity=severity, category=resolved_mode, kind=kind,
                chat_fp=fp, code=upstream_status or status,
                snippet=err_text or note,
                detail={"client_model": client_model, "upstream_model": upstream_model,
                        "stage": stage, "path": record["path"]},
            )
        except Exception:
            pass
        p = _rotated_jsonl_path()
        try:
            with open(p, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if orig:
            req_copy = dict(orig)
            for m in req_copy.get("messages", []):
                c = m.get("content", [])
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "image":
                            d = b.get("data", "")
                            if len(d) > 200:
                                b["data"] = d[:200] + f"... [TRUNCATED {len(d) - 200} chars]"
            try:
                with open(_DEBUG_LAST_REQ, "w") as f:
                    json.dump(req_copy, f, ensure_ascii=False)
            except Exception:
                pass
    except Exception:
        pass


async def debug_errors(request) -> web.Response:
    n = int(request.query.get("n", "20"))
    return web.json_response(list(DEBUG_EVENTS)[-n:])


async def debug_last(request) -> web.Response:
    if not DEBUG_EVENTS:
        return web.Response(text="No errors captured yet.", content_type="text/plain")
    ev = DEBUG_EVENTS[-1]
    lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in ev.items()]
    return web.Response(text="\n".join(lines), content_type="text/plain")


async def debug_stats(request) -> web.Response:
    from collections import Counter
    c_kind = Counter(e.get("kind") for e in DEBUG_EVENTS)
    c_stage = Counter(e.get("stage") for e in DEBUG_EVENTS)
    c_upstream = Counter(str(e.get("upstream_status")) for e in DEBUG_EVENTS)
    return web.json_response({
        "total": len(DEBUG_EVENTS),
        "by_kind": dict(c_kind), "by_stage": dict(c_stage),
        "by_upstream_status": dict(c_upstream),
    })


async def debug_trace(request) -> web.Response:
    ev = DEBUG_EVENTS[-1] if DEBUG_EVENTS else None
    last_sent = None
    try:
        if _DEBUG_LAST_SENT.exists():
            last_sent = json.loads(_DEBUG_LAST_SENT.read_text(encoding="utf-8"))
    except Exception:
        pass
    repair_trace = None
    try:
        if _DEBUG_REPAIR_TRACE.exists():
            repair_trace = json.loads(_DEBUG_REPAIR_TRACE.read_text(encoding="utf-8"))
    except Exception:
        pass
    recent_analysis = list(SENT_ANALYSIS)[-10:]
    return web.json_response({
        "last_event": ev, "last_sent": last_sent,
        "repair_trace": repair_trace, "recent_sent_analysis": recent_analysis,
    })


async def debug_catalog_endpoint(request) -> web.Response:
    """Catalogo deduplicato bug/blocco/errore, vedi DEBUG-CATALOG-SPEC.md."""
    import debug_catalog
    category = request.query.get("mode") or request.query.get("category")
    severity = request.query.get("severity")
    items = debug_catalog.get_catalog(category=category, severity=severity)
    return web.json_response({"total": len(items), "items": items})


async def debug_catalog_entry(request) -> web.Response:
    import debug_catalog
    sig = request.match_info.get("signature", "")
    entry = debug_catalog.get_catalog_entry(sig)
    if entry is None:
        return web.json_response({"error": f"signature '{sig}' non trovata"}, status=404)
    return web.json_response(entry)


# ── Rate limiter ───────────────────────────────────────────────────────────────
def _classify_429(raw: bytes) -> str:
    """Classifica un body 429 MiniMax: 'token_plan' vs 'rpm_tpm'."""
    low = raw[:2000].lower()
    if b"usage limit" in low or b"resets at" in low:
        return "token_plan"
    return "rpm_tpm"


class RateLimitExhausted(Exception):
    """acquire() ha esaurito il budget di attesa senza trovare uno slot."""


class MinimaxRateLimiter:
    """Pacing client-side sui limiti ufficiali MiniMax (sliding window 60s per modello)."""

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
    """Notifica Token Plan esaurito: notify-send + file."""
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
    import traceback
    log(f"{msg}\n{traceback.format_exc()}")


# ── Message sequence repair ────────────────────────────────────────────────────
def _repair_message_sequence(messages: list) -> list:
    """Ripara sequenza dopo troncamento: rimuove tool_result orfani, leading non-user."""
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
# FIX E + FIX AUDIT 2026-07-17: chat_fp|"__remap__" -> modello originale richiesto
# dal client. Scritto da remap_body_for_minimax(), consumato da relay() per
# riscrivere il campo 'model' nella SSE response.
_request_orig_model: dict = {}
