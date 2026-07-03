# Audit `mixed` — colli di bottiglia & bug quando Anthropic orchestra (2026-07-03)

> **File:** `src/ai-router-proxy.py` (2451 righe, aiohttp async) — link `~/.claude/scripts/ai-router-proxy.py`
> **Metodo:** `/swarm-bruteforce` — 163 agenti, 3 round, verifica avversariale per-finding → **97 finding confermati** (deduplicati, falsi positivi confutati).
> **Commit fix:** `b94eab6`

---

## Regola vincolante (confermata dall'utente, 2026-07-03)

`mixed` = **Anthropic unico orchestratore** (fase THINK) + **MiniMax esecutore del codice** (fase ACT).
Se **M3 (esecutore) fallisce → Anthropic prende il comando col suo routing interno** (passthrough `forward_anthropic`, che lascia Anthropic gestire con la sua orchestrazione). **NON** si riusa il piano M3 nel rescue: lo vincolerebbe, contraddicendo "suo routing interno". L'escalation è **temporanea**: deve uscire quando M3 torna disponibile.

Vedi decisione vault: `progetti/ai-router-switch/decisioni/2026-06-28-mixed-orchestrazione-gerarchica`.

---

## Fix applicati (commit `b94eab6`, live in produzione)

### FIX-1 — Timeout THINK dedicato (collo di bottiglia #1)
- **Bug:** `_pipeline_think_act` chiamava `_call_full(forward_anthropic_direct, …)` col **default `timeout=90` applicato 2 volte** (una su `forward`, una su `up.read()`) = **180s worst-case**, per un piano Haiku da ~200 token (`THINK_MAX_TOKENS`). Un Anthropic lento / in coda / che accetta ma stalla teneva il client **muto fino a 180s** prima del fallback M3. E il THINK gira in serie prima che ACT streammi → ogni richiesta lo paga sul TTFB.
- **Nota:** 429/5xx sono già in `FALLBACK_STATUSES` → fast; il timeout copriva il caso sbagliato (stall applicativo).
- **Fix:**
  ```python
  # nuova costante (dopo THINK_MODEL, riga ~1029)
  THINK_TIMEOUT_SEC = float(os.environ.get("AIROUTER_THINK_TIMEOUT_SEC", "12"))
  # call-site (riga ~1203)
  t_status, t_json = await _call_full(forward_anthropic_direct, request, think_body, session, timeout=THINK_TIMEOUT_SEC)
  ```
- **Effetto:** stalli da ~180s → cap 12s, poi fallback M3 pulito. Tunabile via env.

### FIX-2 — Escalation "black-hole" (lock-in permanente su Anthropic-esecutore)
- **Bug:** nel ramo escalation (`anthropic_leads`, righe ~2152-2213), alla ripresa di M3 (draft OK, riga ~2186) il contatore `_mixed_fails` **non veniva mai resettato**. Il time-decay a 60s (`FAIL_RESET_SEC`) vive **solo** dentro `mixed_fail_inc`, che l'escalation non chiama mai; `mixed_anthropic_leads` legge il contatore grezzo e **ri-arma il cooldown** a ogni check → `mixed` restava congelato su Anthropic-finalizza-tutto fino al restart del processo, **anche con M3 tornato sano** (viola "MiniMax esegue").
- **Fix:** `mixed_fail_reset(chat_fp)` subito dopo il draft M3 riuscito:
  ```python
  draft_v1 = _text_from_message(gen_json)
  mixed_fail_reset(chat_fp)   # M3 recovered → esci dall'escalation
  log(f"mixed escalation R1 M3 draft ({len(draft_v1)} chars) → reset escalation (M3 recovered)")
  ```
- **Effetto:** escalation half-open→closed su prima ripresa M3; le richieste successive tornano alla pipeline normale Anthropic-THINK + M3-ACT.

---

## Fix NON applicati (per scelta esplicita) e perché

| Finding swarm | Decisione | Motivo |
|---|---|---|
| **1.5 / SEV-4** — rescue ACT manda `body` grezzo ad Anthropic (esegue con i tools) | **Non toccato** | Per la regola utente "M3 giù → Anthropic col suo routing interno" questo è il comportamento **voluto**. Riusare il piano M3 lo vincolerebbe. |
| **SEV-3 I/O** — `log()`, `log_router_usage()`, `_load_chats`, scan SIDECAR sincroni sul loop | **Rinviato** | Reale ma bassa severità nel setup attuale (append locali µs; SIDECAR memoizzato dopo la 1ª richiesta). Wrappare 94 call-site di `log()` è invasivo e rischioso su un servizio critico. Candidato per un intervento dedicato (queue background + `RotatingFileHandler`). |
| **1.2** — fingerprint = IP → contatore globale unico | **Rinviato** | Cambio semantico del breaker; richiede fp per-conversazione (`hash(system+1° user msg)`, già abbozzato in commento). Da valutare a parte. |
| **1.3 / 1.4** — path non-esatto / `NEW_PIPELINE=0` saltano il THINK | **Rinviato** | Config di default (`NEW_PIPELINE=1`, path `/v1/messages`) è corretta; sono hardening dei bordi. |

> Gli altri finding SEV-3/4/5 (troncamento piano, parse ridondante, header audit, leak model-name su compressi) sono catalogati sotto per un intervento successivo. Non bloccanti per l'operatività attuale.

---

## Verifica (evidence-gate)

```
py_compile ai-router-proxy.py           → OK
systemctl --user restart ai-router      → active
curl :8787/                             → HTTP 200 in 0.0013s
journalctl (post-restart)               → nessun errore; solo DeprecationWarning resp.drain() PREESISTENTI
grep THINK_TIMEOUT_SEC / reset escalation → presenti (righe 1029, 1203, 2198)
git b94eab6 committato + pushato
```

---

## Catalogo completo dei 97 finding (sintesi swarm)

> Ordinamento per impatto decrescente. Le righe citate sono pre-fix (il file è cambiato di +6 righe da FIX-1).

<!-- SWARM_REPORT_BELOW -->

## SEV-1 — Violazioni della regola vincolante "mixed = Anthropic unico orchestratore + MiniMax esecutore"

Sono i bug più gravi: infrangono silenziosamente l'invariante di prodotto, spesso senza alcun segnale a valle.

### 1.1 Escalation PERMANENTE: dopo 2 fail M3, Anthropic diventa esecutore per sempre
- **Cuore del problema**: il contatore `_mixed_fails` non viene mai resettato nel ramo escalation (righe `2152-2213`), e il decay a 60s (`FAIL_RESET_SEC`, riga `177-179`) vive SOLO dentro `mixed_fail_inc`, che l'escalation non chiama mai. `mixed_anthropic_leads` (righe `193-204`) legge il contatore grezzo senza decay e ri-arma il cooldown a ogni check (riga `202`). Risultato: un singolo blip di 2 fail congela `mixed` su Anthropic-esecutore fino al restart del processo, anche con M3 tornato sano.
- **Prova ignorata**: nell'escalation `forward_minimax` ha successo a riga `2173/2186` (M3 dimostrabilmente recuperato) ma `mixed_fail_reset` non viene mai chiamato lì (finding 78).
- **Fix**: applicare il decay `now-last_fail>FAIL_RESET_SEC` DENTRO `mixed_anthropic_leads` prima del confronto con la soglia; e chiamare `mixed_fail_reset(chat_fp)` dopo il successo M3 a riga `2186`.

### 1.2 Fingerprint condiviso + race → 2 fail QUALSIASI flippano tutto il traffico locale
- `_resolve_chat_fingerprint` (righe `297-309`) usa `request.remote` (IP) come default. In setup single-user (leobox, tutto da un IP, nessun `X-Session-ID`) TUTTE le richieste condividono UN solo contatore globale. Con N subagent paralleli, 2 fail di chat indipendenti mandano l'INTERA modalità in escalation (finding 16).
- **Lost-update**: `mixed_fail_reset` (pop) e `mixed_fail_inc` (read-modify-write) sono sezioni critiche separate sotto lo stesso lock; un success concorrente azzera il contatore e il breaker non scatta mai quando dovrebbe (finding 45). Simmetricamente, TOCTOU/thundering-herd: N richieste concorrenti incrementano in blocco da un singolo evento transitorio (finding 17).
- **Fix**: derivare il fp da `hash(system+primo user msg)` (già suggerito nel commento righe `272-274`, non usato), oppure rendere `X-Session-ID` obbligatorio in mixed; contare max 1 incremento per "ondata".

### 1.3 Config DEFAULT (`NEW_PIPELINE=1`) viola già la regola su path non-esatti
- `is_messages = path.endswith("/v1/messages")` (riga `2148`) è match ESATTO. Qualunque path `/v1/` ammesso ma non-messages (`/v1/complete`, `/v1/messages/` con slash finale) salta sia la NEW pipeline (riga `2218`) sia l'escalation (riga `2152`) e cade a riga `2227→2233` dove `forwarders["minimax"]` esegue con ZERO fase THINK (finding 40, 70). Succede a config di default, non solo `NEW_PIPELINE=0`.
- Peggio: se `anthropic_leads=True` (M3 giudicato inaffidabile), una richiesta non-messages viene comunque mandata a MiniMax senza supervisione (finding 71).
- **Fix**: normalizzare il path (`rstrip('/')`), gestire THINK anche per `/v1/` non-messages, o rifiutare esplicitamente.

### 1.4 Toggle env `NEW_PIPELINE=0` reintroduce il comportamento vietato
- Riga `64`: `NEW_PIPELINE = os.environ.get("AIROUTER_NEW_PIPELINE","1")=="1"`. Con env a `0` o stantia, ogni `/v1/messages` cade al path legacy T0/T1 (righe `2227-2264`) dove MiniMax esegue da solo senza THINK, o su rescue Anthropic esegue direttamente in passthrough (righe `2238/2252`) — doppia inversione dei ruoli (finding 9, 10). SPOF gated da una singola env-var senza warning.
- **Fix**: rendere il toggle no-op in mixed (forzare pipeline THINK).

### 1.5 ACT-failure rescue trasforma Anthropic in ESECUTORE (con i tools originali)
- Su fail ACT, righe `1234/1246` fanno `forward_anthropic(request, body, session)` con il `body` ORIGINALE che contiene ancora `tools` (THINK li strippa a riga `1046`, il rescue li reintroduce). Anthropic emette `tool_use` = esegue codice, violando sia la regola mixed sia la regola utente "Anthropic non esegue mai" (finding 21). Un singolo 429/503 transitorio di M3 consegna l'esecuzione ad Anthropic.
- Manca il retry sulla catena esecutori (m2.7→M3→Haiku) prima di escalare (finding 84).
- **Fix**: su fail ACT instradare a un esecutore alternativo MiniMax/locale, mai ad Anthropic-passthrough.

---

## SEV-2 — Timeout non-bounded: silenzio fino a ~13–23 minuti

### 2.1 THINK: doppio timeout da 90s in serie → 180s
- `_call_full` (righe `879-898`) applica `timeout=90` DUE volte separatamente: su header (`886`) e su `up.read()` (`898`) → worst-case ~180s, ammesso dal docstring stesso (riga `882`). Il call-site THINK (riga `1198`) NON passa override. Per un piano da 200 token (`THINK_MAX_TOKENS`, riga `1021/1043`, atteso ~1s) è ~90-180× sovradimensionato (finding 2, 5, 6, 8, 38).
- **Nota chiave**: il 429/5xx è già FAST (in `FALLBACK_STATUSES`, riga `83`); il timeout guarda il caso SBAGLIATO — solo un Anthropic che accetta la connessione poi stalla applicativamente brucia i 180s (finding 38).

### 2.2 Concatenazione senza deadline end-to-end → 330s / 780s / 23min
- Dopo il THINK stallato, i fallback (righe `1202/1209/1219`) fanno `forward_minimax` SENZA `wait_for`, bounded solo dalla session `ClientTimeout(total=600, sock_read=120)` (riga `2390`). Se anche M3 dribbla → +150-600s (finding 37, 67).
- Il rescue ACT→Anthropic (righe `1234/1246`) è un TERZO hop non-bounded → THINK(180s)+ACT(600s)+rescue(600s) ≈ 23 min prima che il client veda un errore (finding 68). Ogni hop apre una nuova `session.request` quindi i ceiling si SOMMANO invece di cappare.
- **Fix**: passare `timeout≈6-8s` dedicato al THINK a riga `1198`; usare un deadline monotonic unico threadato in ogni hop (`deadline - elapsed`) invece di timeout per-fase; wrappare i fallback in `wait_for`.

---

## SEV-3 — Latenza TTFB strutturale: ogni richiesta mixed paga THINK+ACT in serie

Collo di bottiglia di design, presente su tutto il traffico anche happy-path.

### 3.1 Serializzazione totale THINK→ACT
- `_call_full` forza non-stream (`_force_no_stream`, riga `883`) + `up.read()` full-buffer (riga `898`): legge TUTTA la risposta Anthropic prima di ritornare. Il piano è input HARD del system ACT (`_build_act_body`, riga `1177` `PIANO-GUIDA:\n{plan}`), quindi ACT non può streammare finché THINK non è completo (finding 1, 33, 35). TTFB extra vs MiniMax diretto = intera durata THINK (~1.8-3s tipici, di più su contesti grandi). Nessun heartbeat SSE durante THINK: il client vede connessione morta (finding 65).

### 3.2 Doppio prefill dell'INTERO contesto in serie
- Sia `_build_think_body` (riga `1040`) sia `_build_act_body` (riga `1179`) fanno `dict(orig)` mantenendo TUTTI i `messages`. Lo stesso contesto (agentico: 50-200K token) viene prefillato DUE volte back-to-back su due upstream, senza cache cross-provider (finding 34, 63, 64, 86). Su una richiesta da 30MB, THINK carica 30MB sul wire per ottenere 200 token di piano.
- Il codebase HA già `extract_last_user_text` (riga `468`) usato altrove ma non qui.
- **Fix**: costruire il think_body con solo l'ultimo turno utente (`orig['messages'][-1:]`) — il piano dipende solo dalla richiesta corrente. Collassa il prefill THINK a O(ultimo turno).

### 3.3 Nessun fast-path / bypass per richieste banali
- `_pipeline_think_act` scatta per TUTTE le `/v1/messages` (docstring riga `1187`), nessun gate su size/complessità/stream flag. Un prompt di una riga paga lo stesso ~2-3s di THINK di un turno agentico pesante (finding 4, 36, 66). TTFB peggiora 3-8× su richieste corte.
- **Fix**: gate di bypass (max_tokens piccolo, nessun tool, singolo messaggio corto → M3 diretto).

### 3.4 Piano iniettato nel system rompe la prompt-cache MiniMax
- Il piano varia a ogni richiesta e viene messo in `body['system']` (righe `1173-1181`): il prefisso system di ACT non è mai stabile → nessun riuso cache + prefill extra proprio sul TTFB (finding 3).
- **Fix**: mettere il piano come ultimo messaggio user/assistant, tenendo il system costante e cache-abile.

---

## SEV-3 — I/O sincrono bloccante nell'event loop asyncio

Ogni write/read sincrona congela TUTTE le richieste concorrenti (aiohttp single-loop), inclusi gli stream ACT in corso.

- **`log()`** (righe `210-216`): `open()+write()+close()` sincrono, 94 call-site, molti sul hot path (`1701/1775/1784/1921`). File `ai-router.log` 486KB senza rotazione (finding 42, 73).
- **`log_router_usage()`** (righe `638-639`): write sincrona su OGNI risposta dentro `relay()` (call-site `1907`). File `router-usage.jsonl` = 5.4MB reali, senza rotazione (finding 11, 74).
- **`_log_original_model()`** (righe `599-600`): append sincrono per ogni richiesta MiniMax/mixed (finding 11).
- **`_load_chats()`** (righe `331-352`): a scadenza cache (TTL 5s, riga `326`) legge+parse+riscrive sincrono; il cleanup TTL/FIFO su fino a 10000 entry può scatenare un `write_text` durante una semplice LETTURA. `get_chat_mode` è sul dispatch di ogni richiesta (riga `1707`). Nessun dedup in-flight: un burst dopo la scadenza rifà il lavoro K volte (finding 12, 44, 75). `set_chat_mode/_save_chats` idem su path comandi (finding 76).
- **Scan SIDECAR intero** (righe `1891-1898`): in `relay()` mixed, quando l'index `__remap__` è vuoto, legge riga-per-riga l'intero file append-only da 1.9MB, sincrono. **Bug memoization**: riga `1885` `.get('__remap__') or {}` — un dict vuoto è falsy, quindi un index vuoto (fresh process/file ruotato) non viene mai cacheato e si ri-scansiona l'intero file a OGNI richiesta, per sempre (finding 13, 43).
- **`_gc_fail_dicts`** (righe `119-129`): scan O(N) fino a 5000 entry sotto `_counter_lock`, inline nel loop (finding 79); ed è stale-only, non un cap reale → RAM illimitata con X-Session-ID per-richiesta (finding 80).
- **Fix trasversale**: instradare tutte le write di logging su `asyncio.Queue` drenata da un task background (o `asyncio.to_thread`); `RotatingFileHandler` + cap dimensione sui 4 file (`LOG_FILE`, `SIDECAR`, `USAGE_SIDECAR`, `CHAT_STORE`); costruire l'index remap incrementalmente in `_log_original_model` invece di rileggere il file; sentinella `if '__remap__' not in ...` invece di truthiness.

---

## SEV-3 — Correttezza/sicurezza del piano THINK

- **Troncamento silenzioso a 200 token**: `plan = _text_from_message(t_json).strip()` (riga `1215`) con UNICO guard `if not plan:`. Nessun check di `stop_reason=="max_tokens"`. Un piano tagliato a metà frase (non-vuoto) viene iniettato verbatim in ACT (righe `1177-1178`) e M3 improvvisa la coda → esecuzione errata + la coda del piano è di fatto decisa da M3, non da Anthropic (finding 18).
- **Amplificazione safety**: un troncamento può INVERTIRE il senso: "elimina solo il backup temp, NON toccare la config" tagliato dopo "elimina <path>" diventa un imperativo distruttivo, eseguito da M3 con i tools reali (Bash/Edit/git) mantenuti in `dict(orig)` (finding 49).
- **Zero validazione oltre non-vuoto**: preambolo chiacchierato, rifiuto, o domanda di chiarimento di Haiku diventano il "piano" autorevole (finding 19, 82, 94). Esiste `self_review_ok` (righe `1110-1166`) ma la Version C lo bypassa del tutto.
- **`_text_from_message` scarta blocchi non-text/thinking** (righe `861-876`): refusal, `tool_use` forzato (`tool_choice` NON viene poppato in `_build_think_body`), o futuri block-type → `plan=''` → fallback silenzioso a M3 puro anche con 200 OK (finding 61).
- **Lingua hardcoded "in italiano"** (riga `1036`): richieste inglesi (la maggioranza agentica) ricevono steering italiano che può flippare la lingua di M3 e sprecare token di piano peggiorando il troncamento (finding 50).
- **Fix**: check `stop_reason=='max_tokens'` → retry con budget maggiore o fallback M3-diretto; gate anti-domanda/rifiuto; poppare `tool_choice`; lingua del piano che rispecchia la richiesta; delimitare il piano come contesto advisory, non comandi letterali.

---

## SEV-4 — Lavoro sprecato: fallback ri-esegue da zero

- Su fail ACT, il rescue (righe `1234/1246`) usa il `body` ORIGINALE, NON riusa `plan` (riga `1215`) né `act_body` (riga `1227`): il round-trip THINK (fino a 180s + token pagati) è buttato, e Anthropic ri-pianifica ed esegue da zero. Worst-case: TRE round-trip seriali (THINK + ACT fallito + rescue completo) (finding 7, 20, 51, 83).
- Doppio consumo quota Anthropic per richiesta + doppio upload del body (fino a 200MB egress su client da 100MB) (finding 51).
- **Reset mancante su rescue OK**: contrariamente al path T0/T1 (righe `2255/2262`), il rescue ACT non resetta `mixed_fail` (righe `1231/1239`): blip transitori rescued con successo accumulano comunque verso l'escalation permanente (finding 22, 41, 85).
- **Fix**: su fail ACT ritentare `act_body` (piano intatto) su esecutore secondario; resettare/decadere il contatore su rescue riuscito.

---

## SEV-4 — Serializzazione JSON ridondante + amplificazione memoria/DoS

- **~4-5 parse + ~4 dump full-size dello stesso body per richiesta mixed**, tutti sincroni sul loop: json.loads scan comandi (riga `1686`, scartato) → `orig` (riga `2220`) → `_build_think_body` dumps (`1048`) → `_force_no_stream` loads+dumps (`854-856`, ridondante perché stream già False a riga `1042`) → `strip_unsupported_fields` (`573-579`) → `_build_act_body` dumps (`1182`) → `remap_body_for_minimax` loads+dumps (`653-684`) (finding 25, 26, 27, 52, 53, 87, 88). Su body agentico 2-30MB stalla tutte le connessioni concorrenti.
- **Amplificazione DoS**: `client_max_size=100MB` (riga `2328`) è l'unico cap; la pipeline tiene ~5-7 copie full vive per ~180s → ~800MB churn per singola richiesta da 100MB, rischio OOM del router (SPOF totale) (finding 24, 54, 89).
- **Fix**: parse UNA volta al dispatch, threadare il dict (non ri-encodare bytes) attraverso THINK/ACT; serializzare una sola volta prima di ogni upstream; cap dedicato (~1-4MB) per `/v1/messages` in mixed → 413.

---

## SEV-4 — Leak model-name e header di audit mancanti

- **`x-ai-verified` assente su TUTTI i 5 rami di fallback** (righe `1202/1209/1219/1234/1247`), presente solo sul success (riga `1254`). Proprio i rami dove la regola è infranta (M3-diretto senza THINK, o Anthropic-esecutore) sono indistinguibili da una risposta compliant a valle → violazione invisibile al token-ledger e al monitoring (finding 29, 55, 60, 92). Un intero fleet-regression sembra operatività normale.
- **Model-rewrite DEFEATED su risposte compresse/multi-chunk**: `auto_decompress=False` (riga `2393`) + rewrite sui byte grezzi (regex SSE riga `1780`, `json.loads` non-SSE riga `1790`) → con gzip/br o JSON spezzato su chunk il rewrite salta e `"model":"MiniMax-M3"` arriva al client, registrato nel jsonl di Claude Code (finding 30, 56, 91). Rompe la promessa anti-leak "FIX E" proprio sul path principale ACT.
- **Race single-pending su `_request_orig_model`**: i relay mixed usano `chat_fp_for_rewrite="default"` (mai passato esplicito), `pop("default")` fallisce e ricade sull'euristica single-entry (righe `1722-1726`); con 2+ richieste concorrenti → nessun rewrite (leak) o rewrite col model di UN ALTRO client (misattribuzione cross-request). Le entry dei rami d'errore 502 (righe `1204/1211/...`) e dell'ACT fallito (`up.release()` riga `1242`) restano orfane e possono essere pop-ate da richieste future (finding 57, 58, 90).
- **Fix**: passare sempre `chat_fp_for_rewrite=_resolve_chat_fingerprint(request)`; consumare/pop le entry nei rami d'errore in un `finally`; decomprimere il buffer prima del rewrite (o forzare `Accept-Encoding: identity` verso MiniMax quando è previsto rewrite); accumulare i chunk fino a JSON completo.

---

## SEV-5 — Silent failure / codice morto / osservabilità

- **`_call_full` inghiotte il json.loads finale** (righe `933-936`): bare except senza log → 200-con-garbage (HTML da proxy intermedio, body troncato/ancora compresso) diventa `(200, None)` → fallback M3 con log "THINK ko t_status=200" contraddittorio (finding 97). Loggare `len(raw)` + primi 120 byte.
- **Empty-plan mai incrementa `mixed_fail`** (righe `1216-1222`): una regressione di formato che svuota sempre il piano serve M3-only per sempre senza mai triggare il breaker né allarmare (finding 31, 94). Trattare empty-plan come anomalia first-class (warning + counter).
- **`is_t2`/`classify_t2` codice morto sotto default** (righe `2149/2218`): `classify_t2` fa un `json.loads` full del body a ogni richiesta ma il valore è sempre scartato perché la NEW pipeline ritorna prima; l'intero ramo T2 (righe `2266-2313`, unico punto legacy dove Anthropic finalizza) è irraggiungibile → trap manutentivo + terzo parse ridondante (finding 32, 72, 95). Calcolare `is_t2` lazy solo nei rami che lo usano.
- **Escalation blank-answer**: `draft_v1`/`final_text` da `_text_from_message` senza guard empty (righe `2186/2192-2209`): se sia M3 sia Anthropic-finalize subiscono la stessa fragilità di formato → messaggio 200-OK vuoto con `output_tokens=1` che maschera l'emptiness (finding 96). Guard `if not final_text: return 502`.
- **Framing stream/non-stream**: `relay()` usa sempre `StreamResponse` chunked e strippa `content-length` (righe `1729/1732`) anche per client `stream=false` → mismatch con proxy HTTP/1.0 (finding 59, impatto limitato).

---

## Finding negativi verificati (NON bug — non re-investigare)
- **Nessuna doppia esecuzione tool** in una singola richiesta: THINK ha tools strippati e non-stream; su fallback ACT la risposta è `release()`-ata prima di ogni relay → `relay()` gira al più una volta (finding 23, 93).
- **Nessun double-release**: `up` (M3) e `up2` (Anthropic) sono oggetti distinti (finding 93).

---

## Raccomandazioni prioritarie (ordine di intervento)

1. **Bound i timeout** (SEV-2): `THINK_TIMEOUT≈6-8s` a riga `1198` + deadline end-to-end unico. Trasforma stalli da ~23min in ~6s. Basso rischio, alto guadagno.
2. **Fix state-machine breaker** (SEV-1.1/1.2): decay dentro `mixed_anthropic_leads` + reset su success escalation (riga `2186`) + fp per-conversazione. Elimina l'escalation permanente che viola la regola.
3. **Chiudere i buchi della regola mixed** (SEV-1.3/1.4/1.5): normalizzare path, no-op del toggle in mixed, rescue su esecutore MiniMax mai Anthropic.
4. **THINK con solo ultimo turno** (SEV-3.2): `orig['messages'][-1:]` — dimezza il prefill e la banda, riusa `extract_last_user_text` (riga `468`) già presente.
5. **I/O off-loop + rotazione** (SEV-3 I/O): queue background per log/usage/sidecar, `RotatingFileHandler`, sentinella memoization riga `1885`.
6. **Guard sul piano** (SEV-3 correttezza): `stop_reason=='max_tokens'`, pop `tool_choice`, gate anti-refusal/domanda, piano come advisory delimitato.
7. **Parse-once** (SEV-4 JSON): un dict threadato per tutta la pipeline + cap dedicato `/v1/messages`.
8. **Audit header + rewrite compress-safe** (SEV-4): `x-ai-verified` su ogni fallback, decompressione prima del model-rewrite, `chat_fp_for_rewrite` esplicito.