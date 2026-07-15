# ai-router-switch — TODO

## Attivo
- [ ] Test: !router da DUE chat contemporanee per verificare isolamento reale (nota: store ai-router-chats.json già mostra override distinti per session-id → isolamento confermato lato dati, manca solo test live simultaneo)

## Completati
- [x] Fix !router Bug 1 — regex anchor: system-reminder prima di `!router` rompeva `.match()` con `^` → `.search()` senza anchor (commit 3d300a4, 2026-07-15)
- [x] Fix !router Bug 2 — "No conversation found" su chat nuova: `_synthetic_message` hardcodava model `ai-router` → eco `_data['model']` del client (commit bd5bee8, 2026-07-15)
- [x] Fix !router Bug 3 (regressione di Bug 1) — anchor rimosso del tutto era troppo permissivo, matchava `!router` anche in prosa → anchor selettivo `(?:^|>|\n)\s*!router` (commit 240b68c, 2026-07-15)
- [x] Fix m3-code/ask-m3 not_found_error in mode anthropic: model MiniMax → forward_minimax (header minimax-oob, commit b017b36) + config nomi canonici + ripristino symlink ~/.claude/scripts→src (2026-07-14)
- [x] Fix glm-minimax: context-exceed 400 → shrink retry + allow_minimax=True
- [x] Fix mixed fast-path: check context-exceed prima di relay(up)
- [x] Fix T2 pipelines: aggiunge {400} ai fallback check (inverse T2 R1, mixed escalation, mixed T2 R1)
- [x] Fix NameError aiohttp.ClientError in glm_backend.py (import top-level + aiohttp.web.Response)
- [x] Fix ai-mode CLI alias mancanti (mixgm/mixag/mixam)
- [x] Fix per-chat mode override ignorato (get_mode controlla chat override prima del globale)
- [x] Fix !router isolation per-chat: race condition su IP 127.0.0.1 → conversation_fingerprint (commit a3c0812)
- [x] GLM multimodal complete: tier VISION/MULTIMODAL, image/video generation, route_glm_request
- [x] THINK → ACT → VERIFY pattern: tutte le 4 modalità (glm, glm-minimax, anthropic-glm, minimax)
