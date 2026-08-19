# Migrazione a GLM-5.3 e rianalisi del flow — 18/19 agosto 2026

**Richiesta iniziale:** sostituire GLM-5.2 con GLM-5.3 in tutti i punti del proxy.
Da lì: lettura della documentazione ufficiale z.ai, due passate di rianalisi del flow,
sette difetti chiusi, uno stress end-to-end nuovo, deploy verificato.

**Stato finale:** HEAD `42d0cdc`, servizio `ai-router` active dopo restart delle 07:14,
modalità globale `anthropic`, 0 eccezioni nelle righe di log successive all'avvio.
**785 test verdi** (+33), `test_glm_modes.sh` **11/11**, stress nuovo **23/23**.

**Commit:** `6d458b6` · `af1062f` · `22c29cc` · `74672c0` · `9e354c8` · `42d0cdc`

---

## 1. La migrazione (`6d458b6`)

`glm-5.2` → `glm-5.3` in tutti i punti vivi: `role_routing.GLM_THINK`,
`GLM_MODEL_FOR_TIER[TOP]`, `model_context_map`, `_GLM_CONTEXT_LIMITS`,
`GLM_RATE_LIMITS`, il default di `context_manager`, tre tabelle del proxy,
`README.md`, `scripts/ai-mode`, più i test che citavano il modello.
Audit storici in `sviluppo/audit/`, `BUG-CATALOG.md` e le `*-SPEC.md` **non** sono
stati toccati: sono documenti datati, riscriverli falsificherebbe il racconto di
quando `glm-5.2` era vero.

**Verifica del nome prima di scriverlo** (una richiesta da 16 token per ID):

| chiesto a `api.z.ai/api/anthropic` | risposta |
|---|---|
| `glm-5.3` | 200, `"model":"glm-5.3"` |
| `glm-5.2` | 200, **`"model":"glm-5.3"`** |

`glm-5.2` era già un alias server-side di 5.3: la rinomina rende esplicito ciò che
z.ai faceva comunque. L'echo del campo `model` è la prova più economica che esista
che un ID modello sia servito davvero — vale come metodo, non solo per questo caso.

---

## 2. Cosa ha corretto la documentazione ufficiale (`af1062f`, `22c29cc`)

Pagine lette: `guides/llm/glm-5.3`, `api-reference/llm/chat-completion`,
`guides/capabilities/{thinking-mode,cache}`, `guides/overview/pricing`,
`devpack/notice/usage-revision`, `devpack/latest-model`.

### 2.1 Il weekend non è fascia peak

> «Peak hours: Monday to Friday, 14:00–18:00 Singapore Standard Time (UTC+8)» e
> «usage on weekends will be deducted at off-peak rates all day»

`is_peak_hour()` guardava solo l'ora: sabato e domenica pomeriggio `apply_peak_cap`
declassava `glm-5.3` a `glm-4.7` per un sovrapprezzo che non esiste. Ora esclude
`weekday() >= 5`. Test: `test_weekend_mai_in_peak` con orologio e giorno simulati,
più la controprova sui cinque giorni feriali.

### 2.2 Il tetto di `max_tokens` è per modello, non unico

La doc dà **128K di output** alle serie GLM-5 e GLM-4.7 (32K a GLM-4.6V).
Verificato a runtime: `max_tokens=131072` → **200** su `glm-5.3` e su `glm-4.7`.
Il tetto unico a 32.768 tagliava a un quarto l'output di entrambi. Ora c'è
`GLM_MAX_OUTPUT` e `glm_max_tokens_limit(model)`; un modello sconosciuto ricade
sul valore prudente di prima, e `AIROUTER_GLM_MAX_TOKENS_LIMIT` resta un override.

### 2.3 GLM-5.3 è text-only e non lo dice

> «GLM-5.3 currently supports text-only inputs»

Con un blocco `image` risponde **200** e dichiara di non vedere nulla: un fallimento
silenzioso, il peggiore da diagnosticare. `route_image_to_vision` dirotta su
`glm-4.6V` prima del peak cap (che esenta la visione). Provato: stesso body,
`glm-5.3` non vede, `glm-4.6v` descrive. `glm-5V-Turbo` resta configurato come tier
MULTIMODAL ma **non è acquistabile con questa chiave**: 429 `[1311]`.

### 2.4 Verificato e lasciato com'era, di proposito

- `reasoning_effort` (`low`/`high`/`max`, default `max`) è accettato anche
  sull'endpoint Anthropic-compat. Non impostato: `max` è coerente col ruolo THINK.
- `thinking:{"type":"disabled"}` **non** fa fallire la richiesta come la doc lascia
  intendere per il protocollo OpenAI: torna 200 e il modello non ragiona.
- Il suffisso `glm-5.3[1m]` esiste **solo** sull'endpoint Coding Plan: con la chiave
  standard dà 400 `modelCode does not exist`, e senza suffisso 250k token di input
  passano lo stesso (misurato: `input_tokens=250015`).
- Il **context caching** di z.ai è implicito e automatico: niente da configurare.
- Il pricing non elenca ancora 5.3 (l'accesso è a punti via Coding Plan).

---

## 3. Prima passata sul flow GLM (`74672c0`)

### 3.1 Chiedere `glm-5.3` restituiva `glm-4.7`

`resolve_route` ritorna `override=None` quando il modello richiesto è già nativo del
provider: significa **«non riscrivere»**. Il proxy lo leggeva come «usa il default di
modalità» (`_model_override or MID`).

```
prima:  glm-5.3 -> risposta da glm-4.7
dopo:   glm-5.3 -> risposta da glm-5.3
```

Colpisce esattamente la configurazione che la guida z.ai raccomanda per Claude Code
(`ANTHROPIC_DEFAULT_OPUS_MODEL=glm-5.3`). Fix: `canonical_glm_model()` riconosce i
modelli GLM noti senza distinzione di maiuscole — z.ai risponde `glm-4.6v` dove noi
inviamo `glm-4.6V` — e il proxy li rispetta. Un nome non-GLM ricade sul default.

### 3.2 Il controllo sui 200-vuoti non è mai partito

Era dietro `model.startswith("glm-5")`, ma quel `model` è **il modello richiesto dal
client**, che in `glm`/`mix-gm`/`mix-ag` è un nome Anthropic: predicato sempre falso.

**Prova:** nel catalogo gli eventi di categoria `glm` portano `model=claude-sonnet-5`,
e `glm_empty_response` non compare in **4.692** richieste GLM.

Non è stato semplicemente «acceso»: leggere uno stream per ispezionarlo lo bufferizza
— la regressione di TTFB del 2026-07-22 (45 s contro ~1 s). In questo commit la
condizione usa il modello upstream e resta limitata alle risposte **non-SSE**; lo
streaming è stato chiuso dopo, vedi §5.

### 3.3 I log nominavano il modello sbagliato

Sette righe di `forward_glm` dicevano `model=claude-sonnet-5` sotto categoria `glm`.
`lim_model` (già corretto per il limiter) è stato spostato fuori dal `try` — così è
in scope anche negli `except` — e usato ovunque.

---

## 4. Seconda passata, su tutto `handle()` e i cinque backend (`9e354c8`)

Letto `handle()` per intero (righe 255-990), i backend, il relay; misure sul sidecar
(170.191 righe) e sul catalogo.

### 4.1 Lo stesso difetto, altrove

In `local_backend.forward_local` il parametro `upstream_model` era **dichiarato e mai
letto**: tutte e sei le righe di log e gli eventi di categoria `local` nominavano
`claude-opus-5` invece di `code-max`/`code-fast`. Stessa cosa, in un punto, nel 413
anticipato di `qwen_backend`.

### 4.2 Tabella delle protezioni per backend

|                    | glm | qwen | local | minimax | anthropic |
|--------------------|-----|------|-------|---------|-----------|
| tool_isolation     | sì  | sì   | **no → sì (§5)** | sì | sì |
| clamp max_tokens   | sì  | sì   | non serve (§5) | — | — |
| shrink preventivo  | sì  | sì   | coperto dal gate a monte | — | sì |
| rate limiter       | sì  | sì   | —     | sì      | sì |
| empty-check        | sì  | —    | sì    | —       | — |

### 4.3 Verificato sano, con prova

- **Telemetria del modello finale**: `glm-mode:*` (il fallback che dichiara il
  provider e non il modello) era 2.645 richieste storiche, **1 sola** negli ultimi
  7 giorni su ~2.900. Il resto dichiara il modello: `glm:glm-5.3`, `local:code-max`.
- Il relay usa il body **solo** per estrarre il modello originale: che sia costruito
  prima di `promote_system_messages` non falsa i token, che vengono dall'usage upstream.
- Il gate di contesto misura sul modello destinatario reale e conosce i modelli
  locali (`code-max` 131k → safe 104.858; `code-fast` 64k → 52.429).
- `_MINIMAX_BACKEND_MODES` è derivato e non letterale: il buco delle `-2` è chiuso.
- Il dispatch ha un fallback esplicito a 502 invece di restituire `None`.

---

## 5. Chiusura dei punti aperti (`42d0cdc`)

### 5.1 I 200-vuoti in streaming: sbirciare, non bufferizzare

Nuovo modulo `src/stream_peek.py`. Legge lo stream **fino al primo
`content_block_start`**, poi restituisce un `PeekedStream` che riemette i byte
sbirciati e prosegue dall'originale.

- risposta sana → costa i millisecondi del primo blocco (**TTFB misurato 2,56 s**);
- risposta vuota → lo stream finisce prima del primo blocco, e si può **ancora
  ritentare**, perché al client non è stato inviato nulla;
- due tetti (`AIROUTER_SSE_PEEK_CAP_SEC=20`, `AIROUTER_SSE_PEEK_CAP_BYTES=65536`):
  oltre, si torna allo streaming puro invece di restare appesi;
- risposte con `content-encoding` non vengono sbirciate: i marcatori non sono
  leggibili nei byte grezzi, meglio non decidere che decidere male.

Il punto di non ritorno non è la fine della risposta, è **il primo byte inviato al
client**: tutta la tecnica sta nel decidere prima di quel momento.

### 5.2 `local` era l'unico backend senza isolamento tool

E `filter_tools_for_backend(body, "local")` era per giunta un **no-op silenzioso**:
`local` non era in `_BRAND_CHECK`. Aggiunta la voce e il choke-point in
`forward_local`. Provato end-to-end: `stripped=['web_search'] kept=1/2`.

### 5.3 Il clamp di `max_tokens` per `local` non serve

Misurato contro LiteLLM `:4000`: `code-max` accetta `max_tokens=200.000` e risponde
200/`end_turn`. Un clamp sarebbe stato un numero inventato.

---

## 6. Stress end-to-end — `sviluppo/tests/stress_fix_20260819.py`

Istanza isolata sulle porte 8795-8799, richieste **vere** a z.ai, **una prova per
ogni fix**, senza toccare il servizio live. `--carico N` regola la concorrenza.

```
23/23 PASS
T1  ruolo: opus→glm-5.3, haiku→glm-4.7
T2  modello richiesto: glm-5.3, GLM-5.3, glm-4.7 rispettati; ignoto→MID
T3  max_tokens 131072 senza clamp; vision oltre 32.768 abbassato
T4  immagine→glm-4.6v, dirottamento a log, controprova solo-testo→glm-5.3
T5  streaming: TTFB 2,75s · message_start x1 (nessun byte duplicato) · contenuto
T6  mix-gm THINK→glm-5.3 · mix-ag ACT→glm-4.7
T7  10 richieste concorrenti: 10x200
T9  local: tool stranieri rimossi · diagnostica nomina code-max
T8  istanza viva, zero eccezioni nel log
```

**Tre override, non uno,** per un'istanza davvero isolata:
`AIROUTER_PORT_MODE_JSON` (porte), `AIROUTER_LOGS_DIR` (log applicativo — `log()`
scrive su file, non sullo stdout catturato) e `AIROUTER_CATALOG_PATH` (catalogo
diagnostico; punta a un **file**, non a una directory). Senza, le asserzioni leggono
i log di produzione o non trovano nulla.

Due difetti li ha trovati lo stress su sé stesso: il primo giro dava `model=?`
ovunque (byte gzip letti come testo) e non vedeva le righe di log.

---

## 7. Verifica post-deploy sul servizio vero

Sequenza di sicurezza del progetto rispettata (`is-active` + `Restart=always`
confermati prima del restart).

```
glm :8775  opus→glm-5.3 · haiku→glm-4.7 · glm-5.3 richiesto→glm-5.3 · max_tokens 131072
mix-gm:8776 opus→glm-5.3 · mix-ag:8777 haiku→glm-4.7
anthropic:8771 haiku nativo · local:8779 code-max
immagine→glm-4.6v · streaming TTFB 2,56s, message_start x1, contenuto sì
```

10 prove su 10 OK, log pulito (90 righe dopo l'avvio, 0 traceback).

---

## 8. Cosa resta, dichiarato

- **`GLM_RATE_LIMITS` è ancora marcato `placeholder`.** La pagina rate-limit di z.ai
  serve solo il guscio HTML, e i limiti veri del piano sono a punti (5 ore /
  settimana), non RPM/TPM. Il pacing client-side resta prudente.
- **`strip_thinking_blocks` contro il "preserved thinking".** Rimuoviamo i blocchi di
  ragionamento prima di inoltrare: necessario contro i 400 da firma estranea, ma è
  l'opposto di ciò che z.ai raccomanda per il cache hit. Trade-off consapevole.
- **`glm-5V-Turbo`** resta nel tiering ma non è acquistabile con questa chiave.
- **`reasoning_effort`** non impostato (default `max`): se il costo in punti diventa
  un problema, è la prima manopola da girare.

---

## 9. Il filo che lega quasi tutti i difetti

Cinque dei sette nascono dalla stessa domanda mai posta: **da dove arriva quella
stringa?**

- `model` in `forward_glm`/`forward_local` è quello del **client**, non della rotta →
  predicati sempre falsi e log che mandano a caccia del difetto sbagliato;
- `override=None` significa «non riscrivere», non «usa il default»;
- un fake che riemette i byte da capo non è uno `StreamReader`.

I test unitari passavano su tutti e cinque, perché passavano al codice la
combinazione che in produzione non esiste. Ciò che li ha trovati è stato: leggere il
valore vero nei log (`grep model= logs/debug-events.jsonl`), interrogare l'upstream
(`"model"` nella risposta) e far girare il codice contro un servizio reale.
