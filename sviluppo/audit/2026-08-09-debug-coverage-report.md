---
name: 2026-08-09-debug-coverage-report
description: Report coverage telemetria, bug reali e gap resilienza del router AI su 12 modalità (24h log)
updated: 2026-08-09
metadata:
  type: project
---

# Report: Coverage Debug & Resilienza Router — 2026-08-09

## Executive Summary

Il router ha telemetria base uniforme (`router-usage.jsonl` con `mode`+`outcome` per tutte le 12 modalità) ma il sistema di **event-specific debug è fortemente sbilanciato**: GLM ha 8 kind di evento + empty-detection inline + retry, mentre Local ha 0 eventi e Anthropic/MiniMax loggano solo errori di relay (non empty/truncated). Risultato: il blocco minimax "200-ma-vuoto da thinking truncation" (45/611 richieste M3, oggi) era invisibile finché non analizzato a posteriori dal sidecar. **Azione prioritaria**: generalizzare `_glm_is_empty` a tutti i backend + aggiungere `record_event` a `local_backend.py` e nei forward anthropic/minimax.

## 1. Coverage Telemetria per Modalità

| Modalità | router-usage | record_event kind | empty-detection inline | rate limiter | shrink testo | retry su empty |
|---|---|---|---|---|---|---|
| **glm** | completa | 8 (empty/exhausted/block/error/quota/guardrail) | SI `_glm_is_empty` | SI `_classify_429` | NO (solo clamp max_tokens) | SI |
| **qwen** | completa | 4 (empty/truncated/relay_502) | NO | SI `_classify_429` | NO | NO |
| **minimax** | completa | 9 (solo relay_error_502) | NO | SI MinimaxRateLimiter | SI `_try_shrink_body` | NO |
| **anthropic** | completa | 9 (solo relay_error_502) | NO | NO (solo retry reattivo) | NO (solo strip immagini) | NO |
| **local** | completa | 0 | NO | NO | NO | NO |
| mix-am/mix-gm/mix-ag (+ -2) | completa | 6 (via relay, category="mix") | indiretta via relay | n/a | n/a | n/a |
| mix-al | completa | 0 (local) | NO | NO | NO | NO |

## 2. Bug & Errori Reali (24h di log)

Dataset: 5000 richieste router-usage.jsonl, 1887 debug-errors.jsonl, 2278 debug-events.jsonl.

Top 5 errori (debug-errors.jsonl):
1. "Invalid signature in thinking" (922 occorrenze tra 2 varianti: 677+245) — leaking signature thinking Anthropic verso upstream non-Anthropic
2. "boom" (266) — errore generico di test/upstream
3. corpo vuoto (137)
4. unexpected tool_use_id found (110) — id tool non conformi
5. relay_error_400 (1244 occorrenze in debug-events, senza category popolata)

Pattern critici:
- Retry-storm mix-am-2: 146 cluster chat+orig entro 30s coprono 3284/3319 richieste (99% del traffico mode)
- 1517/2278 eventi senza category (67%): i rami relay_error_400/relay_error_529/sse_truncated in streaming_relay.py non settano category
- Mix-gm-2 ha 4.6x piu risposte vuote di mix-am-2 (89/711 = 12.5% vs 88/3319 = 2.7%)
- 4 modalita mute nelle ultime 24h: anthropic, qwen, mix-al, local (0 richieste)

## 3. Latenze per Modalità (p50/p90/p99 total_ms)

| Mode | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| glm | 9 | 1032 | 1140 | 1433 | 1466 |
| mix-ag | 2 | 2356 | 2451 | 2472 | 2475 |
| mix-am-2 | 3319 | 7376 | 23951 | 71478 | 195107 |
| mix-gm-2 | 711 | 7546 | 32611 | 88727 | 273944 |
| minimax | 251 | 9322 | 22855 | 49505 | 78464 |
| mix-am | 127 | 12799 | 27799 | 53707 | 69826 |
| mix-gm | 579 | 13828 | 38783 | 85720 | 229976 |
| mix-ag-2 | 2 | 61950 | 109929 | 120724 | 121924 |

Piu lenta a volume reale: mix-gm (p50 14s, p99 86s). Piu veloce: glm (p50 1s, ma n=9). Max 274s in mix-gm-2 indica timeout runaway.

## 4. Gap di Resilienza (cosa ha GLM e manca agli altri)

| Meccanismo | GLM | MiniMax | Qwen | Anthropic | Local |
|---|---|---|---|---|---|
| Empty-detection inline (_is_empty) | SI | NO | NO | NO | NO |
| Rate limiter preventivo | SI | SI | SI | NO | NO |
| Classificazione 429 (token_plan vs rpm) | SI | SI | NO | NO | NO |
| Shrink testo adattivo | NO | SI | NO | NO | NO |
| Retry su empty-response | SI | NO | NO | NO | NO |
| record_event coverage | 8 kind | 9 kind (solo 502) | 4 kind | 9 kind (solo 502) | 0 kind |

## 5. Raccomandazioni Autofixing/Performance/Resilienza

Priorita ALTA:

1. Generalizzare _glm_is_empty in router_utils._is_empty_response(provider, body, output_tokens), chiamato in streaming_relay.py:520 per ogni stream con output_tokens<=5 o text_blocks==0. Genera record_event(category=<mode>, kind="empty_response_<mode>") per tutte. Stesso pattern del fix GLM commit 703f940. Impact: +45 empty/611 visibili e ritentati in minimax, +89 in mix-gm-2.

2. Aggiungere record_event empty/truncated a forward_anthropic.py e forward_minimax.py (oggi solo relay_error_502). I kind empty_response_anthropic/empty_response_minimax/truncated_response_minimax esistono gia in _KINDS ma non hanno call site nei forward. Impact: modalita pure anthropic/minimax generano eventi empty propri.

3. Aggiungere record_event a local_backend.py (0 eventi oggi). Importare debug_catalog e loggare errori/empty/truncated/timeout con category="local". Kind gia definiti: empty_response_local, truncated_response_local, relay_error_502_local_empty, quota_429_local. Impact: modalita local/mix-al escono dal buio.

Priorita MEDIA:

4. Fixare "Invalid signature in thinking" (922 occorrenze): strip dei thinking blocks Anthropic prima di forwardare a upstream non-Anthropic. Cercare in streaming_relay.py o forward_minimax.py dove i thinking vengono inoltrati. Impact: -922 errori/giorno.

5. Popolare category nei 1517 eventi relay senza categoria (streaming_relay.py): i rami relay_error_400/relay_error_529/sse_truncated non settano category. Derivare dal provider/mode della richiesta. Impact: telemetria analyzizzabile per mode.

6. Creare monitor minimax/qwen/anthropic/local speculari a sviluppo/audit/2026-08-09-glm-empty-monitor/. Impact: strumento diagnostico dedicato per ogni modalita.

Priorita BASSA:

7. Rate limiter preventivo per Anthropic (oggi solo retry reattivo su 429).
8. Shrink testo per Anthropic/GLM/Qwen (oggi solo MiniMax ha _try_shrink_body).

## 6. Dataset & Metodo

- Fonti: ~/.claude/logs/router-usage.jsonl (5000 righe, 2026-08-08 21:19 -> 2026-08-09 22:15), logs/debug-errors.jsonl (1887), logs/debug-events.jsonl (2278).
- Metodo: 3 agenti di indagine paralleli su fronti distinti — (a) analisi log empirica per modalita, (b) mappa superfici retry/self-healing/resilienza, (c) mappa call site record_event + lacune. Sintesi orchestratore.
- Verifiche: ogni finding ha file:riga verificato. Nessuna invenzione: se una modalita ha 0 richieste, e dichiarato.
- Limiti: 4 modalita mute (anthropic/qwen/mix-al/local) non hanno dati empirici recenti — le raccomandazioni per quelle si basano su audit statico del codice.

**Why:** La telemetria del router è uniforme in superficie (tutte le 12 modalità emettono `router-usage.jsonl`) ma il debug specifico per evento è crollato su 4 modalità (anthropic/qwen/mix-al/local), rendendo 922 "Invalid signature in thinking" e 45 risposte vuote minimax da thinking truncation invisibili al sistema.

**How to apply:** Eseguire le 3 priorità ALTA in ordine: (1) generalizzare `_is_empty_response` in [[router_utils]] con call site in streaming_relay.py:520 — riusa il pattern del fix GLM commit 703f940; (2) aggiungere call site `record_event` (kind già definiti in `_KINDS`) in [[forward_anthropic]] e [[forward_minimax]]; (3) instrumentare [[local_backend]] con `debug_catalog`. Poi fixare il leak thinking blocks (priorità MEDIA #4) che da solo copre 922 errori/giorno.
