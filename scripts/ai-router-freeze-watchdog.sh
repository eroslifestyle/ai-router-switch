#!/bin/bash
# AI Router Freeze Watchdog — Tier 1 esterno (RESILIENZA 2026-06-30)
#
# Rileva FREEZE (processo Python che non risponde) tramite:
# 1. Controllo wchan del processo (futex_do_wait anomalo, pipe_read bloccato)
# 2. Heartbeat freshness (state file scritto dal watchdog interno)
# 3. Health endpoint non risponde entro timeout
#
# Su anomalia: SIGKILL + salvataggio crash dump, poi restart pulito.
# Rate-limit: max 5 restart in 300s, poi cooldown 5min.

set -u

PORT="${AIROUTER_PORT:-8787}"
SERVICE="ai-router.service"
STATE_FILE="$HOME/.claude/state/ai-router-resilience-state.json"
CRASH_DIR="$HOME/.claude/state/ai-router-crash-dumps"
WATCHDOG_LOG="$HOME/.claude/logs/ai-router-freeze-watchdog.log"

mkdir -p "$(dirname "$WATCHDOG_LOG")" "$CRASH_DIR" "$(dirname "$STATE_FILE")"

ts() { date "+%Y-%m-%dT%H:%M:%S"; }
log() { echo "[$(ts)] $*" >> "$WATCHDOG_LOG"; }

# ── Rate limit: conta restart recenti ───────────────────────────────────
RATE_FILE="/tmp/ai-router-freeze-watchdog.rate"
RATE_WINDOW_S=300      # ultimi 5 min
RATE_MAX=5             # max restart in finestra
COOLDOWN_S=300         # 5 min cooldown dopo sforamento
LAST_BOOT_FILE="/tmp/ai-router-freeze-watchdog.lastboot"

cleanup_stale_rate() {
    # Rimuovi timestamp più vecchi di RATE_WINDOW_S
    [ -f "$RATE_FILE" ] || return
    local now=$(date +%s)
    local tmp=$(mktemp)
    while IFS= read -r line; do
        local t=$line
        local age=$((now - t))
        if [ "$age" -lt "$RATE_WINDOW_S" ]; then
            echo "$t" >> "$tmp"
        fi
    done < "$RATE_FILE"
    mv "$tmp" "$RATE_FILE"
}

count_restarts() {
    [ -f "$RATE_FILE" ] && wc -l < "$RATE_FILE" | tr -d ' ' || echo 0
}

record_restart() {
    date +%s >> "$RATE_FILE"
}

is_in_cooldown() {
    [ -f "$LAST_BOOT_FILE" ] || return 1
    local now=$(date +%s)
    local last=$(cat "$LAST_BOOT_FILE" 2>/dev/null || echo 0)
    local age=$((now - last))
    [ "$age" -lt "$COOLDOWN_S" ] && return 0
    return 1
}

# ── Rilevazione freeze ──────────────────────────────────────────────────
get_pid() {
    systemctl --user show -p MainPID --value "$SERVICE" 2>/dev/null
}

check_wchan() {
    local pid="$1"
    # futex_do_wait prolungato = deadlock Python GIL/lock
    local wchan=$(cat "/proc/$pid/wchan" 2>/dev/null)
    [ -z "$wchan" ] && return 1
    # 0 = running
    [ "$wchan" = "0" ] && return 0
    if [ "$wchan" = "futex_do_wait" ] || [ "$wchan" = "futex_wait_queue_me" ]; then
        return 2  # sospetto deadlock
    fi
    if [ "$wchan" = "pipe_read" ] || [ "$wchan" = "anon_pipe_read" ]; then
        # pipe_read OK se <1s (subprocess secrets). Se prolungato, sospetto.
        return 3
    fi
    if [ "$wchan" = "ep_poll" ] || [ "$wchan" = "poll_schedule_timeout" ]; then
        # ep_poll = event loop attivo, NORMALE
        return 0
    fi
    return 1  # altro stato
}

check_wchan_age() {
    local pid="$1"
    local file="/proc/$pid/wchan"
    # Stat per verificare se /proc è accessibile
    cat "$file" >/dev/null 2>&1 || return 1
    # Calcola tempo dall'ultima modifica del /proc/*/wchan (non funziona,
    # /proc viene aggiornato in read time). Usiamo invece etime+pcpu dal ps.
    return 0
}

check_heartbeat_fresh() {
    # Il watchdog interno scrive heartbeat_ts ogni 2s nel state file.
    # Se heartbeat > 10s vecchio, watchdog stesso è bloccato.
    [ -f "$STATE_FILE" ] || return 1
    local hb=$(python3 -c "
import json, time, sys
try:
    d = json.load(open('$STATE_FILE'))
    hb = d.get('heartbeat_ts', 0)
    print(int((time.time() - hb) * 1000))
except Exception:
    print(-1)
" 2>/dev/null)
    [ "$hb" -ge 0 ] && [ "$hb" -lt 10000 ] 2>/dev/null
}

check_health() {
    local code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 \
        "http://127.0.0.1:${PORT}/__router_health" 2>/dev/null || echo "000")
    [ "$code" = "200" ] && return 0
    return 1
}

# Controlla se il proxy è in DEGRADED mode (intenzionale, non freeze).
# In DEGRADED il proxy accetta solo health endpoints, NON va riavviato: il
# problema è OAuth subscription mancante, che si risolve con `claude login`,
# non con un restart. Restartare in DEGRADED causerebbe solo loop infinito.
is_in_degraded() {
    local state=$(curl -sS -m 5 "http://127.0.0.1:${PORT}/__resilience" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state','?'))" 2>/dev/null || echo "?")
    [ "$state" = "DEGRADED" ]
}

kill_and_dump() {
    local pid="$1"
    local reason="$2"
    log "FREEZE DETECTED pid=$pid reason=$reason"

    # Crash dump prima del kill
    if [ -d "$CRASH_DIR" ]; then
        local ts=$(date +%Y%m%d_%H%M%S)
        local dump="$CRASH_DIR/freeze-${ts}.txt"
        {
            echo "=== Freeze dump by ai-router-freeze-watchdog ==="
            echo "ts=$(ts)"
            echo "pid=$pid"
            echo "reason=$reason"
            echo "--- stat ---"
            ps -p "$pid" -o pid,stat,pcpu,pmem,etime,wchan:30,cmd 2>&1
            echo "--- /proc/$pid/status (top 15) ---"
            head -15 "/proc/$pid/status" 2>&1
            echo "--- /proc/$pid/stack (kernel) ---"
            cat "/proc/$pid/stack" 2>&1 | head -20
            echo "--- FD aperti (no std) ---"
            ls -la "/proc/$pid/fd/" 2>&1 | grep -v "/dev/null\|/dev/urandom" | head -20
            echo "--- connessioni di rete attive ---"
            ss -tnp 2>&1 | grep ":${PORT} " | head -10
        } > "$dump" 2>&1
        log "crash dump saved: $dump"
    fi

    # SIGKILL al processo
    kill -9 "$pid" 2>/dev/null
    sleep 1
    systemctl --user kill -s SIGKILL "$SERVICE" 2>/dev/null || true
    sleep 1
    systemctl --user restart "$SERVICE" 2>/dev/null
    record_restart
    date +%s > "$LAST_BOOT_FILE"
    log "FORCE RESTART eseguito (reason: $reason)"
}

# ── Main loop ───────────────────────────────────────────────────────────
log "freeze-watchdog started port=$PORT rate_max=$RATE_MAX window=${RATE_WINDOW_S}s cooldown=${COOLDOWN_S}s"

# Check iniziale: cooldown?
if is_in_cooldown; then
    log "IN COOLDOWN — skip questo giro"
fi

cleanup_stale_rate
n_restarts=$(count_restarts)

if [ "$n_restarts" -ge "$RATE_MAX" ]; then
    log "RATE LIMIT RAGGIUNTO ($n_restarts/$RATE_MAX in ${RATE_WINDOW_S}s) — nessuna azione (COOLDOWN)"
    exit 0
fi

# Check servizio attivo
if ! systemctl --user is-active --quiet "$SERVICE"; then
    log "service NOT active, starting"
    systemctl --user start "$SERVICE" 2>/dev/null
    record_restart
    date +%s > "$LAST_BOOT_FILE"
    exit 0
fi

PID=$(get_pid)
if [ -z "$PID" ] || [ "$PID" = "0" ]; then
    log "no PID, restart"
    systemctl --user restart "$SERVICE" 2>/dev/null
    record_restart
    date +%s > "$LAST_BOOT_FILE"
    exit 0
fi

# Boot grace: processo appena (ri)partito → health endpoint può non essere
# ancora in ascolto. Senza grace il watchdog killa un processo sano in boot
# (churn 2026-07-22 06:56: FORCE RESTART 4s dopo lo start di systemd).
BOOT_GRACE_S=30
ETIMES=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
if [ -n "$ETIMES" ] && [ "$ETIMES" -lt "$BOOT_GRACE_S" ] 2>/dev/null; then
    log "BOOT GRACE uptime=${ETIMES}s < ${BOOT_GRACE_S}s — skip check"
    exit 0
fi

# 1) Check /proc/<pid>/wchan per segnali di deadlock
wchan_result=0
check_wchan "$PID"
wchan_result=$?

# 2) Check heartbeat freshness (segnala watchdog interno bloccato)
heartbeat_ok=0
check_heartbeat_fresh && heartbeat_ok=1

# 3) Check health endpoint
health_ok=0
check_health && health_ok=1

# Decision matrix
reason=""
if [ "$health_ok" -eq 0 ] && [ "$heartbeat_ok" -eq 0 ]; then
    reason="health_fail AND no_heartbeat (process fully frozen)"
elif [ "$health_ok" -eq 0 ] && [ "$wchan_result" -eq 2 ]; then
    reason="health_fail AND wchan=futex_do_wait (deadlock sospetto)"
elif [ "$wchan_result" -eq 2 ]; then
    # health può essere ok per cache response, ma wchan deadlock è warning forte
    # Solo kill se heartbeat non è fresco (cioè il vero signal è assente)
    if [ "$heartbeat_ok" -eq 0 ]; then
        reason="wchan=futex_do_wait AND no_heartbeat (true deadlock)"
    else
        # wchan dice futex ma heartbeat fresco: probabilmente solo request in corso
        log "wchan=futex_wait ma heartbeat fresco — skip (likely in-flight request)"
        exit 0
    fi
elif [ "$health_ok" -eq 0 ]; then
    reason="health endpoint FAIL"
fi

# Se siamo in DEGRADED, NON killare (è una modalità intenzionale per OAuth
# mancante; un restart non risolve, solo `claude login` lo risolve in auto-recovery)
if [ -n "$reason" ] && is_in_degraded; then
    log "DEGRADED MODE ATTIVO — skip restart (recovery via `claude login`)"
    exit 0
fi

if [ -n "$reason" ]; then
    kill_and_dump "$PID" "$reason"
else
    log "OK pid=$PID wchan_result=$wchan_result health=$health_ok heartbeat=$heartbeat_ok"
fi
