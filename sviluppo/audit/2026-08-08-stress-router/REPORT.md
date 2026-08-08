# Stress test e audit tecnico — ai-router-switch

**Data:** 2026-08-08, 07:58–08:11 CEST · **HEAD:** `99b7615` · **aiohttp:** 3.13.4
**Modalità globale durante i test:** `mix-am` · **Dati storici:** 7 giorni, 19.068 richieste reali
**Artefatti grezzi:** `findings.json` (strutturato), più sei JSON di misura in questa cartella.

---

## 0. Metodo e limiti — da leggere prima dei numeri

I test sono stati eseguiti su **infrastruttura viva**, la stessa che serviva la sessione che li conduceva. Per questo il carico è stato deliberatamente contenuto: concorrenza massima 50, burst massimo 300 richieste, e solo sull'endpoint `/__router_health`. **Nessun tentativo di saturazione**, nessun test distruttivo, nessun riavvio provocato.

Il traffico reale a pagamento è stato limitato a richieste minime (`max_tokens` 8–16) su tutte e nove le modalità.

Due precisazioni di onestà metodologica, perché entrambe hanno cambiato conclusioni durante l'analisi:

1. **Le due interruzioni osservate alle 08:06:54 e 08:07:55 non sono state provocate dai test.** Sono stop/start esterni (watchdog o utente). Il primo sospetto — «i miei test fanno cadere il router» — è stato smentito dal journal, che mostra `Stopping ai-router.service` esplicito, non un crash.
2. **Due mie ipotesi iniziali sono state smentite dai dati** e sono documentate come tali nel §3: lo spreco da `max_tokens` non è sistemico, e il «95,3% di errori in modalità minimax» era rumore di sonde.

---

## 1. Sintesi esecutiva

Il router è **solido nel nucleo e fragile ai bordi**.

Il cuore del routing regge tutto ciò che gli si lancia contro: 198 combinazioni di modalità e modello, inclusi input malformati, path traversal e stringhe da 5.000 caratteri, senza una sola eccezione non gestita e senza mai instradare fuori dalla catena prevista per la modalità. L'accettazione di connessioni regge 3.266 richieste al secondo con p95 sotto i 10 ms.

I problemi stanno altrove, e sono di tre famiglie:

- **Un difetto critico di disponibilità** nello spegnimento, che rende ogni riavvio un'uccisione forzata con troncamento delle richieste in volo.
- **Due modalità di fatto degradate** (`qwen`, `mix-ag`) che falliscono in silenzio perché poco usate.
- **Assenza di validazione semantica**, per cui un errore di battitura nel nome del modello costa una generazione completa a pagamento invece di un 400 immediato.

| Severità | N | Finding |
|---|---|---|
| **Critica** | 1 | F1 shutdown incompatibile con systemd |
| **Alta** | 3 | F2 qwen degradata · F3 mix-ag inutilizzabile · F4 nessuna validazione semantica |
| **Media** | 4 | F5 campo `model` incoerente · F6 `max_tokens` forzato · F7 risposte vuote · F12 sonde nelle metriche |
| **Bassa** | 4 | F8 gzip incoerente · F9 formato id · F10 asimmetria mix-gm · F11 CRLF nel nome modello |

---

## 2. Il finding critico: lo spegnimento non può riuscire

### Cosa succede

```
ai-router.service: State 'stop-sigterm' timed out. Killing.
ai-router.service: Killing process 833619 (python3) with signal SIGKILL.
ai-router.service: Main process exited, code=killed, status=9/KILL
ai-router.service: Failed with result 'timeout'.
```

### Perché succede

Tre fatti che da soli sono innocui e insieme rendono il SIGKILL inevitabile:

1. `web.AppRunner(app)` viene creato **senza `shutdown_timeout`** — [ai-router-proxy.py:1002](../../../src/ai-router-proxy.py#L1002). Il default di aiohttp 3.13.4, verificato via `inspect`, è **60,0 secondi**.
2. Il cleanup gira in un **ciclo sequenziale** su un runner per porta — [ai-router-proxy.py:1017-1019](../../../src/ai-router-proxy.py#L1017-L1019). Con dieci porte, il tetto teorico è **600 secondi**.
3. La unit concede `TimeoutStopSec=8`.

Il gestore di SIGTERM **esiste ed è corretto** ([:985-992](../../../src/ai-router-proxy.py#L985-L992)): il problema non è che il segnale non venga ricevuto, ma che il drain non possa completare nella finestra concessa. Basta **una sola connessione SSE aperta** — cioè una qualunque sessione Claude Code attiva — perché `cleanup()` resti in attesa.

### Conseguenze osservabili

Richieste client troncate a metà, log e sidecar non flushati, circa **8 secondi di indisponibilità simultanea su tutte e dieci le porte**. È anche l'origine plausibile dei `ClientConnectionResetError: Cannot write to closing transport` già presenti nel journal (07:45:41).

Durante questo audit la firma è stata osservata dal vivo: le nove porte fisse hanno rifiutato la connessione (`curl: (7) Failed to connect`) e sono tornate tutte a 200 dopo cinque secondi di quiete.

---

## 3. Le due ipotesi che i dati hanno smentito

Le riporto perché un audit che nasconde le proprie correzioni è meno utile di uno che le mostra.

### «Il minimo forzato di `max_tokens` è uno spreco sistemico» — **falso**

Il difetto è reale: `MINIMAX_MIN_MAX_TOKENS = 1024` ([minimax_body.py:11](../../../src/minimax_body.py#L11)) alza in silenzio ogni `max_tokens` sotto 1024, e una richiesta con `max_tokens: 8` genera davvero **1024 token**, 128× il richiesto. Sembrava la spiegazione dell'output medio di 1.021 token per richiesta in `mix-am`.

La distribuzione dice altro: su 17.468 risposte, solo **8 hanno esattamente 1024 token (0,0%)** e il **71,9% sta sotto**. La mediana è 607, la media è trascinata dalla coda (massimo 45.944). I client reali mandano `max_tokens` alto, quindi la clausola non scatta quasi mai.

**Conclusione corretta:** violazione del contratto API che colpisce le richieste piccole, non uno spreco di massa. Declassato a severità media.

### «La modalità minimax ha il 95,3% di errori» — **artefatto**

Il primo conteggio dava 182 errori `502` su 191 richieste. Filtrando le sonde (`chat=127.0.0.1`, `sid:test-*`), le richieste reali in sette giorni sono **9**, con **zero errori**: i 502 erano tutti test locali.

È la **quarta volta** che la contaminazione da test falsa una misura in questo progetto. Il fix del 2026-08-07 blocca le scritture della suite, ma le sonde manuali via `curl` restano indistinguibili se non per fingerprint — da cui l'azione **A7**.

---

## 4. Cosa funziona bene

| | Misura |
|---|---|
| **Routing** | 198 combinazioni (9 modalità × 22 modelli, inclusi `None`, stringa vuota, 5.000 caratteri, `../../etc/passwd`, JSON injection, CRLF): **0 eccezioni, 0 provider fuori catena**. Le modalità pure nativizzano sempre il modello straniero. |
| **Concorrenza** | 50 client paralleli: **3.265,8 req/s**, 150/150 a 200, p50 8,6 ms, p95 9,7 ms. Nessuna degradazione. |
| **Health** | Dieci porte, 20 campioni each: p50 fra **3,77 e 4,29 ms**, p95 sotto 5,11 ms. Uniforme. |
| **Validazione sintattica** | JSON malformato, body vuoto, `messages` non lista: **400 corretto in ~146 ms**, senza contattare l'upstream. |

---

## 5. Traffico reale, 7 giorni, sonde escluse

| modalità | req | err% | p50 out | p90 out | input tok | cache_read | empty | tool_only |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `mix-am` | 17.483 | 0,0% | 606 | 1.933 | 16,9 M | **4,48 G** | 415 | 7.831 |
| `qwen` | 942 | **16,0%** | 957 | 11.705 | **55,6 M** | **0** | **205** | 196 |
| `anthropic` | 322 | 1,6% | 484 | 4.637 | 1,35 M | 65,3 M | 16 | 132 |
| `local` | 195 | 0,0% | 1.879 | 5.342 | 4,75 M | 0 | 0 | 15 |
| `mix-gm` | 37 | 0,0% | 39 | 79.573 | 849 K | 0 | 4 | 1 |
| `glm` | 33 | 0,0% | 8 | 30 | 303 | 0 | 4 | 0 |
| `mix-al` | 32 | 0,0% | 101 | 2.088 | 16 K | 0 | 0 | 8 |
| `mix-ag` | 15 | **80,0%** | 0 | 2 | 111 | 0 | 2 | 0 |
| `minimax` | 9 | 0,0% | 28 | 1.024 | 887 | 0 | 5 | 0 |

Tre letture:

- **`qwen` è la modalità più problematica**: una richiesta su sei rifiutata con 429, una su cinque vuota, **59.000 token di input medi** e **cache_read a zero** — ogni richiesta paga l'intero prompt.
- **`mix-ag` è di fatto rotta**: 12 su 15 richieste sono 429, mediana di output 0.
- **`mix-am` è sana sul piano HTTP** (0% errori) ma ha **415 risposte vuote**, una ogni 42: la classe di bug già inseguita più volte è ridotta, non estinta.

**TTFB misurato su richiesta reale minima:** `mix-al` 0,11 s · `local` 0,68 s · `anthropic` 0,88 s · `mix-ag` 0,97 s · `mix-am` 1,32 s · `glm` 1,45 s · `qwen` 1,62 s · `minimax` 1,84 s · `mix-gm` 2,69 s.

---

## 6. Il costo dell'assenza di validazione

Quattro richieste che qualunque API rifiuterebbe, e che qui vengono **servite con 200**:

| richiesta | esito | tempo |
|---|---|---|
| `model: "modello-inesistente-xyz"` | **200**, 519 token generati e fatturati | 5,77 s |
| `model: "../../etc/passwd"` | **200** | 8,52 s |
| `max_tokens: -5` | **200** | 5,30 s |
| `role: "hacker"` | **200** | 9,88 s |

Il path traversal **non è sfruttabile** come lettura di file: il valore finisce nel body JSON verso l'upstream. Ma dimostra che sul campo `model` non esiste alcuna allowlist.

Il costo pratico è l'asimmetria: un errore di sintassi costa **146 ms**, un errore di semantica costa **5–10 secondi e una generazione a pagamento**.

---

## 7. Azioni proposte, in ordine di priorità

| # | Tipo | Azione | Risolve | Rischio |
|---|---|---|---|---|
| **A1** | correttiva | `shutdown_timeout=3.0` esplicito su `AppRunner` + cleanup dei 10 runner con `asyncio.gather`; in alternativa `TimeoutStopSec=20` | F1 | basso |
| **A2** | correttiva | Validazione semantica prima dell'inoltro: allowlist `model`, `max_tokens ≥ 1`, `role ∈ {user, assistant}` | F4 | **medio** — attivare prima in audit-only per una settimana |
| **A3** | correttiva | Uniformare il campo `model` della risposta al modello realmente usato, o esporre `x-ai-actual-model` | F5 | basso |
| **A4** | correttiva | Documentare, misurare o rimuovere il minimo forzato di `max_tokens`; loggarlo quando scatta | F6 | basso |
| **A5** | correttiva | Decidere il destino di `qwen` (429 + cache a zero) e `mix-ag` (80% 429): riparare o marcare non raccomandate | F2, F3 | basso |
| **A6** | migliorativa | Contratto di risposta unico fra backend (Content-Encoding, formato id) con test parametrico sulle 9 modalità | F8, F9 | basso |
| **A7** | migliorativa | Header `x-airouter-synthetic` → campo `synthetic` nel sidecar, invece di indovinare le sonde per fingerprint | F12 | molto basso |
| **A8** | potenziamento | Sonda periodica su tutte e nove le modalità, con vista di disponibilità | F7, F3 | basso |
| **A9** | potenziamento | Registrare `ttfb_ms` e `total_ms` nel sidecar | — | molto basso |

**Su A9, una nota che pesa più di quanto sembri:** il sidecar registra token e stato ma **non la durata**. Questo audit ha potuto misurare la latenza solo con sonde nuove, non sui 19.068 eventi storici. Finché quel campo manca, nessuna regressione di performance è rilevabile a posteriori — la si può solo cogliere sul momento.

---

## 8. Riproducibilità

Tutti i dati grezzi sono in questa cartella:

| file | contenuto |
|---|---|
| `health-baseline.json` | latenze p50/p95/max per dieci porte, 20 campioni ciascuna |
| `resolve-route-matrix.json` | matrice completa 9 × 22 con eccezioni e anomalie |
| `input-anomali.json` | 11 richieste malformate con esito, tempo e corpo |
| `concorrenza.json` | cinque livelli di concorrenza con rps e percentili |
| `modalita-reali.json` | TTFB e latenza per modalità su traffico reale |
| `storico-per-modalita.json` | aggregati 7 giorni per modalità, sonde escluse |
| `findings.json` | **il report strutturato**, pensato per l'elaborazione automatica |
