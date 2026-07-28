# ai-router-switch — TODO unico

**Aggiornato:** 2026-07-28 07:55 · **HEAD di riferimento:** `a935a00` · **Stato:** 4 voci aperte. Il 27–28/07 campagna di 6 fix sul context rate (F1, F8–F11): vedi `.claude/checkpoints/CP_20260728_0736.md`.

Questo file unifica il vecchio `TODO.md` (32 sezioni, 5 blocchi «Attivo» sparsi), `PROJECT-TOD.md` (fermo al 2026-07-18, backlog AQ nel frattempo chiuso) e le voci residue dei 97 checkpoint di sessione. Contesto completo: `.claude/checkpoints/CP_20260727_1600.md`. Il dettaglio verboso dei completati resta recuperabile con `git show fc3fbe8:.claude/TODO.md`.

## Aperti

- [x] **Misurare l'effetto reale dei fix sul context rate (F1, F8–F11). CHIUSA il 2026-07-28: la baseline era un artefatto di misura, non una perdita di token.**
  **Misura richiesta:** 4.779 richieste ≥20k senza cache / 187.348.464 token sui 7 giorni, contro la baseline di 5.215 / 201.283.024. Calo grezzo −8,4%, ma **il confronto non ha significato**: 5 dei 7 giorni sono ancora traffico misurato con la telemetria rotta.
  **Root cause della baseline:** `9fdde3f` (26/07 **10:10**) — «decomprimi il buffer prima di estrarre usage e cache tokens». Con `auto_decompress=False` il relay leggeva byte gzip come utf-8, non trovava mai `message_start`, quindi loggava `cache_read`/`cache_creation` **sempre a 0** e stimava `input_tokens` con `chars//4`. Il commit lo dichiara: «solo telemetria, nessun impatto sulle risposte». **La baseline è stata scritta il 27/07 su dati già invalidati il 26/07.**
  **Prova (taglio al minuto del fix):** prima di `9fdde3f` 5.213/5.312 big senza cache = **98,1%**; dopo, 2/387 = **0,5%**. Il 5.213 pre-fix coincide col 5.215 della baseline. Correlazione oraria: restart 26/07 10:09:01, il nocache passa da 100% (ore 06–09) a 54,3% (ore 10) a 0% (dalle 11 in poi, per due giorni). Il 28/07: 38 richieste big, **0 senza cache**, hit rate 80,2%.
  **Effetto dei 6 fix su questa metrica: non misurabile** — era già a zero 36 ore prima che F1 (`f05ea98`, 27/07 23:05) fosse deployato. Non significa che F1 fosse inutile: il bersaglio era reale ma di **due ordini di grandezza più piccolo**, cioè i **121 `ctx: proactive rewrite`** loggati il 23/24/26 luglio, ognuno dei quali distruggeva i `cache_control`. Il 28/07, dopo `49c26a3`, i rewrite sono **0**.
  **Nessuna causa da cercare altrove**, e nessuna estensione della diagnostica del relay: la perdita apparente è spiegata e chiusa. Da non ripetere: citare 5.215 / 201M come token persi.

- [x] **G4 — non mutilare le richieste di compattazione. CHIUSA il 2026-07-27: nessuna azione sul proxy.**
  **Marker trovato** (dal binario `~/.local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`, non dai log): il messaggio user del turno di compattazione inizia sempre con `CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.` — funzioni `WAo` (compact pieno, costante `MPy`) e `Usd` (varianti `up_to`/`from`, `$Py`/`OPy`), coda `Bsd`, costruito via `zr({content})` come messaggio user. È version-dependent: cambia col bundle del CLI.
  **La vecchia condizione di sblocco era inefficace.** Il 27/07 tre `/compact` manuali reali hanno prodotto **zero** entry `ctx_gate`: `_should_record` ([`src/ai-router-proxy.py:445`](../src/ai-router-proxy.py#L445)) richiede `action in (compact,error)` oppure `pct >= 0.30`, e su provider anthropic 218.349 token = 21,8%. Un compact manuale a contesto basso non passerà **mai** da quel percorso.
  **Esito della correlazione** (`sviluppo/tools/compact_correlate.py`, commit `61f25f9`): 3 INTATTO (27/07, tutti `mix-am` → provider `anthropic`, safe limit 800.000), 2 non applicabili (17/07: modalità `mixed` pre-refactor-tunnel, 100% del traffico riscritto su `MiniMax-M3` dalle pipeline server-side poi rimosse in `99dcc0d`; né il gate osservatore né il rewrite attuale esistevano). **Zero mutilazioni sul codice di oggi.**
  **Rischio residuo — RIDOTTO dai fix del 28/07 (`2f7f0cc`, `49c26a3`).** La vecchia analisi assumeva `get_safe_input_limit("MiniMax-M2.7")` = 160.000 e **MiniMax-M3 = 200.000**. Dalla doc ufficiale M3 ha **1.000.000** di contesto e il buffer ora riserva il `max_tokens` reale: in `minimax` il THINK è M3 con safe 968.000, quindi i 3 preTokens citati (170641, 209148, 218349) **non attivano più il rewrite**. Resta la sola leg ACT su M2.7 (safe 172.800), dove quei valori scatterebbero ancora. **Riaprire solo se** un compact reale avviene sulla leg ACT di `minimax`/`mix-gm`: allora rieseguire il correlatore.

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
