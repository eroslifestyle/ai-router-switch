# ~130 lines
"""Mode management and chat fingerprint extracted from ai-router-proxy.py (~lines 683-866)."""
import hashlib
import json
import threading
import time
from pathlib import Path

from aiohttp import web

from router_constants import (
    MODE_FILE, CHAT_STORE, CHAT_TTL_DAYS, CHAT_MAX_ENTRIES, VALID_MODES,
)
from router_utils import log

# ── Global mutable state ────────────────────────────────────────────────────────
_chat_cache = {"data": None, "ts": 0}
_chat_lock = threading.Lock()  # kept for compat, load_chats is lock-free now

# ── Mode from file ─────────────────────────────────────────────────────────────
def get_file_mode() -> str:
    try:
        m = MODE_FILE.read_text().strip().lower()
        if m in VALID_MODES:
            return m
    except Exception:
        pass
    return "anthropic"


def _current_mode() -> str:
    """Helper per /health: modalità corrente (da file, fallback 'anthropic')."""
    return get_file_mode()


def _err_response(message: str, status: int = 502) -> web.Response:
    """Err response con status propagato dall'upstream."""
    return web.json_response(
        {"type": "error", "error": {"type": "router_error", "message": str(message)}},
        status=status,
    )


# ── Chat fingerprint ────────────────────────────────────────────────────────────
def conversation_fingerprint(data: dict) -> str:
    """Identifica una chat senza session-id: hash(system + primo messaggio utente)."""
    system = data.get("system", "")
    if isinstance(system, list):
        system = " ".join(b.get("text", "") for b in system if isinstance(b, dict))
    first_user = ""
    for m in data.get("messages", []):
        if m.get("role") == "user":
            c = m.get("content", "")
            first_user = c if isinstance(c, str) else " ".join(
                b.get("text", "") for b in c if isinstance(b, dict))
            break
    return hashlib.sha256((str(system) + "||" + str(first_user)).encode()).hexdigest()[:12]


def _resolve_chat_fingerprint(request) -> str:
    """NAT-safe fingerprint. Priorita': X-Claude-Code-Session-Id > X-Session-ID >
    content-hash cache (request['chat_fp'], settata in handle()) > remote.
    Senza la cache, tutte le chat locali senza header collassano su request.remote."""
    sid = (request.headers.get("X-Claude-Code-Session-Id")
           or request.headers.get("x-claude-code-session-id")
           or request.headers.get("X-Session-ID")
           or request.headers.get("x-session-id"))
    if sid:
        return f"sid:{sid[:64]}"
    try:
        cached = request.get("chat_fp")
        if cached:
            return cached
    except Exception:
        pass
    return request.remote or "default"


# ── Chat mode store (lock-free, cache-first) ───────────────────────────────────
def _load_chats() -> dict:
    """Cache + lettura file FUORI dal lock (async-safe)."""
    now = time.time()
    cached = _chat_cache["data"]
    cached_ts = _chat_cache["ts"]
    if cached is not None and now - cached_ts < 5:
        return cached
    try:
        raw = CHAT_STORE.read_text()
        d = json.loads(raw)
    except Exception:
        d = {}
    cutoff = now - CHAT_TTL_DAYS * 86400
    changed = False
    for fp in list(d.keys()):
        if d[fp].get("ts", 0) < cutoff:
            del d[fp]; changed = True
    if len(d) > CHAT_MAX_ENTRIES:
        for fp in sorted(d, key=lambda k: d[k].get("ts", 0))[: len(d) - CHAT_MAX_ENTRIES]:
            del d[fp]; changed = True
    if changed:
        try:
            tmp = CHAT_STORE.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(CHAT_STORE)
        except Exception as e:
            log(f"ERR save chats: {type(e).__name__}")
    _chat_cache["data"] = d
    _chat_cache["ts"] = now
    return d


def _save_chats(d: dict):
    """Write atomico via temp file, NO lock (async-safe)."""
    try:
        tmp = CHAT_STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(CHAT_STORE)
        _chat_cache["data"] = d
        _chat_cache["ts"] = time.time()
    except Exception as e:
        log(f"ERR save chats: {type(e).__name__}")


def get_chat_mode(fp: str):
    try:
        d = _load_chats()
        return d.get(fp, {}).get("mode")
    except Exception:
        return None


def set_chat_mode(fp: str, mode: str):
    d = _load_chats()
    d[fp] = {"mode": mode, "ts": time.time()}
    _save_chats(d)
    log(f"chat {fp} -> mode {mode}")


def clear_chat_mode(fp: str):
    d = _load_chats()
    if fp in d:
        del d[fp]; _save_chats(d)
        log(f"chat {fp} -> reset")


# ── Combined mode getter ────────────────────────────────────────────────────────
_LEGACY_MODE_MAP = {"mixed": "mix-am", "inverse": "minimax",
                    "glm-minimax": "mix-gm", "anthropic-glm": "mix-ag"}


def get_mode(request=None, fp: str = None) -> str:
    """Modalità deterministica: forced_mode per porta fissa, per-chat override, file."""
    if request is not None:
        forced = request.app.get("forced_mode")
        if forced in VALID_MODES:
            return forced
    if fp:
        cm = get_chat_mode(fp)
        cm = _LEGACY_MODE_MAP.get(cm, cm)
        if cm in VALID_MODES:
            return cm
        if cm:
            log(f"chat {fp}: override '{cm}' non valido -> ignorato")
    mode = get_file_mode()
    if mode not in VALID_MODES:
        log(f"mode '{mode}' non valido -> default 'mix-am'")
        mode = "mix-am"
    return mode
