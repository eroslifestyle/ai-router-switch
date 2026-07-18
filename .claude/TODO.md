# ai-router-switch — TODO

## Attivo
- [ ] **Test live !router da DUE chat contemporanee** — isolamento lato store già OK (`ai-router-chats.json` sid distinti). Aprire 2 chat VSCode su `:8787`, mandare `!router minimax` in una e `!router anthropic` nell'altra, verificare che ogni chat risponda con la propria modalità.

## AQ Backlog (non bloccanti)
- [ ] AQ-REF1 — Estrarre `StreamingRelay` come classe (~2h)
- [ ] AQ-REF2 — FailTracker centralizzato
- [ ] AQ-REF3 — BasePipeline + PipelineConfig
- [ ] AQ-REF4 — `providers/base.py`
- [ ] AQ-REF5 — `pipelines/primitives.py`
- [ ] AQ-REF6 — TokenRateLimiter unificato
- [ ] AQ-REF7 — ContextManager
- [ ] AQ-TEST — Test pipeline

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
