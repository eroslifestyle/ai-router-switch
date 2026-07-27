# ai-router-switch — TODO unico

**Aggiornato:** 2026-07-27 · **HEAD di riferimento:** `fc3fbe8` · **Stato:** nessun lavoro eseguibile in autonomia; tutte le voci aperte attendono un'evidenza esterna.

Questo file unifica il vecchio `TODO.md` (32 sezioni, 5 blocchi «Attivo» sparsi), `PROJECT-TOD.md` (fermo al 2026-07-18, backlog AQ nel frattempo chiuso) e le voci residue dei 97 checkpoint di sessione. Contesto completo: `.claude/checkpoints/CP_20260727_1600.md`. Il dettaglio verboso dei completati resta recuperabile con `git show fc3fbe8:.claude/TODO.md`.

## Aperti

- [ ] **G4 — non mutilare le richieste di compattazione.** Serve il marker osservabile del turno `/compact`, che **non è ricavabile dai transcript locali**: `~/.claude/projects/**/*.jsonl` conserva solo il risultato (`compact_boundary`, `isCompactSummary:true`, `compactMetadata.trigger`), non il prompt di riassunto. Resta osservabile solo dal body HTTP e la cattura è armata (`last_user_prefix` in `example_detail`, protetto dalla sovrascrittura con `code=action`).
  **Sblocco:** un `/compact` reale dell'utente. **Prossimo step:** leggere `logs/BUG-CATALOG.jsonl`, entry `kind=ctx_gate` con `code` in `compact|error`, e ricavare il marker dai dati. Mai inventarlo.

- [ ] **400 Anthropic residui.** 36 casi dopo lo strip-query, sporadici, nessuno dopo la mattina del 2026-07-26 (fix `a9d039c` sugli id `server_tool_use` non conformi). Oggi `router_debug.capture()` li registra con snippet e url vuoti, quindi non sono diagnosticabili.
  **Prossimo step:** strumentare la cattura del corpo del 400 upstream. **Expected outcome:** una diagnosi riproducibile, oppure la conferma che il fix `a9d039c` li ha chiusi tutti.

- [ ] **Guardia tool stranieri: da rilevazione a filtro.** La guardia response-side (`a85e505`) oggi rileva e non filtra, per scelta: riscrivere uno stream SSE romperebbe la numerazione dei `content_block`.
  **Sblocco:** un caso reale, `grep FOREIGN-TOOLUSE ~/.claude/logs/ai-router.log`. **Expected outcome:** finché il grep è vuoto, nessuna azione — un filtro su un fenomeno mai osservato non sarebbe verificabile.

- [ ] **(opzionale) Fascia peak GLM 14–18 Asia/Shanghai dal vivo.** La correttezza è già coperta in modo deterministico da `sviluppo/tests/test_peak_scheduler.py`: quattro bordi con ora iniettata, off-by-one catturato dalla prova del nove. Zero righe `peak-cap` nei log perché le modalità GLM sono poco usate.
  **Expected outcome:** nessuna garanzia aggiuntiva sulla correttezza; solo conferma su traffico reale.

- [ ] **Taratura dell'heartbeat sul traffico vero.** `CTX_GATE_HEARTBEAT_PCT = 0.30` (`src/ai-router-proxy.py:146`, env `AIROUTER_CTX_HEARTBEAT_PCT`).
  **Prossimo step:** se dopo una settimana il catalogo resta senza entry `ctx_gate`, abbassare la soglia. Costo nullo: il catalogo deduplica per (modalità, azione) e il throttle resta 300 s per chat.

## Chiusi — indice per fase

Una riga per fase; il dettaglio sta nei commit e nel checkpoint consolidato.

- **19 lug — stabilizzazione.** Crash-loop `UnboundLocalError` (`80b6ab5`), root cause dei 404 MiniMax = header `Host` inoltrato (`a5c31af`), isolamento tool centralizzato (`462f181`), retry-storm mix-am con THINK non bloccante (`c3a2ca8`), 3 bug della modalità GLM pura (`6e51322`), sistema di debug centralizzato.
- **21–22 lug — lavori lunghi e modalità miste.** TRIM INTERCEPT rimosso (`71497ae`), THINK sintetico su Haiku con VERIFY a campione (`d2bb6aa`), streaming passthrough sulle miste (`cd6b4ef`, `5f6c9f5`), retry 429 certificato (`e6fb4fb`, `e5dc339`, `582eca2`), esecutore non più cieco a system e immagini (`bb84a41`, `447d1e6`), warning spostati nell'header `x-ai-verify` (`3b5a664`), isolamento web search (`a227ea3`).
- **23–25 lug — refactor.** Merge totale con WIP worktree scartato come superseded, pipeline morte e codice morto rimossi (`2bd4212`, `0174894`), refactor tunnel con i selettori di modello Anthropic fuori dal router (`99dcc0d`), `VERSION_MANIFEST` riallineato 7/7, `version_drift_check.py` riscritto da stub.
- **26 lug — guardrail e igiene.** Guardrail di contesto G1/G2/G3/G5/G6 (`c58fa1e`, `ada9af7`, `1031dde`, `10de32c`), switch di modalità a voce rimosso, `inverse`/`interactive` eliminati ovunque, 86 override per-chat azzerati, manuali riscritti sui 6 modi reali (`4759195`), id `server_tool_use` sanificati (`a9d039c`), guardia response-side sull'isolamento tool (`a85e505`), mix-gm smentito con misura.
- **AQ Backlog — chiuso.** AQ-RL1/RL2/FIX1 (`8e40532`), AQ-REF1 (`e8fc50c`), AQ-REF2 (`3a64731`), AQ-REF3/4/5 (`0280326`, poi `src/pipelines/` eliminata dal refactor tunnel), AQ-REF6 (limiter già indipendenti), AQ-REF7 (`70dc5e2`), AQ-TEST (`1e40859`).

## Note di manutenzione

- `PROJECT-TOD.md` è superato da questo file: il suo backlog AQ risulta chiuso e la sezione «Done» duplica commit già consolidati qui.
- `PROMPT_CONTINUA_20260726.md` è il prompt di ripresa della sessione del 26/07: superato dal checkpoint consolidato.
- I 97 checkpoint di sessione in `.claude/checkpoints/` sono sostituiti da `CP_20260727_1600.md`. La cartella è in `.gitignore:17`, quindi vive solo sul filesystem.
