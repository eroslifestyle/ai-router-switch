#!/usr/bin/env bash
# Garantisce che il proxy ai-router sia attivo su :8787 (idempotente).
# Lanciato da SessionStart di Claude Code.
# Usa il file originale in src/ con PYTHONPATH esteso, così non servono
# symlink per ogni modulo (providers/, pipelines/, context_*, fail_tracker, …).
# flock -n: se un'altra invocazione concorrente (altra sessione SessionStart)
# ha già il lock, esci subito invece di rischiare un processo doppio in race
# con systemd (che su restart uccide tutto su queste porte via ExecStartPre).
LOCK_FILE="$HOME/.claude/logs/ensure-ai-router.lock"
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

PORT=8787
PROJECT_ROOT="/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch"
if ! ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  nohup env "PYTHONPATH=$PROJECT_ROOT:$PROJECT_ROOT/src" \
    python3 "$PROJECT_ROOT/src/ai-router-proxy.py" \
    >>"$HOME/.claude/logs/ai-router.log" 2>&1 &
fi
exit 0
