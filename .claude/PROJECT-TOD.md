---
name: project-ai-router-switch-tod
type: progetto
status: attivo
updated: 2026-06-29
---

# Project Global TOD — ai-router-switch

**Main HEAD**: 8e40532 · **Branch**: main · **Updated**: 2026-07-17

## ✅ Done (recenti, evidence-gated)

- [x] **AQ-RL1/RL2/FIX1** — AQ riprogettazione flow gerarchici: (RL1) MinimaxRateLimiter per-model asyncio.Lock — richieste su modelli diversi non serializzano più. (RL2) _gc_fail_dicts() fuori da _counter_lock — O(n) scan non blocca ogni fail increment. Throttling GC ogni 1000 incrementi via contatore atomico. (FIX1) _rewrite_glm_model() riscrive 'model' nel body GLM con il modello richiesto (non il tier effettivo). Commit `8e40532` pushato. (2026-07-17)
- [x] **D44+D45** — Sessione 2026-07-11 pomeriggio: (D44) Fast-path MiniMax in mixed — skip redundant Anthropic THINK quando il modello è già MiniMax (passthrough diretto a forward_minimax). Test 10 iter: diff medio ≈0s (varianza API upstream, non routing). Commit `2f238a6`. (D45) Bypass THINK per task leggeri in mixed — criteri: <200 char, 1 msg, no tools → passthrough diretto (nessuna orchestrazione). Test misurato: ~4.5s vs ~24s (~5x miglioramento latenza). Commit `3bfa966`. Proxy live. (2026-07-11)
- [x] **CHUNK-A/B/C-MERGE** — Merge finale 3 stack development: (A) glm_backend.py fix ClientTimeout + _ANTHROPIC_BLOCKED marker, peak_scheduler.py Asia/Shanghai 14-18 UTC+8, GLMRateLimiter dedicato; (B) context_shrink.py adaptive shrink con HHEM + learn(), shrink_policy.json warm-start 8 modelli guardrail 50-92%; (C) _retry_forward 2x in 5 rescue path call sites; sync 4 support modules (token_counter/model_context_map/context_rewrite/summarizer) da progetto; syntax 7/7 OK; test isolato 8795 GLM passthrough Z.ai reale (not_found_error: glm-4.7 = corretto, modello inesistente); rollout live :8787 PID 509251, health + request 429 verficati. Commits `c70ec52` + `1091599` pushati. (2026-07-12)
- [x] **D37+D38+D41** — Sessione 2026-07-11: (D37) E2E inverse mode — header `x-ai-verified: minimax-m3-think+anthropic-oppose+minimax-m2.7-act` · (D38) test 4 modalità — minimax/mixed/inverse OK, anthropic 429 upstream (proxy OK) · (D41) delta-correction TPM — relay finally corregge entry[1] in-place, log conferma 74-91% overstima corretta (est=1051→88, 136560→26379, 170729→44314). Fix: MOD1 (forward_minimax attach), MOD2 (relay finally correction), MOD3 (_forward_minimax_generative attach). Commit `de18154` pushato, proxy live. (2026-07-11)
- [x] **BUG-HAIKU-502-CTX** — 2 fix root cause 502 su body 4.2MB: (1) summarizer.py — "Can not decode content-encoding: br" — aiohttp default Accept-Encoding include brotli; MiniMax risponde brotli e aiohttp senza libreria installata crasha su `resp.json()` → il summarizer falliva e `_try_shrink_body` ritornava None → rescue path inviava body vuoto ad Anthropic → 400. Fix: `Accept-Encoding: gzip` esplicito. (2) ai-router-proxy.py riga 2969 — confrontava con `MINIMAX_CONTEXT_BYTE_LIMIT` (750k) invece di `ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT` (200k) → Haiku riceveva body >200k. Fix: nuova costante 200k + confronto corretto. Commit `12250ef` pushato, test 4.2MB+gzip → HTTP 200 rescue+main. (2026-07-11)
- [x] **GLM-MODES** — 3 nuove modalità GLM/z.ai (endpoint Anthropic-compatible api.z.ai/api/anthropic): `glm` (:8775, tiering turbo→4.7→5.2 via GLM-5.2 classifier, peak-aware 14-18 Shanghai), `glm-minimax` (:8776, GLM-5.2 THINK → MiniMax ACT → verify), `anthropic-glm` (:8777, Anthropic orch → GLM tiered ACT → verify T2). Moduli glm_backend.py + peak_scheduler.py. Fallback catena GLM→MiniMax→Anthropic. Fallimento peak: blocca 5.2/turbo (3x) → Anthropic. Commit `6cc058c` pushato · 12/12 test PASS · 8 porte live · secrets.sh glm.api_key configurato · systemd aggiornato. (2026-07-11)
- [x] **GUI-CARD-REORGANIZE** — Widget card.py riorganizzato (demo 1: card verticali, 2 sezioni SOLO/MIX, pulsanti ON centrati, descrizioni complete). Commits `008e6e5`→`3d35c1a`. Fix: truncamento testi, glyph, spacing. (2026-07-11)
- [x] **DEBUG-SYSTEM + BUG-ORPHAN-BLOCK** — Sistema debug permanente (/debug/last|errors|stats, logs/debug-errors.jsonl in chiaro gzip-decompresso) `e056993` + root cause finale 502: `_repair_message_sequence` rimuoveva interi messaggi solo se TUTTI block=tool_result, falliva su messaggi misti [tool_result,text] → orfano → Anthropic 400. Fix v2 rimuove blocchi orfani + scarta role=system iniettati `22445f9`. Verificato replay body reale 502→200 (2026-07-09)
- [x] **BUG-502-MIXED** — Catena di 3 root cause del 502 mixed mode: (1) 502 mascherato `df0acd7`, (2) vision→Anthropic `cdcdb41`, (3a) shrink role:system `6e2ffda`, (3b) shrink spaia tool_use/tool_result → `_repair_message_sequence` `c21b67d`. Verificato main: big_tool_body 502→200, worst-case thinking+tool+cache 200, no regressioni (2026-07-09)
- [x] **CARD-FIX-9988** — Fix pulsanti switch card GUI: puntavano a proxy `:9988` rimosso → ora `POST :8787/admin/mode/{mode}` + mode da `GET /health` · commit `cb2c4f1` pushato, round-trip curl verificato + test utente live (2026-07-03)
- [x] **BUG-SHRINK-SYSTEM-LIST** — Fix crash `_shrink_and_retry_minimax` linea 1821: `system` field può essere `list` (non solo `str`) → TypeError `can only concatenate list not str`. Normalizza a str con `isinstance` check. Stessa logica già in `_smart_truncate:1715` · commit `098cb48` pushato, health OK, zero errori post-restart · hotfix live 2026-07-04
- [x] **C1/H1/H2/M1/M3/L7/M2/L1/L3/L4/L6a/OPPOSE** — Audit 4-modi + 12 fix P0-P4 (3 crash/bug reali, escalation coerenza, pulizia 7 orfane, OPPOSE modello utente) · commits `4762406` `9813e5a` `334760c` (2026-07-02 20:30-20:50)
- [x] **D36** — Parser JSON OPPOSE/THINK irrobustito per preamboli + code-fence + oggetti multipli · `ea3fb1f` (2026-07-02 01:28, all 6 parser tests green)
- [x] **D36a** — Inverse M3 orchestra + Opus critica + M2.7 code esegue (alias MINIMAX_ORCHESTRATOR_MODEL forzato) · `ea3fb1f` + fix commit
- [x] **D35** — Minimax mode redesign: M3 orchestra (mai esegue) → executor inferiore ACT · commit pushato (M2 sceglie dinamicamente)
- [x] **D34** — OAuth marker Claude Code fix (sk-ant-oat01 richiede marker esatto nel system, fasi THINK/OPPOSE non lo avevano) · `a4d9a77` + refactor etichette
- [x] **D34** — Gerarchia mixed 4 regole bloccanti + 429 escalation immediata · `1730b3b` (pushed) + `ed24e2d` (fix SSE rewrite)
- [x] **D30** — Stress test 4 modalità in carico reale — OK su sessione 2026-06-30 (mixed T0/T1 live, M3 esegue, no blocchi)
- [x] **D42** — OCR + Web Search MiniMax (docs/minimax-capabilities.md). Scoperta: M3 legge le immagini, M2.7 no (vera root cause vision-gate). OCR: _serve_minimax_vision instrada le immagini a M3 in mixed+minimax (server-tool gate precede vision, model-rewrite, fallback su 5xx/context); rimosso gate morto. Web search: MCP minimax-coding-plan-mcp registrato (web_search+understand_image, connesso) · commit d7ca84c, test live 'Rosso'+SSE OK · vault: progetti/ai-router-ocr-websearch-integration
- [x] **D40** — MinimaxRateLimiter: pacing sui limiti ufficiali (M3 200rpm/10M, M2.7 500rpm/20M × safety 0.8), polling backoff su 429 nel choke point forward_minimax (11 call site), MAI fallback Anthropic sui rate limit; Token Plan 429 → alert + 429 sintetico col reset; MINIMAX_FALLBACK_STATUSES nei 6 path verso Anthropic; _fwd_minimax_short (8s) per i siti via _call_full; Semaphore(8); /health con snapshot limiter; fix 502 relay mancante in _mixed_haiku_rescue · commit 9b345d1, unit+smoke verdi, router live · vault: progetti/ai-router-minimax-rate-limiter
- [x] **D39** — Bug fix server-tool gate in mixed: WebSearch (server tool Anthropic senza input_schema) girava a MiniMax → 400 (2013). History con server_tool_use rompeva ogni richiesta MiniMax successiva. Fix: _has_server_tools gate in _pipeline_think_act + _strip_server_tools_for_minimax in remap. Principio: MiniMax non può eseguire capacita server-side Anthropic. · commit 8530a97 pushato, router riavviato + ping 200
- [x] **D33** — Fix tool_use agentici mixed/inverse: `classify_t2` bypassa pipeline su `tools` + routing agentico → Anthropic (MiniMax-M3 non fa tool_use) · `9892a84` (live :8773/:8774 = 429 firma Anthropic)
- [x] **D27** — Bug fix OAuth beta header in `forward_anthropic_direct` (verify T2 inverse) · 998658b
- [x] **D28** — Bug fix `_call_full` connection leak: `up.release()` su tutti i branch · 998658b
- [x] **D29** — Strippo `context_management`/`mcp_servers`/`thinking` da body MiniMax · 998658b

## ⬜ Backlog

- [ ] **AQ-RL1** — Lock-free `MinimaxRateLimiter.acquire()` — lock solo su read-modify-write modello, pruning lock-free su deque — 1h — Q1 (decisione: risolvi subito)
- [ ] **AQ-RL2** — GC fail dicts fuori dal lock — ogni N incrementi invece di O(n) dentro `_counter_lock` — 30min — Q1
- [ ] **AQ-FIX1** — Fix model rewrite in `forward_glm()` — SSE response scrive `glm-5.2` invece del modello richiesto dall'utente — 30min — Q4 (decisione: correggi subito)
- [ ] **AQ-REF1** — Estrarre `StreamingRelay` come classe — estrae ~380 righe da `handle()` — 2h
- [ ] **AQ-REF2** — FailTracker centralizzato — 1 classe invece di 4 dict sparsi — 1h
- [ ] **AQ-REF3** — BasePipeline + Step + Pipeline — unifica inverse/mixed in `PipelineConfig` come dati — 4h — Q3 (decisione: unifica)
- [ ] **AQ-REF4** — Estrarre `providers/base.py` — ProviderBackend interface — 2h
- [ ] **AQ-REF5** — Estrarre `pipelines/primitives.py` — Step/Pipeline classi — 3h
- [ ] **AQ-REF6** — TokenRateLimiter unificato — rate limiter generico con `RateLimitRule` dataclass — 2h
- [ ] **AQ-REF7** — ContextManager in `context/window.py` — trim/shrink/summarize centralizzato — 2h
- [ ] **AQ-TEST** — Test per 3 pipeline principali (mixed/inverse/minimax) — 6h

## 🚫 Deferred / Blocked

- [~] **D32** — Test carico distribuito (k6) — non urgente, fail-mode già coperto da release+timeout

## Cross-ref

- Session TOD corrente: `.claude/session-TOD.md` (snapshot live via hook)
- Checkpoint: `.claude/checkpoints/CP_20260628_1115.md`
- Vault mirror: `~/Obsidian/Memoria/progetti/ai-router-switch/PROJECT-TOD.md`