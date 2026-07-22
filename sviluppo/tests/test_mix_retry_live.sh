#!/bin/bash
# Test LIVE comparativo del retry certificato sulle leg Anthropic delle mix, in
# ISTANZA ISOLATA (mai :8787). Reindirizza l'upstream Anthropic a un fake 429 e
# osserva il backoff nei log del router di test.
#
# Uso: ./test_mix_retry_live.sh <mode> <test-port>
#   mode: mix-am | mix-ag | mix-gm | anthropic
#   test-port: porta del router di test (es. 18773)
set -u
MODE="${1:?mode richiesto}"
RPORT="${2:?test port richiesto}"
SRC="/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch"
FAKE_PORT="${FAKE_PORT:-9429}"
LOG="/tmp/mixretry-${MODE}-${RPORT}.log"
FAKE_LOG="/tmp/fake429-${FAKE_PORT}.log"

cleanup() {
  [ -n "${ROUTER_PID:-}" ] && kill "$ROUTER_PID" 2>/dev/null
  [ -n "${FAKE_PID:-}" ] && kill "$FAKE_PID" 2>/dev/null
  fuser -k -n tcp "$RPORT" 2>/dev/null
  fuser -k -n tcp "$FAKE_PORT" 2>/dev/null
}
trap cleanup EXIT

# 1) Fake upstream 429 (retry-after=1, x-should-retry=true, sempre 429)
FAKE_PORT="$FAKE_PORT" FAKE_RETRY_AFTER="${FAKE_RETRY_AFTER:-1}" \
  FAKE_STATUS="${FAKE_STATUS:-429}" FAKE_SHOULD_RETRY="${FAKE_SHOULD_RETRY:-true}" \
  FAKE_SUCCEED_AFTER="${FAKE_SUCCEED_AFTER:-0}" \
  python3 "$SRC/sviluppo/tests/fake_anthropic_429.py" >"$FAKE_LOG" 2>&1 &
FAKE_PID=$!
sleep 1

# 2) Router di test isolato: SOLO questa porta, mappata alla modalità richiesta.
#    Upstream Anthropic + MiniMax -> fake 429 (per forzare i 429 sulle leg).
#    Backoff base piccolissima (0.05s) per test rapido; retry-after del fake=1s.
export PYTHONPATH="$SRC:${PYTHONPATH:-}"
export AIROUTER_PORT_MODE_JSON="{\"$RPORT\": \"$MODE\"}"
export AIROUTER_PORT="$RPORT"
export AIROUTER_ANTHROPIC_UPSTREAM="http://127.0.0.1:$FAKE_PORT"
export AIROUTER_ANTHROPIC_DIRECT="http://127.0.0.1:$FAKE_PORT"
export AIROUTER_MINIMAX_UPSTREAM="http://127.0.0.1:$FAKE_PORT/anthropic"
export AIROUTER_ANTHROPIC_RETRY_BASE_SEC="${AIROUTER_ANTHROPIC_RETRY_BASE_SEC:-0.05}"
export AIROUTER_ANTHROPIC_MAX_RETRIES="${AIROUTER_ANTHROPIC_MAX_RETRIES:-2}"

python3 -u "$SRC/src/ai-router-proxy.py" >"$LOG" 2>&1 &
ROUTER_PID=$!
sleep 3

if ! kill -0 "$ROUTER_PID" 2>/dev/null; then
  echo "!! router di test morto in boot. Log:"; tail -30 "$LOG"; exit 1
fi

echo "=== $MODE su :$RPORT (upstream->fake429 :$FAKE_PORT) ==="
BODY='{"model":"claude-sonnet-4-5","max_tokens":64,"messages":[{"role":"user","content":"scrivi una funzione python che somma due numeri"}]}'
T0=$(date +%s.%N)
HTTP=$(curl -sS -o /tmp/mixretry-resp.json -w "%{http_code}" -m 30 \
  -H "Content-Type: application/json" \
  -H "X-Claude-Code-Session-Id: retrytest-$MODE" \
  --data "$BODY" "http://127.0.0.1:$RPORT/v1/messages")
T1=$(date +%s.%N)
echo "HTTP=$HTTP  tempo=$(echo "$T1 - $T0" | bc)s"
echo "--- risposta (primi 200 char) ---"; head -c 200 /tmp/mixretry-resp.json; echo
echo "--- router log (retry/429/rescue) ---"
grep -aE "retry|429|rescue|backoff|exhausted|THINK|ACT|PERSISTENTE" "$LOG" | tail -25
echo "--- fake upstream: n. richieste ricevute ---"
grep -c "\[fake\] req" "$FAKE_LOG"
echo "=== fine $MODE ==="
