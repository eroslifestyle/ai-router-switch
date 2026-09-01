# Audit velocità reale — perché il router "sembra" lento

**Data:** 2026-09-02 · **Tipo:** solo lettura, nessuna modifica al codice/config.
**Fonti:** `/home/mrxxx/.claude/logs/router-usage.jsonl(.1)` (224.996 richieste reali, 2026-06-30 → 2026-09-02, campi `ttfb_ms`/`total_ms` presenti su 79.183), lettura di `src/ai-router-proxy.py`, `src/secrets_provider.py`, `src/stream_peek.py`, `src/context_manager.py`, `src/token_counter.py`, `src/glm_backend.py`, `src/local_backend.py`, commenti storici nel codice, benchmark sintetici locali (nessuna chiamata upstream).

## 1. Il quadro generale (tutte le modalità, 79.183 richieste con timing)

| fascia total_ms | richieste | % |
|---|---:|---:|
| < 2s | 3.909 | 4,9% |
| 2–10s | 37.767 | 47,7% |
| 10–30s | 29.654 | 37,4% |
| 30–200s | 7.707 | 9,7% |
| > 200s (al cap del timeout) | 146 | 0,2% |

Solo **1 richiesta su 20 risponde sotto i 2 secondi**. L'85% sta fra 2 e 30 secondi. Su `task_class=coding` (60.429 richieste, la maggioranza del lavoro reale) il quadro è quasi identico: 4,1% sotto 2s, 87% fra 2 e 30s, 8,7% oltre 30s. Non è un'impressione: è la mediana reale di 13 giorni di uso.

## 2. La causa dominante: dimensione dello schema tool, non il proxy in sé

Isolando **solo `mode=anthropic`** (la modalità "pura", relay diretto, quella meno toccata da riscritture) e correlando `ttfb_ms` con `tools_bytes` (byte di definizioni tool spediti nel body, misurati dal sidecar):

| segmento | n | ttfb p50 | ttfb p90 |
|---|---:|---:|---:|
| `tools_bytes ≤ 40 KB` | 1.823 | **1.000 ms** | 2.658 ms |
| `tools_bytes > 40 KB` | 6.171 | **9.834 ms** | 11.736 ms |

Quasi **10x di differenza**, dentro la STESSA modalità, con lo STESSO codice di relay. Stesso pattern con `thinking_blocks` (con thinking attivo: p50 9.746ms; senza: p50 4.822ms — confuso con la dimensione del prompt, i due crescono insieme). `cache_creation` invece correla pochissimo (9.640ms vs 9.232ms) — quindi NON è principalmente un problema di cache prompt rotta.

**Lettura onesta**: un body da 40KB+ di soli schemi-tool (in questa sessione ne ho visti 200+ da MCP: mcp-video da solo espone ~250 tool) fa sì che il modello (qualunque modello, anche chiamato senza proxy) impieghi più tempo a processare il prompt prima del primo token — soprattutto con extended thinking attivo. Il router NON aggiunge quel costo: lo *misura* fedelmente. Se l'utente confronta "modello chiamato direttamente" (poche o zero tool definitions) con "Claude Code attraverso il router" (spesso 200+ tool per gli MCP eager-loaded), il confronto non è a parità di richiesta — la differenza è nel payload, non nel tunnel. Questo combacia con quanto il progetto stesso aveva già misurato l'22-08 (`MCP default eager ridotti`) e solo parzialmente corretto.

**Limite di questa analisi**: non ho un confronto diretto stesso-prompt-stesso-tool-set fatto in parallelo proxy-vs-diretto (avrebbe richiesto chiamate live a pagamento, fuori mandato "non modificare nulla / solo audit"). Quanto sopra è una correlazione forte su dati reali, non una prova di zero-overhead del proxy — vedi §3 per l'overhead che il codice del proxy aggiunge davvero, misurato.

## 3. Overhead che il router aggiunge DAVVERO (misurato nel codice, non ipotizzato)

### 3.1 Il body JSON viene fatto `json.loads()` **8 volte** nella stessa `handle()`
`src/ai-router-proxy.py` righe 278, 319, 335, 388, 660, 733, 801, 845 — più altri parse dentro `context_rewrite.py`, `context_manager.py`, `loop_breaker.py` che non ho contato. Ho fatto un benchmark sintetico locale (body 461KB, ~118K token, 60 tool schemas, 120 messaggi — vicino alla media reale di 234.678 token/richiesta misurata da `airouter-info costo`):

```
json.loads singolo: 0,77 ms
x8 parse sequenziali: 6,2 ms
```

**Verdetto onesto**: è spreco reale e sintomo di codice cresciuto per patch successive (ogni fix ha aggiunto il suo `json.loads(body)` locale invece di riusare un parse comune), ma il costo assoluto è di **millisecondi**, non la causa dei ritardi di secondi/decine di secondi osservati in §1-2. Vale la pena sistemarlo per igiene, non per velocità percepita.

### 3.2 Tutto passa da UN SOLO processo Python (event loop singolo)
`systemctl --user status ai-router` → un solo `python3` (PID 884369) serve **tutte** le 14 modalità e tutte le chat/subagenti che puntano a `:8787`. aiohttp/asyncio è single-thread: qualunque lavoro CPU-bound sincrono nella richiesta di UN chat (parse, stima token, riscrittura contesto) blocca l'event loop per TUTTI gli altri, incluse chat/subagenti indipendenti in corso nello stesso momento. Il costo per-richiesta misurato in §3.1 è piccolo, ma con Workflow/fan-out (più agenti paralleli, pattern usato spesso in questo setup) quei pochi ms si sommano per ogni richiesta in coda: non ho un dato diretto di contention a runtime (richiederebbe strumentazione live, fuori mandato), lo segnalo come rischio architetturale plausibile, non come fatto misurato.

### 3.3 `stream_peek` — buffering deliberato, cap a 20s/64KB
`src/stream_peek.py`: prima di rilasciare il primo byte al client, il router legge fino al primo `content_block_start` (o al cap). È un fix intenzionale a una regressione precedente (commento nel codice: 2026-07-22, 45s invece di ~1s con la bufferizzazione completa del vecchio codice) — quindi oggi è già la versione "veloce". Ma resta, per costruzione, più lento di un relay puro byte-a-byte: nella finestra fra "l'upstream ha iniziato a rispondere" e "il router ha visto il primo content block", il client non vede nulla. Usato su GLM/local, non su `mode=anthropic` puro (quello fa `relay()` diretto, verificato dal traceback in `logs` con header `x-ai-verified: anthropic-pure`).

### 3.4 `secrets_provider.py` — subprocess bash sul cache-miss
Cache TTL 60s (`CACHE_TTL_SEC=60`, riga 21). Sul miss, `subprocess.run(["bash", secrets_script, "get", name], timeout=5)` (riga 195) — uno spawn di processo reale sul path critico, non async. Amortizzato su 60s quindi non è un problema per-richiesta, ma la PRIMA richiesta dopo ogni scadenza cache paga uno spawn di bash sincrono dentro l'event loop condiviso (§3.2).

### 3.5 Retry/backoff con `asyncio.sleep` — reali ma condizionati
Trovati in `glm_backend.py` (righe 198, 684, 704, 739, 761, 813, 825 — con jitter fino a `step + random.uniform(0.5,2)`), `forward_minimax.py` (10s fisso riga 207, backoff riga 286), `qwen_backend.py`, `local_backend.py` (righe 414, 431, 471), `anthropic_rate_limiter.py`. Non sparano sulla richiesta felice — solo su 429/errore — ma quando scattano, **si incatenano**: più tentativi in sequenza, ognuno con la sua sleep, possono sommare svariati secondi prima che l'utente veda un errore o un successo tardivo.

## 4. Le due patologie reali (piccole in volume, enormi in impatto quando capitano)

### 4.1 146 richieste (0,06%) appese fino al cap del timeout
`total_ms` fra 200.000 e 241.117 ms — **il tetto esatto** di `NON_STREAM_SOCK_READ_SEC`/`STREAM_SOCK_READ_SEC`. Distribuzione: 63 su `local:code-max`, 63 su `MiniMax-M2.7`, 9 su `claude-direct`, resto su GLM. Casi tipici (`mode=gpt`/`local`, backend `local:code-max`): `status=200`, `outcome="empty"`, `output_tokens=88` per **241 secondi** di attesa. Il router non fallisce velocemente: aspetta l'intero timeout prima di restituire una risposta quasi vuota con status 200 (non un errore!) — questo è probabilmente il singolo pattern più dannoso per la percezione di lentezza quando capita, perché blocca la sessione per 4 minuti senza nemmeno segnalare un errore chiaro.

### 4.2 Fascia 30–200s: 7.707 richieste (9,7% del totale)
Concentrata su `mix-am-2` (4.117), `mix-ag-2` (1.191), `anthropic` (997), `mix-gm` (437). Questa fascia — non il caso limite del 200s+ — è probabilmente ciò che l'utente percepisce come "lento" nell'uso quotidiano: capita circa 1 richiesta su 10.

### 4.3 Errori più lenti dei successi, e di molto
Confronto `total_ms` mediano fra richieste andate a buon fine (status 200) e fallite, per modalità:

| mode | tot_ms mediana (200 OK) | tot_ms mediana (errore) |
|---|---:|---:|
| anthropic | 14.052 | 10.863 |
| mix-am | 12.931 | **124.903** |
| mix-am-2 | 9.036 | 2.641 |
| mix-gm | 11.041 | 2.493 |

`mix-am` in particolare: quando fallisce, la mediana è **125 secondi** prima di scoprirlo — quasi certamente le catene di retry/backoff di §3.5 sommate prima della resa finale.

## 5. Dato di contesto già noto nel codice (non ri-misurato qui, citato con fonte)
`src/router_utils.py` righe 34-40: misura del 2026-08-16 su 9.898 richieste MiniMax degli ultimi 21 giorni — TTFB dei successi fino a **87,6s** (p99 22,0s), con 333 timeout `"Timeout on reading data from socket"` fra il 26/07 e il 14/08 in modalità `minimax`. Conferma indipendente, con altra fonte, che la modalità minimax ha una coda di TTFB pesante da tempo — coerente con quanto ritrovato qui in §4.

## 6. Sintesi per priorità (cosa pesa di più sulla velocità percepita, in ordine)

1. **Tool-schema payload enorme (>40KB, spesso 200+ tool MCP)** → 10x TTFB anche in modalità pura. Non è colpa del tunnel, è il prompt che si manda. Leva più efficace: ridurre gli MCP eager-loaded (il progetto lo sapeva già dal 22-08, l'ha fatto solo parzialmente).
2. **Richieste appese fino al timeout con esito "empty"/status 200** (§4.1) — piccole in numero, devastanti quando capitano: 4 minuti bloccati senza un errore chiaro.
3. **Retry/backoff incatenati su errore** (§3.5, §4.3) — su `mix-am` un fallimento costa 125s mediani prima di arrendersi.
4. **Fascia 30-200s** (9,7% delle richieste) — il "lento ma non rotto" quotidiano.
5. **Overhead strutturale del router stesso** (8x parse ridondante, single-process) — reale, misurato, ma dell'ordine dei millisecondi: non spiega i secondi/decine di secondi osservati. Vale come pulizia, non come causa della lentezza percepita.

## 7. Cosa NON è provato qui (onestà sui limiti dell'audit)
- Nessun confronto diretto stesso-prompt/stesso-tool-set proxy-vs-API-diretta: servirebbe un test A/B live (costo token reale), fuori mandato per questo giro ("non modificare nulla").
- Nessuna misura diretta di event-loop contention sotto carico concorrente reale (richiederebbe strumentazione a runtime).
- Il costo del rewrite_for_context (compressione contesto vicino al limite) non è stato benchmarkato: si attiva solo condizionalmente (vicino al limite di contesto), andrebbe profilato a parte se si vuole un numero preciso.

## 8. Prossimi passi suggeriti (nessuno eseguito — solo audit)
- Test A/B controllato: stesso body (con e senza i 200+ tool MCP) via `mode=anthropic` vs chiamata diretta ad Anthropic, per isolare l'overhead vero del tunnel dal costo del prompt.
- Instrumentare `outcome=empty` + `total_ms` vicino al cap (§4.1) con un fail-fast (timeout più corto specifico per quel pattern) invece di aspettare 240s.
- Profilare `mix-am` sul path di errore per capire quali retry si incatenano fino a 125s mediani.
- Consolidare gli 8 `json.loads(body)` in un parse unico salvato su `request` — spreco piccolo ma gratuito da eliminare.
