#!/usr/bin/env python3
"""Script di installazione per ai-router-switch."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Import paths module from src/
sys.path.insert(0, str(Path(__file__).parent / "src"))
from paths import config_home, ensure_dir

PYTHON_MIN = (3, 10)
REQUIRED = ["aiohttp", "brotli", "multidict", "PIL"]
# Elenco esplicito, non un range: 8780 e' saltata perche' occupata da un altro
# servizio (chatterbox-gpu su 0.0.0.0:8780) e mix-am-2 usa la 8781. Un range
# nasconderebbe il salto e reintrodurrebbe il conflitto al primo refactor.
FIXED_PORTS = [8771, 8772, 8773, 8774, 8775, 8776, 8777, 8778, 8779, 8781]
DYNAMIC_PORT = 8787
PORTS_STR = " ".join(map(str, FIXED_PORTS + [DYNAMIC_PORT]))


def print_step(msg: str) -> None:
    print(f"\n[STEP] {msg}")


def check_python() -> bool:
    """Verifica che Python sia >= 3.10."""
    if sys.version_info < PYTHON_MIN:
        print(f"[ERRORE] Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ richiesto, "
              f"trovato {sys.version_info.major}.{sys.version_info.minor}")
        return False
    print(f"[OK] Python {sys.version_info.major}.{sys.version_info.minor}."
          f"{sys.version_info.micro}")
    return True


def check_dependencies(args: argparse.Namespace) -> None:
    """Verifica e installa le dipendenze mancanti."""
    missing = []
    for mod in REQUIRED:
        try:
            # Nessun .lower(): il modulo di Pillow si importa come "PIL", con le
            # maiuscole; abbassarlo faceva fallire l'import e dichiarare mancante
            # una dipendenza presente.
            __import__(mod)
        except ImportError:
            missing.append(mod)

    if not missing:
        print("[OK] Tutte le dipendenze sono presenti")
        return

    print(f"[INFO] Dipendenze mancanti: {', '.join(missing)}")
    cmd = "python3 -m pip install -r requirements.txt"
    print(f"[INFO] Eseguire: {cmd}")

    if not missing:
        return

    if not args.yes:
        risp = input("Installare ora? [y/N] ")
        if risp.lower() != "y":
            print("[SKIP] Installazione dipendenze saltata")
            return

    if args.dry_run:
        print(f"[DRY] Installerei con: {cmd}")
        return

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r",
             str(Path(__file__).parent / "requirements.txt")],
            check=True
        )
        print("[OK] Dipendenze installate")
    except subprocess.CalledProcessError as e:
        print(f"[WARN] Installazione fallita: {e}")


def create_directories(dry_run: bool) -> dict:
    """Crea le directory necessarie nella config home."""
    base = config_home()
    dirs = {
        "config": base,
        "logs": base / "logs",
        "secrets": base / "secrets",
        "state": base / "state",
    }

    for name, path in dirs.items():
        if dry_run:
            print(f"[DRY] Creerei: {path}")
        else:
            ensure_dir(path)
            print(f"[OK] {name}: {path}")

    return dirs


def setup_env_file(dry_run: bool) -> Path:
    """Copia .env.example se .env non esiste."""
    base = config_home()
    env_file = base / ".env"
    example = Path(__file__).parent / ".env.example"

    if env_file.exists():
        print(f"[INFO] .env gia presente, non sovrascritto: {env_file}")
    elif example.exists():
        if dry_run:
            print(f"[DRY] Copierei {example} -> {env_file}")
        else:
            shutil.copy2(example, env_file)
            print(f"[OK] Creato {env_file} (da compilare con le chiavi)")
    else:
        print(f"[WARN] .env.example non trovato: {example}")

    return env_file


def _write_systemd_service(repo_root: Path, wrapper: Path,
                           cfg_home: Path) -> str:
    """Genera il service systemd dal template."""
    tmpl = Path(__file__).parent / "systemd" / "ai-router.service.in"
    if tmpl.exists():
        return (tmpl.read_text()
                .replace("@PROJECT_ROOT@", str(repo_root))
                .replace("@WRAPPER@", str(wrapper))
                .replace("@CONFIG_HOME@", str(cfg_home))
                .replace("@PORTS@", PORTS_STR))
    # Fallback minimo se manca il template. Allineato alla unit vera nei due
    # punti che contano: cwd su src/ e Restart=always.
    return f"""[Unit]
Description=AI Router Proxy
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={wrapper}
WorkingDirectory={repo_root}/src
EnvironmentFile=-{cfg_home}/.env
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
"""


def setup_linux_service(args: argparse.Namespace) -> None:
    """Configura il servizio systemd su Linux."""
    base = config_home()
    script_dir = base / "scripts"
    wrapper = script_dir / "ai-router-proxy-wrapper.sh"
    svc_path = Path.home() / ".config" / "systemd" / "user" / "ai-router.service"
    repo_root = Path(__file__).parent.absolute()
    proxy = repo_root / "src" / "ai-router-proxy.py"

    print_step("Registrazione servizio systemd (Linux)")

    if args.dry_run:
        print(f"[DRY] Creerei wrapper: {wrapper}")
        print(f"[DRY] Creerei service: {svc_path}")
        return

    # Wrapper bash
    ensure_dir(script_dir)
    wrapper.write_text(
        f"#!/bin/bash\n"
        f"export PYTHONPATH=\"{repo_root}\"\n"
        # cwd su src/: alcuni moduli si importano per nome piatto e il proxy
        # gira con WorkingDirectory=src. La versione precedente entrava in
        # ~/.config/ai-router, che non esiste: il wrapper moriva subito.
        f"cd \"{repo_root}/src\"\n"
        f"exec {sys.executable} -u \"{proxy}\" \"$@\"\n"
    )
    os.chmod(wrapper, 0o755)
    print(f"[OK] Wrapper: {wrapper}")

    # Service file
    ensure_dir(svc_path.parent)
    svc_path.write_text(_write_systemd_service(repo_root, wrapper, base))
    print(f"[OK] Service: {svc_path}")

    if not shutil.which("systemctl"):
        print("[INFO] systemctl non trovato, service file scritto a mano")
        print("[INFO] Per avviare manualmente: "
              "systemctl --user daemon-reload && "
              "systemctl --user enable --now ai-router")
        return

    print("[INFO] Ricarico systemd: systemctl --user daemon-reload")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)

    if args.start:
        print("[INFO] Abilito e avvio: systemctl --user enable --now "
              "ai-router.service")
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "ai-router.service"],
            check=False
        )
    else:
        print("[INFO] Per avviare: systemctl --user enable --now "
              "ai-router.service")


def setup_macos_service(args: argparse.Namespace) -> None:
    """Configura il servizio launchd su macOS."""
    base = config_home()
    repo_root = Path(__file__).parent.absolute()
    proxy = repo_root / "src" / "ai-router-proxy.py"
    plist = Path.home() / "Library" / "LaunchAgents" / (
        "com.github.eroslifestyle.ai-router.plist"
    )

    print_step("Registrazione servizio launchd (macOS)")

    if args.dry_run:
        print(f"[DRY] Creerei plist: {plist}")
        return

    ensure_dir(plist.parent)
    plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '  <key>Label</key>\n'
        '  <string>com.github.eroslifestyle.ai-router</string>\n'
        '  <key>ProgramArguments</key>\n'
        '  <array>\n'
        f'    <string>{sys.executable}</string>\n'
        '    <string>-u</string>\n'
        f'    <string>{proxy}</string>\n'
        '  </array>\n'
        '  <key>WorkingDirectory</key>\n'
        f'  <string>{repo_root / "src"}</string>\n'
        '  <key>EnvironmentVariables</key>\n'
        '  <dict>\n'
        '    <key>PYTHONPATH</key>\n'
        f'    <string>{repo_root}</string>\n'
        '  </dict>\n'
        '  <key>RunAtLoad</key>\n'
        '  <true/>\n'
        '  <key>KeepAlive</key>\n'
        '  <true/>\n'
        '  <key>StandardOutPath</key>\n'
        f'  <string>{base / "logs" / "stdout.log"}</string>\n'
        '  <key>StandardErrorPath</key>\n'
        f'  <string>{base / "logs" / "stderr.log"}</string>\n'
        '</dict>\n'
        '</plist>\n'
    )
    print(f"[OK] Plist: {plist}")
    print("[INFO] Per caricare: launchctl load "
          "~/Library/LaunchAgents/com.github.eroslifestyle.ai-router.plist")


def setup_windows_service(args: argparse.Namespace) -> None:
    """Genera batch e documenta opzioni su Windows."""
    repo_root = Path(__file__).parent.absolute()
    proxy = repo_root / "src" / "ai-router-proxy.py"
    batch = repo_root / "start-router.bat"

    print_step("Registrazione servizio (Windows)")

    if args.dry_run:
        print(f"[DRY] Creerei batch: {batch}")
        return

    batch.write_text(
        f'@echo off\n'
        f'set "PYTHONPATH={repo_root}"\n'
        f'cd /d "{repo_root / "src"}"\n'
        f'"{sys.executable}" -u "{proxy}" %*\n'
    )
    print(f"[OK] Batch: {batch}")
    print("[INFO] Opzioni documentate:")
    print("  1. Utilita di pianificazione:")
    print(f'     schtasks /create /sc onlogon /tr "\\"{sys.executable}\\" '
          f'-u \\"{proxy}\\"" /tn AI-Router')
    print("  2. NSSM (consigliato):")
    print(f'     nssm install AI-Router "{sys.executable}" "-u {proxy}"')


def setup_service(args: argparse.Namespace) -> None:
    """Seleziona la configurazione servizio in base alla piattaforma."""
    if args.no_service:
        print("[SKIP] Registrazione servizio disabilitata")
        return

    p = sys.platform
    if p.startswith("linux"):
        setup_linux_service(args)
    elif p == "darwin":
        setup_macos_service(args)
    elif p == "win32":
        setup_windows_service(args)
    else:
        print(f"[WARN] Piattaforma non supportata per servizi: {p}")


def print_summary(env_path: Path, cfg_base: Path) -> None:
    """Stampa riepilogo finale con istruzioni per il client."""
    print("\n" + "=" * 60)
    print("RIEPILOGO INSTALLAZIONE")
    print("=" * 60)
    print("\nConfigurazione client Claude Code (settings.json):")
    print(f'  "ANTHROPIC_BASE_URL": "http://127.0.0.1:{DYNAMIC_PORT}"')
    print("\nChiavi API da configurare in:")
    print(f"  {env_path}")
    print(f"\nDirectory log: {cfg_base / 'logs'}")
    print(f"Directory secrets: {cfg_base / 'secrets'}")
    print(f"Directory state: {cfg_base / 'state'}")


def main() -> int:
    """Entry point principale."""
    parser = argparse.ArgumentParser(
        description="Installazione di ai-router-switch"
    )
    parser.add_argument("--no-service", action="store_true",
                       help="Salta la registrazione del servizio")
    parser.add_argument("--start", action="store_true",
                       help="Avvia il servizio subito (Linux)")
    parser.add_argument("--yes", "-y", action="store_true",
                       help="Non chiedere conferma per le dipendenze")
    parser.add_argument("--dry-run", action="store_true",
                       help="Stampa le azioni senza eseguirle")
    args = parser.parse_args()

    print("=" * 60)
    print("AI-ROUTER-SWITCH INSTALLER")
    print("=" * 60)

    if not check_python():
        return 1

    print_step("Controllo dipendenze")
    check_dependencies(args)

    print_step("Creazione directory")
    dirs = create_directories(args.dry_run)

    print_step("Setup file .env")
    env_path = setup_env_file(args.dry_run)

    setup_service(args)

    print_summary(env_path, dirs["config"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
