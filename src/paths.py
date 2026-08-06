"""Percorsi di configurazione per ai-router-switch.

Questo modulo è l'unica fonte di verità per tutti i percorsi
usati dall'applicazione, garantendo compatibilità cross-platform.
"""
from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Final

APP_DIR_NAME: Final[str] = "ai-router-switch"
CREDENTIALS_FILE_NAME: Final[str] = ".credentials.json"
MODE_FILE_NAME: Final[str] = "ai-router-mode"
CHAT_STORE_NAME: Final[str] = "ai-router-chats.json"
POLICY_FILE_NAME: Final[str] = "router-policy.json"
LEARNINGS_FILE_NAME: Final[str] = "router-learnings.json"
SECRETS_SCRIPT_NAME: Final[str] = "secrets.sh"
ENV_FILE_NAME: Final[str] = ".env"


@lru_cache(maxsize=1)
def config_home() -> Path:
    """Directory home di configurazione, con precedenza ESATTA.

    1) AIROUTER_HOME se valorizzata e non vuota
    2) ~/.claude se ESISTE (retrocompatibilità storica)
    3) Percorso piattaforma-specifico (non crea directory)
    """
    # 1) AIROUTER_HOME
    env_home = os.environ.get("AIROUTER_HOME")
    if env_home:
        return Path(env_home).expanduser()

    # 2) ~/.claude retrocompatibile se esiste
    legacy_home = Path.home() / ".claude"
    if legacy_home.is_dir():
        return legacy_home

    # 3) Percorso piattaforma-specifico
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            base = Path(appdata)
        else:
            base = Path.home() / "AppData" / "Roaming"
        return base / APP_DIR_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    # Linux/BSD/altro: XDG_CONFIG_HOME o ~/.config
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def reset_cache() -> None:
    """Svuota la cache di config_home (serve ai test)."""
    config_home.cache_clear()


def logs_dir() -> Path:
    """Directory dei log applicativi."""
    return config_home() / "logs"


def secrets_dir() -> Path:
    """Directory per gli script e file dei secret."""
    return config_home() / "secrets"


def state_dir() -> Path:
    """Directory per i file di stato dell'applicazione."""
    return config_home() / "state"


def credentials_file() -> Path:
    """File JSON con le credenziali dei provider AI."""
    return config_home() / CREDENTIALS_FILE_NAME


def mode_file() -> Path:
    """File che memorizza la modalità corrente del router."""
    return config_home() / MODE_FILE_NAME


def chat_store() -> Path:
    """File JSON con la cronologia delle chat."""
    return config_home() / CHAT_STORE_NAME


def policy_file() -> Path:
    """File JSON con le policy di routing."""
    return config_home() / POLICY_FILE_NAME


def learnings_file() -> Path:
    """File JSON con le informazioni apprese dal router."""
    return config_home() / LEARNINGS_FILE_NAME


def secrets_script() -> Path:
    """Script shell per caricare i secret nell'ambiente."""
    return secrets_dir() / SECRETS_SCRIPT_NAME


def env_file() -> Path:
    """File .env per variabili di ambiente locali."""
    return config_home() / ENV_FILE_NAME


def log_file(name: str) -> Path:
    """Percorso a un file di log specifico."""
    return logs_dir() / name


def ensure_dir(path: Path) -> Path:
    """Crea la directory con parents=True, exist_ok=True.

    Non solleva errori: il router non deve morire per permessi.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def trim_state_dir() -> Path:
    """Directory per il trimming dello stato (usa AIROUTER_TRIM_DIR o temp).

    Su Windows /tmp non esiste, per questo si passa da tempfile.
    """
    custom = os.environ.get("AIROUTER_TRIM_DIR")
    if custom:
        return Path(custom)
    return Path(tempfile.gettempdir()) / "ai-router-trim"
