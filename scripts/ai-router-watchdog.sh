#!/bin/bash
# AI Router Proxy watchdog
# Verifica /__router_health, su 2 fallimenti consecutivi forza restart.
set -u
PORT=8787
URL="http://127.0.0.1:${PORT}/__router_health"
STATE_FILE="/tmp/ai-router-watchdog.fails"
LOG="/home/mrxxx/.claude/logs/ai-router-watchdog.log"
TIMEOUT=5
MAX_FAILS=2

mkdir -p "$(dirname "$LOG")"
ts() { date "+%Y-%m-%dT%H:%M:%S"; }

# Service deve essere attivo
if ! systemctl --user is-active --quiet ai-router.service; then
    echo "[$(ts)] service NOT active, starting" >> "$LOG"
    systemctl --user start ai-router.service
    echo 0 > "$STATE_FILE"
    exit 0
fi

# Probe HTTP
code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$URL" 2>/dev/null || echo "000")
prev=$(cat "$STATE_FILE" 2>/dev/null || echo 0)

# RESILIENZA 2026-06-30: in modalità DEGRADED il proxy accetta solo health endpoints
# e ritorna 503 su /v1/messages. Se siamo in DEGRADED, NON forzare restart:
# è una modalità intenzionale per OAuth subscription mancante. Il restart non
# risolve; serve `claude login` nel terminale (auto-recovery entro ~30s).
if [ "$code" = "200" ]; then
    # Verifica stato resilienza solo se health OK
    res_state=$(curl -sS -m 3 "http://127.0.0.1:${PORT}/__resilience" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','?'))" 2>/dev/null || echo "?")
    if [ "$res_state" = "DEGRADED" ]; then
        echo "[$(ts)] DEGRADED detected (OAuth mancante) — skip FORCE RESTART, auto-recovery via \`claude login\`" >> "$LOG"
        echo 0 > "$STATE_FILE"
        exit 0
    fi
fi

if [ "$code" = "200" ]; then
    if [ "$prev" != "0" ]; then
        echo "[$(ts)] health OK (recovered after $prev fail)" >> "$LOG"
    fi
    echo 0 > "$STATE_FILE"
    exit 0
fi

fails=$((prev + 1))
echo "$fails" > "$STATE_FILE"
echo "[$(ts)] health FAIL code=$code consecutive=$fails/$MAX_FAILS" >> "$LOG"

if [ "$fails" -ge "$MAX_FAILS" ]; then
    PID=$(systemctl --user show -p MainPID --value ai-router.service)
    echo "[$(ts)] FORCE RESTART PID=$PID (was: $fails consecutive fails)" >> "$LOG"
    # Restart con SIGKILL immediato — la unit ha TimeoutStopSec=3
    systemctl --user kill -s SIGKILL ai-router.service 2>/dev/null || true
    sleep 1
    systemctl --user restart ai-router.service
    echo 0 > "$STATE_FILE"
fi
