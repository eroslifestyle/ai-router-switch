# ai-router-switch — TODO

## Completati (sessione 2026-07-22 — audit 6 modalità)
- [x] **Audit 3 modalità pure (anthropic/minimax/glm): TUTTE OK** — smoke live per-chat (mai toccata la modalità globale): PING 200 + SSE OK su ciascuna; isolamento tool verificato con strip reale di `mcp__MiniMax__understand_image` in glm (`logs/BUG-CATALOG.jsonl` 23:25:21 kept=0/1); 429 su claude-sonnet-4-6 = limite per-modello upstream (x-should-retry, Haiku/Fable 200), router trasparente corretto. Deploy verificato: symlink → src, mtime < start 23:01:33 → processo esegue `d058e37`. Dettagli: vault `audit-modalita-pure-miste-20260722.md` + `CP_20260722_0634.md`.
- [x] **Audit 3 miste (parziale)**: code-path mappati; non-stream: mix-am OK (`anthropic-think+minimax-m2.7-act`), mix-ag OK, mix-gm 200 ma body JSON corrotto dai prefissi `[VERIFY-WARNING]`/`[HHEM-WARNING]` (finding aperto)

## Attivo (audit 2026-07-22)
- [ ] Valutare BYPASS-THINK per messaggi banali anche in minimax pura (~5s di THINK sprecati, mix-am ce l'ha)
- [ ] (minore) mix-gm con `stream:true` bufferizza comunque l'intero ACT prima di rispondere (latenza primo byte, SSE valido ma non progressivo) — valutare relay streaming con HHEM/VERIFY post-hoc

## Completati (sessione 2026-07-22 — SSE miste + fix mix-gm, commit `3b5a664`)
- [x] **Test SSE su mix-am/mix-ag/mix-gm**: mix-am OK (message_start), mix-ag OK (ACT glm-4.7 streamma), mix-gm SSE presente MA prefissato `[HHEM-WARNING] event: message_start` → finding più grave del previsto (rompeva anche lo stream)
- [x] `3b5a664` — **Fix prefissi warning mix-gm** (decisione utente: header dedicato + estrazione testo SSE): warning in header `x-ai-verify` (`hhem=<score>`, `verify=incoherent`), body mai alterato; HHEM/VERIFY valutano il testo estratto (text_delta SSE / blocchi text JSON); content_type `text/event-stream` quando l'ACT è SSE; `should_verify` riceve body sintetico per SSE (evita VERIFY "unparseable" a ogni turno — deviazione m2.7 intercettata al diff-review). Verificato live post-restart: JSON e SSE puliti, gate VERIFY corretto (short-output→VERIFIED, SSE→skip)
- [x] **Skip HHEM su risposte corte**: gate ora `len(testo estratto)>300` → niente warning su PING

## Completati (sessione 2026-07-21/22 — esecutore mix cieco a system e immagini)
- [x] `bb84a41` — mix: **executor non riceveva system/piano** — 2 bug: (1) `remap_body_for_minimax` non convertiva il campo top-level `system` (spesso lista di blocchi Anthropic) in messaggio `role=system` → MiniMax riceveva solo i messaggi utente, senza istruzioni né piano THINK → non capiva il contesto e non scriveva file; fix `_inject_system_as_message()` in minimax_body.py. (2) `pipeline_minimax.py` usava `_text_from_message` senza importarla → NameError → fallback executor diretto → piano THINK buttato; fix import da pipeline_anthropic.
- [x] `447d1e6` — mix: **esecutore cieco alle immagini** ("Nessuna immagine allegata" con allegato presente, screenshot 2026-07-22): (1) `_strip_images_from_messages` rimuoveva in silenzio i blocchi image nei messaggi misti → ora ogni image diventa marker testuale esplicito; (2) `_build_think_body` non chiedeva MAI la descrizione delle immagini (ma l'ACT non le riceve per design 38fd747: il piano era la sua unica fonte, vuota) → ora sezione IMMAGINI obbligatoria nel piano + max_tokens 1024 con immagini; (3) regola 6 nella guida esecutore: mai negare/richiedere l'allegato, lavorare sulla descrizione. Router restartato, active + health 200, test funzionali PASS.

## Completati (sessione 2026-07-20/21 — lavori lunghi mix: catena 8 fix fino a TRIM INTERCEPT)
- [x] `75aa186` — context_alert: rimosso canale notify-send (fp illeggibile); restano log+bell e banner in-chat
- [x] `152b790` — shrink: PREAMBLE nel summary compresso — il modello non si lamenta più del contesto compresso
- [x] `cff717e` — shrink: `build_shrink_summary([])` ritornava `""` → system vuoto → "msg vuoto"
- [x] `a128b06` — ctx: shrink proattivo pre-400 quando backend=MiniMax (bottleneck 200K vs client 1M); NB amplificava temporaneamente il TRIM INTERCEPT
- [x] `aa89bce` — mix: tool_use/tool_result orfani nel THINK body → `_linearize_tool_blocks` + THINK_MAX_TOKENS 200→512
- [x] `5ae37ea` — mix: `build_act_body` distruggeva il system originale (istruzioni skill) → esecutore abbandonava dopo 2-3 tool call
- [x] `535aff6` — mix: nuovo `src/pipeline_common.py` — `build_executor_body()` UNICO (preserva system, appende piano THINK + completion guard); scoperto che in mix-ag/mix-gm il piano THINK non arrivava MAI all'esecutore
- [x] `71497ae` — ctx: **RIMOSSO TRIM INTERCEPT** (root cause strutturale): `handle()` sostituiva il body appena arrivato con uno salvato al turno precedente → modello cieco all'ultimo messaggio/tool_result; fp="default" → contaminazione cross-chat. Rimossi anche `_trim_context_after_response` (slice no-op) e `_save_trim_state`
- [x] `38fd747` — mix: **vision → flusso THINK→ACT→VERIFY con immagini solo in THINK** (2026-07-21): M3 con immagini rispondeva con saluto generico perché Anthropic THINK era completamente bypassato; ora (1) `_inject_task_mode_for_images` in ai-router-proxy forza "analizza+esegui, non salutare" se ≥2 immagini; (2) `_shrink_images_in_messages` ridimensiona PNG base64 → JPEG 1024px q70 PRIMA dello shrink testuale; (3) `_strip_images_from_messages` in pipeline_common.py toglie immagini dal body ACT (esecutore riceve solo testo+piano, mai media); (4) stesso strip in pipeline_minimax.py. Principio: THINK legge tutto, ACT riceve solo testo, VERIFY controlla. Fix applicato a tutte le modalità mix (mix-am, mix-ag, mix-gm).
- [x] `d2bb6aa` — mix: **redesign THINK/ACT/VERIFY (2026-07-21)** — root cause timeout THINK: `_build_think_body` passava il body INTERO (800KB) al MODELLO UTENTE con budget 4-8s → skip permanente + piani da 10c. Ora: `pipeline_common.build_think_digest` (summary hard-capped 12KB + ultimi 6 msg + immagini solo ultimo msg) su Haiku SEMPRE (~23KB, costo ~0); VERIFY a campione via `should_verify` (sospetto o 1/N, default 5) in mix-gm/mix-ag, RIMOSSO retry automatico ACT su INCOERENTE; fix bug latente mix-ag VERIFY (`role:system` in messages → 400 Anthropic a ogni turno); `THINK_MAX_TOKENS` 200→512 in router_constants; creato symlink mancante `~/.claude/scripts/pipeline_common.py`. Verificato live: `mix-am THINK OK plan=549c`, zero skip/timeout. Modalità pure NON toccate.

## Completati (sessione 2026-07-19 ~23:00 — crash-loop totale risolto)
- [x] **Fix crash-loop totale da 2 UnboundLocalError** (`80b6ab5`): il commit `66553f0` aveva introdotto (1) `from aiohttp import web` locale dentro `handle()` → `web` shadowata per tutto lo scope, riga 255 esplodeva su ogni path fuori dal ramo ctx-error; (2) `plan` mai inizializzata in `_pipeline_think_act` → UnboundLocalError riga 728 su THINK KO/timeout/exception (ogni richiesta mix-am in 500). Watchdog SIGKILL + start-limit systemd = zero auto-restart, tutte le chat bloccate. Recovery: `reset-failed` + start. Verificato: compile OK, health 200, smoke `/v1/messages` 200 in 3.5s, journal pulito.

## Completati (sessione 2026-07-19 tardanotte — refactor sistema debug centralizzato)
- [x] **Refactor sistema debug centralizzato** (`e451d20`): nuovo `src/router_debug.py` — classe `DebugLogger` singleton con `capture()` → RAM deque (maxlen=500, warm-up da JSONL) + 2 JSONL + BUG-CATALOG dedup + health file `.router_health.json` + snapshot last-request. Fix: `_orig_flags()` `cache_control_count` corretto (era `img_count`). Fix: ts locale senza Z fasullo. Nuovo endpoint `GET /debug/health`. Errori interni ora in `logs/debug-system-errors.log` (prima silenced). `forward_minimax.py` e `forward_anthropic.py` ora catturano eventi che prima erano invisibili. SPEC: `sviluppo/DEBUG-SYSTEM-REFACTOR-SPEC.md`. Servizio restartato: `active`.

## Attivo
- [ ] **Osservare stabilità post-`71497ae`** (TRIM INTERCEPT rimosso) su chat reali lunghe in mix-am/mix-gm: journal + `logs/debug-errors.jsonl`. Expected: zero "messaggio vuoto/troncato", zero tool call ripetuti identici.
- [ ] **Verificare /wiki all reale in mix-am**: tutti i 6 passaggi completati senza abbandono (completion guard `535aff6`).
- [x] **Audit fingerprint fp="default" CHIUSO** (`b4c5133`, 2026-07-21): confermato live (log `fp=127.0.0.1`) — senza session header `_think_count`, `fail_tracker`, `_verify_turn_count`, `_request_orig_model`, ContextManager e banner erano condivisi cross-chat. Fix single-point: `handle()` cache `conversation_fingerprint(body)` su `request['chat_fp']` dopo la lettura del body; `_resolve_chat_fingerprint` la usa come fallback prima di `request.remote` → tutti i call site a valle (pipeline, `remap_body_for_minimax`, GLM, banner) risolvono la stessa fp per-chat, chiavi coerenti col chat-mode store (`dd62647`). Verificato live :8773: 2 chat → fp `68f06409b2c3`/`85ee0a51fcc4` distinte, THINK/ACT integri.
- [ ] **Osservare stabilità post-`80b6ab5`** (crash-loop UnboundLocalError): journal + `logs/debug-errors.jsonl` puliti su chat reali; se anomalie → `GET /debug/health` e `GET /debug/catalog`.
- [ ] **Osservare scomparsa 404 MiniMax post-fix Host** (`a5c31af`, 2026-07-19 21:30): con `HOP_HEADERS` filtrati in `forward_minimax` i 404 nginx non dovrebbero più comparire. Se ricompaiono → il fix Host non era l'unica causa; usare `logs/debug-errors.jsonl` (note con `alb_receive_time`/url). **Verifica iniziale 2026-07-19 21:35**: 0 errori dopo il restart 21:20 (ultimo 404 alle 21:17, pre-restart); smoke mix-am + minimax post-restart 200 con risposta M2.7 reale, nessun nuovo entry nel log. Resta da osservare su chat reali lunghe. ⚠ ATTENZIONE lettura log: i `ts` in `debug-errors.jsonl` hanno suffisso `Z` ma sono ORA LOCALE (CEST), non UTC — non confrontarli con orari UTC.
- [ ] **Verificare 400 anthropic post strip-query** (`?beta=true` rimosso dall'URL upstream in `forward_anthropic`/proxy, live da 21:04): 4 episodi `relay_error_400` alle 20:55-20:57 pre-restart, zero dopo (riconfermato 21:35). Il nuovo log `[forward_anthropic] 400 body:` cattura il body al prossimo episodio.
- [ ] **Fix timestamp debug log**: `debug_catalog.py`/logger scrivono ora locale con suffisso `Z` (fake-UTC) — usare `datetime.now(timezone.utc)` o togliere la `Z`. Micro-fix, ma va live solo al prossimo restart (non riavviare apposta).
- [x] **Committare `router-mode/card.py`** (2026-07-19, commit `b326f21` pushato): testata offscreen (`QT_QPA_PLATFORM=offscreen`) — `get_service_status()`=active, Start disabilitato/Stop abilitato coerenti, health e mode letti OK. Nessun click su Stop (router protetto).
- [ ] **Monitorare consumo Anthropic vs MiniMax** dopo revert bypass visione M3 (2026-07-19) — ora M3 prova per primo su tutte le immagini invece di deviarle subito ad Anthropic. Verificare che il rapporto Anthropic/MiniMax si riequilibri sui prossimi log.
- [x] **Registrare Web Search MCP Server z.ai lato client** (2026-07-19, ok esplicito utente): `claude mcp add --scope user --transport http zai https://api.z.ai/api/mcp/web_search_prime/mcp` con Bearer chiave GLM → status ✔ Connected. Nome server `zai` scelto apposta: i tool diventano `mcp__zai__*`, che `is_glm_branded_tool` (tool_isolation.py) riconosce come GLM. Verificato `filter_tools_for_backend`: glm tiene `mcp__zai__webSearchPrime` e strippa MiniMax; minimax/anthropic strippano zai. Attivo dalle prossime sessioni client (nessun restart router necessario).
- [ ] **Osservare mix-am post-fix InvalidHTTPResponse** (2026-07-19 22:05, commit `4a256ce`): il fix retry-storm `c3a2ca8` aveva introdotto una REGRESSIONE — `ClientTimeout(total=12)` copriva anche il body e troncava lo stream SSE già in relay su lavori grandi (body 500KB+) → `InvalidHTTPResponse` lato client + rescue su transport chiuso. Ora `act_timeout_sec` limita solo l'attesa header (`asyncio.wait_for`), body eredita sock_read=120s; guard `is_closing()` in `_mixed_haiku_rescue`. Verificare su chat reali che le deleghe grandi a MiniMax completino e che spariscano `mixed_rescue_502` con `upstream_status:0`+`haiku_stage=200`. Env tunabili invariati: `AIROUTER_MIX_AM_THINK_FAST_SEC` (4), `AIROUTER_MIX_AM_ACT_TIMEOUT_SEC` (12).
- [ ] **Valutare generazione periodica di BUG-CATALOG.md**: lo script `scripts/generate_bug_report.py` è manuale oggi. Valutare se legarlo a un trigger (es. post-restart, o cron leggero) per mantenere la documentazione dei bug corrente senza intervento umano.

## Completati (sessione 2026-07-19 tarda sera — fix InvalidHTTPResponse mix-am)
- [x] **ROOT CAUSE InvalidHTTPResponse su deleghe grandi mix-am** (`4a256ce`): `ClientTimeout(total=12)` di `c3a2ca8` scattava a metà relay dello stream SSE (body 533-613KB) → risposta troncata al client; la rescue chain otteneva 200 da user-model e Haiku ma ogni relay falliva con `Cannot write to closing transport` (2 chiamate API sprecate per retry). Fix: timeout solo-header in `forward_minimax` + guard transport chiuso in `_mixed_haiku_rescue`. Smoke anthropic + mix-am 200 post-restart. Firma log per riconoscerlo: `mixed_rescue_502` con `upstream_status:0` + `note:haiku_stage=200`.

## Completati (sessione 2026-07-19 notte — audit isolamento 6 modalità + ROOT CAUSE Host header)
- [x] **ROOT CAUSE 404 nginx MiniMax** (`a5c31af`): `forward_minimax` inoltrava `Host: 127.0.0.1:8787` del client all'upstream (aiohttp rispetta l'Host esplicito) → nginx MiniMax non matcha server_name → 404. Prova: stesso body 200 senza Host, 404 con Host farlocco. Corregge la diagnosi «ALB flaky lato loro». Fix: filtro `HOP_HEADERS` (come forward_anthropic) in entrambi i builder header.
- [x] **mix-gm rotto al 100%** (`a5c31af`): ImportError `_build_minimax_act_body_retry` — pipeline_glm importava da pipeline_anthropic ma post-split vive in pipeline_minimax. Ogni richiesta mix-gm → 500.
- [x] **Isolamento solo-minimax** (`a5c31af`): `_shrink_and_retry_minimax` param `allow_anthropic_rescue=False` dal call-site solo-minimax → 502 pulito invece di scalare su Haiku. mix-am mantiene il rescue.
- [x] **Regex `!router` con trattino** (`a5c31af`): `(\w+)` → `([\w-]+)`, prima `!router mix-gm` rispondeva con l'help.
- [x] **Legacy mode map** (`a5c31af`): override per-chat legacy («mixed»/«inverse»/«glm-minimax»/«anthropic-glm») ora mappati/validati in `get_mode` (prima passavano non validati → dispatch indefinito).
- [x] **Fallback non-messages GLM per-modo** (`a5c31af`): mix-ag→anthropic, mix-gm→minimax, glm puro→502 (prima sempre minimax).
- [x] **Smoke test 6/6 modalità** con sessioni isolate post-fix: anthropic/minimax/glm/mix-am/mix-ag/mix-gm tutti OK; catalogo debug pulito, 0 ImportError.

## Completati (sessione 2026-07-19 sera — fix mix-am raw-relay + ultra deep debug + retry-storm)
- [x] **Fix 404 raw-relay mix-am** (`bcf4322`): `FALLBACK_STATUSES` in `router_constants.py` non includeva 404 → MiniMax/Anthropic 404 relayato grezzo (HTML nginx) al client invece di fare failover. Aggiunto 404 al set.
- [x] **Fix mix-ag mancava rescue chain** (`2af31e9`): su fallimento GLM ACT faceva un solo fallback raw `forward_anthropic+relay()` senza Haiku-rescue. Allineato a `_anthropic_rescue` come mix-am/mix-gm.
- [x] **Fix `!router status/reset` fingerprint** (`5bdafcc`): controllavano un solo fingerprint senza il fallback content-hash che `dd62647` aveva già dato al routing messaggi normali → status poteva mostrare "default" anche con override salvato.
- [x] **Diagnostica relay** (`0558031`): `streaming_relay.py` cattura ora `upstream.url` + header `server`/`cf-ray`/`via`/`alb_receive_time` nel `note` di ogni `relay_error_*` — ha permesso di attribuire definitivamente i 404 a MiniMax (header `alb_receive_time`), non a middlebox di rete locale.
- [x] **Fix raw-relay scorciatoie mix-am** (`9dd3894`): 3 punti in `_pipeline_think_act` (BYPASS-THINK messaggi leggeri, fallback THINK-exception, fallback THINK-ko/piano-vuoto) chiamavano `forward_minimax+relay()` SENZA controllare lo status. Tutti e 4 gli usi ora controllano `FALLBACK_STATUSES` prima del relay.
- [x] **Fix FAST-PATH MiniMax mix-am** (`1cebd02`): quando il client richiede modello `minimax*` in mix-am, controllava lo status solo per 400 context-exceed. Allineato agli altri 4 punti.
- [x] **Sistema ULTRA DEEP DEBUG** (6 commit `5eb7a0d`→`389efae`): nuovo `src/debug_catalog.py` — cattura/deduplica ogni bug/blocco/errore in tutte le 6 modalità. Fix collaterale: `debug_capture()` usava `get_file_mode()` (globale) invece del mode realmente risolto. Endpoint `GET /debug/catalog[/{signature}]`. Generatore `scripts/generate_bug_report.py` → `BUG-CATALOG.md`. SPEC in `DEBUG-CATALOG-SPEC.md`.
- [x] **Fix retry-storm mix-am** (`c3a2ca8`): chat "bloccate" con MiniMax giù = latenza prima del primo byte (pipeline sequenziale, no byte finché catena non finisce → 40-60s → client retry-storm). Stesso bug già fixato in glm, mai applicato a mix-am. Fix: `asyncio.wait_for(THINK, 4s)` + `forward_minimax(act_timeout_sec=12)` + unificazione 3 path fallback. Verificato: turno 4.26s (prima 40-60s).

- [x] **FASE B — split modulare COMPLETATA**: step1-3 ✅ (3e32dcb), step4 sse_utils.py (ac10fc5), step5 minimax_body.py (66b85dc), step6 trim_smart.py (37d9558). Proxy: 4344→3992 LOC (**-352 total**). Moduli nuovi: sse_utils.py, minimax_body.py, trim_smart.py.
- [x] **FASE B — split FINALE `ai-router-proxy.py`**: step7 (2026-07-19) split in 10 moduli, proxy 3992→695 righe (−83%). Commit `8135a27` pushato. Nuovi moduli: router_constants.py, router_utils.py, router_mode.py, router_commands.py, router_auth.py, forward_anthropic.py, forward_minimax.py, pipeline_minimax.py, pipeline_anthropic.py, pipeline_glm.py.
- [x] **Fix crash-loop totale post-split FASE B** (2026-07-19 sera, commit `1e610ec`): `Path(__file__).parent.parent` non risolveva il symlink di deploy `~/.claude/scripts` → `ModuleNotFoundError fail_tracker` a ogni avvio (TUTTE le modalità down, mascherato da is-active flapping). + `NameError MINIMAX_MODEL` non importato in `pipeline_anthropic.py` (crash su ogni richiesta mix-am). + resync 3 file deployati ancora come copie fisiche (`ai-router-proxy.py`, `forward_anthropic.py`, `forward_minimax.py`) → symlink. Verificato end-to-end: richiesta reale mix-am eseguita pulita nei log, `NRestarts=0`.

## Completati (sessione 2026-07-19 pomeriggio — fix 400 background + isolamento tool centralizzato)
- [x] Fix bug 400 ricorrente su THINK/VERIFY in background modalità GLM pura — `system` prompt era iniettato come messaggio `role:"system"` dentro `messages` (invalido per endpoint Anthropic-compatible z.ai, richiede `system` top-level); content a blocchi (tool/immagine) azzerava silenziosamente l'array messages (commit aabb2f7)
- [x] Isolamento tool per-provider centralizzato su TUTTE le modalità (pure + mix-am/mix-ag/mix-gm) — nuovo `src/tool_isolation.py`, choke-point unico dentro `forward_anthropic/forward_anthropic_direct/forward_minimax/forward_glm`. Chiude leak reale: MCP MiniMax visibile a GLM in mix-ag, server-tool Anthropic visibili a MiniMax in mix-gm (stesso bug 2013 di mix-am, mai coperto qui). Rimosse le vecchie funzioni duplicate `_strip_foreign_branded_tools`/`strip_foreign_branded_tools_for_glm` (commit 0a9ae82)
- [x] Fix collaterale: `sviluppo/tests/test_glm_modes.sh` non impostava `PYTHONPATH` con la root del repo (dove vive `fail_tracker.py`, non in `src/`) — istanza di test isolata non partiva mai, indipendentemente da altre modifiche
- [x] Committati 9 file di piani ricerca "comunicazione bilaterale multi-modello" rimasti non tracciati (commit fec9b39)

## Completati (sessione 2026-07-19 — FASE A fix bilaterali + FASE B pausa per mappatura)
- [x] FASE A1 — trim-state atomico: tempfile.NamedTemporaryFile + os.replace + threading.Lock per-fp (commit 95b50b1, test `test_trim_race.sh` PASS=4/0)
- [x] FASE A2 — VERIFY enforcing mix-gm: retry ×1 su incoerenza, prefisso [VERIFY-WARNING], nuova `_build_minimax_act_body_retry()` (commit f97a439, test `test_mixgm_verify_retry.sh` PASS=5/0)
- [x] A3 — marcatori OBIETTIVO/VINCOLI/NON FARE in `_build_think_body` (commit 33c39d1)
- [x] A4 — HHEM gate (:4002) su ACT e VERIFY in mix-gm, fail-open, nuovo `src/hhem_gate.py` (commit 33c39d1)
- [x] A5 — audit boundedness fallback chain: ZERO ricorsione, max 4 hop (commit in-memory)
- [x] Scoperta CRITICA: struttura modulare esistente parziale — `providers/base.py` (153 LOC, 7 funzioni) e `pipelines/primitives.py` (82 LOC, 5 funzioni) già esistono e il proxy li importa. Piano FASE B basato su assunzione monolite inattendibile → PAUSA per mappatura. Checkpoint: `CP_20260719_1320.md`
- [x] Tentativo errato: creato `src/router_utils.py` con codice INVENTATO (non copiato dal sorgente) → ELIMINATO prima di commit

## Completati (sessione 2026-07-19 — debug modalità GLM pura)
- [x] Fix 1/5 — connection-release prematura in `forward_glm` (return da dentro `async with`), tier key mai risolta a modello reale, `.read()`/`.release()` su `web.Response` nello STEP THINK (commit 6e51322)
- [x] Fix 2/5 — mode per-chat non applicato quando manca `X-Claude-Code-Session-Id` (mismatch fingerprint write/read path, commit dd62647)
- [x] Fix 3/5 — THINK+VERIFY bloccanti (10-20s prima del primo byte) causavano retry-storm lato client con backoff esponenziale; fix ACT immediato + THINK/VERIFY in background (commit f843cc3)
- [x] Fix comando `!router` intercettava richieste ausiliarie di Claude Code (title-generation) per regex troppo permissiva (commit 73c569e)
- [x] Fix 5/5 — ROOT CAUSE del sintomo "Insufficient balance": `has_multimodal_content` dirottava qualunque messaggio a image-gen per un tool con "image"/"generation" nel nome (es. mcp__MiniMax__understand_image) → endpoint z.ai senza credito, mai loggato (commit b470dfc)
- [x] Ricerca web nativa z.ai preferita su MiniMax/Anthropic in modalità glm (commit e4429f9)
- [x] Isolamento tool nativi per TUTTE le modalità pure (anthropic/minimax/glm) — zero mixing tra provider, tool locali Claude Code mai toccati (commit 462f181)
- [x] Fix gate DEGRADED (OAuth Anthropic) bloccava anche minimax/glm pure, che non ne hanno bisogno (commit 7e2eaec)

## AQ Backlog (non bloccanti)
- [x] AQ-REF1 — Estrarre `StreamingRelay` come classe (commit e8fc50c)
- [x] AQ-REF2 — FailTracker centralizzato → `fail_tracker.py` (commit 3a64731)
- [x] AQ-REF3/4/5 — providers + pipelines moduli → `src/providers/base.py`, `src/pipelines/primitives.py` (commit 0280326)
- [x] AQ-REF6 — Rate limiter (già indipendenti: MinimaxRateLimiter + GLMRateLimiter)
- [x] AQ-REF7 — ContextManager (integrazione osserva-only, commit 70dc5e2)
- [x] AQ-TEST — Test pipeline funzionali → `sviluppo/tests/test_pipeline.py` (commit 1e40859)

## Completati
- [x] Test isolamento !router (store OK, test simultaneo live rimanente)
- [x] Bug anthropic-glm 8775 (già risolto: codice attuale corretto)
- [x] Audit proxy 12 finding: rimappatura + 2 fix (fp undefined r1390, commento r4451) + restart router (PID 913290→1071183, 8774 chiusa) + sync src/ + commit 0991ce2 (2026-07-17)
- [x] Fix mixgm/glm-minimax crash post-riavvio PC (2026-07-17, dopo restart)
  - 3 bug concatenati: symlink drift (fix: symlink 6 moduli), aiohttp import mancante, relay vs web.Response
- [x] Fix !router Bug 1 — regex anchor: `.match()` → `.search()`, anchor `(?:^|>|\n)` (commit 3d300a4, 2026-07-15)
- [x] Fix !router Bug 2 — synthetic message: eco model client (commit bd5bee8, 2026-07-15)
- [x] Fix !router Bug 3 — anchor troppo permissivo: anchor selettivo (commit 240b68c, 2026-07-15)
- [x] Fix m3-code/ask-m3 not_found_error in mode anthropic: passthrough → forward_minimax + header minimax-oob (commit b017b36, 2026-07-14)
- [x] Docs allineati: README + manuali IT/EN + HTML generati (commit 0adc9c8, 2026-07-14)
- [x] AQ RL1 — lock-free MinimaxRateLimiter (lock per-modello, commit 8e40532)
- [x] AQ RL2 — GC fail dicts fuori dal lock (commit 8e40532)
- [x] AQ FIX1 — GLM model rewrite in `forward_glm()` (commit 8e40532)
