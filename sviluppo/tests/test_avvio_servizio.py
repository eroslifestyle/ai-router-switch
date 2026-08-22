"""Test del percorso "avvia il router": pannello GUI e unit systemd.

Coprono i due difetti per cui il router restava OFFLINE senza spiegazioni:
il pulsante Start che non poteva ripartire da una unit in stato failed e non
mostrava l'errore, e l'ExecStartPre che non liberava nessuna porta perche'
systemd svuotava la variabile di shell prima di passare il comando a sh.
"""
import importlib.util
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
CARD = RADICE / "router-mode" / "card.py"
UNIT = RADICE / "systemd" / "ai-router.service.in"


# ── card.py: la GUI importa PySide6, qui basta uno scheletro ──────────────────
def _stub_pyside():
    """Registra moduli PySide6 fittizi: le classi servono solo come basi."""
    nomi = {
        "PySide6.QtCore": ["Qt", "QTimer", "QPoint"],
        "PySide6.QtGui": ["QColor", "QFont", "QIcon"],
        "PySide6.QtWidgets": [
            "QApplication", "QWidget", "QVBoxLayout", "QHBoxLayout",
            "QLabel", "QPushButton", "QGridLayout", "QGraphicsDropShadowEffect",
        ],
    }
    sys.modules.setdefault("PySide6", types.ModuleType("PySide6"))
    for modulo, classi in nomi.items():
        m = types.ModuleType(modulo)
        for c in classi:
            setattr(m, c, type(c, (object,), {}))
        sys.modules[modulo] = m


@pytest.fixture(scope="module")
def card():
    _stub_pyside()
    spec = importlib.util.spec_from_file_location("card_gui", CARD)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _finto_systemctl(tmp_path, esito=0, stderr="", registro=None):
    """Mette un `systemctl` fittizio in testa al PATH e restituisce il registro delle chiamate."""
    registro = registro or (tmp_path / "chiamate.txt")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    finto = bin_dir / "systemctl"
    finto.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{registro}"\n'
        f'[ -n "{stderr}" ] && echo "{stderr}" >&2\n'
        f"exit {esito}\n"
    )
    finto.chmod(0o755)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return registro


@pytest.fixture
def path_isolato(monkeypatch):
    monkeypatch.setenv("PATH", os.environ["PATH"])


def test_start_passa_prima_da_reset_failed(card, tmp_path, path_isolato):
    """Dopo StartLimitBurst la unit resta failed e `start` non fa nulla: serve reset-failed."""
    registro = _finto_systemctl(tmp_path)

    riuscito, messaggio = card.run_systemctl("start")

    chiamate = [r.strip() for r in registro.read_text().splitlines()]
    assert riuscito, f"Atteso: comando riuscito, Ottenuto: fallito ({messaggio})"
    assert chiamate == ["--user reset-failed ai-router", "--user start ai-router"], \
        f"Atteso: reset-failed poi start, Ottenuto: {chiamate}"


def test_stop_non_azzera_lo_stato_di_errore(card, tmp_path, path_isolato):
    """Solo start e restart hanno bisogno del reset: uno stop non deve nascondere un errore precedente."""
    registro = _finto_systemctl(tmp_path)

    card.run_systemctl("stop")

    chiamate = [r.strip() for r in registro.read_text().splitlines()]
    assert chiamate == ["--user stop ai-router"], f"Atteso: solo stop, Ottenuto: {chiamate}"


def test_errore_di_systemctl_viene_riportato(card, tmp_path, path_isolato):
    """Prima l'errore finiva in capture_output e il pannello restava OFFLINE senza motivo."""
    _finto_systemctl(tmp_path, esito=1, stderr="Job for ai-router.service failed")

    riuscito, messaggio = card.run_systemctl("start")

    assert not riuscito, "Atteso: fallimento riportato, Ottenuto: esito positivo"
    assert "ai-router.service failed" in messaggio, \
        f"Atteso: il messaggio di systemctl, Ottenuto: '{messaggio}'"


def test_systemctl_assente_non_solleva(card, tmp_path, monkeypatch):
    """Su una macchina senza systemd il pannello deve dirlo, non morire."""
    vuota = tmp_path / "vuota"
    vuota.mkdir()
    monkeypatch.setenv("PATH", str(vuota))

    riuscito, messaggio = card.run_systemctl("start")

    assert not riuscito, "Atteso: fallimento, Ottenuto: esito positivo"
    assert "systemctl" in messaggio, f"Atteso: messaggio su systemctl, Ottenuto: '{messaggio}'"


# ── unit systemd ──────────────────────────────────────────────────────────────
def _righe_exec():
    return [r for r in UNIT.read_text().splitlines() if r.startswith("Exec")]


def test_nessuna_variabile_di_shell_nelle_righe_exec():
    """systemd espande $VAR nelle righe Exec prima di sh: una variabile di shell diventa stringa vuota.

    Era il caso di `for p in ...; do fuser -k -n tcp $p; done`: il comando
    eseguito era `fuser -k -n tcp` senza porte, quindi nessun processo orfano
    veniva ucciso e il proxy nuovo moriva sul bind della porta principale.
    """
    for riga in _righe_exec():
        assert "$" not in riga, f"Atteso: nessun '$' nelle righe Exec, Ottenuto: {riga}"


def test_lo_step_di_prestart_libera_davvero_le_porte():
    """L'ExecStartPre deve nominare le porte, altrimenti non uccide niente."""
    prestart = [r for r in _righe_exec() if r.startswith("ExecStartPre")]
    assert prestart, "Atteso: un ExecStartPre che libera le porte, Ottenuto: nessuno"
    assert any("@PORTS@" in r and "fuser" in r for r in prestart), \
        f"Atteso: fuser con l'elenco delle porte, Ottenuto: {prestart}"


def test_le_porte_generate_coprono_quelle_su_cui_il_router_ascolta():
    """Se l'elenco di install.py e la mappa del router divergono, una porta orfana resta viva e blocca l'avvio."""
    sys.path.insert(0, str(RADICE / "src"))
    import router_constants

    spec = importlib.util.spec_from_file_location("install_script", RADICE / "install.py")
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)

    attese = set(router_constants.LISTEN_PORTS)
    generate = {int(p) for p in install.PORTS_STR.split()}
    assert attese <= generate, f"Atteso: tutte le porte in ascolto liberate, Ottenuto: mancano {sorted(attese - generate)}"


def test_la_unit_generata_non_contiene_segnaposto():
    """Un @SEGNAPOSTO@ rimasto nella unit e' un servizio che non parte."""
    spec = importlib.util.spec_from_file_location("install_script", RADICE / "install.py")
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)

    generata = install._write_systemd_service(RADICE, RADICE / "wrapper.sh", Path.home() / ".claude")

    residui = re.findall(r"@[A-Z_]+@", generata)
    assert not residui, f"Atteso: nessun segnaposto, Ottenuto: {residui}"
