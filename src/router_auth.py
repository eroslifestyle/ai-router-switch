# ~80 lines
"""Auth utilities extracted from ai-router-proxy.py (~lines 973-1164)."""
import json
import os
import subprocess
import sys

import paths
import secrets_provider
from router_utils import log

_CREDS_PATH = paths.credentials_file()
_oauth_file_cache = {"token": "", "mtime": -1.0}


def _read_oauth_from_keychain() -> str:
    """Legge il token OAuth dal Keychain macOS, dove Claude Code lo tiene invece che su file."""
    if sys.platform != "darwin":
        return ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return ""
        return json.loads(out.stdout).get("claudeAiOauth", {}).get("accessToken", "")
    except Exception:
        return ""


def _read_oauth_from_file() -> str:
    """Token OAuth da file, con cache su mtime; su macOS ripiega sul Keychain."""
    try:
        mtime = _CREDS_PATH.stat().st_mtime
    except Exception:
        return _oauth_file_cache["token"] or _read_oauth_from_keychain()
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
    """Chiave MiniMax: env MINIMAX_API_KEY, poi la catena di secrets_provider."""
    return await secrets_provider.get_secret_async(
        "minimax.api_key", extra_env=("MINIMAX_API_KEY",),
    )
