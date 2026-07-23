# PIANO — Riscrittura router in stile OpenAI Agents SDK

> Redatto 2026-07-22. Basato su: analisi `openai/openai-agents-python` (28k⭐) + audit evidence-based del router attuale (3 subagenti, riga per riga). NON ancora approvato/implementato.

## Obiettivo

Adottare il modello architetturale dell'OpenAI Agents SDK dentro il router per **eliminare la classe di bug di comunicazione tra modelli**. Non riscrivere il trasporto HTTP: riscrivere il *layer di orchestrazione* (THINK→ACT→VERIFY) in modo unificato e tipizzato.

---

## Parte 1 — Cosa fa l'OpenAI Agents SDK (le 4 idee da portare)

### Idea A — Run loop unico con `next_step` tipizzato
Un solo `while True`. Ogni turno l'orchestratore produce un output classificato in uno stato esplicito:
`NextStepFinalOutput | NextStepHandoff | NextStepRunAgain | NextStepInterruption`.
La priorità di decisione è fissa e leggibile in un punto solo. Non ci sono `if status in {...}` sparsi.

### Idea B — `input_filter` per-transizione (handoff_filters.py)
Prima di passare il contesto da un agente all'altro, una funzione normalizza la history per il target. Nel SDK filtra la conversation history; noi lo generalizziamo a: strip immagini se target text-only, strip server-tool, inietta marker OAuth, floor max_tokens, repair sequenza tool.

### Idea C — Modello per-agente risolto a runtime (`get_model`)
Ogni agente ha `agent.model`; la risoluzione avviene a ogni turno. Cambiare `current_agent` cambia automaticamente il backend. Un solo punto di risoluzione, nessun `forward_*` duplicato al call-site.

### Idea D — Distinzione handoff vs agent-as-tool
- **Handoff** = il sub-agente prende il controllo, riceve la history (filtrata).
- **Agent-as-tool** = l'orchestratore resta al comando, il sub gira in loop annidato e ritorna un risultato.
Il nostro THINK→ACT→VERIFY è concettualmente **agent-as-tool** (l'orchestratore Anthropic/GLM chiama l'esecutore come tool e ne riceve l'output), NON handoff.

---

## Parte 2 — Stato reale del router (audit, non memoria)

### Fatti verificati
- **6 modalità reali**, non 7: `anthropic, minimax, mix-am, mix-ag, mix-gm, glm`. `mixed/inverse/glm-minimax/anthropic-glm` sono alias (`router_mode.py:147`). `inverse` → `minimax` puro.
- **`minimax` puro non orchestra più**: `pipeline_minimax.py:116` è passthrough. Le funzioni THINK minimax (righe 14-67) sono **codice morto** ancora importato.
- **`mix-am` ha 2 implementazioni parallele**: inline in `ai-router-proxy.py:578-729` (T0/T1/T2) + `_pipeline_think_act` (`pipeline_anthropic.py:667`), selezionate da flag `anthropic_leads`/`NEW_PIPELINE`.
- **3 copie quasi-identiche** di THINK→ACT→VERIFY: `_pipeline_think_act` (mix-am), `_anthropic_glm_think_act_verify` (mix-ag), `_glm_minimax_think_act_verify` (mix-gm).
- **3 implementazioni di "digest history"**: `build_think_digest` (pipeline_common), `build_glm_think_body` (glm_backend), `build_think_body` (primitives, **dead code**).
- **2 `_anthropic_system` con stesso nome, comportamento diverso**: uno aggiunge `CLAUDE_CODE_MARKER` (`pipeline_anthropic.py:61`), l'altro no (`primitives.py:55`).
- **Choke-point tool già unificato e funzionante**: `tool_isolation.filter_tools_for_backend()` (2026-07-19). È già un `input_filter` — ma **solo per i tool**.

### Bug reali dal catalog (`logs/BUG-CATALOG.jsonl`)
| kind | occorrenze | natura |
|---|---|---|
| `tool_isolation_strip` | 507 | normalizzazione riuscita loggata come block (non è un bug) |
| `rate_limit_429_exhausted` (anthropic) | 22+42 | catena escalation esaurita |
| `glm_exhausted / glm_429_backoff` | ~20 | fallback GLM esaurito |
| `minimax_fallback_5xx` | 2 | 502 in ACT |
| `mixed_rescue_502` | 1 | rescue chain |
| `relay_error_401/404` | 2 | auth / model marker |

### I 5 punti fragili confermati (dove nascono i bug di comunicazione)
1. **VERIFY/finalize senza `CLAUDE_CODE_MARKER`** su leg OAuth Sonnet/Opus (`pipeline_glm.py:105-111`, `primitives.py:55-125`) → 429 silenzioso.
2. **Nessuno strip server-tool Anthropic verso GLM** (`glm_backend.py` manca l'equivalente di `strip_server_tools_for_minimax`) → GLM 400.
3. **Nessuna `_repair_message_sequence` sul path ACT MiniMax** (`forward_minimax.py:90` importata, mai chiamata) → 400 su tool_result orfani.
4. **Bypass di `build_executor_body` quando piano THINK vuoto** (`pipeline_glm.py:85,195`) → perso completion-guard proprio quando serve.
5. **Commenti "VERIFY" fuorvianti in mix-am** (`_pipeline_think_act` non ha VERIFY) → rischio manutenzione.

**Diagnosi unificante**: ogni bug è una *trasformazione body/history incoerente* applicata in una copia della pipeline ma non nelle altre. La causa radice è la **triplicazione** delle pipeline + la **non-tipizzazione** delle transizioni.

---

## Parte 3 — Architettura target

### 3.1 Un solo run loop tipizzato
Nuovo modulo `src/agent_loop.py`:

```
run_agent_turn(ctx) -> StepResult
  StepResult.next_step ∈ {FinalOutput, Escalate, RunAgain, Interruption}
```

Sostituisce le 3 copie con **una** funzione parametrizzata da un descrittore di modalità:

```
ModeSpec(
  think_backend:  "anthropic" | "glm" | None,   # None = no THINK
  act_backend:    "minimax" | "glm" | "anthropic",
  verify_backend: "anthropic" | "glm" | None,
  act_chain:      [modelli in ordine di escalation],
  rescue:         RescueSpec | None,             # es. Haiku/Sonnet finale
  use_hhem:       bool,
)
```

Le 3 modalità mix diventano 3 `ModeSpec`, non 3 funzioni.

### 3.2 Registry di `input_filter` per-transizione
Nuovo modulo `src/transition_filters.py`. Generalizza `tool_isolation`. Ogni transizione `(from_backend, to_backend)` applica in pipeline una lista di filtri:

```
FILTERS_TO["minimax"] = [
  inject_system_as_message,
  strip_server_tools,          # già esiste in minimax_body
  strip_images_to_placeholder, # già esiste in pipeline_common
  repair_message_sequence,     # ← FIX #3: ora applicato sempre
  filter_tools_for_backend,    # già esiste
  floor_max_tokens(1024),
]
FILTERS_TO["glm"] = [
  system_as_toplevel_string,
  strip_server_tools_for_glm,  # ← FIX #2: nuovo, portato da minimax
  strip_images_to_placeholder,
  repair_message_sequence,
  filter_tools_for_backend,
  clamp_max_tokens(32768),
]
FILTERS_TO["anthropic"] = [
  inject_claude_code_marker,   # ← FIX #1: sempre, anche su VERIFY/finalize
  strip_unsupported_fields,
  filter_tools_for_backend,
  repair_message_sequence,
]
```

**Un solo punto** costruisce il body per un backend: `build_body_for(backend, ctx)`. Elimina i forward sparsi e i builder inline duplicati.

### 3.3 VERIFY come guardrail tipizzato con tripwire
Da `guardrail.py` dell'SDK: la verifica ritorna `GuardrailResult(tripwire_triggered: bool)`. Se triggered → `next_step = RunAgain` (retry ACT) o `Escalate`, non solo un log. Unifica `should_verify` + HHEM + LLM-verify in un solo gate applicato a tutte le mix.

### 3.4 Terminazione ed escalation esplicite
`fail_tracker` resta ma alimenta stati tipizzati: `Escalate(reason, next_model)`. La catena ACT (`[m2.7, M3]`, GLM tiers) diventa dato in `ModeSpec.act_chain`, non loop hardcoded in 3 punti.

---

## Parte 4 — Cosa NON cambia (vincoli)
- Regola "Anthropic non esegue mai" (resta: Anthropic solo THINK/VERIFY/rescue).
- `mix-gm` mai fallback ad Anthropic (resta in `ModeSpec.rescue=None`).
- Router = punto unico switcher: **restart solo con procedura CLAUDE.md** (systemd active + Restart=always verificati).
- Isolamento per-chat `!router` (nessuna intercettazione).
- Trasporto HTTP, streaming_relay, auth: intatti.
- `delega context zero`: formalizzata dentro gli `input_filter` (il target riceve sempre un body autosufficiente e normalizzato).

---

## Parte 5 — Fasi di implementazione (incrementali, ognuna testabile isolata)

> Ogni fase è un commit verificato. Test su istanza isolata (`sviluppo/tests/`, override `AIROUTER_PORT_MODE_JSON`), MAI su `:8787` live.

**Fase 0 — Quick-win bug fix (basso rischio, alto valore)**
Applica i 5 fix puntuali SENZA rifattorizzare, per fermare subito i bug:
- FIX #1: `inject_claude_code_marker` nei 2 punti VERIFY/finalize scoperti.
- FIX #2: `strip_server_tools_for_glm` in `glm_backend`.
- FIX #3: chiamare `_repair_message_sequence` nel path ACT MiniMax.
- FIX #4: usare sempre `build_executor_body` anche con piano vuoto.
- FIX #5: correggere/rimuovere commenti VERIFY fuorvianti; rimuovere dead code.

**Fase 1 — `transition_filters.py`**
Estrai le trasformazioni sparse in un registry per-transizione. Il choke-point `forward_*` chiama `build_body_for(backend, ctx)`. Verifica: stessi byte in uscita di prima (diff golden su `debug-last-sent.json`).

**Fase 2 — `agent_loop.py` + `ModeSpec`**
Sostituisci le 3 copie THINK→ACT→VERIFY con una funzione + 3 spec. Verifica: test per modalità (`test_glm_modes.sh` + nuovi per mix-am).

**Fase 3 — VERIFY-guardrail tipizzato**
Unifica i gate di verifica; tripwire → RunAgain/Escalate. Verifica: HHEM e LLM-verify producono retry, non solo log.

**Fase 4 — Terminazione tipizzata + pulizia**
`StepResult.next_step` esplicito ovunque; rimuovi `mix-am` inline duplicato in proxy; rimuovi dead code minimax. Verifica: full regression su 6 modalità.

---

## Parte 6 — Rischi
- **Router è infra critica**: ogni fase deve lasciare il router funzionante. Fase 0 e 1 sono reversibili e non toccano il dispatch. Fase 2+ tocca il cuore → richiede test isolati estesi prima del merge.
- **Golden-diff obbligatorio**: prima di ogni merge, confronto byte-a-byte del body inviato ai backend (pre/post refactor) per garantire zero regressione di trasporto.
- **Rollback**: ogni fase è un commit isolato; `git revert` singolo se una fase rompe.

---

## Decisione aperta
Partire da **Fase 0** (fix immediati dei 5 bug, basso rischio) e poi valutare Fase 1+ dopo aver visto il catalog pulirsi? Oppure andare diretti alla riscrittura strutturale (Fase 1→4)?

---

## Bug preesistente scoperto durante Fase 1 (da fixare a parte)

`tool_isolation.is_anthropic_server_tool()` (riga 37) ritorna `True` per QUALSIASI tool senza `input_schema`. Ma i tool MCP GLM `mcp__zai__...` non hanno `input_schema` → vengono classificati come "server-tool Anthropic" e strippati anche quando `backend="glm"`. Effetto: in modalità GLM il tool nativo web_search_prime viene rimosso. Fix: `is_anthropic_server_tool` deve escludere i nomi già riconosciuti come GLM/MiniMax-branded prima di applicare la regola "no input_schema". Non tocca la Fase 1 (transition_filters è additivo); da schedulare come fix indipendente in Fase 4/pulizia.

---

## STATO FINALE (2026-07-22) — implementato

Branch `fase2-agent-loop`. Tutti i moduli additivi o dietro flag (default OFF): **router live invariato**.

### Completato
- **Fase 1** `transition_filters.py` — registry input_filter per-backend (testato 3 backend)
- **Fase 2a** `mode_spec.py` — 3 modalità mix come dati (testato + assert regole)
- **Fase 2b** `agent_loop.py` — run loop tipizzato StepType (testato 3 scenari)
- **Fase 3** `verify_guardrail.py` — tripwire unificato (testato con HHEM reale, score 0.0088 su claim falso)
- **Fase 4** cablaggio + pulizia:
  - `agent_loop_glm.py` — adapter mix-ag/mix-gm dietro `AIROUTER_AGENT_LOOP=1`
  - FIX #3 repair MiniMax dietro `AIROUTER_TRANSITION_FILTERS=1`
  - Fix bug tool_isolation (zai in glm), rescue Haiku reale
  - Rimosso dead code minimax (-102 righe) + import morti nel proxy
  - Smoke test proxy: parte con flag ON (mix-ag) e OFF (anthropic), zero errori

### Stash obsoleto risolto
`uncommitted-refactor-pre-restart` analizzato (obsoleto/rotto/regressivo, nulla da recuperare), backup in `docs/sessions/stash-refactor-pre-restart.patch`, scartato con conferma utente.

### Duplicazione temporanea documentata (INTENZIONALE)
Le pipeline classiche `_anthropic_glm_think_act_verify` / `_glm_minimax_think_act_verify` restano come **fallback** (flag OFF). Sono sostituite da agent_loop_glm quando `AIROUTER_AGENT_LOOP=1`. **Rimozione delle classiche: SOLO dopo che il flag ON ha girato in produzione senza regressioni** (evita rottura mix-ag/mix-gm senza fallback).

### Prossimo step (sessione dedicata)
1. Attivare `AIROUTER_AGENT_LOOP=1` + `AIROUTER_TRANSITION_FILTERS=1` in produzione, monitorare
2. Se stabile N giorni → rimuovere le 2 pipeline classiche GLM (zero duplicazioni definitivo)
3. Cablare anche mix-am su agent_loop (oggi usa ancora `_pipeline_think_act`)

---

## VALIDAZIONE PRODUZIONE-SIMILE (2026-07-23) + decisione mix-am

### Test live isolato superato (`sviluppo/tests/test_agent_loop_glm.sh`)
- mix-ag: pipeline classiche (OFF) e agent_loop (ON) → entrambi HTTP 200
- mix-gm: entrambi HTTP 200
- mix-gm sotto fallimento totale (tutto 502) → ritorna 502, **MAI rescue Anthropic** (regola inviolabile confermata sotto traffico reale)

### Decisione su mix-am — NON cablato su agent_loop (motivata)
mix-am usa `_pipeline_think_act`, che è GIÀ una singola funzione unificata (non una delle 3 copie duplicate). Il problema che il refactor risolveva era la TRIPLICAZIONE (mix-am/mix-ag/mix-gm quasi identiche) — ma di fatto le copie erano le 2 GLM (`_anthropic_glm_think_act_verify`, `_glm_minimax_think_act_verify`), ora unificate da agent_loop_glm. `_pipeline_think_act` ha guard di pre-processing complessi (shrink, web_search block, server_tools bypass, vision, context-exceed) che un adapter dovrebbe replicare fedelmente: alto rischio di divergenza, beneficio nullo (mix-am non soffre di duplicazione). Cablarlo sarebbe simmetria estetica, non risoluzione di un problema → **deliberatamente escluso**.

### Stato: "completa tutto" raggiunto per la parte che risolve problemi reali
- Le 3 copie duplicate: unificate (agent_loop + agent_loop_glm), validate
- I bug di comunicazione: fixati (marker, server-tool, repair, tool_isolation, rescue Haiku)
- Dead code: rimosso
- Stash obsoleto: scartato
- mix-am: lasciato su `_pipeline_think_act` (già unificato, nessun problema da risolvere)

### Unico step residuo (produzione, non codice)
Rimuovere le 2 pipeline classiche GLM DOPO che `AIROUTER_AGENT_LOOP=1` ha girato in produzione N giorni senza regressioni. È una decisione operativa (attivare il flag e monitorare), non un'attività di sviluppo. Finché il flag è OFF, le classiche sono l'unico path attivo e la duplicazione è dormiente (agent_loop_glm non viene mai invocato).
