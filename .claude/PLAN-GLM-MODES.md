# PIANO — 3 nuove modalità GLM per ai-router-proxy

> Definito con 10 sessioni AskUserQuestion (2026-07-10). Endpoint z.ai **Anthropic-compatible** verificato in pratica (curl OK su glm-5.2/glm-5-turbo/glm-4.7, streaming SSE nativo, reasoning_effort accettato).

## Fatti verificati (evidence-gate)
- `https://api.z.ai/api/anthropic/v1/messages` risponde in formato Anthropic nativo → GLM è backend gemello di MiniMax. **Nessun adapter OpenAI necessario.**
- Auth: `Authorization: Bearer <GLM_API_KEY>` + `anthropic-version: 2023-06-01`.
- Modelli confermati: `glm-5.2` (1M ctx), `glm-5-turbo` (200K), `glm-4.7` (200K).
- Peak z.ai = 14:00–18:00 Asia/Shanghai → Italia 08–12 (estate) / 07–11 (inverno). Solo `glm-5.2`/`glm-5-turbo` = 3x in peak; `glm-4.7` assunto non-3x.
- Limiti RPM/TPM GLM NON documentati → rate limiter con default conservativi via env (stimati).

## Le 3 modalità
| Nome CLI | Ruolo | Pipeline |
|---|---|---|
| `glm` | Solo GLM, 5.2 orchestra tiering | GLM-5.2 classifica → esegue col tier (turbo→4.7→5.2) |
| `glm-minimax` | GLM-5.2 orchestra, MiniMax esegue | GLM-5.2 THINK → MiniMax ACT → GLM verify (solo task complessi, 1 iter) |
| `anthropic-glm` | Anthropic orchestra, GLM esegue | Anthropic(modello client) THINK → GLM tiered ACT → Anthropic verify (solo T2 critici) |

## Decisioni chiave
- **Chiave**: env `GLM_API_KEY` → fallback `secrets.sh get glm.api_key`. Mai hardcoded.
- **Classificazione (mod 1)**: GLM-5.2 classifica (JSON {tier}); pre-filtro euristico locale (dimensione prompt, tool/codice, keyword difficoltà, reasoning richiesto). Classifica ko → fallback euristica locale.
- **Escalation tier**: dopo 2 fallimenti → tier superiore. Promozione vale solo per la richiesta corrente.
- **reasoning_effort per tier**: turbo=low, 4.7=medium, 5.2=high.
- **Logica peak** (dinamica da 14–18 Asia/Shanghai): in peak tier cappato a 4.7. Classifica chiede turbo→usa 4.7; chiede 5.2→**Anthropic esegue**. Off-peak: tiering pieno.
- **Fallback errore/quota** (off-peak): catena GLM→MiniMax→Anthropic. Quota 5h esaurita (429 'resets at')→fallback+alert desktop.
- **mod 2 verify**: solo task complessi/agentici, 1 giro correzione ridelegato a MiniMax poi accetta.
- **mod 3 verify**: solo task critici (T2, come inverse). Orchestratore = modello Anthropic richiesto dal client.
- **Cost tracking**: log tier+peak-flag+moltiplicatore in router-usage.jsonl. Solo log (no soglia alert ora). Promo off-peak 1x fino 2026-09-30 → warning informativo con scadenza.

## Struttura codice (rollout: branch + istanza test su porte alte, zero tocco a :8787)
- **NUOVO** `src/glm_backend.py`: `forward_glm`, `get_glm_key`, tiering, classificazione, `GlmRateLimiter` (default env conservativi).
- **NUOVO** `src/peak_scheduler.py`: `is_peak_hour()`, `peak_tier_cap()`, promo-window helper (riutilizzabile).
- **MODIFICA** `src/ai-router-proxy.py`: `VALID_MODES` += 3; `PORT_MODE` += 8775/8776/8777; 3 rami `if mode ==` in `handle()`; `parse_router_command`/`_NL_MODE` estesi; usage log GLM. **Zero modifiche ai path anthropic/minimax/mixed/inverse.**
- **MODIFICA** `scripts/ai-mode`: nuovi arg CLI. `scripts/ai-stack-guard.sh`: monitora 8775-8777.
- **MODIFICA** `router-mode/card.py` + `routestats`: mostra nuove modalità.
- **NUOVO** `sviluppo/test_glm_modes.sh`: test automatico (3 modalità, peak/off-peak, fallback, tiering, streaming+non-stream).
- **DOCS**: README progetto + AI-ROUTER-POLICY + CLAUDE.md progetto.
- **MEMORIA**: file design + checkpoint fine lavoro.

## Ordine: moduli condivisi completi (glm_backend + peak_scheduler) → aggancio 3 modalità in blocco → test → docs → commit.
