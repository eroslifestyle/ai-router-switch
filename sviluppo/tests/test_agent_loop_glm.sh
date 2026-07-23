#!/bin/bash
# Test LIVE isolato del cablaggio agent_loop (mix-ag/mix-gm) vs pipeline classiche.
# Confronta AIROUTER_AGENT_LOOP=1 (agent_loop) vs =0 (pipeline classiche) contro
# un fake upstream che risponde 200. Entrambi devono produrre 200 al client.
# Istanza ISOLATA (mai :8787).
#
# Uso: ./test_agent_loop_glm.sh <mode> <test-port>
#   mode: mix-ag | mix-gm
#   test-port base (usa port e port+1)
set -u
MODE="${1:-mix-ag}"
RPORT="${2:-18810}"
SRC="/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch"
FAKE_PORT="${FAKE_PORT:-9430}"

cleanup() {
  [ -n "${R_ON:-}" ] && kill "$R_ON" 2>/dev/null
  [ -n "${R_OFF:-}" ] && kill "$R_OFF" 2>/dev/null
  [ -n "${FAKE_PID:-}" ] && kill "$FAKE_PID" 2>/dev/null
  fuser -k -n tcp "$RPORT" 2>/dev/null
  fuser -k -n tcp "$((RPORT+1))" 2>/dev/null
  fuser -k -n tcp "$FAKE_PORT" 2>/dev/null
}
trap cleanup EXIT

# Fake upstream: risponde SEMPRE 200 (SUCCEED_AFTER=0 con STATUS=200 -> path errore
# mai preso perché STATUS=200; ma per sicurezza usiamo SUCCEED_AFTER alto).
FAKE_PORT="$FAKE_PORT" FAKE_STATUS=200 FAKE_SUCCEED_AFTER=0 \
  python3 "$SRC/sviluppo/tests/fake_anthropic_429.py" >"/tmp/fake-alg-$FAKE_PORT.log" 2>&1 &
FAKE_PID=$!
sleep 1

export PYTHONPATH="$SRC:${PYTHONPATH:-}"
export AIROUTER_ANTHROPIC_UPSTREAM="http://127.0.0.1:$FAKE_PORT"
export AIROUTER_ANTHROPIC_DIRECT="http://127.0.0.1:$FAKE_PORT"
export AIROUTER_MINIMAX_UPSTREAM="http://127.0.0.1:$FAKE_PORT/anthropic"
export GLM_UPSTREAM_URL="http://127.0.0.1:$FAKE_PORT"
export AIROUTER_ANTHROPIC_RETRY_BASE_SEC="0.05"
export AIROUTER_ANTHROPIC_MAX_RETRIES="1"

# Router OFF (pipeline classiche)
AIROUTER_PORT_MODE_JSON="{\"$RPORT\": \"$MODE\"}" AIROUTER_PORT="$RPORT" \
  AIROUTER_AGENT_LOOP=0 \
  python3 -u "$SRC/src/ai-router-proxy.py" >"/tmp/alg-off-$RPORT.log" 2>&1 &
R_OFF=$!

# Router ON (agent_loop)
AIROUTER_PORT_MODE_JSON="{\"$((RPORT+1))\": \"$MODE\"}" AIROUTER_PORT="$((RPORT+1))" \
  AIROUTER_AGENT_LOOP=1 AIROUTER_TRANSITION_FILTERS=1 \
  python3 -u "$SRC/src/ai-router-proxy.py" >"/tmp/alg-on-$((RPORT+1)).log" 2>&1 &
R_ON=$!
sleep 4

for tag in OFF ON; do
  if [ "$tag" = "OFF" ]; then P=$RPORT; PID=$R_OFF; else P=$((RPORT+1)); PID=$R_ON; fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "!! router $tag morto in boot. Log:"; tail -20 "/tmp/alg-${tag,,}-$P.log"; exit 1
  fi
done

BODY='{"model":"claude-sonnet-4-5","max_tokens":64,"messages":[{"role":"user","content":"ciao test"}]}'
echo "=== $MODE: confronto pipeline classiche (OFF) vs agent_loop (ON) ==="
FAIL=0
for tag in OFF ON; do
  if [ "$tag" = "OFF" ]; then P=$RPORT; else P=$((RPORT+1)); fi
  HTTP=$(curl -sS -o "/tmp/alg-resp-$tag.json" -w "%{http_code}" -m 25 \
    -H "Content-Type: application/json" \
    -H "X-Claude-Code-Session-Id: algtest-$MODE-$tag" \
    --data "$BODY" "http://127.0.0.1:$P/v1/messages" 2>/dev/null)
  echo "  $tag (:$P) -> HTTP $HTTP"
  [ "$HTTP" = "200" ] || FAIL=1
done

if [ "$FAIL" = "0" ]; then
  echo "PASS: entrambi ($MODE OFF/ON) rispondono 200"
  exit 0
else
  echo "FAIL: divergenza tra pipeline classiche e agent_loop"
  echo "--- log ON ---"; tail -15 "/tmp/alg-on-$((RPORT+1)).log"
  exit 1
fi
