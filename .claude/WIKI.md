---
name: ai-router-switch-wiki-consolidata-20260727-20260803
description: Wiki consolidato delle campagne di lavoro 27/07 → 03/08 2026 sul proxy ai-router-switch: architettura, sei modalità, 49 commit, lezioni, divieti, comandi di verifica.
updated: 2026-08-03
metadata:
  type: project
---

# ai-router-switch — Wiki consolidato (27/07 → 03/08 2026)

**Why:** conservare in un'unica pagina le decisioni, i fix, le root cause e i divieti operativi emersi nelle otto campagne del periodo, per evitare di reintrodurre errori già analizzati e rendere verificabile lo stato del proxy.

**How to apply:** leggere la sezione "Comandi di verifica" per stabilire la baseline; prima di toccare il proxy rileggere i "Divieti operativi consolidati" e le "Lezioni trasversali"; per dettagli su un singolo fix usare i wikilink di sezione 9.

---

## 1. Stato al 2026-08-03

- Branch `main`, HEAD `6049d01` ("docs(todo): chiusa la voce mix-am 'MiniMax allucina o risponde vuoto' + nuova voce su TRUNCATE_MAX_LEN", 2026-08-02 17:50:51 +0200).
- Working tree: unico file modificato ` M BUG-CATALOG.md`, modifica preesistente e fuori scope, da non committare.
- Suite test: `python3 -m pytest -q` dalla root → **168 passed, 0 errors** (10,79 s). `python3 -m pytest sviluppo/tests/ -q` → **116 passed, 0 errors**.
- Servizio `ai-router` (systemd utente): `active`, `NRestarts=0`.
- `curl http://localhost:8787/health` → **200**.
- Modalità router globale attiva: `mix-am`.
- Commit nel periodo 27/07 → 03/08: **49**.
- Checkpoint di sessione nel periodo: **10** file in `.claude/checkpoints/`, dal `CP_20260727_1600.md` al `CP_20260802_1743.md`, più `archivio-checkpoint-20260623-20260726.tar.gz` che contiene i 97 checkpoint precedenti.

---

## 2. Che cos'è il progetto

`ai-router-switch` è un proxy aiohttp in ascolto su `127.0.0.1:8787` che instrada le richieste di Claude Code verso tre provider: Anthropic, MiniMax e GLM/z.ai.

Principio fondante, deciso il 2026-07-25: **il router è un tunnel trasparente**. Guarda quale modello è richiesto e quale modalità è attiva, riscrive il campo `model` del body e inoltra. Non orchestra fasi, non tiene stato, non conta fallimenti. La gerarchia THINK / ACT / VERIFY vive solo in `~/.claude/CLAUDE.md`, mai nel proxy. Il refactor `99dcc0d` (25/07) ha rimosso circa 1.900 righe di pipeline server-side. La tabella di rimappatura è `src/role_routing.py`, funzione pura coperta da 48 test in `tests/test_role_routing.py`.

Porte in ascolto: **8787** (dinamica, segue la modalità globale) più sei porte per-modalità isolate: **8771** anthropic, **8772** minimax, **8773** mix-am, **8775** glm, **8776** mix-gm, **8777** mix-ag. La 8774 è libera: era `inverse`, modalità rimossa il 2026-07-26.

Deploy a symlink: `~/.claude/scripts/*.py` puntano a `src/`, mai copie. Il servizio gira con `WorkingDirectory=<repo>/src` e `Restart=always`.

---

## 3. Le sei modalità

`VALID_MODES = ("anthropic", "minimax", "mix-am", "mix-ag", "mix-gm", "glm")` in `src/router_constants.py`. `mixed`, `glm-minimax`, `anthropic-glm` sono alias accettati solo dal CLI `ai-mode`, non da `!router`.

| modalità | THINK | esegue | conseguenza |
|---|---|---|---|
| anthropic | Fable/Opus/Sonnet, scelta manuale | Haiku | chi verifica ha anche pensato |
| minimax | M3 | M2.7 | idem |
| glm | GLM-5.2 | glm-4.7 | GLM puro, nessun fallback cross-provider |
| mix-am | Anthropic | MiniMax M2.7 | chi verifica non è chi ha eseguito |
| mix-ag | Anthropic | GLM | idem |
| mix-gm | GLM-5.2 | MiniMax M2.7 | mai Anthropic nella catena |

Isolamento per-chat: `!router <mode>` vale per la singola sessione (header `X-Claude-Code-Session-Id`, store `~/.claude/ai-router-chats.json`, chiave `sid:<uuid>`); `ai-mode <mode>` è globale (`~/.claude/ai-router-mode`). Senza header, il fingerprint per-chat si ricava dal content-hash di `system` più il primo messaggio.

---

## 4. Mappa dei componenti

Trappola strutturale ricorrente: **`streaming_relay.py` e `fail_tracker.py` stanno nella ROOT del repo, non in `src/`**. Cercarli con `ls src/` dà un falso negativo. Sono importati per `sys.path`.

Moduli citati nelle campagne di questo periodo:

- `src/role_routing.py` — rimappatura pura modalità×modello → provider. Funzioni `resolve_route()`, `model_provider()`, `_nativize()`, costante `_NATIVE_EXECUTOR`.
- `src/ai-router-proxy.py` — handler principale. `CTX_GATE_HEARTBEAT_PCT = 0.30` alla riga 145, env `AIROUTER_CTX_HEARTBEAT_PCT`. Sessione condivisa con `ClientTimeout(connect=30, sock_read=120, sock_connect=15)` alla riga 914.
- `src/context_rewrite.py` — riscrittura del contesto. `KEEP_RECENT_IMAGES = 2`, `TOOL_RESULT_MAX_CHARS = 4000`, funzioni `rewrite_for_context()`, `_build_system()`, `_rewrite_impl()`.
- `src/context_manager.py` — budget di output.
- `src/token_counter.py` — stima token per provider. `IMAGE_TOKEN_COST = 1600`, `estimate_tokens_body()`, `bytes_per_token()`.
- `src/model_context_map.py` — unica fonte di verità sui context window.
- `src/tool_isolation.py` — isolamento tool per provider. `filter_tools_for_backend()`, `brand_of_tool_name()`, `detect_foreign_tool_use()` alla riga 179.
- `src/cache_optimizer.py` — modulo nuovo del 01/08, `ensure_tail_cache_breakpoint()`.
- `src/forward_anthropic.py`, `src/forward_minimax.py`, `src/glm_backend.py` — i tre forward.
- `src/pipeline_anthropic.py` (`_try_shrink_body_haiku`), `src/pipeline_minimax.py` (`_try_shrink_body`, `_effective_minimax_model()`).
- `src/minimax_body.py`, `src/trim_smart.py` (`_smart_sample_middle`), `src/router_utils.py` (`log_router_usage`, `collect_tools_stats`, `upstream_timeout_for`), `src/debug_catalog.py` (`_sanitize_snippet`), `src/router_debug.py`.
- `src/self_healing/` — package nuovo del 31/07: `sensor.py`, `watcher.py`, `m3_source.py`, `auto_fixer.py`. Attenzione: `router_policy.py` **non** sta nel package, è `src/router_policy.py`, importato dal proxy alla riga 719 (`from router_policy import is_degraded`).
- `src/peak_scheduler.py` — fascia peak GLM 14-18 Asia/Shanghai.
- `sviluppo/tools/check_model_context.py`, `sviluppo/tools/compact_correlate.py` — strumenti offline.

---

## 5. Cronologia delle campagne 27/07 → 03/08

### Campagna A — 27/07 — Consolidamento e chiusura di G4 (5 commit)

Commit: `1a683a9`, `ceedeb4`, `61f25f9`, `e1284e7`, `56474e6`.

`1a683a9` (checkpoint unico consolidato + TODO unificato); `61f25f9` (correlatore `compact_correlate`); `e1284e7` (G4 chiusa).

I 97 checkpoint di sessione dell'11-26 luglio sono stati consolidati in un unico file e archiviati. G4 è stata chiusa **senza toccare il proxy**: il marker che Claude Code usa per `/compact` è stato ricavato dal binario del CLI ed è la stringa `CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.` Il correlatore `sviluppo/tools/compact_correlate.py` (sola lettura) ha esaminato i 5 compact storici: 3 INTATTO, 2 non applicabili perché del 17/07, cioè architettura pre-refactor-tunnel. Zero mutilazioni sul codice attuale.

### Campagna B — 27-28/07 — Fix al context rate: F1, F8, F9, F10, F11 (7 commit)

Commit: `f05ea98` (F1), `d5b3664` (F8), `f33176a`, `ccb1fc3` (F9), `2f7f0cc` (F10), `49c26a3` (F11), `a935a00`.

- **F1** `f05ea98` — `src/context_rewrite.py._build_system()` preservava la lista `system` byte per byte invece di appiattirla con `json.dumps`; il summary va in coda dopo i breakpoint di `cache_control`. Prima, il `json.dumps` di ogni item appiattiva i blocchi e distruggeva i `cache_control`. Test nuovo `sviluppo/tests/test_cache_control_preserved.py`, 5 test.
- **F8** `d5b3664` — `ctx_model = _early_override or _early_model`: il gate di contesto misura il limite sul modello destinatario reale, non sul default per provider. Prima, per Anthropic usava sempre `claude-opus-4-8` con safe limit 800k. Riproduzione pre-fix: una richiesta a Haiku da 208.904 token passava intatta e Anthropic rispondeva `400 prompt is too long: 208904 tokens > 200000 maximum`.
- **F9** `ccb1fc3` — `src/token_counter.py` usa il tokenizer del provider. Byte per token misurati su un payload di 50.107 byte via `/v1/messages/count_tokens`: Anthropic nuovo **2,55**, Anthropic vecchio **3,53**, MiniMax **3,85**, GLM **4,06**. Test nuovo `test_token_estimate_per_model.py`, 7 test.
- **F10** `2f7f0cc` — context window dalle doc ufficiali: MiniMax-M3 = 1.000.000 (non 200.000); M2.7/M2.5/M2 = 204.800; glm-4.7 = 200.000 (non 128.000); glm-5.2 = 1M; glm-5-turbo = 200.000. Eliminata la doppia tabella `glm_backend._GLM_CONTEXT_LIMITS`, che diceva 115.000 per glm-4.7.
- **F11** `49c26a3` — il buffer per l'output è il `max_tokens` richiesto con minimo 8192, non un `BUFFER_PERCENT = 20` fisso. Sui modelli da 1M dà +168.000 token di contesto utile per richiesta e sposta la compressione dall'80% al 96,8%.

Tabella finale dei limiti, tutti da documentazione ufficiale:

| modello | context | safe con max_tokens 32k | byte/token |
|---|---|---|---|
| claude-opus-5 / sonnet-5 / fable-5 | 1.000.000 | 968.000 | 2,5 |
| claude-haiku-4-5-20251001 | 200.000 | 168.000 | 3,5 |
| MiniMax-M3 | 1.000.000 | 968.000 | 3,8 |
| MiniMax-M2.7 | 204.800 | 172.800 | 3,8 |
| glm-5.2 | 1.000.000 | 968.000 | 4,0 |
| glm-4.7 / glm-5-turbo | 200.000 | 168.000 | 4,0 |

### Campagna C — 28/07 — La baseline era un artefatto di misura (4 commit)

Commit: `29ba9db`, `0d2c834`, `847d3b4`, `d61d549`.

Si doveva misurare l'effetto dei sei fix della campagna B sul cache hit rate. Esito: **non misurabile**, perché la baseline stessa era rotta. La causa è il commit `9fdde3f` del 26/07 alle 10:10:03 ("fix(relay): decomprimi il buffer prima di estrarre usage e cache tokens"): con `ClientSession(auto_decompress=False)` il relay leggeva byte gzip come utf-8, non trovava mai `message_start`, e loggava `cache_read` e `cache_creation` sempre a 0 stimando `input_tokens` come `chars//4`. Quel commit dichiarava "solo telemetria, nessun impatto sulle risposte".

Prova per segmentazione al minuto del fix: prima di `9fdde3f`, 5.213 richieste big senza cache su 5.312, cioè il **98,1%**; dopo, **2 su 387**, cioè lo **0,5%**. Il 5.213 pre-fix coincide col 5.215 della baseline. Correlazione oraria indipendente: restart del router alle 10:09:01, nocache al 100% nelle ore 06-09, 54,3% nell'ora 10, 0% dalle 11 in poi per due giorni. Stato del 28/07: 38 richieste big, 0 senza cache, cache hit rate 80,2%, 0 rewrite.

F1 non è stato dichiarato inutile: il suo bersaglio reale erano i **121** eventi `ctx: proactive rewrite` loggati il 23, 24 e 26 luglio, ognuno dei quali distruggeva i `cache_control`. Dopo `49c26a3` sono 0.

Scoperta collaterale: 4 test non venivano mai eseguiti benché la suite dicesse "106 passed".

### Campagna D — 28/07 — Test in suite, 502 non-streaming, risposte vuote di ask-m3 (3 commit)

Commit: `41a4ef8`, `7c84535`, `fdead17`.

Tre chiusure nella stessa sessione.

**I 4 test mai eseguiti.** `sviluppo/tests/test_gate_e2e.py` (3 test) e `test_mixgm_stream_ttfb.py` (1 test) davano `ERROR fixture 'h' not found`. Scelta la strada (a), farli entrare in suite, e non la (b), rinominarli fuori dal pattern `test_*`: erano già verdi come script standalone, e la (b) avrebbe congelato fuori dalla copertura proprio il gate di contesto. Sono serviti tre interventi distinti, due dei quali non erano nella diagnosi iniziale:

1. Fixture assente → nuovo `sviluppo/tests/conftest.py` con `@pytest_asyncio.fixture` che legge `request.module.Harness` e garantisce `stop()` nel `finally`.
2. Marker troppo tardi → `pytest-asyncio` 1.4.0 gira in modalità **strict**; aggiungere `pytest.mark.asyncio` da un hook `pytest_collection_modifyitems` non funziona, il plugin ha già deciso. Serve `pytestmark = pytest.mark.asyncio` a livello di modulo. Scartato `asyncio_mode=auto`.
3. Moduli cachati → `router_constants` risolve gli indirizzi upstream una volta sola all'import e resta in `sys.modules`, quindi il secondo file ereditava la porta del fake server del primo, ormai chiuso, e prendeva 502. Il conftest chiama `_purge_src_modules()` prima di ogni `start()` e dopo ogni `stop()`.

Danno collaterale corretto: `test_mix_anthropic_retry.py` usava, intorno alla riga 39, `asyncio.get_event_loop().run_until_complete()` — oggi quella riga porta il commento che documenta la scelta —, che da solo passa ma esplode con `RuntimeError: There is no current event loop` appena un test asyncio gira nella stessa sessione. Sostituito con `asyncio.run()`. Interprete in uso: Python 3.12.3.

**I 502 sulle non-streaming.** `7c84535`. La sessione condivisa (`src/ai-router-proxy.py:914`) imponeva `sock_read=120` a ogni richiesta. Su una risposta non-streaming l'upstream non manda un byte finché la generazione non è conclusa, quindi quei 120 secondi smettono di proteggere dagli stall e diventano **un tetto sulla durata della generazione**. 59 casi in `ai-router.log` fra il 26 e il 28/07, tutti su `minimax passthrough`, di cui circa 43 su traffico reale. Non era un difetto di MiniMax: `forward_anthropic` non passava alcun timeout per-request e `glm_backend` usava `total=120`, il più stretto dei tre; non si manifestava perché quei client usano sempre lo streaming. Fix: costante `NON_STREAM_SOCK_READ_SEC` (env `AIROUTER_NON_STREAM_SOCK_READ_SEC`, default 600) più helper `upstream_timeout_for(body)`, applicato a MiniMax, Anthropic in 3 call site e GLM. Lo streaming continua a ereditare i 120 secondi, dove servono. Verifica live: una richiesta non-streaming con `max_tokens=32000` che moriva a 120,4 s ora ritorna **HTTP 200 in 126,9 s** con `end_turn` e blocchi thinking più text completi. Telemetria: quei 502 finivano solo in `ai-router.log` e mai nel sidecar, 59 contro 0; ora sono registrati.

**Le risposte vuote di ask-m3.** Fix solo lato client, in `~/.local/bin/ask-m3`, router intatto e nessun restart. `M3_MAXTOK=0` in `~/.claude/m3/config.env` aveva il commento "0 = NESSUN LIMITE", che è falso: `max_tokens` è obbligatorio nella Messages API e il codice lo traduceva in 8192. I modelli MiniMax sono reasoning, il blocco thinking consuma lo stesso budget, quindi su prompt complessi si arrivava a `stop_reason=max_tokens` con un solo blocco thinking e zero blocchi text; `ask-m3` estraeva solo i blocchi `type=="text"` e stampava "(risposta vuota)". Riproduzione: un blocco thinking da 12.649 caratteri. Fix: `stream: true` nel payload più `_sse_to_message()` che ricompone, `DEFAULT_MAXTOK_FALLBACK = 32000`, `M3_MAXTOK=32000`, ed exit code 4 con messaggio esplicito quando il caso è troncamento e non vuoto.

Suite: da 106 passed + 4 errors a **120 passed, 0 errors**.

### Campagna E — 28-29/07 — Models API, codeburn, telemetria dei tool, connettori MCP (8 commit)

Commit: `54817e4`, `3cb9163`, `032e066`, `066179a`, `7e16382`, `81f23cc`, `395442b`, `8e483f5`.

**Models API valutata e scartata a runtime.** `GET /v1/models?limit=100` risponde 200 con il token OAuth di Claude Code (header `authorization: Bearer` più `anthropic-beta: oauth-2025-04-20`), restituisce 11 modelli ed espone `max_input_tokens` e `max_tokens`. Confronto col traffico reale su **112.244 richieste Anthropic** del sidecar: mappa statica e API concordano al **99,97%**, divergono su 9 richieste (0,01%), e 18 richieste (0,02%) sono su modelli che l'API non copre. Decisione: non sostituire a runtime, per tre motivi in ordine di peso — il guadagno è lo 0,01% contro il costo di una dipendenza di rete nell'hot path; 9 chiavi Anthropic della mappa non sono nel catalogo dell'account e cadrebbero sul default 200.000 venendo compresse a 160.000 dal gate, fra cui `claude-sonnet-4-7` che ha traffico reale; se l'API fosse irraggiungibile al boot l'unico fallback sarebbe di nuovo una tabella statica, cioè la doppia verità che si voleva evitare. L'API entra invece come controllo di coerenza **offline**: `sviluppo/tools/check_model_context.py`, che non contiene nemmeno un valore di context ma li legge tutti dalla mappa e dalla risposta HTTP, ed esce 0 se concorde, 1 se diverge, 2 se manca il token o la rete. Non è in suite pytest, che diventerebbe dipendente da rete e OAuth. Correzione trovata: `claude-sonnet-4-5` valeva 200.000 mentre l'id di catalogo `claude-sonnet-4-5-20250929` riporta 1.000.000, una sottostima di 5 volte ereditata da una sezione SPEC non verificata.

**Triage codeburn.** L'audit del 27/07 (sola lettura) dava health F 20/100 su 855 sessioni e un risparmio dichiarato di 1,98 miliardi di token in 30 giorni. Tre finding: `low-worth-sessions` 923M, `context-heavy-sessions` 674M, `mcp-low-coverage` 315M. Scelto `mcp-low-coverage`, il più piccolo, perché unico con una leva di configurazione deterministica e reversibile. Nota: `debinex`, citato nel finding, non esiste più nella configurazione (0 occorrenze in `~/.claude.json`), quindi quella parte del finding è obsoleta.

**Telemetria del blocco tools.** `collect_tools_stats()` in `src/router_utils.py`, agganciata al logging centrale in `streaming_relay.py` riga 459, dietro il flag `AIROUTER_TOOLS_TELEMETRY` spento di default: a flag spento esce prima di qualsiasi parsing e l'entry del sidecar resta identica. Misura reale: **64 tool per 142.056 byte**, circa 56.822 token. Per server: `claude_ai_Gmail` 32.379 byte (22,8%), `claude_ai_Google_Calendar` 26.378 byte (18,6%), `claude_ai_Google_Drive` 10.795 byte (7,6%), `zai` 1.351 byte (1,0%), tool nativi non MCP 71.112 byte (50,1%). I tre connettori Google insieme pesano 69.552 byte, circa **27.820 token**, il 49,0% del blocco. **Dato che ridimensiona il finding:** su 9 richieste osservate, 7 hanno `input_tokens=2` e `cache_read` fra 116.037 e 244.049; solo 2 pagano pieno. I 315M di codeburn sono un limite superiore teorico, non un risparmio disponibile. Resta reale il costo non fatturabile: su un modello da 200.000 come Haiku quei token sono il 13,9% della finestra. Suite da 120 a **128 passed**.

**Connettori MCP.** Il 29/07, su richiesta esplicita dell'utente, i tre connettori `claude.ai Gmail`, `claude.ai Google Calendar` e `claude.ai Google Drive` sono stati messi in `disabledMcpServers` in `~/.claude/settings.json` — da non confondere con `disabledMcpjsonServers`, che vale solo per i server da `.mcp.json`. **La verifica dinamica ha poi SMENTITO l'efficacia**: i connettori continuano a comparire. La prova statica ricavata dal bundle del CLI era rassicurante ma non era il flusso reale. L'unica strada praticabile resta disconnetterli dal browser su `https://claude.ai/customize/connectors`.

### Campagna F — 31/07 — Enforcement della gerarchia e self-healing loop (8 commit)

Commit: `466e18f`, `613158f`, `1284506`, `f43519c`, `51e6f87`, `f4ee6c5`, `0c60e29`, `c5399be`.

**enforce_hierarchy deny mode-aware.** Diagnosi da `~/.claude/logs/ai-router.log`: in `mix-gm`, su 905 richieste, GLM ne serviva 746 (82%) e MiniMax 159 (18%), rapporto THINK:ACT circa 4,7:1; in `mix-am`, su 7.832 richieste, Anthropic 5.023 (64%) e MiniMax 2.807 (36%), rapporto circa 1,8:1. Cioè mix-gm delegava all'esecutore la metà delle volte di mix-am. **Il proxy non era la root cause**: la tabella `src/role_routing.py` righe 55-56 e la chain `~/.claude/lib/router_chain_dispatcher.py` righe 242-253 erano corrette. Il buco era l'enforcement: l'hook `~/.claude/hooks/enforce_hierarchy.py` era audit-only. Ora è mode-aware, con `DENY_CODING_MODES` che restituisce `permissionDecision: deny` su Edit/Write/MultiEdit di codice di progetto, con una reason che ordina `m3-code "<spec>" > file`. Il `deny` non è `exit 2`: è una risposta pulita che non rompe il prompt di autorizzazione. Esenzioni invariate: file non-codice, file sotto `~/.claude/`, micro-edit fino a 15 righe, `HIERARCHY_ALLOW=1`, marker `.hierarchy_allow` con finestra 900 s. Dato cruciale per la misura: **`m3-code` chiama MiniMax direttamente, out-of-band, non via `:8787`**, quindi le deleghe non appaiono nel log del proxy; l'indicatore corretto è `~/.claude/m3/hierarchy-violations.jsonl`, non i conteggi di `ai-router.log`.

**Self-healing loop, quattro fasi.** Obiettivo: rilevare i "valori zero", cioè le risposte HTTP 200 ma vuote, imparare i pattern persistenti modello×task→fallimento, correggere in runtime e proporre fix di codice offline. L'architettura tiene il proxy trasparente: il watcher è un layer esterno e l'actuator scrive un file di policy che il proxy legge, con lo stesso pattern di `ai-router-mode`.

- Fase 1 SENSOR `613158f` — `src/self_healing/sensor.py` con `classify_outcome` fra `{ok, empty, truncated, tool_only, error}` e `classify_task` fra `{coding, reasoning, chat, vision, tool_heavy}`, euristica senza LLM; `log_router_usage` guadagna 6 campi nel sidecar; il parser SSE di `streaming_relay.py` estrae `delta.stop_reason` e conta i `content_block_start` per tipo. Test `test_sensor.py` 14/14.
- Fase 2 WATCHER `1284506` e `f43519c` — `watcher.py` con `OutcomeLearner`, EWMA più decay temporale. Test 7/7.
- Fase 2.5 `51e6f87` — `m3_source.py`, che rende visibile al watcher il coding MiniMax out-of-band. Era necessaria perché il watcher, guardando solo `router-usage.jsonl`, era **cieco proprio sul caso d'uso centrale**. La fonte di verità per il coding-MiniMax è `~/.claude/m3/usage.jsonl` più `routing-telemetry.jsonl`, non il proxy.
- Fase 3 ACTUATOR `f4ee6c5` — `src/router_policy.py` con hot-reload, il proxy lo consulta, retry sugli empty.
- Fase 4 AUTO-FIXER `0c60e29` e `c5399be` — `auto_fixer.py`, offline e gated, mai sul live: su 240 entry reali ha classificato 6 bug ricorrenti, il più frequente `minimax_context_exceed` con 142 occorrenze. Il fix `c5399be` gli fa leggere `debug-errors.jsonl`, perché `BUG-CATALOG.jsonl` nel percorso che usava non esisteva.

Deploy completato con restart il 2026-08-01 alle 10:44: la telemetria outcome ha rilevato un valore zero reale nel primo minuto (`glm-5.2 + vision -> empty`). Follow-up non bloccanti: far girare il watcher come servizio systemd o cron invece che a mano con `python3 -m self_healing.watcher --once`; formalizzare `test_router_policy.py` e `test_auto_fixer.py`; verificare che il conteggio `text_blocks` sia corretto per tutti e tre i provider.

### Campagna G — 01/08 — Nativizzazione dei modelli stranieri e stall mid-stream (10 commit)

Commit: `fdb3376`, `a12986e`, `0bf505b`, `ba2025c`, `6a2dc5f`, `aeccaaf`, `ae8a74d`, `f158d50`, `6e3ab65`, `9eec723`.

**404 su modello straniero in `mode anthropic`.** `resolve_route('anthropic','MiniMax-M3')` ritornava `('anthropic', None)`, quindi il nome straniero finiva tale e quale ad `api.anthropic.com`, che è l'unico provider a validare il nome, e rispondeva `404 not_found_error`. La sorgente era `ask-m3`, dietro `m3-code`/`m3x`/`m3-wiki`/`m3-fanout`, che chiama `http://localhost:8787/v1/messages` con `model: MiniMax-M3`; il meccanismo `minimax-oob` che copriva il caso era stato perso nel refactor tunnel `99dcc0d`. Il fix è in **due punti**, e il primo da solo non basta pur sembrando funzionare: `fdb3376` aggiunge `model_provider()` e la nativizzazione sull'esecutore nativo (`anthropic→claude-haiku-4-5-20251001`, `minimax→MiniMax-M2.7`, `glm→glm-4.7`), e `a12986e` fa sì che il ramo `_provider == "anthropic"` **scriva davvero l'override nel body**: prima lo calcolava e lo ignorava, tanto che il log mostrava `override=claude-haiku...` mentre la risposta restava 404.

**Il detector di stall che causava lo stall.** Scritto in `0bf505b`, rollbackato in `ba2025c`. Prima versione: cercava `b'message_stop'` nei chunk grezzi, ma la sessione usa `auto_decompress=False` e Anthropic risponde `Content-Encoding: gzip` anche su `text/event-stream` (magic `1f8b` verificato), quindi il marker non matchava mai e l'allarme scattava su circa il 100% del traffico, 20 falsi positivi in 7 minuti. Seconda versione, con `zlib.decompressobj()`: l'`import zlib` era **locale** dentro il `finally` della stessa funzione, quindi `zlib` diventava locale per tutto lo scope e dava `UnboundLocalError` dopo `resp.prepare()` e fuori dal `try` del loop — nessun log e nessun chunk al client.

**La causa vera dello stall: il prompt caching era morto.** Non era il router a troncare gli stream. Con `cache_creation=0` e input fra 151k e 226k token riprocessati a ogni turno, la generazione andava a **un chunk ogni 12 secondi** (27 chunk in 5 minuti e 30) e il client VSCode chiudeva la connessione con `Cannot write to closing transport [client_closing=True upstream_eof=False]`. Le sessioni sane in parallelo avevano `input=2` e chiudevano in 30-40 secondi. Due bug distinti sui `cache_control`:

1. `aeccaaf` — `filter_tools_for_backend` in `src/tool_isolation.py` rimuoveva i tool stranieri **senza trasferire il breakpoint** che Claude Code mette sull'ultimo tool: `stripped=['mcp__zai__web_search_prime']`, `kept=57/58`, **3263 occorrenze**. Ora il breakpoint passa all'ultimo tool rimasto e il tool straniero resta rimosso, quindi l'isolamento è intatto.
2. `f158d50`, **decisivo** — la coda della conversazione restava senza breakpoint. Reso visibile da `ae8a74d`, che logga il conteggio per sezione: `cache: OK bp=s2/m1/t0 read=56982 creation=0 input=226748`, cioè 3 slot su 4 occupati e l'unico sui messaggi su un turno vecchio. Nuovo modulo `src/cache_optimizer.py` con `ensure_tail_cache_breakpoint()`, agganciato in `src/forward_anthropic.py` in due punti dopo `sanitize_server_tool_ids`. Guardrail: no-op se i 4 breakpoint sono già occupati, se il body è sotto 20.000 caratteri, se non è JSON valido, o se la coda ha già un breakpoint; in caso di errore ritorna il body originale e non solleva mai eccezioni.

Risultato misurato dopo il restart delle 12:46:30, mediana su 32 richieste: input non cachato **da 226.748 a 2**, `cache_creation` da 0 fisso a 24k-36k, `cache_read` in crescita monotona fino a **320.924**, velocità dello stream **da 0,08 a 9,5 chunk/s**, e **zero** stall patologici. `6a2dc5f` aggiunge la diagnostica che distingue `client_closing` da `upstream_eof`, cioè "client sparito" da rottura lato server. `6e3ab65` copre `ensure_tail_cache_breakpoint` con i test. Suite a **168 passed**.

---

Errore di analisi corretto in corsa: lo strip dei tool NON è esclusivo delle modalità pure, `src/forward_anthropic.py` lo applica anche alla leg Anthropic delle modalità miste, alle righe 222 e 332 (dove `ensure_tail_cache_breakpoint` è agganciato alle righe 245 e 353, subito dopo `sanitize_server_tool_ids` alle 241 e 349). La discriminante è se il tool rimosso portava il breakpoint, cioè l'ordine dei tool nel workspace: per questo il guasto colpiva una chat sola. Secondo errore corretto: il conteggio grezzo faceva sembrare `mix-am` la modalità più colpita, ma era solo esposizione al traffico; normalizzando, la modalità non discrimina, e nemmeno `cache_creation=0` da solo.

### Campagna H — 02/08 — In mix-am il system prompt non arrivava mai all'esecutore (4 commit)

Sintomo riportato dall'utente: in `mix-am`, MiniMax genera contenuti allucinati o vuoti.

**Root cause.** Il system prompt non arrivava mai all'esecutore MiniMax. `minimax_body._inject_system_as_message` spostava il campo top-level `system` dentro `messages` come `{"role":"system"}`; subito dopo, in `forward_minimax` con `AIROUTER_TRANSITION_FILTERS=1` (attivo in produzione, letto da `/proc/<pid>/environ`), `_repair_message_sequence` applicava `[m for m in messages if m.get("role") != "system"]` e cancellava proprio quel messaggio. Due trasformazioni singolarmente sensate che si annullavano a vicenda. Sui body grandi spariva con esso anche il riassunto del contesto, che `context_rewrite` mette nel `system`.

**Prova A/B**, con system "rispondi SEMPRE e SOLO BANANA": MiniMax chiamato direttamente rispondeva `BANANA`; via router si presentava con "Ciao! Mi chiamo MiniMax-M2.7", cioè identico al caso senza system; dopo il fix risponde `BANANA`. **Prova sul contesto**: con un body da 1135KB, `input_tokens` valeva 736 e la risposta era "CODICE-ALFA: (non fornito)"; dopo il fix `input_tokens` è 86.944 e la risposta è corretta. Verificato inoltre che `api.minimaxi.chat/anthropic` onora il `system` top-level anche come lista di blocchi con `cache_control`: la conversione era inutile oltre che dannosa. Rimosse `_inject_system_as_message` e `_system_to_text`.

**Altri dieci difetti misurati e corretti nella stessa passata:**
1. Tutti e tre i percorsi di shrink (`pipeline_minimax._try_shrink_body`, `pipeline_anthropic._try_shrink_body_haiku`, `context_rewrite._rewrite_impl`) buttavano il riassunto nel fallback; l'ultimo sceglieva `min(candidates, key=len)`, cioè il candidato più povero, in silenzio.
2. `_try_shrink_body_haiku` assegnava `shrunk["messages"]` prima di `_repair_message_sequence` senza riassegnare, quindi la riparazione non aveva effetto.
3. `system` come lista veniva serializzato con `json.dumps` grezzo nei percorsi di shrink.
4. `streaming_relay._acc_limit=16384` leggeva solo i primi 16KB, producendo `outcome="empty"` falsi: il 91% degli empty MiniMax aveva più di 4000 token, con mediana 9982. Aggiunti un buffer di coda e il flag `measure_partial`; `classify_outcome` ora ritorna `unknown` su misura incompleta.
5. Il ramo non-streaming del relay non contava i blocchi, quindi ogni risposta JSON riuscita risultava `empty`.
6. `trim_smart._smart_sample_middle` ignorava il budget sui messaggi pesanti: 300 messaggi davano 549.418 caratteri sia con budget 560.000 sia con 8.000, quindi i loop iterativi non convergevano proprio nel caso peggiore.
7. `context_rewrite` passava il budget in token dove serve in caratteri, producendo un riassunto quattro volte più piccolo, 52k dei circa 196k token consentiti; con `bytes_per_token(model)` sale a 86.944, il 43% della finestra.
8. `final_override` etichettava come `MiniMax-M3` richieste servite da M2.7: 834 in 7 giorni. Ora `_effective_minimax_model()`.
9. `startswith("MiniMax")` era case-sensitive e mancava gli alias minuscoli.
10. `debug_catalog` non sanificava gli snippet: i byte gzip producevano 155 NUL in `BUG-CATALOG.md`, che git classificava come binario. Aggiunto `_sanitize_snippet` al choke-point e rigenerato il file in UTF-8, 199 entry intatte e 35 campi sanificati.

Rimosso anche `src/summarizer.py`, codice morto che costruiva a sua volta un `role="system"`.

Decisione sul fallback dello shrink: iterativo, e `400` esplicito se fallisce, mai un 200 su contesto vuoto — un errore onesto vale più di un output inventato.

Limite noto e accettato: si usa il 43% della finestra e non il 75% teorico, per via di `TRUNCATE_MAX_LEN=1800`; alzarlo richiede prima una misura della qualità del riassunto.

### Coda del 02/08 — modifica non coperta da alcun checkpoint

Alle 18:36 del 02/08, su richiesta esplicita dell'utente ("elimina questo blocco"), l'intercettazione delle redirezioni Bash verso file di codice è stata declassata da blocco ad audit. Stato verificato il 2026-08-03 in `~/.claude/hooks/enforce_hierarchy.py`: `DENY_CODING_MODES = {"mix-gm", "mix-am", "minimax"}` alla riga 25 continua a valere per Edit/Write/MultiEdit, mentre `_check_bash_redirect` alla riga 243 registra e lascia passare — riga 275, `log_event("allowed", target, ext, f"{mode}-bash-redirect-audit", "Bash", cwd, "0")`. L'ultimo checkpoint del periodo, `CP_20260802_1743.md`, è delle 17:43 e quindi non contiene questa modifica.

---

## 6. Lezioni trasversali (errori di metodo, non di codice)

- **Datare il misuratore prima di usare la misura.** La baseline "5.215 richieste / 201.283.024 token persi" era un artefatto: il relay parsava byte gzip come utf-8 ed è stato corretto prima che la baseline fosse scritta. Prima di confrontare due periodi, segmentare al minuto dell'ultimo fix che ha toccato lo strumento.
- **Se un detector allarma su quasi tutto il traffico, è rotto il detector.** 20 falsi positivi in 7 minuti, incluse richieste andate a buon fine.
- **Import sempre a livello di modulo.** Un `import zlib` dentro una funzione rende il nome locale per tutto lo scope e produce `UnboundLocalError` a runtime, dopo `resp.prepare()` e fuori dal `try`: nessun log, nessun chunk. Stessa famiglia di guasti del crash-loop del 19/07.
- **Un log che dice `override=X` prova solo che X è stato calcolato, non applicato.** I rami minimax e glm lo applicavano, quello anthropic lo ignorava.
- **Verificare che il percorso che dovrebbe registrare un evento sia raggiungibile da quell'evento.** La cattura del marker `/compact` era armata dentro un ramo gated a `pct >= 30%`, mentre un compact manuale avviene a contesto qualsiasi: tre compact reali, zero entry.
- **Controprove su fix a più punti: disattivare tutti i punti insieme.** Disattivandone uno solo su due la suite restava verde e portava alla conclusione errata che il purge fosse superfluo.
- **Guardare `stop_reason` e i tipi di blocco prima di dedurre l'incapacità di un modello.** Due risposte vuote sembravano incapacità di MiniMax: era budget esaurito nel blocco thinking.
- **Sommare `input_tokens + cache_read` prima di gridare alla regressione.** Un calo di `input_tokens` dopo un restart è prompt caching: 136 + 74.811 = 74.947, cioè il totale precedente invariato.
- **Un fallimento invisibile al sidecar non è misurabile.** I 59 errori 502 del passthrough finivano solo in `ai-router.log` e 0 in telemetria: il fix non sarebbe stato verificabile.
- **La telemetria vuota non significa sistema sano.** Va sempre verificato che il canale scriva davvero.
- **Normalizzare per esposizione prima di attribuire una colpa.** Il conteggio grezzo indicava mix-am come la modalità più colpita; era solo la più trafficata.
- **Leggere il sorgente è un'ipotesi, non una verifica.** La prova statica sul bundle del CLI diceva che `disabledMcpServers` avrebbe disattivato i connettori: la misura dinamica lo ha smentito.
- **Delegare patch a `m3x` su file esistenti del router: 3 fallimenti su 3**, di cui uno passava `ast.parse` (blocco de-indentato fuori dalla funzione). Per codice nuovo e isolato la qualità è buona. Se si delega, guardare l'indentazione nel `git diff`, non fidarsi di `ast.parse`.
- **`py_compile` non è una prova.** `ImportError` e `NameError` dentro le funzioni esplodono solo a runtime.
- **Un output delegato può arrivare troncato senza errore.** La prima stesura di questa stessa pagina si è interrotta a metà frase: va sempre controllata la fine del file, non solo l'inizio.

## 7. Divieti operativi consolidati

**Servizio.** Mai riavviare `ai-router` senza richiesta esplicita e senza la sequenza:
`systemctl --user is-active ai-router` deve dare `active`, poi `systemctl --user cat ai-router | grep -i restart`
deve confermare `Restart=always`, poi il restart, poi `sleep 4`, poi di nuovo `is-active`. Un restart
tronca ogni stream in volo e il client mostra esattamente `Response stalled mid-stream`. Dopo un SIGKILL
con start-limit serve `systemctl --user reset-failed`. Il `systemd` si copia solo dall'installato al
repo, mai il contrario: il repo è obsoleto, l'installato è hardened.

**Contesto e cache.** Il gate resta osservatore: il router non deve emettere un 400 di contesto, perché
bloccare la richiesta blocca anche `/compact` e rende la sessione irrecuperabile. Non riportare la stima
dei token a `char/4`, era la causa di F9. Non reintrodurre una seconda tabella di context limit accanto a
`model_context_map`. Non superare i 4 `cache_control` per richiesta, Anthropic risponde 400. Non rimuovere
lo strip dei tool stranieri: l'isolamento è una regola utente, e il fix preserva il breakpoint lasciando
il tool rimosso fuori. Non toccare `TRUNCATE_MAX_LEN=1800` senza una misura di qualità del riassunto.

**MiniMax e mix-am.** Non reintrodurre alcuna conversione di `system` in messaggio `role=system`: viene
cancellato dal repair a valle. Non disattivare `AIROUTER_TRANSITION_FILTERS`, ripara le sequenze
`tool_result` orfane. Mai usare `outcome` della telemetria MiniMax senza controllare `measure_partial`.
Non abbassare `NON_STREAM_SOCK_READ_SEC` a 120 o meno.

**Codice.** Import sempre a livello di modulo. Non reintrodurre un `except` muto nell'import lazy di
`router_utils` in `glm_backend`, un fallback muto lì ha già causato la regressione del 2026-07-25. Non
toccare `relay`, `retry` e `oauth` senza misura: il rollback `ba2025c` è il precedente recente. Non
modificare a mano `BUG-CATALOG.md`, è generato da `scripts/generate_bug_report.py` a partire da
`logs/BUG-CATALOG.jsonl`. Non toccare la tabella `src/role_routing.py` né la chain
`router_chain_dispatcher._CHAINS` per problemi di enforcement: erano già corrette.

**Isolamento fra provider.** `glm` puro: solo GLM, nessun fallback cross-provider, 502 pulito, mai Haiku.
`mix-gm`: GLM più MiniMax, mai Anthropic. L'esecutore di wiki e memoria è quello della catena della
modalità attiva.

**Test.** Vietato testare contro `:8787` live: usare `_make_app` in-process con upstream finto, oppure
`AIROUTER_PORT_MODE_JSON`. Ogni test async deve usare `asyncio.run()`. Con `curl` su passthrough anthropic
serve `--compressed`, altrimenti il gzip dà un errore di parsing fuorviante. Testare la cache su Haiku:
Sonnet e Opus rispondono 429 org-level anche a richieste minuscole. I test end-to-end importano il proxy
in-process e la sua `log()` scrive sul file di log di produzione, quindi per contare eventi reali vanno
filtrati i fingerprint sintetici (`sid:sid-`, `fp=127.0.0.1`).

**Git e strumenti esterni.** Non assumere di essere l'unico agente sul working tree: rileggere
`git log --oneline -3` e `git status` prima di ogni commit. `git add` mirato, mai `git add -A`.
`.claude/checkpoints/` è in `.gitignore`, i checkpoint vivono solo sul filesystem. Non committare
` M BUG-CATALOG.md`. Non eseguire `codeburn --apply` né `codeburn guard install`: toccano `settings.json`
e archiviano skill e agenti, in collisione con gli hook. Non eseguire il PLAN handoff: PTY Linux rotto,
upstream macOS-first.

## 8. Comandi di verifica

```bash
cd "/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch"

# stato
git status && git log --oneline -5
python3 -m pytest -q                      # atteso: 168 passed
python3 -m pytest sviluppo/tests/ -q      # atteso: 116 passed
systemctl --user is-active ai-router      # atteso: active
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8787/health   # atteso: 200

# salute del prompt caching (la riga la scrive il relay nel log del router, non il sidecar)
grep "cache: OK bp=" ~/.claude/logs/ai-router.log | tail -20
# sano: input=2 con creation>0.  patologico: creation=0 con input a sei cifre.

# health-check del relay: il rapporto fra 200 e primo chunk
awk '/\[TIMESTAMP_START\] START/,0' ~/.claude/logs/ai-router.log > /tmp/w.log
echo "200=$(grep -cE '\-> 200 /v1/messages|anthropic \(pure\) -> 200' /tmp/w.log) first_chunk=$(grep -c 'relay first chunk' /tmp/w.log)"
# sano: first_chunk >= 200

# nativizzazione su porta isolata (non tocca la modalità globale di :8787)
curl -s --compressed -X POST http://127.0.0.1:8771/v1/messages \
  -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -H 'x-claude-code-session-id: sid:probe-1' \
  -d '{"model":"MiniMax-M3","max_tokens":8,"messages":[{"role":"user","content":"ok"}]}'
# atteso: 200 con "model":"claude-haiku-4-5-20251001"

# coerenza offline fra mappa dei context e catalogo Anthropic
python3 sviluppo/tools/check_model_context.py   # 0 concorde, 1 diverge, 2 token/rete mancanti
```

Log del router in `~/.claude/logs/ai-router.log`. Il sidecar di telemetria è `~/.claude/logs/router-usage.jsonl`. Campi di base: `ts, status, input_tokens, output_tokens, cache_read, cache_creation, mode, orig, final, client, chat` — il modello sta in `final` e `orig`, **non esiste un campo `model`**. Dal 31/07 ogni entry porta anche i campi del self-healing (`outcome`, `stop_reason`, `task_class`, `text_blocks`, `thinking_blocks`, `tool_use_blocks`) e, quando `AIROUTER_TOOLS_TELEMETRY` è attivo, quelli sui tool (`tools_bytes`, `tools_count`, `tools_mcp_bytes`, `tools_mcp_count`, `tools_mcp_servers`) — verificato sull'ultima entry il 2026-08-03. Il catalogo bug e il debug vivono in `logs/BUG-CATALOG.jsonl` e `logs/debug-errors.jsonl` dentro il progetto; `~/.claude/logs/debug-errors.jsonl` è un sink storico fermo dal 19/07 che non va usato. `journalctl --user -u ai-router` è vuoto. Il formato dei timestamp nel log è `[2026-07-28T07:48:03]`, con la parentesi quadra iniziale.

## 9. Riferimenti

Pagine di memoria collegate: [[fix-stall-anthropic-cache-breakpoint-20260801]], [[system-prompt-perso-esecutore-minimax-20260802]], [[context-rate-revisione-completa-20260728]].

Checkpoint di sessione del periodo, in `.claude/checkpoints/`: `CP_20260727_1600.md` (consolidato base), `CP_20260728_0736.md`, `CP_20260728_1652.md`, `CP_20260728_2121.md`, `CP_20260729_1741.md`, `CP_20260731_1238.md`, `CP_20260731_selfhealing.md`, `CP_20260801_1150.md`, `CP_20260801_1300.md`, `CP_20260802_1743.md`.

Archivio dei 97 checkpoint precedenti: `.claude/checkpoints/archivio-checkpoint-20260623-20260726.tar.gz`.

TODO operativo: `.claude/TODO.md`. Regole di progetto: `CLAUDE.md` nella root, che è gitignored.
Piano del self-healing: `~/.claude/plans/frolicking-sleeping-sloth.md`.
