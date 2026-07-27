# Prompt di continuazione — ai-router-switch — 2026-07-27

Da incollare come primo messaggio di una chat nuova. Rigenerare quando il consolidato cambia.

---

Lavori su `ai-router-switch` in `/mnt/backup/Dropbox/1 Programmazione/Progetti/ai-router-switch`.

**Prima di rispondermi, leggi questi due file, che sono l'unica fonte di stato del progetto:**
1. `.claude/checkpoints/CP_20260727_1600.md` — checkpoint unico consolidato (sostituisce 97 checkpoint di sessione)
2. `.claude/TODO.md` — 5 voci aperte con il rispettivo sblocco

Non cercare altri file di stato: `PROJECT-TOD.md` e i checkpoint per-sessione sono stati rimossi il 2026-07-27 (commit `1a683a9`); i vecchi stanno in `.claude/checkpoints/archivio-checkpoint-20260623-20260726.tar.gz`, da aprire solo se ti servono per archeologia.

**Che cos'è il progetto.** Proxy aiohttp su `127.0.0.1:8787` che instrada Claude Code verso Anthropic, MiniMax o GLM secondo 6 modalità (`anthropic`, `minimax`, `glm`, `mix-am`, `mix-ag`, `mix-gm`). È un **tunnel trasparente**: riscrive il campo `model` e inoltra. La gerarchia THINK/ACT/VERIFY vive in `~/.claude/CLAUDE.md`, mai nel proxy.

**Stato al 2026-07-27 16:00** — riverificalo, non fidarti di questa riga: HEAD `1a683a9`, working tree pulito a meno di `BUG-CATALOG.md` che si rigenera dal traffico; servizio `ai-router` active, `/health` 200, 7 porte in LISTEN; 30 moduli in `src/`; suite verde.

**Situazione di lavoro.** Non c'è lavoro eseguibile in autonomia: le 5 voci aperte attendono tutte un'evidenza esterna (un `/compact` reale, un 400 catturato col corpo, un `FOREIGN-TOOLUSE` nei log, traffico GLM in fascia peak, una settimana di telemetria). Se ti chiedo qualcosa di nuovo, parti da lì; se ti chiedo di «continuare», la risposta corretta è dirmi che non c'è nulla di sbloccato e proporre cosa misurare.

**Regole vincolanti di questo progetto:**
- **Non intercettare mai un messaggio che inizia con `!router `.** Deve arrivare al proxy, che lo confina a questa chat. Se rispondi tu, l'isolamento è già rotto. `ai-mode <mode>` invece è **globale** su tutte le chat: eseguilo solo se te lo chiedo esplicitamente.
- **Restart del router solo con la sequenza completa**: `systemctl --user is-active ai-router` → `systemctl --user cat ai-router | grep -i restart` → restart → `sleep 3` → `is-active`. Mai `kill`/`pkill` a freddo. Ogni restart uccide gli SSE attivi di tutte le chat: avvisami prima.
- **Il gate di contesto resta osservatore**: il router non deve mai emettere un 400 di contesto, perché bloccherebbe anche `/compact` e renderebbe la sessione irrecuperabile.
- **Il codice di progetto lo scrive l'esecutore della catena della modalità attiva**, non tu direttamente: tu pianifichi, verifichi con evidenza letterale, applichi e committi. Esenzioni: file sotto `~/.claude/` e micro-edit ≤15 righe.
- **Non sei l'unico agente sul working tree**: rileggi `git log --oneline -3` e `git status` prima di ogni commit e prima di ogni patch a righe fisse.
- **Vietato testare contro `:8787` live**: usa `_make_app` in-process con upstream finto, o `AIROUTER_PORT_MODE_JSON`.

**Trappole diagnostiche già pagate (non ricascarci):**
- `fail_tracker.py` e `streaming_relay.py` stanno nella **ROOT** del repo, non in `src/`.
- I `.bak` in `src/` contengono simboli morti (`_mixed_haiku_rescue`, `_trim_context_after_response`): un `grep -r` senza filtro li fa sembrare vivi.
- Il log del proxy è `~/.claude/logs/ai-router.log`, non `journalctl`. Il catalogo è `logs/BUG-CATALOG.jsonl` nella project root; la chiave persistita è `example_detail`, non `detail`.
- I timestamp di `debug-errors.jsonl` anteriori al 26/07 sono ora locale con un `Z` fasullo: confrontali solo con orari locali.
- `py_compile` non è una prova: `NameError` e `ImportError` dentro funzioni esplodono solo a runtime.
- `src/graphify-out/` può essere cache stale.

**Come voglio che lavori:** italiano, output denso, niente recap non richiesti. Grep mirato invece di Read integrali. Prima di dire «fatto/testato/funziona», mostrami l'output letterale che lo prova. Se un tuo passo fallisce, dimmelo con l'errore vero.

Comincia leggendo i due file e dandomi tre righe di stato: HEAD reale, servizio, e qual è la prima voce aperta.
