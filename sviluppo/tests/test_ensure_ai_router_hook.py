"""Test dell'hook SessionStart che tiene vivo il proxy.

I due casi coperti sono quelli che facevano fallire l'avvio in silenzio:
la directory dei log inesistente (lock non apribile) e l'assenza di `ss`
sulla macchina (rilevamento della porta sempre negativo, quindi un secondo
proxy lanciato sopra il primo, che moriva con errno 98).

Il proxy vero non viene mai avviato: `python3` e `ss` sono sostituiti da stub
nel PATH, cosi' il test non occupa porte e non lascia processi in giro.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
HOOK = RADICE / "scripts" / "ensure-ai-router.sh"


def _finto_path(tmp_path: Path) -> Path:
    """Crea una directory con gli stub di `python3` e `ss` e la restituisce.

    Lo stub di python3 intercetta SOLO l'avvio del proxy (scrive un marcatore
    ed esce); ogni altra invocazione — per esempio la risoluzione di
    paths.logs_dir() — passa all'interprete vero.
    Lo stub di `ss` non stampa nulla: simula sia la sua assenza sia le
    installazioni dove non elenca le porte in ascolto.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marcatore = tmp_path / "proxy-avviato"

    stub_py = bin_dir / "python3"
    stub_py.write_text(
        "#!/usr/bin/env bash\n"
        f'case "$*" in\n'
        f'  *ai-router-proxy.py*) echo avviato > "{marcatore}" ; exit 0 ;;\n'
        f'esac\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    stub_py.chmod(0o755)

    stub_ss = bin_dir / "ss"
    stub_ss.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub_ss.chmod(0o755)
    return bin_dir


def _esegui(tmp_path: Path, home_config: Path, porta: int):
    bin_dir = _finto_path(tmp_path)
    ambiente = dict(os.environ)
    ambiente["PATH"] = f"{bin_dir}{os.pathsep}{ambiente.get('PATH', '')}"
    ambiente["AIROUTER_HOME"] = str(home_config)
    ambiente.pop("AIROUTER_LOGS_DIR", None)
    ambiente["AIROUTER_PORT"] = str(porta)
    return subprocess.run(
        ["bash", str(HOOK)], capture_output=True, text=True, timeout=60, env=ambiente
    )


def _attendi(percorso: Path, secondi: float = 10.0) -> bool:
    """Il proxy parte in background (nohup ... &): il marcatore compare poco dopo l'uscita dell'hook."""
    scadenza = time.monotonic() + secondi
    while time.monotonic() < scadenza:
        if percorso.exists():
            return True
        time.sleep(0.05)
    return percorso.exists()


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_hook_eseguibile():
    """Senza il bit di esecuzione SessionStart non lo lancia e il router non parte."""
    assert os.access(HOOK, os.X_OK), f"Atteso: {HOOK} eseguibile, Ottenuto: bit di esecuzione assente"


def test_directory_dei_log_mancante_avvia_comunque_il_proxy(tmp_path):
    """Su una installazione pulita la directory dei log non esiste: l'hook deve crearla e avviare il proxy.

    Prima il lock si apriva a mano su ~/.claude/logs; se la directory non
    c'era, `exec 9>` falliva, `flock -n 9` rispondeva "Bad file descriptor" e
    il `|| exit 0` chiudeva l'hook con codice 0 senza avviare nulla.
    """
    home_config = tmp_path / "config-nuova"
    marcatore = tmp_path / "proxy-avviato"

    risultato = _esegui(tmp_path, home_config, _porta_libera())

    assert risultato.returncode == 0, f"Atteso: codice 0, Ottenuto: {risultato.returncode} ({risultato.stderr.strip()})"
    assert (home_config / "logs").is_dir(), f"Atteso: creata {home_config / 'logs'}, Ottenuto: directory assente"
    assert _attendi(marcatore), f"Atteso: proxy avviato, Ottenuto: nessun avvio (stderr: {risultato.stderr.strip()})"


def test_porta_gia_in_ascolto_non_avvia_un_secondo_proxy(tmp_path):
    """Con un proxy gia' in ascolto l'hook non deve lanciarne un altro, anche senza `ss`.

    Il secondo processo morirebbe subito ("errno 98 ... FATAL: uscita per bind
    fallito sulla porta principale"): il rilevamento ora e' una connessione TCP
    vera, non l'output di `ss`, che su molte macchine non esiste.
    """
    home_config = tmp_path / "config"
    marcatore = tmp_path / "proxy-avviato"

    with socket.socket() as ascoltatore:
        ascoltatore.bind(("127.0.0.1", 0))
        ascoltatore.listen(1)
        porta = ascoltatore.getsockname()[1]
        risultato = _esegui(tmp_path, home_config, porta)

    assert risultato.returncode == 0, f"Atteso: codice 0, Ottenuto: {risultato.returncode}"
    assert not _attendi(marcatore, secondi=2.0), \
        "Atteso: nessun avvio con la porta occupata, Ottenuto: proxy lanciato lo stesso"


@pytest.mark.skipif(sys.platform == "win32", reason="hook bash, non usato su Windows")
def test_sintassi_bash_valida():
    """Un errore di sintassi nell'hook e' un router che non parte a ogni sessione."""
    risultato = subprocess.run(["bash", "-n", str(HOOK)], capture_output=True, text=True, timeout=30)
    assert risultato.returncode == 0, f"Atteso: sintassi valida, Ottenuto: {risultato.stderr.strip()}"
