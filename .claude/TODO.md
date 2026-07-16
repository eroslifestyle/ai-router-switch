# ai-router-switch — TODO

## Attivo
- [ ] Test: !router da DUE chat contemporanee per verificare isolamento reale (nota: store ai-router-chats.json già mostra override distinti per session-id → isolamento confermato lato dati, manca solo test live simultaneo)
- [ ] Bug anthropic-glm 8775 — `glm_think_act_verify` riga 485: `tier, _ = await classify_tier(...)` → ValueError "too many values to unpack" (firma di `classify_tier` cambiata, chiamante non aggiornato). glm-only e glm-minimax (8787) NON affetti, ma anthropic-glm dedicated porta 8775 KO. Da fixare separatamente.

## Completati
- [x] Fix mixgm/glm-minimax crash post-riavvio PC (2026-07-17, dopo restart)
  - 3 bug concatenati scoperti uno dopo l'altro via journalctl + log + test:
    1. `~/.claude/scripts/glm_backend.py` era stub vecchio (12 lug, 14KB) senza `build_glm_think_body`. Python importava quello al posto di `src/glm_backend.py` (16 lug, 27KB) perché `sys.path[0]` = directory dello script eseguito (= `~/.claude/scripts/`, anche se il file è un symlink a `src/`). Fix: symlink di tutti i 6 moduli duplicati (`glm_backend.py`, `peak_scheduler.py`, `context_rewrite.py`, `model_context_map.py`, `summarizer.py`, `token_counter.py`) da `~/.claude/scripts/` → `src/`.
    2. 3 occorrenze di `aiohttp.web.Response(body=act_raw, ...)` nei think-act-verify (`_pipeline_minimax_orchestrate`, `_anthropic_glm_think_act_verify`, `_glm_minimax_think_act_verify`): `aiohttp` non era importato come modulo (solo `from aiohttp import web`). Fix: `web.Response(...)` (alias già presente).
    3. Stesse 3 righe facevano `return relay(web.Response(...))` — doppio bug: (a) `relay` è `async def` quindi serviva `await relay(...)`, (b) `relay` si aspetta un `aiohttp.ClientResponse` (con `.content`), non un `web.Response`. Fix: `return web.Response(...)` diretto (è già una response completa, niente streaming da fare).
- [x] Fix !router Bug 1 — regex anchor: system-reminder prima di `!router` rompeva `.match()` con `^` → `.search()` senza anchor (commit 3d300a4, 2026-07-15)
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
