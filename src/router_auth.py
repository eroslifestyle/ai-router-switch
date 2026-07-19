# ~80 lines
"""Auth utilities extracted from ai-router-proxy.py (~lines 973-1164)."""
import asyncio
import json
import os
import threading
import time
from pathlib import Path

from router_constants import KEY_FILE
from router_utils import log

_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
_oauth_file_cache = {"token": "", "mtime": -1.0}
_minimax_key_cache = {"key": None, "ts": 0}
_key_cache_lock = threading.Lock()


def _read_oauth_from_file() -> str:
    """Legge il token OAuth dal file di credenziali Claude Code con cache mtime."""
    try:
        mtime = _CREDS_PATH.stat().st_mtime
    except Exception:
        return _oauth_file_cache["token"]
    if mtime == _oauth_file_cache["mtime"]:
        return _oauth_file_cache["token"]
    try:
        with open(_CREDS_PATH) as f:
            tok = json.load(f).get("claudeAiOauth", {}).get("accessToken", "")
        _oauth_file_cache["token"] = tok
        _oauth_file_cache["mtime"] = mtime
        return tok
    except Exception:
        return _oauth_file_cache["token"]


def _load_oauth_token():
    """Carica il token OAuth Anthropic da ~/.claude/.credentials.json."""
    if os.environ.get("ANTHROPIC_OAUTH_TOKEN"):
        return
    tok = _read_oauth_from_file()
    if tok:
        os.environ["ANTHROPIC_OAUTH_TOKEN"] = tok


def _reload_oauth_token() -> bool:
    """Ricarica il token da .credentials.json. Ritorna True se ha popolato ANTHROPIC_OAUTH_TOKEN."""
    tok = _read_oauth_from_file()
    if tok:
        cur = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
        if tok != cur:
            log(f"oauth token reload: {'changed' if cur else 'initial'}")
        os.environ["ANTHROPIC_OAUTH_TOKEN"] = tok
        return True
    return False


async def get_minimax_key() -> str:
    """Cache-first minimax key: env > secrets.sh subprocess."""
    now = time.time()
    with _key_cache_lock:
        cached = _minimax_key_cache["key"]
        cached_ts = _minimax_key_cache["ts"]
        if cached and now - cached_ts < 60:
            return cached
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        try:
            import subprocess
            try:
                loop = asyncio.get_running_loop()
                proc = await asyncio.to_thread(
                    lambda: subprocess.check_output(
                        ["bash", str(KEY_FILE), "get", "minimax.api_key"],
                        timeout=5, text=True,
                    )
                )
                key = proc.strip() if isinstance(proc, str) else proc.decode().strip()
            except RuntimeError:
                proc = subprocess.check_output(
                    ["bash", str(KEY_FILE), "get", "minimax.api_key"],
                    text=True, timeout=5,
                )
                key = proc.strip()
        except Exception as e:
            log(f"ERR get key: {type(e).__name__}")
            key = ""
    with _key_cache_lock:
        if not _minimax_key_cache["key"] or now - _minimax_key_cache["ts"] >= 60:
            _minimax_key_cache["key"] = key
            _minimax_key_cache["ts"] = now
    return key
