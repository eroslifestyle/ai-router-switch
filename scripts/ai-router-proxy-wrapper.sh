#!/bin/bash
# Wrapper per avviare ai-router-proxy con PYTHONPATH che contiene spazi.
# Necessario perché il path /mnt/backup/Dropbox/1 Programmazione/...
# contiene uno spazio e systemd Environment= non lo gestisce correttamente.
set -e
export PYTHONPATH="/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch:${PYTHONPATH:-}"
# -u: stdout non bufferizzato — senza, i print del proxy restano nel buffer
# e journalctl appare muto (log invisibili durante il debug).
exec /usr/bin/python3 -u /home/mrxxx/.claude/scripts/ai-router-proxy.py "$@"