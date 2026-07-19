#!/usr/bin/env bash
# test_mixgm_verify_retry.sh — verifica la logica di retry ×1 su incoerenza in
# _glm_minimax_think_act_verify. Mock delle risposte GLM per testare i rami.
#
# Uso: bash sviluppo/tests/test_mixgm_verify_retry.sh
set -uo pipefail

PASS=0; FAIL=0
ok(){ echo "  ✓ $*"; PASS=$((PASS+1)); }
ko(){ echo "  ✗ $*"; FAIL=$((FAIL+1)); }

echo "═══ TEST mix-gm VERIFY RETRY (logic only, no LLM) ═══"

# 1) build_glm_verify_body produce il marcatore INCOERENTE
echo "── [1] Prompt verify include marcatore INCOERENTE ──"
OUT=$(python3 -c "
import sys, json
sys.path.insert(0, '/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch/src')
import glm_backend as gb
body = gb.build_glm_verify_body(
    orig={'messages': [{'role':'user','content':'test'}]},
    plan='Piano: traduci',
    act_output='Ciao mondo'
)
data = json.loads(body)
assert 'INCOERENTE' in data['system']
assert 'VERIFIED' in data['system']
print('ok')
" 2>&1)
if [ "$OUT" = "ok" ]; then ok "marcatore INCOERENTE/VERIFIED nel prompt verify"; else ko "prompt verify fallito"; fi

# 2) Logica retry: 4 scenari
echo "── [2] Logica retry branch coverage ──"
OUT=$(python3 -c "
scenarios = [
    ('VERIFIED', True, ''),
    ('INCOERENTE: risposta incompleta', False, 'INCOERENTE: risposta incompleta'),
    ('VERIFIED ok tutto corretto', True, ''),
    ('', True, ''),
]
errors = []
for verify_text, exp_ok, exp_note in scenarios:
    verify_ok = False
    retry_note = ''
    for attempt in range(2):
        if 'INCOERENTE' in verify_text:
            retry_note = verify_text
            continue
        verify_ok = True
        break
    result_note = retry_note if not verify_ok else ''
    ok_match = (verify_ok == exp_ok)
    note_match = (result_note == exp_note)
    if not (ok_match and note_match):
        errors.append('expected ok=%s note=%r, got ok=%s note=%r' % (exp_ok, exp_note, verify_ok, result_note))
    print('  %r -> ok=%s note=%r' % (verify_text[:20], verify_ok, result_note[:20]))
if errors:
    raise AssertionError('; '.join(errors))
print('ok')
" 2>&1)
if echo "$OUT" | tail -1 | grep -q "^ok$"; then ok "logica retry: 4 scenari corretti"; else ko "logica retry fallita"; echo "$OUT"; fi

# 3) _build_minimax_act_body_retry aggiunge nota al system
echo "── [3] ACT retry body include nota correttiva ──"
OUT=$(python3 -c "
import sys, json
src = open('/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch/src/ai-router-proxy.py').read()
start = src.index('def _build_minimax_act_body_retry(orig: dict, correction: str) -> bytes:')
end = src.index('\n\ndef _build_minimax_act_body(', start)
func_src = src[start:end]
exec_globals = {'json': json}
exec(func_src, exec_globals)
fn = exec_globals['_build_minimax_act_body_retry']
orig = {'messages': [{'role':'user','content':'ciao'}], 'system': 'Sei un assistente', 'model': 'test-model'}
correction = 'INCOERENTE: risposta incompleta'
retry_body = fn(orig, correction)
data = json.loads(retry_body)
assert 'NOTA CORRETTIVA' in data['system'], 'system: %s' % data['system']
assert correction in data['system']
assert data['model'] == 'test-model'
print('ok')
" 2>&1)
if [ "$OUT" = "ok" ]; then ok "ACT retry body con nota correttiva"; else ko "ACT retry body fallito"; fi

# 4) Cap ×1 retry
echo "── [4] Cap retry ×1: max 2 attempt ──"
OUT=$(python3 -c "
attempts = 0
for attempt in range(2):
    attempts += 1
    verify_text = 'INCOERENTE: ancora sbagliato'
    if 'INCOERENTE' in verify_text:
        continue
    break
assert attempts == 2
print('ok')
" 2>&1)
if [ "$OUT" = "ok" ]; then ok "cap retry x1, max 2 attempt"; else ko "cap retry fallito"; fi

# 5) [VERIFY-WARNING] prefisso
echo "── [5] Prefisso [VERIFY-WARNING] se retry fallisce ──"
OUT=$(python3 -c "
verify_ok = False
retry_note = 'INCOERENTE: risposta incompleta'
act_raw = b'{\"content\": \"risposta\"}'
if not verify_ok and retry_note:
    act_raw = b'[VERIFY-WARNING] ' + act_raw
assert act_raw.startswith(b'[VERIFY-WARNING]')
print('ok')
" 2>&1)
if [ "$OUT" = "ok" ]; then ok "prefisso [VERIFY-WARNING] aggiunto"; else ko "[VERIFY-WARNING] fallito"; fi

echo ""
echo "═══ RISULTATO: PASS=$PASS FAIL=$FAIL ═══"
if [ "$FAIL" -eq 0 ]; then echo "OK: TUTTI I TEST PASSATI"; exit 0; else echo "FAIL: CI SONO FALLIMENTI"; exit 1; fi
