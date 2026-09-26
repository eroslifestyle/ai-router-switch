"""Test per isolamento di debug_logs_dir dalla produzione.

Verifica che:
1. paths.debug_logs_dir() rispetti AIROUTER_LOGS_DIR
2. router_debug.py NON usi _PROJECT_ROOT / "logs" ma paths.debug_logs_dir()
3. Sotto conftest (che imposta AIROUTER_LOGS_DIR), debug_logs_dir stia fuori dal repo
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))


def test_debug_logs_dir_respects_env(monkeypatch, tmp_path):
    """debug_logs_dir() ritorna il percorso di AIROUTER_LOGS_DIR se valorizzato."""
    import paths

    monkeypatch.setenv("AIROUTER_LOGS_DIR", str(tmp_path))
    # Pulisci cache se presente
    if hasattr(paths.debug_logs_dir, 'cache_clear'):
        paths.debug_logs_dir.cache_clear()

    result = paths.debug_logs_dir()
    assert result == tmp_path


def test_debug_logs_dir_default_is_repo_logs(monkeypatch):
    """Senza AIROUTER_LOGS_DIR, debug_logs_dir() torna a <repo>/logs."""
    import paths

    # Rimuovi la variabile
    monkeypatch.delenv("AIROUTER_LOGS_DIR", raising=False)
    if hasattr(paths.debug_logs_dir, 'cache_clear'):
        paths.debug_logs_dir.cache_clear()

    result = paths.debug_logs_dir()
    assert result == REPO / "logs"
    # Verifica che il parent sia il repo
    assert result.parent.name == REPO.name


def test_router_debug_uses_paths_debug_logs_dir(monkeypatch, tmp_path):
    """router_debug._LOGS_DIR proviene da paths.debug_logs_dir(), non da _PROJECT_ROOT."""
    monkeypatch.setenv("AIROUTER_LOGS_DIR", str(tmp_path))
    # Pulisci cache
    import paths
    if hasattr(paths.debug_logs_dir, 'cache_clear'):
        paths.debug_logs_dir.cache_clear()

    # Re-importa router_debug per farlo leggere la var env
    import importlib
    import router_debug
    importlib.reload(router_debug)

    assert router_debug._LOGS_DIR == tmp_path


def test_conftest_isolates_debug_logs(monkeypatch, tmp_path):
    """Sotto conftest (che imposta AIROUTER_LOGS_DIR), debug_logs_dir NON è dentro <repo>/logs."""
    # Questo test simula lo scenario della conftest
    import paths
    import os

    conftest_logs_dir = tmp_path / "test_logs"
    monkeypatch.setenv("AIROUTER_LOGS_DIR", str(conftest_logs_dir))
    if hasattr(paths.debug_logs_dir, 'cache_clear'):
        paths.debug_logs_dir.cache_clear()

    result = paths.debug_logs_dir()
    assert result == conftest_logs_dir
    # Verifica che NON sia dentro REPO/logs
    assert not str(result).startswith(str(REPO / "logs"))
