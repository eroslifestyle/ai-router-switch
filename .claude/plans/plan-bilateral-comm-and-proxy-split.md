# Piano: comunicazione bilaterale 3-modello + frazionamento ai-router-proxy.py

## Fonti lette (9/9 documenti in `sviluppo/Protocolli di comunicazione LLM/`)

Piano definitivo identificato: **`PIANO-FINALE-SINTESI-3-MODELS-2026-07-19.md`** (13:45, il più recente, dichiarato "sostituisce tutti i piani precedenti nella cartella"). Consolida `PIANO-FINALE-verificato-2026-07-19.md` (13:23) e `Anthropic_PIANO-FINALE-verificato-2026-07-19.md` (13:08, quasi identico al 13:23, stessa sessione Sonnet con solo differenze di forma). I 6 documenti restanti (P1 `comunicazione-multi-modello-2026.md`, P2 `mixed-mode-bilateral-redesign-2026-07-19.md`, P3 `piano-comunicazione-bilaterale-2026.md`, `Minimax_piano-universale-3-models-2026.md`, `audit-comparativo-piani.md`, `prompt-ricerca-universale-3-models.md`) sono bozze/ricerca preliminare esplicitamente scartate dal piano finale.

**Scoperta chiave del piano finale (evidence-gate su HEAD `b470dfc`)**: dei "5 bug reali" su cui si basava la vittoria di P2 nell'audit comparativo, **solo 1 è ancora presente**. Gli altri 4 erano già stati risolti da commit precedenti alla stesura dei documenti stessi. Di conseguenza lo scope reale è minuscolo: niente `HandoffPacket`, `anti_loop_guard.py`, `ContextBudgeter`, `semantic_cache.py`, MCP/A2A layer, LLMLingua — tutti scartati esplicitamente come over-engineering per bug inesistenti.

**Verifica personale sul codice attuale (HEAD `3706de4`, più recente di `b470dfc`)**: ho controllato con grep/Read che i commit intermedi (`fec9b39`→`3706de4`, tutti su GLM system-role e tool-isolation) NON toccano né il trim-state né `_glm_minimax_think_act_verify`. **Entrambi i problemi del piano finale sono ancora presenti oggi**, confermato:
- `_trim_context_after_response` (riga 2259) usa ancora `Path.write_bytes()` diretto, zero `tempfile`/`os.replace`/lock in tutto il file (grep vuoto).
- `_glm_minimax_think_act_verify` (riga 3454) logga `verify_text[:100]` ma ritorna sempre `act_raw` incondizionatamente — VERIFY resta osservazionale, non enforcing.

## Vincolo tassativo — 3 modalità "solo" pure

Le modalità pure (solo-anthropic/solo-minimax/solo-glm) usano isolamento tool dedicato (`_strip_foreign_branded_tools` in `ai-router-proxy.py`, `strip_foreign_branded_tools_for_glm` in `glm_backend.py`, commit `462f181`, già completato). **Nessuna delle modifiche di questo piano tocca quella logica**: i due fix riguardano `_trim_context_after_response` (funzione condivisa da tutte le modalità ma che non altera il branching di modalità) e `_glm_minimax_think_act_verify` (modalità mista `mix-gm`, non una modalità pura). Guardrail: dopo ogni fix, rieseguire `sviluppo/tests/test_glm_modes.sh` e un test manuale `!router anthropic|minimax|glm` per confermare zero regressioni sull'isolamento tool.

---

## FASE A — Fix comunicazione bilaterale (nel monolite, PRIMA dello split)

Si fa prima dello split perché sono diff piccole e mirate (~45-95 righe), più facili da verificare in un file che già si conosce; lo split successivo diventerà un puro move meccanico del codice già corretto.

### A1. Trim-state atomico (bug reale, obbligatorio)
- File: `src/ai-router-proxy.py`, funzione `_trim_context_after_response` (riga 2259) + blocco read→unlink in `handle()`.
- Fix: scrivi su `tempfile.NamedTemporaryFile(dir=stessa_dir, delete=False, suffix='.tmp')` poi `os.replace(tmp, target)` (atomico POSIX). Aggiungi dict globale `trim_locks: dict[str, threading.Lock]` per-fingerprint, acquisito attorno a `exists()`→`read_bytes()`→`unlink()`.
- Stdlib puro (`tempfile`, `os.replace`, `threading.Lock`), zero nuove dipendenze.
- Stima: ~15 righe.

### A2. VERIFY enforcing in mix-gm (gap reale, obbligatorio)
- File: `src/ai-router-proxy.py`, funzione `_glm_minimax_think_act_verify` (riga 3454) + prompt builder GLM verify (in `src/glm_backend.py`, funzione `build_glm_verify_body`).
- Fix: nel prompt di VERIFY chiedi un marcatore esplicito (`"Se l'output è incoerente, inizia con: INCOERENTE: [motivo]"`). Dopo la verifica, se `verify_text.startswith("INCOERENTE")` → 1 retry di ACT con nota di correzione iniettata nel `system`; se il retry fallisce ancora, ritorna `act_raw` con prefisso `[VERIFY-WARNING]`. Cap a 1 retry (coerente col pattern R1/R2 già nel codice).
- Stima: ~30 righe.

### A3. (Opzionale, valutare con l'utente prima di procedere) Marcatori testuali nel piano THINK
- `_build_think_body` (riga 2025): aggiungere `OBIETTIVO:`/`VINCOLI:`/`NON FARE:` come testo libero nel prompt. Zero rischio parse-fail. Utile solo se poi si implementa A4.

### A4. (Opzionale, valutare con l'utente) HHEM gate su ACT/VERIFY
- Nuovo file `src/hhem_gate.py` (~40 righe): wiring minimo verso il servizio locale HHEM (`:4002`) su risposte >300-500 char in `mix-am`/`mix-gm`. **Prerequisito da verificare prima**: raggiungibilità del servizio `:4002` dal processo proxy in produzione (non assunta, va controllata con una chiamata reale).

### A5. Audit grep-first (nessun nuovo modulo)
- Verificare che le fallback chain di `mix-ag`/`mix-gm`/`_glm_execute_with_chain` terminino in step finiti (il piano le dichiara già lineari a 2-3 hop, mai ricorsive — solo da confermare con lettura mirata, non un audit esteso).

### A6. Test
- `sviluppo/tests/test_trim_race.sh` (richieste concorrenti stesso fingerprint, verifica zero corruzione).
- `sviluppo/tests/test_mixgm_verify_retry.sh` (forza incongruenza, verifica 1 retry poi stop).
- Rieseguire `sviluppo/tests/test_glm_modes.sh` esistente (istanza isolata, non tocca `:8787` live).

### Esplicitamente NON fare (per decisione del piano finale, evidence-based)
`handoff_packet.py`/IMCP dataclass, `anti_loop_guard.py`, `semantic_cache.py`, `context_budgeter.py`, LLMLingua (torch ~2GB), layer MCP/A2A dedicato, KV-Cache compression, CoVe full 4-step.

---

## FASE B — Frazionamento di `src/ai-router-proxy.py` (4322 righe, 90 funzioni/classi top-level)

### Perché ora e in questo ordine
Il file supera di molto la soglia (200-400 righe ottimali, 800 hard-max, split modulare a 500 LOC per `modular-architecture.md`). È un servizio **systemd single-process critico e sempre attivo** (`ai-router` su `:8787`) — niente big-bang rewrite. Split incrementale, un modulo per volta, ciascuno commitabile/revertibile singolarmente, con verifica live ad ogni step (regola `router-restart-safety` di CLAUDE.md: mai restart senza confermare `systemctl --user is-active ai-router` prima/dopo).

### Mappa moduli proposta (ordine = ordine di estrazione, dal più sicuro al più rischioso)

| # | Nuovo file | Contenuto (funzioni attuali) | Rischio |
|---|---|---|---|
| 1 | `src/router_utils.py` | `_decompress_upstream`, `_body_has_images`, `_orig_flags`, `_analyze_body_structure`, `_strip_images_body`, `_force_no_stream`, `_text_from_message`, `extract_last_user_text`, `classify_t2`, `strip_unsupported_fields`, `_has_image_blocks`, `_has_server_tools`, `_has_web_search_tool`, `_is_context_too_large_for_minimax` | **Minimo** — funzioni pure, zero stato condiviso |
| 2 | `src/router_ratelimit.py` | `_classify_429`, `RateLimitExhausted`, `MinimaxRateLimiter`, `_minimax_alert` | **Basso** — classe self-contained, ma singleton: va istanziata una volta sola e importata ovunque (mai due istanze) |
| 3 | `src/router_debug.py` | `_rotated_jsonl_path`, `debug_capture`, `debug_errors`, `debug_last`, `debug_stats`, `debug_trace` | **Basso** — endpoint diagnostici isolati, dipende solo da `log()` |
| 4 | `src/router_auth.py` | `get_minimax_key`, `_read_oauth_from_file`, `_load_oauth_token`, `_reload_oauth_token` | **Basso-medio** — cache token in-memory, va preservata come stato condiviso singolo |
| 5 | `src/router_context.py` | `_build_shrink_summary`, `_smart_truncate`, `_smart_sample_middle`, `_trim_context_after_response` (con fix A1 già dentro), `_shrink_and_retry_minimax`, `_repair_message_sequence`, `_try_shrink_body`, `_is_context_exceed_400` | **Medio** — qui vive il fix A1, quindi va estratto DOPO che A1 è testato in produzione nel monolite |
| 6 | `src/router_state.py` | `get_file_mode`, `_current_mode`, `get_mode`, `conversation_fingerprint`, `_resolve_chat_fingerprint`, `_load_chats`, `_save_chats`, `get_chat_mode`, `set_chat_mode`, `clear_chat_mode`, `parse_router_command`, `_router_reply_text`, `_synthetic_message` | **Medio-alto** — contiene la logica `!router` (regola CLAUDE.md "isolamento per-chat", massima attenzione: NON alterare la semantica dell'isolamento fingerprint/sessione durante il move) |
| 7 | `src/router_forward.py` | `forward_anthropic`, `forward_anthropic_direct`, `forward_minimax`, `_fwd_minimax_short`, `_forward_minimax_generative`, `_route_v1_images/videos/music/audio_speech`, `_minimax_est_tokens`, `_SyntheticResponse`, `_synthetic_429`, `_synthetic_context_exceed`, `_retry_forward`, `_call_full`, `remap_body_for_minimax`, `_strip_server_tools_for_minimax` | **Alto** — cuore del traffico verso le API upstream, massima superficie di regressione |
| 8 | `src/router_pipelines_mixed.py` | `_anthropic_system`, `_build_finalize_body`, `_build_think_body`, `_parse_plan_text`, `_parse_think_json`, `_build_act_body`, `_pipeline_think_act`, `_serve_minimax_vision`, `_anthropic_rescue`, `_mixed_haiku_rescue` | **Alto** — pipeline THINK/ACT completa |
| 9 | `src/router_pipelines_minimax.py` | `_build_minimax_think_body`, `_pick_minimax_executor`, `_build_minimax_act_body`, `_pipeline_minimax_orchestrate` | **Alto** |
| 10 | `src/router_pipelines_glm.py` | `_anthropic_glm_think_act_verify`, `_glm_minimax_think_act_verify` (con fix A2 già dentro), `_glm_execute_with_chain`, `_handle_glm_mode`, `_sse_events_from_message`, `_prepare_sse_response`, `_send_sse_message` | **Alto** — stessa logica: estrarre DOPO che A2 è testato in produzione |
| 11 | `src/ai-router-proxy.py` (resta) | `log`, `log_exc`, `_path_allowed`, `handle()` (dispatcher, si accorcia da ~540 a poche decine di righe di puro routing/import), `_make_app`, `_run_multiport`, `main` | — punto di ingresso, ultimo a essere toccato |

`handle()` (il dispatcher da 541 righe) **non viene spezzato in questa fase**: resta nel file principale e si limita a chiamare le funzioni ora importate dai moduli. Spezzare la logica di dispatch stessa è un secondo intervento, solo dopo che l'estrazione dei moduli 1-10 è stabile in produzione per un periodo di osservazione.

### Regole di esecuzione per ogni singolo modulo estratto
1. Un modulo per commit, un modulo per volta — mai batch di più moduli nello stesso commit.
2. Move puro: stesso corpo funzione, stessa firma. Le uniche modifiche accettate sono `import` e riferimenti a stato condiviso (es. `MinimaxRateLimiter` singleton, `trim_locks`, cache oauth) — mai logica.
3. Dopo il move: `python -m py_compile` su tutti i file toccati, poi `sviluppo/tests/test_glm_modes.sh` (istanza isolata).
4. Prima del restart del servizio live: verificare `systemctl --user is-active ai-router` (deve essere `active`) e `Restart=on-failure`/`always` in `systemctl --user cat ai-router` — per la regola di progetto CLAUDE.md "Restart router — regola obbligatoria".
5. Restart, poi smoke test live su almeno le 3 modalità solo + 1 modalità mista + 1 modalità GLM, con `!router <mode>` isolato alla chat di test (mai `ai-mode` globale per il test).
6. Se una verifica fallisce: `git revert` del singolo commit del modulo, non rollback multiplo.
7. `graphify update .` dopo ogni modulo per tenere il grafo di conoscenza coerente col codice reale.

### Stato condiviso da preservare esplicitamente (rischio principale dello split)
- `MinimaxRateLimiter`: singleton — una sola istanza globale, importata da `router_forward.py` e da `handle()`, mai reistanziata in due moduli.
- Cache OAuth token (`_load_oauth_token`/`_reload_oauth_token`): stato in-memory, un solo owner (`router_auth.py`), altri moduli lo importano, non lo duplicano.
- `trim_locks` (nuovo da A1): dict globale in `router_context.py`, unico.
- Store chat-mode (`~/.claude/ai-router-chats.json`, letto/scritto da `router_state.py`): nessuna modifica di formato, solo move.

---

## Sequenza complessiva proposta

1. **FASE A** (2-4 giorni stimati dal piano finale): A1 → A2 → test → commit+push (2 commit separati, uno per fix, come da regola "mai commit a metà").
2. Decisione con l'utente su A3/A4 (opzionali) — chiedere prima di implementarli, non sono bug-fix.
3. **FASE B**: estrazione moduli 1→10 nell'ordine di rischio crescente indicato, un commit per modulo, verifica live ad ogni step.
4. Solo a fine Fase B, valutare (in un piano separato, non ora) se spezzare anche `handle()`.

## Rischi principali e mitigazioni
- **Router è servizio production single-point** (`:8787`, ai-router-policy) → mai restart senza conferma systemd, mai batch di più moduli nello stesso ciclo di verifica.
- **Stato condiviso duplicato per errore** (rate limiter, oauth cache, trim locks) → checklist esplicita sopra, verificata a ogni modulo.
- **Comando `!router` rotto durante lo split di `router_state.py`** (modulo #6, il più delicato dopo i forward) → test manuale `!router` dedicato oltre a `test_glm_modes.sh`.
- **Regressione sulle 3 modalità pure** → nessuna fase tocca `_strip_foreign_branded_tools`/`strip_foreign_branded_tools_for_glm`, ma vanno comunque testate ad ogni step per garanzia (tool isolation dipende da `handle()` che chiama funzioni ora sparse in più moduli).
