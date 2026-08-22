from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

ROOT: Path = Path(__file__).resolve().parent.parent
LOG_FILE: Path = ROOT / "logs" / "update.log"
PORTS: list[int] = [8787, 8771, 8772, 8773, 8774, 8775, 8776, 8777, 8778, 8779, 8781, 8788, 8784, 8785, 8786]
SERVICE_NAME: str = "ai-router"


def log(msg: str) -> None:
    """Scrive su stdout e in append sul file di log con marcatura temporale."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Esegue subprocess.run con capture_output e text, mai shell=True."""
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Scorciatoia che chiama run con git davanti e cwd sulla root."""
    return run(["git"] + list(args), cwd=ROOT)


def is_dirty() -> bool:
    """Ritorna True se git status --porcelain ha output (repo sporco)."""
    result = run(["git", "status", "--porcelain"], cwd=ROOT)
    return bool(result.stdout.strip())


def current_commit() -> str:
    """Ritorna lo sha di HEAD."""
    result = run(["git", "rev-parse", "HEAD"], cwd=ROOT)
    return result.stdout.strip()


def commits_behind() -> int:
    """Conta i commit tra HEAD e @{u}; ritorna 0 se fallisce."""
    result = run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=ROOT)
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def service_available() -> bool:
    """Ritorna True se systemctl conosce ai-router.service."""
    result = run(["which", "systemctl"])
    if result.returncode != 0:
        return False
    check = run(["systemctl", "--user", "list-unit-files", "--no-legend"])
    return f"{SERVICE_NAME}.service" in check.stdout


def restart_service() -> bool:
    """Riavvia il servizio se disponibile, altrimenti avvisa e ritorna False."""
    if not service_available():
        log("Servizio systemd non disponibile, riavvio saltato.")
        return False
    result = run(["systemctl", "--user", "restart", SERVICE_NAME])
    if result.returncode == 0:
        log("Servizio riavviato.")
        return True
    log(f"Errore riavvio servizio: {result.stderr.strip()}")
    return False


def health(port: int, timeout: float = 3) -> bool:
    """Interroga /__router_health e ritorna True solo su codice 200."""
    url = f"http://127.0.0.1:{port}/__router_health"
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except URLError:
        return False
    except Exception:
        return False


def wait_health(seconds: int = 30) -> bool:
    """Attende che la porta 8787 risponda per secondi secondi."""
    for i in range(seconds):
        if health(8787):
            return True
        time.sleep(1)
    return False


def all_ports_ok() -> list[int]:
    """Ritorna la lista delle porte che NON rispondono."""
    return [p for p in PORTS if not health(p)]


def rollback(commit: str, motivo: str) -> None:
    """Esegue git reset --hard, riavvia, riattende salute, stampa avviso."""
    log(f"ROLLBACK: {motivo}")
    print(f"Aggiornamento annullato. Motivo: {motivo}")
    result = git("reset", "--hard", commit)
    if result.returncode != 0:
        err = result.stderr.strip()
        log(f"Errore grave reset: {err}")
        print("ERRORE GRAVE: reset fallito. Eseguire manualmente:")
        print(f"  git reset --hard {commit}")
        return
    log(f"Reset a {commit} completato.")
    restart_service()
    if wait_health(30):
        log("Salute ripristinata dopo rollback.")
        print("Rollback completato con successo.")
    else:
        log("Salute NON ripristinata dopo rollback.")
        print("ATTENZIONE: rollback eseguito ma salute non ripristinata.")


def update(args: argparse.Namespace) -> int:
    """Flusso principale di aggiornamento con fetch, pull, test e rollback."""
    dry = args.dry_run

    if is_dirty():
        log("Repository sporco, aggiornamento rifiutato.")
        result = run(["git", "status", "--porcelain"], cwd=ROOT)
        print("File modificati non tracciati:")
        print(result.stdout)
        print("Non eseguo stash automatici. Ritorno.")
        return 1

    prev = current_commit()
    log(f"Commit attuale: {prev}")

    if dry:
        print(" Eseguirei: git fetch --quiet")

    if not dry:
        fet = git("fetch", "--quiet")
        if fet.returncode != 0:
            log(f"Fetch fallito: {fet.stderr.strip()}")
            return 1

    n = commits_behind()
    if n == 0:
        log("Già aggiornato, nessun commit da applicare.")
        print("Il repository è già aggiornato.")
        return 0

    print(f"Ci sono {n} commit da applicare.")

    if args.check:
        print(f"Aggiornamento disponibile: {n} commit.")
        return 0

    if not args.yes:
        print(f"Applicare {n} commit? (y/N)")
        try:
            risp = input().strip().lower()
        except EOFError:
            risp = ""
        if risp not in ("y", "yes"):
            print("Aggiornamento annullato dall'utente.")
            return 0

    if dry:
        print(" Eseguirei: git pull --ff-only")

    if not dry:
        pull = git("pull", "--ff-only")
        if pull.returncode != 0:
            log("Pull fallito: storia divergente?")
            print("Pull fallito: la storia è divergente.")
            print("Intervento manuale richiesto.")
            return 1

    new = current_commit()
    log(f"Nuovo commit: {new}")

    if not args.no_test:
        if dry:
            print(" Eseguirei: python3 -m pytest -q (timeout 900s)")
        else:
            log("Esecuzione test in corso...")
            test = run(
                ["python3", "-m", "pytest", "-q"],
                cwd=ROOT,
                timeout=900,
            )
            if test.returncode != 0:
                log("Test falliti, rollback in corso.")
                print("Test falliti!")
                print(test.stdout)
                rollback(prev, "Test falliti")
                return 1
            log("Test superati.")

    if not args.no_restart:
        if dry:
            print(f" Eseguirei: systemctl --user restart {SERVICE_NAME}")
        else:
            restart_service()

    if not dry:
        if not wait_health(30):
            log("Salute non disponibile dopo riavvio, rollback.")
            rollback(prev, "Salute non ripristinata")
            return 1

    ko = all_ports_ok()
    if ko:
        log(f"Porte non raggiungibili: {ko}")
        print(f"Porte non raggiungibili: {ko}")
        rollback(prev, f"Porte non raggiungibili: {ko}")
        return 1

    print(f"Vecchio commit: {prev}")
    print(f"Nuovo commit:   {new}")
    applied = n
    print(f"Commit applicati: {applied}")
    log(f"Aggiornamento completato: {prev} -> {new} ({applied} commit)")
    return 0


def main() -> int:
    """Parsa argomenti e avvia l'aggiornamento."""
    parser = argparse.ArgumentParser(
        description="Aggiornamento di ai-router-switch da git.",
        prog="ai-mode update",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Mostra quanti aggiornamenti ci sono senza applicarli",
    )
    parser.add_argument(
        "--no-test",
        action="store_true",
        help="Salta lesecuzione dei test",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Salta il riavvio del servizio",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Conferma automatica senza chiedere",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stampa cosa farebbe senza eseguirlo",
    )
    args = parser.parse_args()
    log("Aggiornamento avviato.")
    code = update(args)
    log(f"Terminato con codice: {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
