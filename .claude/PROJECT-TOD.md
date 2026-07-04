---
name: project-ai-router-switch-tod
type: progetto
status: attivo
updated: 2026-06-29
---

# Project Global TOD — ai-router-switch

**Main HEAD**: cb2c4f1 · **Branch**: fix/audit-4modes-p0-p1 · **Updated**: 2026-07-04 14:28

## ✅ Done (recenti, evidence-gated)

- [x] **CARD-FIX-9988** — Fix pulsanti switch card GUI: puntavano a proxy `:9988` rimosso → ora `POST :8787/admin/mode/{mode}` + mode da `GET /health` · commit `cb2c4f1` pushato, round-trip curl verificato + test utente live (2026-07-03)
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

- [ ] **D37** — E2E live inverse completo (M3 THINK → Opus OPPOSE → M2.7 ACT) su task complesso
      Comando: `echo "inverse" > ~/.claude/ai-router-mode && curl -X POST http://localhost:8787/v1/messages -H "Content-Type: application/json" -H "anthropic-version: 2023-06-01" -d '{"model":"claude-opus-4-8","max_tokens":300,"messages":[{"role":"user","content":"Implementa Redis queue con worker pool e test."}]}'`
      Done when: header `x-ai-verified: minimax-m3-think+opus-oppose+minimax-m2.7-act`, log mostra `OPPOSE iter0: approved=... fixes=...` (critica applicata), esecutore ACT=MiniMax-M2.7
- [ ] **D38** — Mode switch + re-test (mixed vs minimax vs inverse vs anthropic puri) su 4 task diversi (leggero/medio/pesante/agentico)
      Comando: script bash che testa tutte e 4 le porte (8787=dynamic/8771=anthropic/8772=minimax/8773=mixed/8774=inverse) in sequenza
      Done when: tutti gli header x-ai-verified corretti, tutti gli esecutori i modelli attesi (Anthropic/M3 orchestra→executor/M2.7)

- [ ] **D41** — Delta-correction TPM con usage reale (confer M3 2026-07-04): su successo il limiter registra la stima (bytes/4+max_tokens) che sovrastima le risposte corte → sottoutilizzo TPM 30-60%. relay() estrae già l'usage dai chunk SSE (FIX F _acc_buf): retro-alimentare MINIMAX_LIMITER.record con i token reali · P2
      Comando: `grep -n "_acc_buf\|extract_usage" src/ai-router-proxy.py` per il punto di aggancio in relay()
      Done when: snapshot /health mostra tpm_used ≈ usage reale (non stima), unit test delta-correction verde

## 🚫 Deferred / Blocked

- [~] **D32** — Test carico distribuito (k6) — non urgente, fail-mode già coperto da release+timeout

## Cross-ref

- Session TOD corrente: `.claude/session-TOD.md` (snapshot live via hook)
- Checkpoint: `.claude/checkpoints/CP_20260628_1115.md`
- Vault mirror: `~/Obsidian/Memoria/progetti/ai-router-switch/PROJECT-TOD.md`