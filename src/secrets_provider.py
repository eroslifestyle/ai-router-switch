"""Provider per la risoluzione di secrets API, cross-platform.

Nato per sostituire le chiamate bash allo script secrets.sh su Windows.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import paths

# =============================================================================
# Costanti
# =============================================================================

CACHE_TTL_SEC: int = 60

# =============================================================================
# Cache thread-safe con TTL
# =============================================================================


class _CacheEntry:
    __slots__ = ("value", "timestamp")

    def __init__(self, value: str) -> None:
        self.value: str = value
        self.timestamp: float = time.monotonic()


class _SecretCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple[str, tuple[str, ...]], _CacheEntry] = {}

    def get(self, key: tuple[str, tuple[str, ...]]) -> str | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry.timestamp > CACHE_TTL_SEC:
                del self._data[key]
                return None
            return entry.value

    def set(self, key: tuple[str, tuple[str, ...]], value: str) -> None:
        with self._lock:
            self._data[key] = _CacheEntry(value)

    def clear(self) -> None:
        """Svuota la cache."""
        with self._lock:
            self._data.clear()


_cache = _SecretCache()


def clear_cache() -> None:
    """Svuota la cache dei secrets (usato dai test)."""
    _cache.clear()


# =============================================================================
# Utilità di mascheramento
# =============================================================================


def mask(value: str) -> str:
    """Maschera un valore per non esporlo nei log.

    Ritorna prime 3 + ... + ultime 4 lettere se > 12 chars,
    altrimenti '***'.
    """
    if len(value) <= 12:
        return "***"
    return f"{value[:3]}...{value[-4:]}"


# =============================================================================
# Gestione .env
# =============================================================================

def _parse_env(content: str) -> dict[str, str]:
    """Parser minimale del formato .env.

    Ignora righe vuote e commenti (#), gestisce valori con o senza
    apici, ripulisce spazi e apici esterni.
    """
    result: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, raw_value = stripped.partition("=")
        key = key.strip()
        value = raw_value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        result[key] = value
    return result


def _load_env_file(path: str | Path | None = None) -> dict[str, str] | None:
    """Carica un file .env dal path dato, o da paths.env_file() se None."""
    env_path = path if path is not None else paths.env_file()
    if env_path is None:
        return None
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            return _parse_env(f.read())
    except OSError:
        return None


# =============================================================================
# API pubblica
# =============================================================================


def env_var_name(name: str) -> str:
    """Converte 'provider.campo' in 'PROVIDER_CAMPO' per variabili ambiente.

    Punti e trattini diventano underscore, tutto maiuscolo.
    """
    return name.replace(".", "_").replace("-", "_").upper()


def _get_secret_sync(
    name: str,
    *,
    extra_env: tuple[str, ...] = (),
    env_files: tuple[str | Path, ...] = (),
    use_cache: bool = True,
) -> str:
    """Implementazione sincrona della risoluzione secrets."""
    cache_key = (name, extra_env, env_files)
    if use_cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

    # 1. extra_env (alias storici)
    for var in extra_env:
        val = os.environ.get(var, "")
        if val:
            if use_cache:
                _cache.set(cache_key, val)
            return val

    # 2. env_var_name(name)
    env_name = env_var_name(name)
    val = os.environ.get(env_name, "")
    if val:
        if use_cache:
            _cache.set(cache_key, val)
        return val

    # 3. env_files aggiuntivi + .env dalla config dir
    for env_file in env_files:
        env_data = _load_env_file(env_file)
        if env_data is not None:
            val = env_data.get(env_name, "")
            if not val:
                val = env_data.get(name, "")
            if val:
                if use_cache:
                    _cache.set(cache_key, val)
                return val

    # 3b. .env dalla config dir (solo se non gia trovato in env_files)
    env_data = _load_env_file()
    if env_data is not None:
        val = env_data.get(env_name, "")
        if not val:
            val = env_data.get(name, "")
        if val:
            if use_cache:
                _cache.set(cache_key, val)
            return val

    # 4. secrets script (solo se bash disponibile)
    script = paths.secrets_script()
    if script.exists() and shutil.which("bash"):
        try:
            result = subprocess.run(
                ["bash", str(script), "get", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                val = result.stdout.strip()
                if val:
                    if use_cache:
                        _cache.set(cache_key, val)
                    return val
        except (subprocess.TimeoutExpired, OSError):
            pass

    # 5. keyring (se disponibile)
    try:
        import keyring

        val = keyring.get_password("ai-router-switch", name) or ""
        if val:
            if use_cache:
                _cache.set(cache_key, val)
            return val
    except ImportError:
        pass

    # Non trovato
    if use_cache:
        _cache.set(cache_key, "")
    return ""


def get_secret(
    name: str,
    *,
    extra_env: tuple[str, ...] = (),
    env_files: tuple[str | Path, ...] = (),
    use_cache: bool = True,
) -> str:
    """Risolve un secret dalla catena: extra_env -> env -> env_files -> .env -> script -> keyring.

    Non solleva mai eccezioni. Valori mancanti sono condizioni normali.
    """
    return _get_secret_sync(
        name, extra_env=extra_env, env_files=env_files, use_cache=use_cache
    )


async def get_secret_async(
    name: str,
    *,
    extra_env: tuple[str, ...] = (),
    env_files: tuple[str | Path, ...] = (),
    use_cache: bool = True,
) -> str:
    """Versione asincrona di get_secret: esegue I/O bloccante in thread separato.

    Evita di bloccare il loop asyncio durante letture file e subprocess.
    """
    return await asyncio.to_thread(
        _get_secret_sync, name, extra_env=extra_env, env_files=env_files, use_cache=use_cache
    )
