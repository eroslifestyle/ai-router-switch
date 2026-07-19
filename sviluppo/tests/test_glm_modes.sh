#!/usr/bin/env bash
# test_glm_modes.sh — test end-to-end delle 3 modalità GLM su istanza ISOLATA.
# Lancia il proxy su porte alte (8795-8797 + dinamica 8799) SENZA toccare il
# servizio live (:8787 + 8771-8777). Verifica routing, tiering, streaming, peak.
#
# Uso: bash sviluppo/tests/test_glm_modes.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROXY="$ROOT/src/ai-router-proxy.py"
DYN_PORT=8799
declare -A TP=( [glm]=8795 [glm-minimax]=8796 [anthropic-glm]=8797 )
PORT_JSON='{"8795":"glm","8796":"glm-minimax","8797":"anthropic-glm"}'
PASS=0; FAIL=0
ok(){ echo "  ✓ $*"; PASS=$((PASS+1)); }
ko(){ echo "  ✗ $*"; FAIL=$((FAIL+1)); }

echo "═══ TEST GLM MODES (istanza isolata, porte 8795-8799) ═══"

# 1) Import moduli standalone
echo "── [1] Import moduli ──"
(cd "$ROOT/src" && python3 -c "import glm_backend, peak_scheduler; print('ok')" >/dev/null 2>&1) \
  && ok "glm_backend + peak_scheduler importabili" || ko "import moduli fallito"

# 2) Logica peak/tiering (unit, zero rete)
echo "── [2] Unit tiering + peak ──"
(cd "$ROOT/src" && python3 - <<'PY'
import sys, json
import glm_backend as gb, peak_scheduler as ps
simple=json.dumps({"messages":[{"role":"user","content":"traduci ciao"}]}).encode()
hard=json.dumps({"messages":[{"role":"user","content":"refactor architettura multi-file race condition"}]}).encode()
assert gb.heuristic_tier(simple)==gb.GLM_TIER_TURBO, "simple deve dare turbo"
assert gb.heuristic_tier(hard)==gb.GLM_TIER_TOP, "hard deve dare top"
# peak forzato
gb.is_peak_hour=lambda now=None: True
gb.should_block_glm_model=lambda m,now=None: m in ("glm-5.2","glm-5-turbo")
assert gb.apply_peak_cap("glm-5.2")==(gb._ANTHROPIC_BLOCKED,True), "5.2 peak -> anthropic"
assert gb.apply_peak_cap("glm-5-turbo")==("glm-4.7",True), "turbo peak -> 4.7"
assert gb.apply_peak_cap("glm-4.7")==("glm-4.7",False), "4.7 peak resta"
print("ok")
PY
) >/dev/null 2>&1 && ok "tiering + peak-cap corretti" || ko "unit tiering/peak fallito"

# 3) Avvio istanza isolata
echo "── [3] Avvio istanza isolata ──"
LOG_TEST="/tmp/glm-test-proxy.log"
AIROUTER_PORT=$DYN_PORT AIROUTER_PORT_MODE_JSON="$PORT_JSON" \
  PYTHONPATH="$ROOT:$ROOT/src" \
  python3 "$PROXY" >"$LOG_TEST" 2>&1 &
TEST_PID=$!
cleanup(){ kill "$TEST_PID" 2>/dev/null; wait "$TEST_PID" 2>/dev/null; }
trap cleanup EXIT
sleep 3
if ss -ltn 2>/dev/null | grep -q "127.0.0.1:$DYN_PORT "; then
  ok "istanza avviata (pid $TEST_PID) su :$DYN_PORT"
else
  ko "avvio istanza fallito — log:"; tail -15 "$LOG_TEST"; echo "PASS=$PASS FAIL=$((FAIL+1))"; exit 1
fi

# 4) Porte GLM in ascolto
echo "── [4] Porte GLM fisse ──"
for m in "${!TP[@]}"; do
  p=${TP[$m]}
  ss -ltn 2>/dev/null | grep -q "127.0.0.1:$p " && ok "porta :$p ($m) in ascolto" || ko "porta :$p ($m) assente"
done

# 5) Health con scheduling GLM
echo "── [5] /health scheduling ──"
H=$(curl -s -m5 "http://127.0.0.1:$DYN_PORT/health" 2>/dev/null)
echo "$H" | grep -q '"glm"' && ok "health espone stato GLM" || ko "health senza stato GLM"
echo "$H" | grep -q '"scheduling"' && ok "scheduling presente" || ko "scheduling assente"

# 6) Chiamata reale per ogni modalità (non-stream)
echo "── [6] Chiamata reale per modalità (non-stream) ──"
for m in glm glm-minimax anthropic-glm; do
  p=${TP[$m]}
  R=$(curl -s -m40 --compressed -X POST "http://127.0.0.1:$p/v1/messages" \
    -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" \
    -d '{"model":"claude-sonnet-4-6","max_tokens":40,"messages":[{"role":"user","content":"Rispondi solo con la parola PING"}]}' 2>/dev/null)
  if echo "$R" | grep -qiE '"type"\s*:\s*"message"|PING'; then
    ok "$m risponde ($(echo "$R" | grep -oE '"model":"[^"]*"' | head -1))"
  else
    ko "$m non risponde correttamente: $(echo "$R" | head -c 200)"
  fi
done

# 7) Streaming SSE (modalità glm)
echo "── [7] Streaming SSE (glm) ──"
S=$(curl -s -m40 -N -X POST "http://127.0.0.1:${TP[glm]}/v1/messages" \
  -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":30,"stream":true,"messages":[{"role":"user","content":"conta 1 2 3"}]}' 2>/dev/null | head -c 600)
echo "$S" | grep -q "message_start" && ok "SSE message_start ricevuto" || ko "SSE non valido: $(echo "$S" | head -c 150)"

echo ""
echo "═══ RISULTATO: PASS=$PASS FAIL=$FAIL ═══"
[ "$FAIL" -eq 0 ] && echo "✅ TUTTI I TEST PASSATI" || echo "❌ CI SONO FALLIMENTI"
exit "$FAIL"
