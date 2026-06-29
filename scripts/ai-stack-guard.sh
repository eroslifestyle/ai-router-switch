#!/usr/bin/env bash
# ai-stack-guard — watchdog UNICO dello stack AI router.
# Garantisce che ai-router (:8787) sia SEMPRE attivo (resilienza a kill/crash/OOM).
# Doppio livello: systemd (primario) + questo watchdog (backstop indipendente).
# Lanciato da cron ogni minuto e @reboot.
# Backend DIRETTO alle API, nessun proxy intermedio (2026-06-29).
set -uo pipefail

ROUTER=/home/mrxxx/.claude/scripts/ai-router-proxy.py
LOG=/home/mrxxx/.claude/logs/ai-stack-guard.log
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
  nohup "$@" >> "/home/mrxxx/.claude/logs/${name}.log" 2>&1 &
  sleep 4
  up "$port" && note "  ✓ $name ripristinato via nohup" || note "  ✗ $name ANCORA giù"
}

# ai-router (8787)
ensure "ai-router" 8787 "ai-router.service" \
  /usr/bin/python3 "$ROUTER"

exit 0
