#!/usr/bin/env bash
# test_glm_modes.sh — test end-to-end delle 3 modalità GLM su istanza ISOLATA.
# Nomi di modalita' aggiornati ai canonici il 2026-08-03: i legacy glm-minimax e
# anthropic-glm sono stati rinominati mix-gm/mix-ag il 2026-07-26 e da allora
# AIROUTER_PORT_MODE_JSON li scartava (filtra su VALID_MODES), quindi le porte
# 8796/8797 non venivano mai aperte e il test falliva sempre su quelle.
# Lancia il proxy su porte alte (8795-8797 + dinamica 8799) SENZA toccare il
# servizio live (:8787 + 8771-8777). Verifica routing, tiering, streaming, peak.
#
# Uso: bash sviluppo/tests/test_glm_modes.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROXY="$ROOT/src/ai-router-proxy.py"
DYN_PORT=8799
declare -A TP=( [glm]=8795 [mix-gm]=8796 [mix-ag]=8797 )
PORT_JSON='{"8795":"glm","8796":"mix-gm","8797":"mix-ag"}'
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
import glm_backend as gb
import peak_scheduler as ps

# heuristic_tier e' basato sulla DIMENSIONE del body (vedi la sua docstring),
# non sul contenuto: le soglie sono 800.000 e 150.000 caratteri.
piccolo = json.dumps({"messages": [{"role": "user", "content": "traduci ciao"}]}).encode()
medio = b'x' * 200_000
grande = b'x' * 900_000
assert gb.heuristic_tier(piccolo) == gb.GLM_TIER_MID, "body piccolo deve dare MID"
assert gb.heuristic_tier(medio) == gb.GLM_TIER_TURBO, "body >150K deve dare TURBO"
assert gb.heuristic_tier(grande) == gb.GLM_TIER_TOP, "body >800K deve dare TOP"

# apply_peak_cap prende un TIER e consulta peak_scheduler con import lazy:
# va patchato li', non su glm_backend.
ps.should_block_glm_model = lambda tier, now=None: tier in (gb.GLM_TIER_TOP, gb.GLM_TIER_TURBO)
assert gb.apply_peak_cap(gb.GLM_TIER_TOP) == (gb.GLM_TIER_MID, True), "TOP in peak -> MID"
assert gb.apply_peak_cap(gb.GLM_TIER_TURBO) == (gb.GLM_TIER_MID, True), "TURBO in peak -> MID"
assert gb.apply_peak_cap(gb.GLM_TIER_MID) == (gb.GLM_TIER_MID, False), "MID resta MID"
assert gb.apply_peak_cap(gb.GLM_TIER_VISION) == (gb.GLM_TIER_VISION, False), "VISION esente"
print("ok")
PY
) >/dev/null 2>&1 && ok "tiering per dimensione + peak-cap corretti" || ko "unit tiering/peak fallito"

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

# Helper per rate limit (non conteggiato come FAIL)
skip(){ echo "  ~ $* (non conteggiato)"; }

# 6) Chiamata reale per ogni modalità (non-stream)
echo "── [6] Chiamata reale per modalità (non-stream) ──"
for m in glm mix-gm mix-ag; do
  p=${TP[$m]}
  R=$(curl -s -m40 --compressed -X POST "http://127.0.0.1:$p/v1/messages" \
    -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" \
    -d '{"model":"claude-sonnet-4-6","max_tokens":40,"messages":[{"role":"user","content":"Rispondi solo con la parola PING"}]}' 2>/dev/null)
  if echo "$R" | grep -qiE '"type"\s*:\s*"message"|PING'; then
    ok "$m risponde ($(echo "$R" | grep -oE '"model":"[^"]*"' | head -1))"
  elif echo "$R" | grep -qi 'rate_limit_error'; then
    skip "$m: rate limit UPSTREAM, non un difetto del router — $(echo "$R" | head -c 120)"
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
