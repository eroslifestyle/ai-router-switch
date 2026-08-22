#!/usr/bin/env bash
# Raccoglie in un colpo solo tutto quello che serve per capire perche' il
# router non parte: dipendenze, stato del servizio, chi tiene le porte,
# coda del log, risposta di /health.
# Non modifica niente e non avvia niente: si puo' lanciare sempre.
# Uso: scripts/router-doctor.sh

PORT="${AIROUTER_PORT:-8787}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVICE=ai-router

if command -v python3 &>/dev/null; then PY=python3; elif command -v python &>/dev/null; then PY=python; else PY=""; fi

titolo(){ printf '\n── %s %s\n' "$1" "$(printf '─%.0s' $(seq 1 $((60 - ${#1}))))"; }

titolo "Python e dipendenze"
if [ -z "$PY" ]; then
  echo "python3 NON trovato: il proxy non puo' partire"
else
  "$PY" -V
  "$PY" - <<'EOF'
import importlib.util
mancanti = [m for m in ("aiohttp", "brotli", "multidict", "PIL") if not importlib.util.find_spec(m)]
print("dipendenze mancanti:", ", ".join(mancanti) if mancanti else "nessuna")
EOF
fi

titolo "Percorsi"
if [ -n "$PY" ]; then
  "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import paths; print("config home:", paths.config_home()); print("logs:", paths.logs_dir()); print("modalita\x27:", paths.mode_file())' \
    "$PROJECT_ROOT/src" 2>&1
fi
LOG_DIR="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import paths; print(paths.logs_dir())' "$PROJECT_ROOT/src" 2>/dev/null)"
[ -n "$LOG_DIR" ] || LOG_DIR="${AIROUTER_HOME:-$HOME/.claude}/logs"

titolo "Servizio systemd"
if command -v systemctl &>/dev/null; then
  echo "is-active : $(systemctl --user is-active $SERVICE 2>&1)"
  echo "is-enabled: $(systemctl --user is-enabled $SERVICE 2>&1)"
  systemctl --user status $SERVICE --no-pager -n 8 2>&1 | tail -12
  echo "(unit in stato failed? sbloccala con: systemctl --user reset-failed $SERVICE)"
else
  echo "systemctl non trovato (avvio manuale o macOS/launchd)"
fi

titolo "Porta $PORT"
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  echo "qualcuno ascolta su 127.0.0.1:$PORT"
else
  echo "NESSUNO ascolta su 127.0.0.1:$PORT  <-- e' questo che da' 'Connection refused'"
fi
if command -v fuser &>/dev/null; then
  pid_porta="$(fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ')"
  echo "processi sulla porta:${pid_porta:- nessuno}"
fi
# La classe di caratteri evita che pgrep trovi se stesso; il grep scarta questo script.
echo "processi ai-router-proxy:"
pgrep -af "[a]i-router-proxy\.py" 2>/dev/null | grep -v router-doctor | head -5 || true

titolo "Health"
if command -v curl &>/dev/null; then
  curl -s -m 3 "http://127.0.0.1:$PORT/health" || echo "(nessuna risposta)"
  echo
fi

titolo "Ultime righe di $LOG_DIR/ai-router.log"
if [ -f "$LOG_DIR/ai-router.log" ]; then
  tail -25 "$LOG_DIR/ai-router.log"
  echo
  echo -n "errori di bind nel log: "
  grep -c "FATAL\|errno 98" "$LOG_DIR/ai-router.log" 2>/dev/null || echo 0
else
  echo "log assente: il proxy non e' mai stato avviato da qui"
fi
