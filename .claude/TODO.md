# ai-router-switch — TODO

## Attivo
- [ ] **Monitorare consumo Anthropic vs MiniMax** dopo revert bypass visione M3 (2026-07-19) — ora M3 prova per primo su tutte le immagini invece di deviarle subito ad Anthropic. Verificare che il rapporto Anthropic/MiniMax si riequilibri sui prossimi log.
- [ ] **Registrare Web Search MCP Server z.ai lato client** (`api.z.ai/api/mcp/web_search_prime/mcp`, Bearer con chiave GLM) nelle impostazioni MCP di Claude Code/VSCode — senza questo passo, la modalità glm pura non ha capacità di ricerca web (lo stripping incondizionato rimuove i tool esterni anche se il nativo non è ancora configurato, per design). **Non farlo senza conferma esplicita utente**: è config MCP globale (`~/.claude.json`), impatta tutti i progetti.
- [ ] **Osservare mix-am post-fix retry-storm** (2026-07-19 sera, commit `c3a2ca8`): la pipeline ora ha THINK non-bloccante (4s) + timeout per-tentativo ACT MiniMax (12s). Verificare sulle prossime chat reali in mix-am che non ricompaiano "chat bloccate" e che `logs/debug-events.jsonl` non mostri più `Server disconnected` in raffica. Env tunabili: `AIROUTER_MIX_AM_THINK_FAST_SEC` (default 4), `AIROUTER_MIX_AM_ACT_TIMEOUT_SEC` (default 12).
- [ ] **Valutare generazione periodica di BUG-CATALOG.md**: lo script `scripts/generate_bug_report.py` è manuale oggi. Valutare se legarlo a un trigger (es. post-restart, o cron leggero) per mantenere la documentazione dei bug corrente senza intervento umano.

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
