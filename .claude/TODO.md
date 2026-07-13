# ai-router-switch — TODO

## Attivo
- [ ] Test: !router da DUE chat contemporanee per verificare isolamento reale

## Completati
- [x] Fix glm-minimax: context-exceed 400 → shrink retry + allow_minimax=True
- [x] Fix mixed fast-path: check context-exceed prima di relay(up)
- [x] Fix T2 pipelines: aggiunge {400} ai fallback check (inverse T2 R1, mixed escalation, mixed T2 R1)
- [x] Fix NameError aiohttp.ClientError in glm_backend.py (import top-level + aiohttp.web.Response)
- [x] Fix ai-mode CLI alias mancanti (mixgm/mixag/mixam)
- [x] Fix per-chat mode override ignorato (get_mode controlla chat override prima del globale)
- [x] Fix !router isolation per-chat: race condition su IP 127.0.0.1 → conversation_fingerprint (commit a3c0812)
- [x] GLM multimodal complete: tier VISION/MULTIMODAL, image/video generation, route_glm_request
- [x] THINK → ACT → VERIFY pattern: tutte le 4 modalità (glm, glm-minimax, anthropic-glm, minimax)
