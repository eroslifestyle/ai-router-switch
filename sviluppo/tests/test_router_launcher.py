import os
import subprocess
from pathlib import Path


RADICE = Path(__file__).resolve().parents[2]
LAUNCHER = RADICE / "scripts" / "router"
MODALITA_ATTESE = ("anthropic", "minimax", "mix-am", "mix-ag", "mix-gm", "glm", "qwen", "mix-al", "local")


def _esegui(argomenti, session_id=None):
    """Esegue il launcher con un ambiente controllato e restituisce il CompletedProcess."""
    ambiente = dict(os.environ)
    ambiente.pop("CLAUDE_CODE_SESSION_ID", None)
    if session_id is not None:
        ambiente["CLAUDE_CODE_SESSION_ID"] = session_id
    return subprocess.run([str(LAUNCHER), *argomenti], capture_output=True, text=True, timeout=30, env=ambiente)


def test_launcher_esiste_ed_e_eseguibile():
    """Senza il bit di esecuzione il comando non e invocabile e il modulo tornerebbe irraggiungibile."""
    esiste = LAUNCHER.exists()
    eseguibile = os.access(LAUNCHER, os.X_OK)
    assert esiste, f"Atteso: {LAUNCHER} esiste, Ottenuto: non trovato"
    assert eseguibile, f"Atteso: {LAUNCHER} eseguibile, Ottenuto: bit di esecuzione assente"


def test_senza_argomenti_elenca_tutte_le_modalita():
    """Elenca tutte le modalita supportate quando invocato senza argomenti."""
    risultato = _esegui([])
    for modo in MODALITA_ATTESE:
        assert modo in risultato.stdout, f"Atteso: '{modo}' presente nell'output, Ottenuto: modalita assente"
    assert risultato.returncode == 0, f"Atteso: codice 0, Ottenuto: {risultato.returncode}"


def test_senza_session_id_non_cambia_nulla_e_suggerisce_ai_mode():
    """Senza sessione identificabile il comando deve rifiutarsi, non ricadere sul cambio globale, che sposterebbe tutte le chat."""
    risultato = _esegui(["minimax"])
    assert risultato.returncode == 1, f"Atteso: codice 1, Ottenuto: {risultato.returncode}"
    assert "ai-mode" in risultato.stdout, f"Atteso: suggerimento 'ai-mode', Ottenuto: '{risultato.stdout.strip()}'"


def test_argomento_non_valido_risponde_con_l_help():
    # Questo test usa un argomento inventato e non modifica mai una sessione reale.
    # Serve solo per verificare che il launcher riconosce input invalidi e mostra l'help.
    risultato = _esegui(["modalita-inesistente-xyz"], session_id="test-suite-router-cli")
    assert risultato.returncode != 0, f"Atteso: codice non 0, Ottenuto: {risultato.returncode}"
    assert "!router" in risultato.stdout, f"Atteso: help contiene '!router', Ottenuto: '{risultato.stdout.strip()}'"


def test_funziona_anche_invocato_via_symlink(tmp_path):
    """Il launcher risolve la radice del repository seguendo i symlink, quindi deve funzionare anche installato altrove, per esempio in una directory del PATH."""
    symlink = tmp_path / "router-link"
    symlink.symlink_to(LAUNCHER)
    ambiente = dict(os.environ)
    ambiente.pop("CLAUDE_CODE_SESSION_ID", None)
    risultato = subprocess.run([str(symlink)], capture_output=True, text=True, timeout=30, env=ambiente)
    assert risultato.returncode == 0, f"Atteso: codice 0, Ottenuto: {risultato.returncode}"
    assert "anthropic" in risultato.stdout, f"Atteso: 'anthropic' nell'output, Ottenuto: '{risultato.stdout.strip()}'"
