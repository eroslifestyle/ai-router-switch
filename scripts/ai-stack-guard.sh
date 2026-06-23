#!/usr/bin/env bash
# ai-stack-guard — watchdog UNICO dello stack AI router.
# Garantisce che i 3 servizi siano SEMPRE attivi (resilienza a kill/crash/OOM).
# Doppio livello: systemd (primario) + questo watchdog (backstop indipendente).
# Lanciato da cron ogni minuto e @reboot.
set -uo pipefail

VENV=/opt/headroom/.venv
HB="$VENV/bin/python3 -m headroom.cli"
ROUTER=$HOME/.claude/scripts/ai-router-proxy.py
LOG=$HOME/.claude/logs/ai-stack-guard.log
mkdir -p "$(dirname "$LOG")"

ts(){ date '+%Y-%m-%dT%H:%M:%S'; }
up(){ ss -ltn 2>/dev/null | grep -q "127.0.0.1:$1 "; }
note(){ echo "[$(ts)] $*" >> "$LOG"; }

# Prova prima systemd (se il service esiste), poi fallback nohup diretto.
ensure(){
  local name="$1" port="$2" svc="$3"; shift 3
  up "$port" && return 0
  note "PORTA $port ($name) GIÙ — riavvio"
  # tentativo 1: systemd
  if systemctl --user list-unit-files "$svc" >/dev/null 2>&1; then
    systemctl --user reset-failed "$svc" 2>/dev/null
    systemctl --user restart "$svc" 2>/dev/null
    sleep 4
    up "$port" && { note "  ✓ $name ripristinato via systemd"; return 0; }
  fi
  # tentativo 2: nohup diretto (backstop se systemd degraded)
  note "  systemd ko -> nohup diretto $name"
  nohup "$@" >> "$HOME/.claude/logs/${name}.log" 2>&1 &
  sleep 4
  up "$port" && note "  ✓ $name ripristinato via nohup" || note "  ✗ $name ANCORA giù"
}

# headroom#1 (8787) -> Anthropic
ensure "headroom1" 8791 "headroom-proxy.service" \
  env HEADROOM_TELEMETRY=off HEADROOM_UPDATE_CHECK=off \
  $VENV/bin/python3 -m headroom.cli proxy --host 127.0.0.1 --port 8791 --mode token --backend anthropic

# headroom#2 (8790) -> MiniMax
ensure "headroom2" 8790 "headroom-minimax.service" \
  env ANTHROPIC_TARGET_API_URL=https://api.minimax.io/anthropic \
  HEADROOM_TELEMETRY=off HEADROOM_UPDATE_CHECK=off HEADROOM_SKIP_UPSTREAM_CHECK=1 \
  $VENV/bin/python3 -m headroom.cli proxy --host 127.0.0.1 --port 8790 --mode token --backend anthropic

# ai-router (8789)
ensure "ai-router" 8787 "ai-router.service" \
  /usr/bin/python3 "$ROUTER"

exit 0
