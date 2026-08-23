#!/usr/bin/env bash
# Garantisce che il proxy ai-router sia attivo su :8787 (idempotente).
# Lanciato da SessionStart di Claude Code.
# Usa il file originale in src/ con PYTHONPATH esteso, così non servono
# symlink per ogni modulo (providers/, pipelines/, context_*, streaming_relay, …).

PORT="${AIROUTER_PORT:-8787}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if command -v python3 &>/dev/null; then
  PY=python3
elif command -v python &>/dev/null; then
  PY=python
else
  echo "[ensure-ai-router] serve Python 3 per avviare il proxy" >&2
  exit 0
fi

# La directory dei log la decide paths.py (AIROUTER_LOGS_DIR, AIROUTER_HOME,
# ~/.claude storica, XDG). Scriverla a mano come "$HOME/.claude/logs" mandava
# lock e log altrove rispetto al proxy su ogni installazione che non usa
# ~/.claude, e su una installazione pulita la directory non esisteva nemmeno.
LOG_DIR="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import paths; print(paths.logs_dir())' \
  "$PROJECT_ROOT/src" 2>/dev/null)"
[ -n "$LOG_DIR" ] || LOG_DIR="${AIROUTER_HOME:-$HOME/.claude}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null

# flock -n: se un'altra invocazione concorrente (altra sessione SessionStart)
# ha già il lock, esci subito invece di rischiare un processo doppio in race
# con systemd (che su restart uccide tutto su queste porte via ExecStartPre).
# Se il lock NON si apre si prosegue SENZA lock: prima un `exec 9>` fallito
# lasciava il descrittore chiuso, flock rispondeva "Bad file descriptor" e il
# `|| exit 0` faceva uscire l'hook con codice 0 — il router non partiva e non
# lo diceva a nessuno.
LOCK_FILE="$LOG_DIR/ensure-ai-router.lock"
if exec 9>"$LOCK_FILE" 2>/dev/null; then
  flock -n 9 || exit 0
else
  echo "[ensure-ai-router] lock non apribile ($LOCK_FILE), proseguo senza lock" >&2
fi

# Qualcuno è già in ascolto sulla porta?
# La prova vera è una connessione TCP: `ss` non c'è ovunque (container minimali,
# busybox, macOS) e quando manca il vecchio test `ss -ltn | grep :8787` era
# sempre falso, quindi l'hook lanciava un SECONDO proxy che moriva subito con
# "errno 98 ... FATAL: uscita per bind fallito sulla porta principale".
port_in_ascolto(){
  local p="$1"
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
    return 0
  fi
  if command -v ss &>/dev/null; then
    ss -ltn 2>/dev/null | grep -qE "[:.]$p[[:space:]]" && return 0
  fi
  return 1
}

if ! port_in_ascolto "$PORT"; then
  nohup env "PYTHONPATH=$PROJECT_ROOT:$PROJECT_ROOT/src" \
    "$PY" "$PROJECT_ROOT/src/ai-router-proxy.py" \
    >>"$LOG_DIR/ai-router.log" 2>&1 &
fi
exit 0
