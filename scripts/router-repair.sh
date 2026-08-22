#!/usr/bin/env bash
# Rimette in piedi il router e VERIFICA che risponda. Un comando solo.
#
# Ordine: libera le porte dagli orfani, azzera lo stato failed della unit,
# ricarica systemd, prova ad avviare il servizio; se systemd non c'e' o non
# regge, avvia il proxy direttamente. Alla fine interroga /health e, se non
# risponde, stampa la coda del log con il motivo.
#
# Uso: scripts/router-repair.sh

PORT="${AIROUTER_PORT:-8787}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVICE=ai-router

if command -v python3 &>/dev/null; then PY=python3; elif command -v python &>/dev/null; then PY=python; else
  echo "python3 non trovato: il proxy non puo' partire"; exit 1
fi

LOG_DIR="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import paths; print(paths.logs_dir())' "$PROJECT_ROOT/src" 2>/dev/null)"
[ -n "$LOG_DIR" ] || LOG_DIR="${AIROUTER_HOME:-$HOME/.claude}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null

in_ascolto(){ (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }

attendi_ascolto(){
  local n=0
  while [ $n -lt "${1:-15}" ]; do
    in_ascolto && return 0
    sleep 1
    n=$((n + 1))
  done
  return 1
}

echo "1. dipendenze"
"$PY" - <<'EOF'
import importlib.util
mancanti = [m for m in ("aiohttp", "brotli", "multidict", "PIL") if not importlib.util.find_spec(m)]
if mancanti:
    print("   MANCANTI:", ", ".join(mancanti), "→ python3 -m pip install -r requirements.txt")
    raise SystemExit(2)
print("   ok")
EOF
[ $? -eq 2 ] && exit 1

echo "2. processi orfani sulle porte del router"
"$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import router_constants as c; print(" ".join(str(p) for p in c.LISTEN_PORTS))' \
  "$PROJECT_ROOT/src" 2>/dev/null > "$LOG_DIR/.porte" || echo "$PORT" > "$LOG_DIR/.porte"
PORTE="$(cat "$LOG_DIR/.porte")"; rm -f "$LOG_DIR/.porte"
if command -v fuser &>/dev/null; then
  # shellcheck disable=SC2086
  # fuser stampa un PID per porta: qui interessa solo l'esito.
  if fuser -k -n tcp $PORTE >/dev/null 2>&1; then echo "   orfani terminati"; else echo "   nessun orfano"; fi
else
  pkill -f "ai-router-proxy\.py" 2>/dev/null && echo "   proxy precedenti terminati" || echo "   nessun proxy attivo"
fi
sleep 1

echo "3. servizio"
avviato_da_systemd=0
if command -v systemctl &>/dev/null && systemctl --user show-environment &>/dev/null; then
  systemctl --user reset-failed "$SERVICE" &>/dev/null
  systemctl --user daemon-reload &>/dev/null
  if systemctl --user start "$SERVICE" 2>&1; then
    attendi_ascolto 15 && avviato_da_systemd=1
  fi
  echo "   is-active: $(systemctl --user is-active $SERVICE 2>&1)"
else
  echo "   systemd utente non disponibile, avvio diretto"
fi

if [ "$avviato_da_systemd" -eq 0 ] && ! in_ascolto; then
  echo "   avvio diretto del proxy"
  nohup env "PYTHONPATH=$PROJECT_ROOT:$PROJECT_ROOT/src" \
    "$PY" "$PROJECT_ROOT/src/ai-router-proxy.py" >>"$LOG_DIR/ai-router.log" 2>&1 &
  attendi_ascolto 15
fi

echo "4. verifica"
if in_ascolto; then
  echo "   127.0.0.1:$PORT risponde"
  if command -v curl &>/dev/null; then
    echo -n "   /health: "; curl -s -m 3 "http://127.0.0.1:$PORT/health" | head -c 200; echo
  fi
  echo
  echo "ROUTER SU. Se Claude Code dava 'Connection refused', riprova adesso."
  exit 0
fi

echo "   NESSUNA risposta su 127.0.0.1:$PORT"
echo
echo "Ultime righe di $LOG_DIR/ai-router.log:"
tail -20 "$LOG_DIR/ai-router.log" 2>/dev/null || echo "(log assente)"
echo
echo "ROUTER GIU'. Nel frattempo, per lavorare senza router: unset ANTHROPIC_BASE_URL"
exit 1
