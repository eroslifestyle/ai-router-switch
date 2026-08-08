"""Pulizia degli import inutilizzati prima del gate ruff (audit D3).

Perche' serve: la storia dei tre ticket di self-fix diceva che due tentativi su
tre erano falliti per la stessa causa, e non per sfortuna:

    failed:gateD: ruff fallito: F401 MINIMAX_ORCHESTRATOR_MODEL imported but unused
    failed:gateD: ruff fallito: F401 pathlib.Path imported but unused

Il generatore di patch lascia sistematicamente import orfani quando rimuove il
codice che li usava. La pulizia mirata a F401 sui soli file toccati rende
quelle patch accettabili senza allentare il gate.

I test lavorano su un albero temporaneo: non toccano mai il repo.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from self_healing.fixer import (  # noqa: E402
    _dettaglio_eccezione,
    _pulisci_import_inutilizzati,
)

RUFF_ASSENTE = shutil.which("ruff") is None

FILE_CON_IMPORT_ORFANO = '''\
import json
import pathlib


def somma(a, b):
    """L'import di pathlib e' rimasto orfano: lo usava il codice rimosso."""
    return json.dumps({"r": a + b})
'''

FILE_PULITO = '''\
import json


def somma(a, b):
    return json.dumps({"r": a + b})
'''


def _lint(percorso, cwd):
    """Esegue `ruff check` come farebbe il gate D e ritorna il returncode."""
    return subprocess.run(
        ["ruff", "check", percorso],
        cwd=cwd, capture_output=True, text=True,
    ).returncode


@pytest.mark.skipif(RUFF_ASSENTE, reason="ruff non installato")
class TestPuliziaF401:

    def test_il_gate_respingerebbe_la_patch_sporca(self, tmp_path):
        """Braccio di controllo: senza pulizia il lint fallisce davvero.

        Senza questo, un test che verifica solo il dopo passerebbe anche se la
        pulizia non servisse a nulla.
        """
        f = tmp_path / "modulo.py"
        f.write_text(FILE_CON_IMPORT_ORFANO)
        assert _lint("modulo.py", tmp_path) != 0

    def test_dopo_la_pulizia_il_gate_passa(self, tmp_path):
        f = tmp_path / "modulo.py"
        f.write_text(FILE_CON_IMPORT_ORFANO)

        _pulisci_import_inutilizzati(tmp_path, ["modulo.py"])

        assert _lint("modulo.py", tmp_path) == 0
        assert "import pathlib" not in f.read_text()
        # L'import davvero usato resta: la pulizia non e' un `--fix` cieco.
        assert "import json" in f.read_text()

    def test_file_gia_pulito_non_cambia(self, tmp_path):
        f = tmp_path / "modulo.py"
        f.write_text(FILE_PULITO)
        prima = f.read_text()

        _pulisci_import_inutilizzati(tmp_path, ["modulo.py"])

        assert f.read_text() == prima

    def test_tocca_solo_i_file_indicati(self, tmp_path):
        """Un file sporco NON elencato deve restare intatto.

        E' il vincolo che impedisce di rifare il danno gia' capitato una volta
        in questo repo con un `ruff --fix` sull'albero intero.
        """
        toccato = tmp_path / "toccato.py"
        toccato.write_text(FILE_CON_IMPORT_ORFANO)
        estraneo = tmp_path / "estraneo.py"
        estraneo.write_text(FILE_CON_IMPORT_ORFANO)

        _pulisci_import_inutilizzati(tmp_path, ["toccato.py"])

        assert "import pathlib" not in toccato.read_text()
        assert "import pathlib" in estraneo.read_text()

    def test_non_corregge_regole_diverse_da_f401(self, tmp_path):
        """Solo F401. Un `--fix` largo ha gia' svuotato un blocco try qui."""
        f = tmp_path / "modulo.py"
        f.write_text("import json\nx = 1;\ndef f():\n    return json.dumps(x)\n")
        prima = f.read_text()

        _pulisci_import_inutilizzati(tmp_path, ["modulo.py"])

        # Il punto e virgola superfluo e altre regole di stile restano.
        assert f.read_text() == prima

    def test_lista_vuota_non_rompe(self, tmp_path):
        _pulisci_import_inutilizzati(tmp_path, [])

    def test_file_inesistente_non_solleva(self, tmp_path):
        """Il gate D filtra i file cancellati, ma la funzione regge lo stesso."""
        _pulisci_import_inutilizzati(tmp_path, ["non_esiste.py"])


class TestDettaglioEccezione:
    """D3: 'failed:exception-CalledProcessError' non diceva nulla di utile."""

    def test_estrae_comando_codice_e_stderr(self):
        exc = subprocess.CalledProcessError(
            returncode=128, cmd=["git", "worktree", "add", "x"],
            stderr="fatal: 'x' already exists",
        )
        d = _dettaglio_eccezione(exc)
        assert "git worktree add x" in d
        assert "rc=128" in d
        assert "already exists" in d

    def test_regge_stderr_in_byte(self):
        exc = subprocess.CalledProcessError(
            returncode=1, cmd="ruff check", stderr=b"boom binario",
        )
        assert "boom binario" in _dettaglio_eccezione(exc)

    def test_eccezione_generica_resta_leggibile(self):
        d = _dettaglio_eccezione(ValueError("valore non valido"))
        assert "ValueError" in d and "valore non valido" in d

    def test_stderr_lungo_viene_troncato(self):
        exc = subprocess.CalledProcessError(
            returncode=1, cmd="x", stderr="A" * 5000,
        )
        d = _dettaglio_eccezione(exc)
        assert len(d) < 1000, "il dettaglio finisce nel log, non deve esplodere"


class TestStatoDisattivazione:
    """D1: il log ripeteva la stessa riga quattro volte al giorno.

    Il fixer resta SPENTO — un fixer che modifica codice senza supervisione non
    si accende in una sessione autonoma — ma il suo stato deve essere leggibile.
    """

    def _stato(self, tmp_path, monkeypatch, righe_log, ticket=()):
        from self_healing import fixer
        log = tmp_path / "self-fix.log"
        log.write_text("\n".join(righe_log) + "\n", encoding="utf-8")
        stato_dir = tmp_path / "self-fix"
        stato_dir.mkdir()
        for nome in ticket:
            (stato_dir / nome).write_text("{}", encoding="utf-8")
        monkeypatch.setattr(fixer, "LOG_FILE", log)
        monkeypatch.setattr(fixer, "STATE_DIR", stato_dir)
        return fixer._stato_disattivazione()

    def test_riporta_da_quando_e_spento(self, tmp_path, monkeypatch):
        s = self._stato(tmp_path, monkeypatch, [
            "[fixer] 2026-08-06 19:23:38 Self-fixer disattivato (X)",
            "[fixer] 2026-08-07 03:18:40 Self-fixer disattivato (X)",
        ])
        assert "2026-08-06 19:23:38" in s

    def test_una_esecuzione_riuscita_azzera_il_conteggio(self, tmp_path, monkeypatch):
        """Conta la sequenza ININTERROTTA in fondo, non la prima riga del file."""
        s = self._stato(tmp_path, monkeypatch, [
            "[fixer] 2026-08-01 10:00:00 Self-fixer disattivato (X)",
            "[fixer] 2026-08-05 10:00:00 Ticket elaborato",
            "[fixer] 2026-08-07 03:18:40 Self-fixer disattivato (X)",
        ])
        assert "2026-08-07 03:18:40" in s
        assert "2026-08-01" not in s

    def test_conta_i_ticket_in_coda(self, tmp_path, monkeypatch):
        s = self._stato(tmp_path, monkeypatch,
                        ["[fixer] 2026-08-06 19:23:38 Self-fixer disattivato (X)"],
                        ticket=("relay_error_502-8d1ee04d.json",))
        assert "1 ticket in coda" in s
        assert "relay_error_502-8d1ee04d.json" in s

    def test_coda_vuota(self, tmp_path, monkeypatch):
        s = self._stato(tmp_path, monkeypatch,
                        ["[fixer] 2026-08-06 19:23:38 Self-fixer disattivato (X)"])
        assert "0 ticket in coda" in s

    def test_rimanda_alla_decisione(self, tmp_path, monkeypatch):
        """Lo stato deve dire cosa fare, non solo cosa succede."""
        s = self._stato(tmp_path, monkeypatch,
                        ["[fixer] 2026-08-06 19:23:38 Self-fixer disattivato (X)"])
        assert "TODO" in s

    def test_log_assente_non_solleva(self, tmp_path, monkeypatch):
        from self_healing import fixer
        monkeypatch.setattr(fixer, "LOG_FILE", tmp_path / "non-esiste.log")
        monkeypatch.setattr(fixer, "STATE_DIR", tmp_path / "nemmeno")
        assert isinstance(fixer._stato_disattivazione(), str)
