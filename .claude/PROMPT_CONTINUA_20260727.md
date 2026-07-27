# Prompt di continuazione — ai-router-switch — 2026-07-27 (aggiornato dopo la chiusura di G4)

Da incollare come primo messaggio di una chat nuova. Rigenerare quando il consolidato cambia.

---

# Continua: ai-router-switch

Lavori su `ai-router-switch` in `/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch`. Sei l'orchestratore: pianifichi, verifichi con evidenza letterale, applichi e committi — l'esecuzione del codice di progetto la deleghi all'esecutore della catena della modalità router attiva. Non hai memoria della sessione precedente oltre a questo prompt.

INIZIA COSÌ: leggi `.claude/checkpoints/CP_20260727_1600.md` (checkpoint unico consolidato) e `.claude/TODO.md` (4 voci aperte), poi esegui `git log --oneline -3` e `git status` per verificare il drift, poi rispondimi con tre righe: HEAD reale, stato del servizio, prima voce aperta.

Non cercare altri file di stato: `PROJECT-TOD.md` e i checkpoint per-sessione sono stati rimossi il 2026-07-27 (commit `1a683a9`); i vecchi stanno in `.claude/checkpoints/archivio-checkpoint-20260623-20260726.tar.gz`, da aprire solo per archeologia.

## Obiettivo

Far avanzare le 4 voci aperte del TODO, o chiuderne una con evidenza concreta, senza introdurre codice per fenomeni mai osservati.

## Che cos'è il progetto

Proxy aiohttp su `127.0.0.1:8787` che instrada Claude Code verso Anthropic, MiniMax o GLM secondo 6 modalità (`anthropic`, `minimax`, `glm`, `mix-am`, `mix-ag`, `mix-gm`). È un tunnel trasparente: riscrive il campo `model` e inoltra. La gerarchia THINK/ACT/VERIFY vive in `~/.claude/CLAUDE.md`, mai nel proxy.

## Stato — riverificalo, non fidarti di queste righe

Fatto e verificato: HEAD `e1284e7`, working tree pulito a meno di `BUG-CATALOG.md` che si rigenera dal traffico. Servizio `ai-router` active, NRestarts 0, `/health` 200, porte in LISTEN 8771 8772 8773 8775 8776 8777 8787. Test: 11/11 file `.py` di `sviluppo/tests/` eseguiti singolarmente OK, più `tests/test_role_routing.py` 48 passed.

G4 («non mutilare le richieste di compattazione») è stata CHIUSA il 27/07 senza toccare il proxy: marker `/compact` ricavato dal binario del CLI, correlazione sui 5 compact storici con esito 3 INTATTO e 2 non applicabili, zero mutilazioni sul codice attuale.

Prossimo passo esatto — voce TODO-1, «400 Anthropic residui»: strumentare `router_debug.capture()` perché registri il corpo del 400 upstream, oggi con `snippet` e `url` vuoti. Expected outcome misurabile: dopo un 400 reale, in `logs/debug-errors.jsonl` una entry `kind=relay_error_400` con il campo `upstream_error` non vuoto (oggi il file ne contiene 52 senza corpo utile). Il campo esiste già in `src/router_debug.py:232`, troncato a 2000 caratteri: verifica prima perché arrivi vuoto, invece di aggiungerne uno nuovo.

## Poi (in ordine)

1. Guardia tool stranieri, da rilevazione a filtro: agire SOLO se `grep FOREIGN-TOOLUSE ~/.claude/logs/ai-router.log` restituisce un caso reale. Finché è vuoto, nessuna azione.
2. Taratura dell'heartbeat: `CTX_GATE_HEARTBEAT_PCT = 0.30` in `src/ai-router-proxy.py:146` (env `AIROUTER_CTX_HEARTBEAT_PCT`). Se dopo una settimana il catalogo resta senza entry `ctx_gate`, abbassarla. Caso concreto a supporto: i 3 `/compact` manuali del 27/07 stavano al 21,8% e non hanno lasciato traccia.
3. (opzionale) Fascia peak GLM 14–18 Asia/Shanghai dal vivo: la correttezza è già coperta in modo deterministico da `sviluppo/tests/test_peak_scheduler.py`, serve solo conferma su traffico reale.

## Do NOT

- NON intercettare mai un messaggio che inizia con `!router `: deve arrivare al proxy, che lo confina a quella chat. Se rispondi tu, l'isolamento è già rotto. `ai-mode <mode>` è invece globale su tutte le chat: eseguilo solo su richiesta esplicita.
- NON riavviare il router se non con la sequenza completa: `systemctl --user is-active ai-router`, poi `systemctl --user cat ai-router | grep -i restart`, poi il restart, poi `sleep 3`, poi `is-active`. MAI `kill`/`pkill` a freddo. Ogni restart uccide gli SSE attivi di tutte le chat: avvisa prima.
- NON far emettere al router un 400 di contesto: il gate resta osservatore, altrimenti blocca anche `/compact` e rende la sessione irrecuperabile.
- NON scrivere tu il codice di progetto: lo scrive l'esecutore della catena della modalità attiva. Esenzioni: file sotto `~/.claude/` e micro-edit ≤15 righe.
- NON assumere di essere l'unico agente sul working tree: rileggi `git log --oneline -3` e `git status` prima di ogni commit e di ogni patch a righe fisse.
- NON testare contro `:8787` live: usa `_make_app` in-process con upstream finto, oppure `AIROUTER_PORT_MODE_JSON`.
- NON usare `python3 -m pytest sviluppo/tests/`: dà 46 passed e 4 ERROR `fixture 'h' not found`, perché `test_gate_e2e.py` e `test_mixgm_stream_ttfb.py` usano un harness interno via `__main__`. Esegui i file singolarmente con `python3 sviluppo/tests/test_X.py`. Per `role_routing` invece pytest va bene: `python3 -m pytest tests/test_role_routing.py -q`.
- NON cercare `fail_tracker.py` e `streaming_relay.py` in `src/`: stanno nella ROOT del repo. I `.bak` in `src/` contengono simboli morti e un `grep -r` senza filtro li fa sembrare vivi.
- NON cercare le righe del proxy con `journalctl`: è vuoto. Il log è `~/.claude/logs/ai-router.log`; il catalogo è `logs/BUG-CATALOG.jsonl` nella project root, dove la chiave persistita è `example_detail`, non `detail`.
- NON considerare `py_compile` una prova: `NameError` e `ImportError` dentro funzioni esplodono solo a runtime.
- NON fidarti di `src/graphify-out/`: può essere cache stale.

## Failed approaches (non riprovare)

- Attendere un evento su un percorso gated da una soglia: la cattura del marker `/compact` viveva nel ramo che registra solo con `pct >= 30%`, mentre un compact manuale avviene a contesto qualsiasi. Tre compact reali, zero entry.
- Correlare per finestra temporale senza fingerprint: in `~/.claude/logs/router-usage.jsonl` il campo `chat` vale sempre `default`, mai un `sid:`. Due sessioni che compattano a 25 s di distanza si attribuiscono lo stesso record.
- Leggere un dato del 17/07 come se valesse per il codice di oggi: quel giorno il 100% del traffico `mixed` finiva su MiniMax-M3 per le pipeline server-side rimosse in `99dcc0d`. Un input upstream molto minore del contesto client era normale by design.
- Saltare il degrado sulle richieste di compattazione: su una leg stretta scambierebbe un riassunto povero con un compact fallito, cioè un 400 secco. Se mai servisse, vale solo con la clausola «salta il degrado solo se il body sta comunque nel limite del provider risolto».
- `m3-code` e `ask-m3` attraverso `:8787`: risposta vuota, 0 token, 121,5 s — anche in `mix-am`, non solo in `anthropic`. Via funzionante: LiteLLM diretto su `http://127.0.0.1:4000/v1/chat/completions`, modello `minimax-m2.7-hs`, chiave `LITELLM_PI_CLIENT_KEY` da `~/.secrets/litellm-pi-client.env` (attenzione: `LITELLM_OPENAI_COMPAT_KEY` contiene il riferimento letterale `$LITELLM_PI_CLIENT_KEY`, non il valore).
- Implementare una voce TODO senza riverificare che il bersaglio esista: è già successo due volte, col buffering mix-gm già risolto da un refactor e con G4.

## Risorse

- Project root: /mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch
- HEAD: e1284e7 (e1284e75d36a413b1e625dc7e84f6a18da3d8344)
- Commit della sessione del 27/07 pomeriggio: 61f25f9 (correlatore), e1284e7 (docs G4)
- Checkpoint consolidato: .claude/checkpoints/CP_20260727_1600.md
- TODO: .claude/TODO.md
- Correlatore compact: sviluppo/tools/compact_correlate.py
- Log proxy: ~/.claude/logs/ai-router.log · debug: logs/debug-errors.jsonl · catalogo: logs/BUG-CATALOG.jsonl
- Pagina vault: /home/mrxxx/Obsidian/Memoria/progetti/ai-router-switch/g4-compact-marker-e-chiusura-20260727.md

## Fatto quando

La voce TODO-1 è chiusa quando esiste una entry `relay_error_400` in `logs/debug-errors.jsonl` con `upstream_error` popolato e una diagnosi riproducibile, oppure quando è dimostrato con i log che il fix `a9d039c` li ha chiusi tutti. In entrambi i casi: `.claude/TODO.md` aggiornato, suite verde con l'esecuzione file-per-file, commit e push fatti.
