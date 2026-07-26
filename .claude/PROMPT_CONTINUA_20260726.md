# Prompt di continuazione — ai-router-switch, 2026-07-26

> Blocco copiabile per aprire una nuova sessione. Self-contained: chi lo riceve non ha accesso alla chat precedente.
> Generato a fine `/wiki all` del 2026-07-26. HEAD di riferimento: `8f82101`.

````
# Continua: ai-router-switch — 5 voci residue, tutte sbloccabili solo da te

Stai riprendendo lo sviluppo di ai-router-switch (proxy :8787 che instrada Claude Code verso Anthropic/MiniMax/GLM in 6 modalità). Sei l'orchestratore: pianifichi e verifichi, deleghi l'esecuzione del codice all'esecutore della modalità attiva.

INIZIA COSÌ: leggi il checkpoint /mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch/.claude/checkpoints/CP_20260726_2015.md, poi esegui `git log --oneline -3` e `git status` per verificare il drift, poi procedi.

## Obiettivo
Chiudere le 5 voci residue del TODO. Nessuna è eseguibile in autonomia: ognuna aspetta un input esterno preciso.

## Stato
Fatto e verificato: 9 bug risolti, test da 2 a 9 file tutti verdi (ognuno con prova del nove), TODO da 20 a 5. HEAD 8f82101 su origin/main, working tree pulito, servizio ai-router active (health 200, models 200, 7 porte).

Prossimo passo — dipende da quale input arriva per primo:
1. Se l'utente esegue un /compact reale → leggi logs/BUG-CATALOG.jsonl, filtra kind=="ctx_gate", leggi example_detail.last_user_prefix e ricava il marker del turno di compattazione DAI DATI. Expected outcome: una stringa reale osservata, mai inventata. Solo allora implementa G4.
2. Se l'utente autorizza a toccare il relay → implementa il fix del buffering stream:true in mix-gm DIETRO FLAG env con default OFF, usando sviluppo/tests/test_relay_characterization.py come rete di sicurezza (deve restare verde a flag spento). Expected outcome: TTFB misurato prima/dopo, test verdi in entrambe le posizioni del flag.

## Do NOT
- NON toccare relay / retry / OAuth senza autorizzazione esplicita dell'utente: è hot path certificato.
- NON inventare il marker /compact: senza evidenza nei log, G4 non si implementa.
- NON reintrodurre import dentro funzione di nomi già importati a livello modulo (presidiato da sviluppo/tests/test_module_names_resolved.py).
- NON far emettere al router un 400 di contesto: il gate resta osservatore.
- NON copiare systemd/ai-router.service dal repo all'installato: si copia solo installato → repo.
- NON eseguire /wiki all di iniziativa: è user-only.
- NON testare contro la porta 8787 live: usa _make_app in-process con upstream finto, o AIROUTER_PORT_MODE_JSON.
- NON assumere di essere l'unico agente sul working tree: una seconda istanza ha committato 5 volte il 26/07. Rileggi git log e git status prima di ogni commit e prima di ogni patch a righe fisse.

## Failed approaches (non riprovare)
- Delegare senza passare le firme verbatim → costante rimossa lasciandone 5 usi, body parsato come lista invece che dict, assert e fixture inventati.
- Fidarsi di "compila" o dei test statici come prova: tre bug passavano py_compile ed erano codice mai eseguito.
- Sospettare il codice reale quando fallisce un fake: aiohttp usa CIMultiDict, ClientResponse.release() è SINCRONO, esiste .closed.
- Cercare il corpo degli errori upstream nel campo snippet: sta in upstream_error.
- Delegare spec lunghe a m3-code: output troncato a 8192 token senza errore (usa --max 16000); su prompt molto lunghi muore a ~120 s con 0 token.

## Risorse
- Repo: /mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch
- Checkpoint: .claude/checkpoints/CP_20260726_2015.md
- TODO: .claude/TODO.md (5 voci aperte)
- Pagina vault: /home/mrxxx/Obsidian/Memoria/progetti/ai-router-switch/sessione-nove-bug-silenziosi-router-20260726.md
- Test: python3 sviluppo/tests/<nome>.py e python3 -m pytest tests/test_role_routing.py
- Log router: ~/.claude/logs/ai-router.log · traceback delle eccezioni: journalctl --user -u ai-router
- Commit chiave: c58fa1e ada9af7 1031dde 18abe59 a9d039c

## Criterio di completamento
Una voce è chiusa quando: il fix è committato e pushato, la suite completa è verde, il router è stato riavviato con la procedura (is-active → verifica Restart=always → restart → sleep → is-active + health 200), e il comportamento è stato provato sul percorso reale, non solo letto nel codice.
````

## Le 5 voci residue, in dettaglio

| # | Voce | Cosa la sblocca |
|---|---|---|
| 1 | **G4 — non mutilare le richieste di compattazione** | Un `/compact` reale dell'utente. Il prompt di compattazione non è persistito nei transcript (esistono solo `compact_boundary` e `isCompactSummary`, strutture locali di Claude Code, non il body HTTP): la cattura è armata via `last_user_prefix` nella telemetria `ctx_gate`. |
| 2 | **Buffering `stream:true` in mix-gm** | Autorizzazione esplicita a toccare il relay. Coperto da `test_relay_characterization.py`. |
| 3 | **Guardia response-side isolamento tool** | Stessa autorizzazione: filtrare un `tool_use` straniero imitato dalla history richiede di intervenire sulla risposta in streaming. |
| 4 | **`/wiki all` in mix-am** | ✅ Eseguito il 2026-07-26 alle 21:58, 6 passaggi completati senza abbandono. |
| 5 | **Fascia peak GLM dal vivo** | Opzionale: la logica è già verificata in modo deterministico da `test_peak_scheduler.py` (bordi 13/14/17/18 con ora iniettata). |

## Note d'uso

- Il blocco è racchiuso in un fence a **quattro backtick** perché al suo interno compaiono backtick singoli: un fence a tre verrebbe chiuso prematuramente e il resto uscirebbe come markdown non copiabile.
- Il vault Obsidian **non ha un remote git**: i suoi commit restano locali. Il repo di progetto invece è pushato su `origin/main`.
