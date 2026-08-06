#!/usr/bin/env python3
"""
CLI wrapper per il router di Claude Code.

La bash di Claude Code interpreta il carattere ! a inizio riga come comando
shell, quindi "!router mix-am" verrebbe eseguito come "router mix-am" dalla
shell e non raggiungerebbe mai il proxy. Questo script permette di impostare
la modalita' del router direttamente da terminale, per la sessione corrente.
"""

import os
import sys


def calcola_src_dir():
    """Restituisce la directory contenente questo script."""
    return os.path.dirname(os.path.realpath(__file__))


def costruisci_comando(argomento):
    """Costruisce il comando nel formato atteso dal parser."""
    return f"!router {argomento}"


def main():
    src_dir = calcola_src_dir()
    sys.path.insert(0, src_dir)

    try:
        from router_commands import parse_router_command, _router_reply_text
        from router_constants import VALID_MODES
    except ImportError:
        print(f"Errore: moduli non trovati in {src_dir}")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Uso: router <modalita>")
        print(f"Modalita': {', '.join(VALID_MODES)}")
        sys.exit(0)

    # Il fingerprint si calcola solo dopo aver verificato che c'e' un argomento:
    # 'router' senza argomenti deve poter stampare l'aiuto anche fuori da una sessione.
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        print("Variabile CLAUDE_CODE_SESSION_ID assente o vuota.")
        print("Impossibile determinare la sessione attiva.")
        print("Per un cambio globale, usa: ai-mode <modalita>")
        sys.exit(1)

    fp = "sid:" + session_id[:64]

    cmd = costruisci_comando(sys.argv[1])
    cmd_parsed = parse_router_command(cmd)

    if cmd_parsed is None:
        print("Uso: router <modalita>")
        print(f"Modalita': {', '.join(VALID_MODES)}")
        sys.exit(2)

    print(_router_reply_text(cmd_parsed, fp))
    sys.exit(0)


if __name__ == "__main__":
    main()
