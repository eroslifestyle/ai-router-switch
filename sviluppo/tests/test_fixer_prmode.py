import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from self_healing import fixer  # noqa: E402

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def noop_log():
    """Sostituisce fixer._log con noop per evitare scrittura su file."""
    with patch.object(fixer, "_log", autospec=True):
        yield


class TestGate:
    """Test che il gate AIROUTER_SELF_FIX_ENABLED controlli l'esecuzione."""

    def test_no_env_main_returns_0_process_not_called(self, monkeypatch):
        """Senza la variabile, main() esce 0 e process_ticket non e' chiamato."""
        called = []
        monkeypatch.delenv("AIROUTER_SELF_FIX_ENABLED", raising=False)
        monkeypatch.setattr(
            fixer, "process_ticket", lambda *a, **kw: (called.append(1) or 0)
        )
        monkeypatch.setattr(sys, "argv", ["fixer"])
        assert fixer.main() == 0
        assert not called

    def test_env_0_main_returns_0_process_not_called(self, monkeypatch):
        """Con la variabile a '0', main() esce 0 e process_ticket non e' chiamato."""
        called = []
        monkeypatch.setenv("AIROUTER_SELF_FIX_ENABLED", "0")
        monkeypatch.setattr(
            fixer, "process_ticket", lambda *a, **kw: (called.append(1) or 0)
        )
        monkeypatch.setattr(sys, "argv", ["fixer"])
        assert fixer.main() == 0
        assert not called

    def test_env_1_no_args_returns_1_proves_gate_first(self, monkeypatch):
        """Con gate attivo e senza argomenti, main() esce 1 (aiuto), provando
        che il gate e' valutato prima del controllo argomenti."""
        monkeypatch.setenv("AIROUTER_SELF_FIX_ENABLED", "1")
        monkeypatch.setattr(sys, "argv", ["fixer"])
        assert fixer.main() == 1


class TestOpenPullRequest:
    """Test che open_pull_request non sollevi mai eccezioni."""

    def test_gh_not_found_no_subprocess(self, monkeypatch):
        """Quando shutil.which ritorna None, nessun sottoprocesso e' eseguito."""
        calls = []
        monkeypatch.setattr(fixer.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(
            fixer.subprocess, "run", lambda *a, **kw: (calls.append(1) or MagicMock())
        )
        fixer.open_pull_request("feature-x", {"id": "t1", "kind": "relay_error_502", "count": 12})
        assert calls == []

    def test_subprocess_raises_no_propagation(self, monkeypatch):
        """Quando sottoprocess.run solleva, open_pull_request non propaga."""
        monkeypatch.setattr(fixer.shutil, "which", lambda cmd: "/usr/bin/gh")
        monkeypatch.setattr(
            fixer.subprocess,
            "run",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        fixer.open_pull_request("feature-x", {"id": "t1", "kind": "relay_error_502", "count": 12})
