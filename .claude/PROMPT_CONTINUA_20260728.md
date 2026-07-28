# Continua: ai-router-switch — chiudere i falsi negativi della suite, poi la mappa dei context window

Stai riprendendo **ai-router-switch**, il proxy LLM su `:8787` (6 modalità: `anthropic`, `minimax`, `glm`, `mix-am`, `mix-ag`, `mix-gm`). Sei l'orchestratore: pianifichi e verifichi, deleghi l'esecuzione all'ACT della catena attiva. **NON scrivi codice di progetto direttamente.**

**INIZIA COSÌ:** leggi `.claude/TODO.md` (voce 1 chiusa il 28/07, contiene l'esito della misura), poi `git status` e `git log --oneline -3` per verificare il drift rispetto a **HEAD `29ba9db`**, poi procedi col prossimo passo.

## Obiettivo

Rendere la suite un misuratore affidabile — oggi 4 test non vengono **mai** eseguiti e la suite riporta comunque «106 passed» — e poi togliere di mezzo per sempre la classe di bug «modello nuovo non mappato», che ha morso due volte il 27–28/07.

## Stato

**Campagna context rate chiusa e misurata.** I 6 fix (`f05ea98`, `d5b3664`, `f33176a`, `ccb1fc3`, `2f7f0cc`, `49c26a3`) sono deployati e pushati. La misura sul traffico reale (commit `29ba9db`) ha però stabilito un fatto che va tenuto presente in ogni ragionamento futuro sui costi:

> **La baseline «5.215 richieste ≥20k senza cache / 201.283.024 token persi» era un artefatto di misura, non una perdita.** Con `auto_decompress=False` il relay leggeva byte gzip come utf-8, non trovava mai `message_start`, e loggava `cache_read`/`cache_creation` **sempre a 0** stimando `input_tokens` con `chars//4`. Fixato da **`9fdde3f` (26/07 10:10)**, cioè **prima** che la baseline venisse scritta il 27/07. Tagliando al minuto del fix: **98,1% (5.213/5.312) → 0,5% (2/387)**. L'effetto dei 6 fix su quella metrica è **non misurabile**: era già a zero 36 ore prima di F1. Il bersaglio reale di F1 erano i **121 `ctx: proactive rewrite`** del 23/24/26 luglio, non 5.215 richieste. Il 28/07: 38 richieste big, **0 senza cache**, hit rate **80,2%**, **0 rewrite**.

Router `active`. Suite: **106 passed, 4 errors** — ma vedi sotto, il 106 dipende dal comando.

## Prossimo passo esatto — expected outcome: 110 test raccolti ed eseguiti, 0 errori di setup

**I 4 errori `fixture 'h' not found` non sono un dettaglio cosmetico: sono 4 test che non girano mai.** Coprono il gate di contesto end-to-end e il TTFB di `mix-gm` — cioè proprio l'area toccata dalla campagna appena chiusa. La suite li conta come `errors` e continua a dire «106 passed», il che rende il verde meno informativo di quanto sembri.

Diagnosi già fatta, **non rifarla**:

- File coinvolti: `sviluppo/tests/test_gate_e2e.py` (3 test) e `sviluppo/tests/test_mixgm_stream_ttfb.py` (1 test).
- Entrambi definiscono in cima una classe `Harness` con `start()`/`stop()` e dichiarano i test come `async def test_x(h)`, ma **nel repo non esiste alcun `conftest.py`** (verificato con `find . -name conftest.py`) e nessuna fixture fornisce `h`. `pytest-asyncio` **è installato** (le fixture `_asyncio_loop_factory`, `anyio_backend` compaiono tra quelle disponibili).
- In fondo a ciascun file c'è un runner che fa `await h.start()` / `await h.stop()` (righe ~124 e ~192): i file **nascono come script standalone** ed è pytest a raccoglierli per via del prefisso `test_`.

Due strade, **scegli e dichiara quale**, non farle entrambe:

- **(a) fixture** — aggiungere un `conftest.py` (o una `@pytest_asyncio.fixture` locale) che costruisce l'`Harness`, fa `start()` e garantisce `stop()` in teardown. Preferibile: i test entrano davvero nella suite.
- **(b) separazione** — se l'harness richiede un'istanza reale del router e non è ragionevole in suite, rinominare i file fuori dal pattern `test_*` e documentarli come script manuali. Onesto, ma **non aumenta la copertura**.

Verifica finale obbligatoria, con **entrambi** i comandi (danno numeri diversi e la differenza va capita, non subita):

```bash
python3 -m pytest -q                    # dalla root: raccoglie sviluppo/tests + ./tests → oggi 106 passed, 4 errors
python3 -m pytest sviluppo/tests/ -q    # solo sviluppo/tests → oggi 58 passed, 4 errors
```

**Expected outcome:** `python3 -m pytest -q` riporta **110 passed, 0 errors** (strada a) — oppure **106 passed, 0 errors** con 4 script esclusi e documentati (strada b). In entrambi i casi la riga `ERROR ... fixture 'h' not found` sparisce.

## Poi (in ordine)

1. **Valutare la Models API di Anthropic** (`/docs/en/api/models/list`, campo `max_input_tokens`) per sostituire la mappa statica di `src/model_context_map.py`. Chiuderebbe per sempre la classe «modello nuovo non mappato» (F8: `claude-opus-5` assente dalla mappa; F10: `MiniMax-M3` a 200k invece di 1M). Attenzione: l'API copre **solo** i modelli Anthropic — MiniMax e GLM resterebbero comunque da mantenere a mano, quindi valuta se il guadagno giustifica una seconda fonte di verità (vedi Do NOT).
2. **Agire sui finding di codeburn** (audit read-only del 27/07, health F 20/100): low-worth-sessions 923M token, context-heavy-sessions 674M, mcp-low-coverage 315M in 30 giorni. Server MCP a copertura zero: `debinex` 0/76 su 81 sessioni, Gmail 1/16 su 702 sessioni, Calendar 0/11. Ogni azione va decisa esplicitamente, **mai** via `codeburn --apply`.
3. **400 Anthropic residui** (voce aperta nel TODO): 36 casi, nessuno dopo la mattina del 26/07 (fix `a9d039c`). `router_debug.capture()` li registra con `snippet` e `url` vuoti → non diagnosticabili. Strumentare la cattura del corpo, oppure dichiarare chiusi.
4. **Taratura dell'heartbeat** (`CTX_GATE_HEARTBEAT_PCT = 0.30`, `src/ai-router-proxy.py:146`, env `AIROUTER_CTX_HEARTBEAT_PCT`): se dopo una settimana il catalogo resta senza entry `ctx_gate`, abbassare la soglia. Costo nullo: il catalogo deduplica per (modalità, azione), throttle 300 s per chat.

## Do NOT

- **NON** citare più «5.215 richieste / 201.283.024 token» come token persi: è un artefatto di misura, dimostrato e chiuso. Se serve una baseline di costo, ricostruiscila **solo su dati posteriori al 26/07 10:10**.
- **NON** estendere la diagnostica del relay oltre le richieste con final `claude-direct` allo scopo di «trovare l'altra causa» della perdita di cache: non esiste altra causa, la perdita apparente era il misuratore fermo.
- **NON** riportare la stima dei token a `char/4` per «recuperare margine»: era esattamente la causa del bug F9. La leva legittima è `max_tokens`, già usata.
- **NON** reintrodurre una seconda tabella di context limit accanto a `model_context_map` — vale anche per l'eventuale adozione della Models API: se la adotti, deve **sostituire** la mappa per i modelli Anthropic, non affiancarla. La doppia verità è già costata un bug (`glm_backend` diceva 115.000 per `glm-4.7` mentre la doc dice 200.000).
- **NON** aggiungere modelli alla mappa senza dichiarare nel commento se il valore è verificato. Oggi **NON** verificati: `MiniMax-M3.5`, `MiniMax-Haiku`, `glm-4.6v`, `glm-4v`, `glm-4`.
- **NON** riavviare il router senza la procedura obbligatoria: `systemctl --user is-active ai-router` → conferma `Restart=always` → restart → `sleep 3` → verifica `active`.
- **NON** eseguire `codeburn --apply` né `codeburn guard install`: toccano `settings.json` e archiviano skill/agenti, in collisione con i nostri hook.
- **NON** toccare `BUG-CATALOG.md`: la modifica nel working tree (` M BUG-CATALOG.md`) è preesistente e va lasciata intatta.
- **NON** eseguire il PLAN handoff (`local-ai-stack/sviluppo/handoff-integration/PLAN.md`): declassato a priorità bassa, vedi Failed approaches.

## Failed approaches (non riprovare)

- **Confrontare il conteggio «≥20k senza cache» su una finestra di 7 giorni** per valutare i fix del 27–28/07: 5 dei 7 giorni sono telemetria rotta pre-`9fdde3f`, il confronto grezzo (−8,4%) non significa nulla. Segmentare sempre al minuto del fix che ha toccato il misuratore.
- **Testare la cache con `curl` senza `--compressed`**: la risposta arriva gzip e il parser JSON muore su `0x8b`.
- **Testare la cache su Sonnet o Opus**: rispondono 429 org-level anche a richieste minuscole. Usare **Haiku**, che passa.
- **Dedurre i byte/token dalla documentazione invece di misurarli**: la doc dice «~30% in più» per il tokenizer nuovo, la misura reale è **+38%**. Per Anthropic esiste `/v1/messages/count_tokens`, gratuito e preciso.
- **Cercare «Magic Chat» su GitHub**: non esiste nell'ecosistema agent. Già provate `magic chat`, `magicchat in:name`, `magic-chat in:name`, `magic + topic:claude-skill`, più una `m3-web`.
- **Eseguire il PLAN handoff**: porta PTY Linux rotto (`script` BSD vs util-linux), upstream macOS-first, patch nostra da mantenere per sempre, e ogni run è una sessione CLI intera.

## Risorse

- **TODO:** `.claude/TODO.md` — voce 1 chiusa con l'esito completo della misura; 4 voci aperte.
- **Checkpoint:** `.claude/checkpoints/CP_20260728_0736.md` (la sua voce «Misurare l'effetto reale dei fix» è **superata** dall'esito nel TODO).
- **Vault:** `/home/mrxxx/Obsidian/Memoria/progetti/ai-router-switch/context-rate-revisione-completa-20260728.md` — sezione finale «Esito della misura sul traffico reale».
- **Sidecar:** `/home/mrxxx/.claude/logs/router-usage.jsonl`. Campi: `ts, status, input_tokens, output_tokens, cache_read, cache_creation, mode, orig, final, client, chat`. **Il modello sta in `final`/`orig`, NON in `model`.**
- **Log router:** `/home/mrxxx/.claude/logs/ai-router.log`, timestamp nel formato `[2026-07-28T07:48:03]` (parentesi quadra iniziale: i regex ancorati a `^[0-9]` falliscono). Marker utili: `ctx: proactive rewrite`, `ctx: WARN`, `ctx: ERROR`, `ctx: backend-bottleneck shrink`, `FOREIGN-TOOLUSE`.
- **File chiave:** `src/context_rewrite.py`, `src/model_context_map.py`, `src/token_counter.py`, `src/context_manager.py`, `src/ai-router-proxy.py` (gate ~riga 379, heartbeat riga 146), `src/glm_backend.py`. Nota: `streaming_relay.py` e `fail_tracker.py` stanno in **root**, non in `src/`.
- **Regole di delega:** `/home/mrxxx/.claude/docs/delega-verifica-contratti.md`.
- **HEAD:** `29ba9db` · branch `main` · modalità attiva `mix-am`.

## Fatto quando

`python3 -m pytest -q` gira senza la riga `ERROR ... fixture 'h' not found`, i 4 test o sono eseguiti davvero o sono esplicitamente fuori suite e documentati come tali, il TODO dice quale delle due strade è stata scelta e perché, e il lavoro è committato e pushato. Il criterio è l'output letterale di pytest, non l'impressione che «adesso è verde».
