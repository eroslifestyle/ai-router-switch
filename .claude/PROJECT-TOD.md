---
name: project-ai-router-switch-tod
type: progetto
status: attivo
updated: 2026-06-29
---

# Project Global TOD — ai-router-switch

**Main HEAD**: 9892a84 · **Branch**: main · **Updated**: 2026-06-29 22:39

## ✅ Done (recenti, evidence-gated)

- [x] **D33** — Fix tool_use agentici mixed/inverse: `classify_t2` bypassa pipeline su `tools` + routing agentico → Anthropic (MiniMax-M3 non fa tool_use) · `9892a84` (live :8773/:8774 = 429 firma Anthropic)
- [x] **D27** — Bug fix OAuth beta header in `forward_anthropic_direct` (verify T2 inverse) · 998658b
- [x] **D28** — Bug fix `_call_full` connection leak: `up.release()` su tutti i branch · 998658b
- [x] **D29** — Strippo `context_management`/`mcp_servers`/`thinking` da body MiniMax · 998658b

## 🔄 In Progress

- [ ] **D30** — Stress test 4 modalità in carico reale (Claude Code + app) · sessione 2026-06-28
      Owner: sessione corrente
      Done when: 10+ richieste/giorno per modalità senza blocchi/429 non gestiti

## ⬜ Backlog

- [ ] **D31** — Monitoring esposto: `/__router_health` arricchito con breaker state + inverse/mixed fail counters
      Comando: `curl http://127.0.0.1:8787/__router_health | jq` dopo aver aggiunto `_breaker` e `_inverse_fails` al JSON
      Done when: response JSON include `breaker_state`, `inverse_fails_per_chat`, `mixed_anthropic_leads`

## 🚫 Deferred / Blocked

- [~] **D32** — Test carico distribuito (k6) — non urgente, fail-mode già coperto da release+timeout

## Cross-ref

- Session TOD corrente: `.claude/session-TOD.md` (snapshot live via hook)
- Checkpoint: `.claude/checkpoints/CP_20260628_1115.md`
- Vault mirror: `~/Obsidian/Memoria/progetti/ai-router-switch/PROJECT-TOD.md`