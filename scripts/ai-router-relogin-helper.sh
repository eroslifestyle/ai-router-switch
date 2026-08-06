#!/bin/bash
# AI Router Re-Login Helper (RESILIENZA 2026-06-30)
#
# Dopo un reset leobox / reboot, ~/.claude/.credentials.json può mancare o essere
# scaduto. Questo helper mostra lo stato attuale e guida l'utente al re-login.
#
# Uso:
#   ai-router-relogin-helper.sh           # mostra stato corrente
#   ai-router-relogin-helper.sh --wait    # resta in aspetto finché OAuth è OK
#
# Dopo `claude login` nel terminale (Claude Code subscription), il proxy
# rileverà il nuovo token automaticamente entro ~30s.

set -u
PORT="${AIROUTER_PORT:-8787}"
RESILIENCE_URL="http://127.0.0.1:${PORT}/__resilience"
HEALTH_URL="http://127.0.0.1:${PORT}/health"

green() { printf "\033[32m%s\033[0m" "$1"; }
red() { printf "\033[31m%s\033[0m" "$1"; }
yellow() { printf "\033[33m%s\033[0m" "$1"; }
bold() { printf "\033[1m%s\033[0m" "$1"; }

print_status() {
    echo ""
    bold "─── AI Router Proxy — Stato Resilienza ───"
    echo ""

    # Health check
    health=$(curl -sS -m 3 "$HEALTH_URL" -w "HTTP=%{http_code}" 2>&1 | tail -1)
    if [[ "$health" == *"200"* ]]; then
        printf "Health: %s %s\n" "$(green '✓')" "$health"
    else
        printf "Health: %s proxy non risponde\n" "$(red '✗')"
        echo "  → forse il proxy non è partito. Controlla:"
        echo "    systemctl --user status ai-router.service"
        return 1
    fi

    # Stato resilienza
    state_json=$(curl -sS -m 5 "$RESILIENCE_URL" 2>/dev/null)
    if [ -z "$state_json" ]; then
        echo "  ⚠ impossibile leggere /__resilience (modulo resilienza assente?)"
        return 1
    fi

    state=$(echo "$state_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','?'))" 2>/dev/null)
    oauth=$(echo "$state_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(bool(d.get('oauth_present', False)))" 2>/dev/null)
    sub=$(echo "$state_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('oauth_subscription','?'))" 2>/dev/null)
    exp_h=$(echo "$state_json" | python3 -c "import sys,json; e=json.load(sys.stdin).get('oauth_expires_in_h'); print(f'{e:.1f}h' if e else '?')" 2>/dev/null)
    has_refresh=$(echo "$state_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('oauth_has_refresh',False))" 2>/dev/null)
    heartbeat_age=$(python3 -c "
import json, time
d = json.loads('''$state_json'''.replace('\"\"\"',''))
hb = d.get('heartbeat_ts', 0)
if hb: print(f'{(time.time()-hb):.1f}s ago')
else: print('none')
" 2>/dev/null || echo "?")
    pid=$(echo "$state_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pid','?'))" 2>/dev/null)
    uptime=$(echo "$state_json" | python3 -c "import sys,json; u=json.load(sys.stdin).get('heartbeat_uptime_s',0); print(f'{u}s' if u else '?')" 2>/dev/null)

    echo ""
    echo "Stato servizio: $([ "$state" = "OK" ] && green "$state" || red "$state")"
    echo "PID: $pid (uptime: $uptime)"
    echo "OAuth subscription: $([ "$oauth" = "True" ] && green "presente" || red "MANCANTE")"
    echo "  - Subscription tier: $sub"
    echo "  - Scadenza token: $exp_h"
    echo "  - Refresh token disponibile: $has_refresh"
    echo "  - Heartbeat watchdog: $heartbeat_age"
    echo ""

    if [ "$state" = "DEGRADED" ]; then
        echo "$(yellow '⚠ Modalità DEGRADED:')"
        echo "Il proxy accetta SOLO /health e /__resilience finché OAuth non torna valido."
        echo ""
        bold "Per ripristinare:"
        echo ""
        echo "  1. Apri un terminale"
        echo "  2. Esegui: $(bold 'claude login')"
        echo "     (login OAuth subscription via web, come fa Claude Code)"
        echo "  3. Il proxy rileverà automaticamente il nuovo token entro ~30s"
        echo ""
        bold "Comandi utili:"
        echo "  - Stato live:        $(bold 'curl http://127.0.0.1:$PORT/__resilience | jq .')"
        echo "  - Forza self-test:   $(bold 'touch ~/.claude/.credentials.json')"
        echo "  - Log servizio:      $(bold 'journalctl --user -u ai-router -f')"
        echo "  - Log proxy:         $(bold 'tail -f ~/.claude/logs/ai-router.log')"
        return 1
    elif [ "$state" = "OK" ]; then
        echo "$(green '✓ Tutto operativo!')"
        echo ""
        echo "  Anthropic OAuth subscription: $sub, expires tra $exp_h"
        if [[ "${exp_h%h}" =~ ^[0-9.]+$ ]] && python3 -c "import sys; sys.exit(0 if float('$exp_h'.rstrip('h'))<2 else 1)" 2>/dev/null; then
            echo "  $(yellow '⚠') Token scade tra meno di 2 ore: fai una richiesta qualunque"
            echo "    (refresh OAuth accade automaticamente)"
        fi
        return 0
    else
        echo "$(yellow '? Stato sconosciuto:') $state"
        return 2
    fi
}

wait_for_recovery() {
    echo ""
    bold "Resto in attesa del recovery OAuth..."
    echo "(premi Ctrl+C per uscire)"
    while true; do
        state_json=$(curl -sS -m 5 "$RESILIENCE_URL" 2>/dev/null)
        state=$(echo "$state_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','?'))" 2>/dev/null)
        if [ "$state" = "OK" ]; then
            echo ""
            green "✓ RECOVERY COMPLETATO! Proxy operativo."
            return 0
        fi
        printf "."
        sleep 5
    done
}

case "${1:-}" in
    --wait|-w)
        print_status || true
        wait_for_recovery
        ;;
    --help|-h)
        echo "Uso: ai-router-relogin-helper.sh [--wait]"
        echo ""
        echo "  --wait    resta in attesa che OAuth torni OK (utile dopo `claude login`)"
        ;;
    *)
        print_status
        ;;
esac
