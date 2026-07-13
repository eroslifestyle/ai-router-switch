# ai-router-switch — TODO

## Attivo
- [ ] Verificare fix context-exceed su sessione reale (chat lunga con 714+ messaggi)

## Completati
- [x] Fix glm-minimax: context-exceed 400 → shrink retry + allow_minimax=True
- [x] Fix mixed fast-path: check context-exceed prima di relay(up)
- [x] Fix T2 pipelines: aggiunge {400} ai fallback check (inverse T2 R1, mixed escalation, mixed T2 R1)
