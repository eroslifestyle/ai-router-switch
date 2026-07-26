# PIANO — Guardrail context window (2026-07-26)

Stato: **proposto, non approvato**. HEAD al momento della stesura: `9fdde3f`. Router `active`, mode globale `mix-am`.

## 1. Problema (verificato, non congetturato)

Il 400 "Context a 114%. Usa /compact." **lo genera il nostro router**, non Anthropic:
`src/ai-router-proxy.py:368-372`.

Log sessione reale `sid:8eed9bb5-23d9-4b44-a07d-1f6b0166f4fe` (mode=mix-am):

```
10:17-10:19  ctx: COMPACT 90.1→93.5%       rewrite proattivo OK, chat viva
10:25:03     ctx: ERROR threshold 114.2%   400 sintetico
10:25:29     ctx: ERROR threshold 115.1%   il /compact dell'utente, bloccato uguale
11:46:42     ctx: ERROR threshold 105.4%   sessione ancora morta 1h20 dopo
```

### Root cause: due misure disallineate

| Chi | Limite usato | Riferimento |
|---|---|---|
| `CTX.pre_check()` decide `error` | modello **per-modalità** → `mix-am` = `MiniMax-M2.7` = **200K** | `context_manager.py:216-225` |
| `rewrite_for_context()` decide se ridurre | **provider effettivo** (fix 2d68c1c) → richiesta THINK = Anthropic = **1M** | `ai-router-proxy.py:352-360` |

Quando la richiesta va ad Anthropic il rewrite **non riscrive** (234K stanno in 1M), quindi il
codice cade nel ramo `elif ctx_check["action"] == "error"` e restituisce 400.

Riproduzione deterministica (body sintetico 935.568 b, model `claude-opus-*`, mode `mix-am`):

```
pre_check: action=error est=233.892 limit=200.000 pct=1.169
resolve_route(mix-am, claude-opus-…) → provider=anthropic
rewrite(ctx_model=claude-opus-4-8):  changed=False  935.568b   ← nessuna riduzione → 400
rewrite(ctx_model=MiniMax-M2.7):     changed=True    92.286b
```

### Aggravanti

1. `/compact` invia un body **più grande** del turno normale → stesso gate → sessione irrecuperabile.
2. Stima = `byte//4` (`token_counter.py:11`): uno screenshot PNG incollato (≈1,4 MB base64)
   pesa ~350K token stimati contro ~1.600 reali. Da solo può spingere oltre soglia.

## 2. Guardrail

Ordine di esecuzione: **G2+G1** (sbloccano), poi **G3**, **G5**, **G6**, **G4** per ultimo.

### G1 — Il router non emette MAI un 400 di contesto di testa sua
- File: `src/ai-router-proxy.py:366-372`.
- Rimuovere il `return web.json_response(..., status=400)`. Il gate resta **osservatore**: logga,
  notifica, tenta il rewrite — poi **inoltra comunque** e lascia decidere l'upstream, che conosce
  i token veri. `post_check`/`_compact_or_clear` resta la rete di sicurezza sul 400 reale.
- Motivo: un 400 nostro blocca anche l'unica via d'uscita (`/compact`) → sessione morta.
- Accettazione: nessun `ctx: ERROR threshold` seguito da 400 emesso dal router; il turno arriva a upstream.

### G2 — Misura unica, sul provider che riceverà davvero il body
- File: `src/context_manager.py` (`pre_check` accetta `model` opzionale; se assente resta il
  default per-modalità), `src/ai-router-proxy.py:328` (passa il modello risolto — la risoluzione
  `_early_provider`/`_early_model` è già a monte, righe 313-323).
- Regola: il limite è quello del **provider effettivo risolto**, lo stesso che usa il rewrite.
- Accettazione (unit):
  - `pre_check("mix-am", model="claude-opus-4-8")` con 234K token → `limit=1.000.000`, `action=ok`;
  - `pre_check("mix-am", model="MiniMax-M2.7")` con 234K token → `limit=200.000`, `action=error`.

### G3 — Stima token image-aware
- File: `src/token_counter.py` (nuova `estimate_tokens_body(body: bytes) -> int`), usata da
  `context_manager._estimate_tokens` e `context_rewrite`.
- Regola: ogni blocco `{"type":"image","source":{"type":"base64","data":…}}` costa un forfait
  `IMAGE_TOKEN_COST = 1600` e i suoi byte base64 **escono** dal conteggio `char/4`.
- Limite dichiarato: i `document`/PDF base64 restano fuori scope in questo giro, documentarlo.
- Accettazione: body con 1 immagine da 1,4 MB → stima < 5K token (oggi ~350K).

### G4 — Non mutilare le richieste di compattazione
- Con G1 il compact non viene più **bloccato**. Resta però il rischio che il rewrite proattivo lo
  **tagli**, producendo un riassunto monco.
- **Prerequisito**: identificare empiricamente il marker reale della richiesta di compattazione di
  Claude Code (loggare i body dei turni `/compact` e leggerli). **Nessun marker inventato**: se non
  lo si osserva, G4 non si implementa.
- Accettazione: da definire dopo la verifica del marker.

### G5 — Degradazione progressiva invece di resa
- File: `src/context_rewrite.py`, fra ATTEMPT 1 e ATTEMPT 2.
- Nuovo passo: drop dei blocchi immagine più vecchi + troncamento dei `tool_result` grossi,
  prima di scendere agli ultimi 2 messaggi. In ogni caso ritornare sempre il body più piccolo ottenuto.
- Accettazione: body con immagini → riduzione senza perdere gli ultimi N messaggi testuali.

### G6 — Telemetria del gate
- Riga strutturata in `logs/debug-errors.jsonl`: `{fp, mode, model, provider, est_tokens,
  limit_client, limit_backend, action, rewritten_bytes}`.
- Serve a misurare se il gate protegge davvero o se danneggia soltanto.

## 3. Test

Nuovo `sviluppo/tests/test_context_guardrails.py` (stile allineato a `test_pipeline.py`),
copertura unitaria G1/G2/G3/G5. Funzionale su istanza isolata riusando il pattern di
`test_glm_modes.sh` (override porta via `AIROUTER_PORT_MODE_JSON`, **mai** toccare `:8787` live).

## 4. Esecuzione, deploy, commit

- Catena mix-am: THINK+VERIFY a me (Opus 5), **codice scritto da MiniMax M2.7** (`m3-code`/`m3x`).
  Escalation solo esecuzione dopo 2 fallimenti: Sonnet → Opus → Fable.
- Deploy: il live gira con `cwd=/…/ai-router-switch/src` ed entrypoint via symlink
  (`~/.claude/scripts/ai-router-proxy.py` → repo). Modifiche nel repo + restart bastano:
  **nessuna copia fisica da sincronizzare**.
- Restart con la sequenza obbligatoria di `CLAUDE.md`: `is-active` → `cat | grep -i restart` →
  restart → `sleep 3` → `is-active`.
- Commit Conventional + push dopo verifica.

## 5. Do NOT

- Non toccare relay / retry / OAuth (hot path già certificato).
- Non reintrodurre il TRIM INTERCEPT (bug strutturale, rimosso 2026-07-21).
- Non modificare le soglie 80/88/90: gli alert restano utili, è il **blocco** a essere sbagliato.
- Non inventare il marker `/compact` (vedi G4).
- Non testare contro `:8787` live.

## 6. Failed approaches (storico rilevante)

- Sostituire il body corrente con uno salvato al turno precedente (TRIM INTERCEPT) → messaggi
  stantii, tool call ripetuti, contaminazione cross-chat. Rimosso in `71497ae`.
- Gattare lo shrink MiniMax senza allineare il `pre_check` (`2d68c1c`, oggi) → è esattamente
  la causa di questo blocco.
