# Qwen token-plan — riattivazione + modalità mixaq/mixql (2026-10-06)

## Contesto
Nuovo endpoint Qwen **token-plan** (abbonamento a crediti, Anthropic-compatible):
`https://token-plan.maas.qwencloudapi.com/apps/anthropic`. Sostituisce l'host workspace
aliyuncs (`qwen_upstream()` dà precedenza a `QWEN_API_BASE`). Chiave nel vault TPM
namespace `qwen` (`QWEN_API_KEY` + `api_key`), caricata nel servizio via
`secret run minimax glm qwen`.

## Probe live (GATE) — 2026-10-06
Sonda `curl` su `/v1/messages` per modello (chiave da env, mai in log):

| Modello | Esito |
|---|---|
| qwen3.8-max | ✅ 200 |
| qwen3.8-flash | ✅ 200 |
| qwen3.7-plus | ✅ 200 |
| deepseek-v4-pro | ✅ 200 |
| deepseek-v4-flash | ✅ 200 (vision-capable) |
| glm-5.2 / glm-5.3 | ✅ 200 |
| kimi-k2.7-code | ✅ 200 |
| **qwen3-coder-plus** | ❌ 400 (NON servito) |
| **qwen3-vl-plus** | ❌ 400 (NON servito) |
| qwen3-max / qwen-flash / qwen-plus / kimi-k3 | ❌ 400 |

**Due scoperte dal gate:** `qwen3-coder-plus` (ex `QWEN_ACT`) e `qwen3-vl-plus` (vision/OCR)
NON esistono su questo endpoint. La ladder `flash → deepseek-v4-pro → qwen3.8-max` è invece
tutta confermata viva.

> Nota: le terze parti (glm-5.x, deepseek-v4-*, kimi-k2.7-code) sono incluse nel pool crediti
> del token-plan ed esposte via endpoint Anthropic-compatible (confermato dal probe).

## Decisioni implementate
- **Endpoint**: `QWEN_API_BASE` globale (drop-in `60-qwen-tokenplan.conf`). Rollback = rimuovere il drop-in + `daemon-reload` + restart.
- **Ladder ACT** (agent-side, max 2 fail su compile/test + giudizio + errore HTTP): `qwen3.8-flash → deepseek-v4-pro → qwen3.8-max`. Default `QWEN_ACT=qwen3.8-flash`.
- **Roster**: tier CODER → `deepseek-v4-pro` (leader SWE-Verified, −50% di notte).
- **mixaq**: Anthropic THINK (`/model`) + Qwen ACT; deny-mode (`ENFORCE_DENY_MODES`), in `MODES_USING_ANTHROPIC`. Porta sandbox **8789**.
- **mixql**: Qwen THINK (`qwen3.8-max`) + local ACT (`coder-next-ablit`); deny-mode. Porta sandbox **8791** (8790 occupata, errno 98). VERIFY = Qwen (solo diff). Local giù → diagnosi prima, switch a Qwen solo se irrecuperabile.
- **max_tokens** per-modello (`qwen_max_output_for`, default 131072).

## Verifica end-to-end (live, via porte modalità, senza toccare :8787)
- `8778` qwen: ACT(haiku) → `qwen3.8-flash` 200 ✓
- `8789` mixaq: ACT(haiku) → `qwen3.8-flash` 200 ✓
- `8791` mixql: THINK(opus) → `qwen3.8-max` 200 ✓
- `8791` mixql ACT(haiku→local): **timeout** (backend locale lento/giù — noto, non regressione).
- `pytest sviluppo/tests/`: intera suite verde (exit 0); `test_mixaq_mixql.py` nuovo (9 test).

Commit CORE: `ab3d5d8`. Servizio riavviato (procedura sicura, `active`, `Restart=always`).

## Probe riproducibile
```bash
QWEN_KEY=...   # dal vault: secret get qwen.QWEN_API_KEY
URL=https://token-plan.maas.qwencloudapi.com/apps/anthropic/v1/messages
for m in qwen3.8-max qwen3.8-flash deepseek-v4-pro qwen3-coder-plus qwen3-vl-plus; do
  curl -sS -o /dev/null -w "$m %{http_code}\n" -H "x-api-key: $QWEN_KEY" \
    -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
    -X POST "$URL" --data "{\"model\":\"$m\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
done
```

## TODO / aperti
- **MCP `qwen-native`** (OCR/immagini/video/audio/embed/rerank/scrape/web-search): da costruire; servizi multimediali richiedono host+workspace aliyuncs (QWEN_WORKSPACE_ID non configurato → quei tool resteranno stub finché non c'è il workspace).
- **Probe vision/OCR**: `qwen3-vl-plus` assente → OCR/VQA su `deepseek-v4-flash` (vision-capable), da confermare con un blocco image reale.
- **Finestra context** `qwen3.8-max`: in `model_context_map.py` resta 983_616 (valore storico "dal gateway"); verificare col token-plan.
- `fun-music-v1`: 404 sull'account (storico) → tool musica stub.
