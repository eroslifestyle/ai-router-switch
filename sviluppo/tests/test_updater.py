"""Test per src/updater.py — aggiornamento con verifica e rollback.

Nessun test esegue git, systemctl, pytest o richieste di rete vere: tutto passa
per monkeypatch. La fixture autouse sostituisce anche `log`, che altrimenti
scriverebbe davvero in logs/update.log a ogni test.
"""
import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import updater  # noqa: E402  — l'import deve seguire l'aggiunta al sys.path


class Esito:
    """Finto risultato di subprocess.run, con i soli campi che updater legge."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Risposta:
    """Finta risposta HTTP usabile come gestore di contesto, come urlopen."""

    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def argomenti(check=False, no_test=False, no_restart=False, yes=True, dry_run=False):
    """Namespace degli argomenti con yes=True come predefinito.

    Il predefinito è deliberato: con yes=False update chiama input(), e sotto
    pytest lo stdin catturato fa sollevare OSError.
    """
    return argparse.Namespace(
        check=check, no_test=no_test, no_restart=no_restart, yes=yes, dry_run=dry_run
    )


@pytest.fixture(autouse=True)
def zittisci_log(monkeypatch):
    """Impedisce ai test di scrivere davvero sul file di log."""
    monkeypatch.setattr(updater, "log", lambda _msg: None)


# ── is_dirty ──────────────────────────────────────────────────────────────────

def test_is_dirty_vero_con_output(monkeypatch):
    """Un working tree con modifiche va riconosciuto come sporco."""
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(stdout=" M file.py\n"))
    assert updater.is_dirty() is True


def test_is_dirty_falso_senza_output(monkeypatch):
    """Un working tree pulito non deve bloccare l'aggiornamento."""
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(stdout=""))
    assert updater.is_dirty() is False


def test_is_dirty_falso_con_solo_spazi(monkeypatch):
    """Righe di solo spazio bianco non sono modifiche."""
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(stdout="  \n\t\n"))
    assert updater.is_dirty() is False


# ── commits_behind ────────────────────────────────────────────────────────────

def test_commits_behind_legge_il_numero(monkeypatch):
    """Il conteggio dei commit arretrati viene letto dall'output di git."""
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(stdout="7\n"))
    assert updater.commits_behind() == 7


def test_commits_behind_zero_se_git_fallisce(monkeypatch):
    """Senza upstream configurato git esce diverso da zero: si assume nessun aggiornamento."""
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(returncode=128, stderr="no upstream"))
    assert updater.commits_behind() == 0


def test_commits_behind_zero_se_output_non_numerico(monkeypatch):
    """Un output inatteso non deve far esplodere l'updater con ValueError."""
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(stdout="boh"))
    assert updater.commits_behind() == 0


# ── health ────────────────────────────────────────────────────────────────────

def test_health_vero_solo_con_200(monkeypatch):
    """Il proxy è sano solo quando risponde 200."""
    monkeypatch.setattr(updater, "urlopen", lambda url, timeout=3: Risposta(200))
    assert updater.health(8787) is True


def test_health_falso_con_500(monkeypatch):
    """Un 500 non è salute: è il caso in cui il rollback deve scattare."""
    monkeypatch.setattr(updater, "urlopen", lambda url, timeout=3: Risposta(500))
    assert updater.health(8787) is False


def test_health_falso_su_errore_di_rete(monkeypatch):
    """Porta chiusa o connessione rifiutata valgono come non sano."""
    def esplode(url, timeout=3):
        raise updater.URLError("connessione rifiutata")

    monkeypatch.setattr(updater, "urlopen", esplode)
    assert updater.health(8787) is False


def test_health_falso_su_eccezione_qualsiasi(monkeypatch):
    """Nessuna eccezione deve propagarsi da un controllo di salute."""
    def esplode(url, timeout=3):
        raise RuntimeError("imprevisto")

    monkeypatch.setattr(updater, "urlopen", esplode)
    assert updater.health(8787) is False


# ── all_ports_ok ──────────────────────────────────────────────────────────────

def test_all_ports_ok_vuota_quando_tutte_rispondono(monkeypatch):
    """Nessuna porta in errore significa lista vuota, non lista delle sane."""
    monkeypatch.setattr(updater, "health", lambda porta, timeout=3: True)
    assert updater.all_ports_ok() == []


def test_all_ports_ok_elenca_solo_le_fallite(monkeypatch):
    """La lista restituita contiene esattamente le porte che non rispondono."""
    mute = {8774, 8779}
    monkeypatch.setattr(updater, "health", lambda porta, timeout=3: porta not in mute)
    assert sorted(updater.all_ports_ok()) == [8774, 8779]


def test_all_ports_ok_interroga_tutte_le_porte(monkeypatch):
    """Il controllo copre l'intero elenco PORTS: una porta dimenticata non verrebbe verificata."""
    viste = []

    def finta(porta, timeout=3):
        viste.append(porta)
        return True

    monkeypatch.setattr(updater, "health", finta)
    updater.all_ports_ok()
    assert viste == updater.PORTS


# ── wait_health ───────────────────────────────────────────────────────────────

def test_wait_health_vero_subito(monkeypatch):
    """Se il proxy è già sano non si aspetta nemmeno un secondo."""
    monkeypatch.setattr(updater, "health", lambda porta, timeout=3: True)
    monkeypatch.setattr(updater.time, "sleep", lambda _s: None)
    assert updater.wait_health(3) is True


def test_wait_health_falso_dopo_i_tentativi(monkeypatch):
    """Esaurita l'attesa senza risposta si dichiara il fallimento, non si aspetta all'infinito."""
    monkeypatch.setattr(updater, "health", lambda porta, timeout=3: False)
    monkeypatch.setattr(updater.time, "sleep", lambda _s: None)
    assert updater.wait_health(3) is False


# ── update: il flusso principale ──────────────────────────────────────────────

@pytest.fixture
def comandi(monkeypatch):
    """Registra i comandi git e neutralizza il resto del flusso.

    Ritorna la lista dei comandi, così ogni test può affermare che pull o reset
    siano stati chiamati o meno.
    """
    visti = []

    def finto_git(*args):
        visti.append(list(args))
        return Esito()

    monkeypatch.setattr(updater, "git", finto_git)
    monkeypatch.setattr(updater, "current_commit", lambda: "abc1234")
    monkeypatch.setattr(updater, "restart_service", lambda: True)
    monkeypatch.setattr(updater, "wait_health", lambda seconds=30: True)
    monkeypatch.setattr(updater, "all_ports_ok", lambda: [])
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito())
    return visti


def _ha(comandi, *prefisso) -> bool:
    """Vero se fra i comandi git registrati ce n'è uno che inizia col prefisso dato."""
    return any(c[: len(prefisso)] == list(prefisso) for c in comandi)


def test_update_repo_sporco_non_tocca_nulla(monkeypatch, comandi):
    """Con modifiche locali si esce subito: nessun pull, nessuno stash silenzioso."""
    monkeypatch.setattr(updater, "is_dirty", lambda: True)
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(stdout=" M src/x.py\n"))
    assert updater.update(argomenti()) == 1
    assert not _ha(comandi, "pull")


def test_update_gia_aggiornato_esce_pulito(monkeypatch, comandi):
    """Zero commit arretrati: si esce con successo senza pull né test."""
    monkeypatch.setattr(updater, "is_dirty", lambda: False)
    monkeypatch.setattr(updater, "commits_behind", lambda: 0)
    assert updater.update(argomenti()) == 0
    assert not _ha(comandi, "pull")


def test_update_check_non_modifica_nulla(monkeypatch, comandi):
    """--check riferisce che ci sono aggiornamenti ma non li applica: è il suo contratto."""
    monkeypatch.setattr(updater, "is_dirty", lambda: False)
    monkeypatch.setattr(updater, "commits_behind", lambda: 5)
    assert updater.update(argomenti(check=True)) == 0
    assert not _ha(comandi, "pull")


def test_update_test_falliti_fanno_rollback(monkeypatch, comandi):
    """Suite rossa dopo il pull: si torna allo sha di partenza e si esce in errore."""
    monkeypatch.setattr(updater, "is_dirty", lambda: False)
    monkeypatch.setattr(updater, "commits_behind", lambda: 2)
    monkeypatch.setattr(updater, "run", lambda *a, **k: Esito(returncode=1, stdout="1 failed"))
    assert updater.update(argomenti()) == 1
    assert _ha(comandi, "reset", "--hard", "abc1234")


def test_update_salute_mai_raggiunta_fa_rollback(monkeypatch, comandi):
    """Se dopo il riavvio il proxy non risponde, l'aggiornamento viene annullato."""
    monkeypatch.setattr(updater, "is_dirty", lambda: False)
    monkeypatch.setattr(updater, "commits_behind", lambda: 1)
    monkeypatch.setattr(updater, "wait_health", lambda seconds=30: False)
    assert updater.update(argomenti(no_test=True)) == 1
    assert _ha(comandi, "reset", "--hard", "abc1234")


def test_update_porta_muta_fa_rollback(monkeypatch, comandi):
    """Una porta che non risponde basta ad annullare: le nove fisse contano quanto la dinamica."""
    monkeypatch.setattr(updater, "is_dirty", lambda: False)
    monkeypatch.setattr(updater, "commits_behind", lambda: 1)
    monkeypatch.setattr(updater, "all_ports_ok", lambda: [8776])
    assert updater.update(argomenti(no_test=True)) == 1
    assert _ha(comandi, "reset", "--hard", "abc1234")


def test_update_percorso_felice_non_fa_reset(monkeypatch, comandi):
    """Pull, test, salute e porte a posto: si esce con 0 e non si tocca il reset."""
    monkeypatch.setattr(updater, "is_dirty", lambda: False)
    monkeypatch.setattr(updater, "commits_behind", lambda: 3)
    assert updater.update(argomenti()) == 0
    assert _ha(comandi, "pull", "--ff-only")
    assert not _ha(comandi, "reset")


# ── coerenza fra le tre liste di porte ────────────────────────────────────────

def test_le_tre_liste_di_porte_non_divergono():
    """L'elenco delle porte è ripetuto in tre posti indipendenti: se uno cambia
    senza gli altri, il difetto è silenzioso e si manifesta solo in produzione.

    - `router_constants` è la fonte di verità: `PORT_MODE` mappa porta → modalità
      e `LISTEN_PORTS` è ciò che il proxy apre davvero;
    - `updater.PORTS` decide quali porte il controllo di salute interroga dopo un
      aggiornamento: una porta assente non verrebbe mai verificata;
    - `install.py` genera la `ExecStartPre` della unit systemd, che libera le porte
      prima dell'avvio: una porta assente resta occupata da un processo orfano e
      il riavvio fallisce solo su quella.

    Il caso reale che ha motivato questo test: il 2026-08-07 la unit installata
    elencava nove porte su dieci, senza la 8779 di `local`, aggiunta dopo che la
    unit era stata generata.
    """
    import importlib.util
    import pathlib
    import sys

    radice = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(radice / "src"))
    from router_constants import LISTEN_PORTS, PORT_MODE, VALID_MODES

    spec = importlib.util.spec_from_file_location("install_mod", radice / "install.py")
    install_mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(install_mod)
    except SystemExit:
        pass  # install.py può uscire se invocato senza argomenti: le costanti bastano

    attese = sorted(LISTEN_PORTS)
    assert sorted(updater.PORTS) == attese, (
        f"updater.PORTS {sorted(updater.PORTS)} diverge da LISTEN_PORTS {attese}"
    )
    assert sorted(install_mod.FIXED_PORTS + [install_mod.DYNAMIC_PORT]) == attese, (
        "install.py genererebbe una ExecStartPre che non copre tutte le porte aperte"
    )
    # E ogni modalità dichiarata deve avere la sua porta fissa, altrimenti esiste
    # una modalità raggiungibile solo dalla porta dinamica 8787.
    assert sorted(PORT_MODE.values()) == sorted(VALID_MODES), (
        f"PORT_MODE copre {sorted(PORT_MODE.values())}, VALID_MODES è {sorted(VALID_MODES)}"
    )
