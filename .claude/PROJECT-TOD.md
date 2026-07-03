---
name: project-ai-router-switch-tod
type: progetto
status: attivo
updated: 2026-06-29
---

# Project Global TOD — ai-router-switch

**Main HEAD**: cb2c4f1 · **Branch**: fix/audit-4modes-p0-p1 · **Updated**: 2026-07-03 05:35

## ✅ Done (recenti, evidence-gated)

- [x] **CARD-FIX-9988** — Fix pulsanti switch card GUI: puntavano a proxy `:9988` rimosso → ora `POST :8787/admin/mode/{mode}` + mode da `GET /health` · commit `cb2c4f1` pushato, round-trip curl verificato + test utente live (2026-07-03)
- [x] **C1/H1/H2/M1/M3/L7/M2/L1/L3/L4/L6a/OPPOSE** — Audit 4-modi + 12 fix P0-P4 (3 crash/bug reali, escalation coerenza, pulizia 7 orfane, OPPOSE modello utente) · commits `4762406` `9813e5a` `334760c` (2026-07-02 20:30-20:50)
- [x] **D36** — Parser JSON OPPOSE/THINK irrobustito per preamboli + code-fence + oggetti multipli · `ea3fb1f` (2026-07-02 01:28, all 6 parser tests green)
- [x] **D36a** — Inverse M3 orchestra + Opus critica + M2.7 code esegue (alias MINIMAX_ORCHESTRATOR_MODEL forzato) · `ea3fb1f` + fix commit
- [x] **D35** — Minimax mode redesign: M3 orchestra (mai esegue) → executor inferiore ACT · commit pushato (M2 sceglie dinamicamente)
- [x] **D34** — OAuth marker Claude Code fix (sk-ant-oat01 richiede marker esatto nel system, fasi THINK/OPPOSE non lo avevano) · `a4d9a77` + refactor etichette
- [x] **D34** — Gerarchia mixed 4 regole bloccanti + 429 escalation immediata · `1730b3b` (pushed) + `ed24e2d` (fix SSE rewrite)
- [x] **D30** — Stress test 4 modalità in carico reale — OK su sessione 2026-06-30 (mixed T0/T1 live, M3 esegue, no blocchi)
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

## 🚫 Deferred / Blocked

- [~] **D32** — Test carico distribuito (k6) — non urgente, fail-mode già coperto da release+timeout

## Cross-ref

- Session TOD corrente: `.claude/session-TOD.md` (snapshot live via hook)
- Checkpoint: `.claude/checkpoints/CP_20260628_1115.md`
- Vault mirror: `~/Obsidian/Memoria/progetti/ai-router-switch/PROJECT-TOD.md`