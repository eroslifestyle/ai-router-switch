# AI Router Proxy — Guida Operativa Completa

> Versione documento: 2026-07-26 · Progetto: [ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)

---

## Panoramica

AI Router Proxy è un proxy **self-hosted** che si pone davanti a Claude Code (e qualunque client
Anthropic-format) e instrada il traffico verso **Claude**, **MiniMax**, o **GLM/z.ai** scegliendo
il backend a seconda della modalità attiva.

Il router è un **singolo processo Python/aiohttp** in ascolto su 10 porte:

| Porta | Ruolo |
|-------|-------|
| `8787` | Dinamica — segue `ai-mode` |
| `8771` | Forzata: `anthropic` |
| `8772` | Forzata: `minimax` |
| `8773` | Forzata: `mix-am` |
| `8774` | Forzata: `mix-al` |
| `8775` | Forzata: `glm` |
| `8776` | Forzata: `mix-gm` |
| `8777` | Forzata: `mix-ag` |
| `8778` | Forzata: `qwen` |
| `8779` | Forzata: `local` |

*(la `8774` era della modalità `inverse`, rimossa il 2026-07-26; dal 2026-08-04 è di `mix-al`)*

**Regola aurea:** il router seleziona il backend. Non tocca impostazioni, skills,
agenti, MCP, tools o system prompt del modello.

**Il router è un tunnel trasparente.** Non orchestra fasi e non tiene stato: guarda
quale modello è richiesto e quale modalità è attiva, riscrive il campo `model` e
inoltra. La gerarchia THINK/ACT/VERIFY e l'escalation vivono nella configurazione
del client, non qui. La mappa è una tabella-dati in `src/role_routing.py`.

---

## Le Nove Modalità

Ogni modalità è una coppia di destinazioni: una per il modello che **pensa** (THINK)
e una per il modello che **esegue** (ACT). Il router deduce il ruolo dal nome del
modello in arrivo — `claude-opus`/`claude-sonnet`/`claude-fable` sono THINK,
`claude-haiku` è ACT — e inoltra al provider corrispondente.

Il **VERIFY non ha una rotta propria**: lo esegue sempre lo stesso modello che ha
fatto il THINK, quindi la richiesta di verifica arriva col nome di quel modello e
ricade da sé sulla rotta THINK. Nelle modalità miste ne segue che *chi verifica non
è mai chi ha eseguito*.

| Modalità | THINK | ACT | Alias legacy accettato |
|---|---|---|---|
| `anthropic` | Anthropic | Anthropic (Haiku) | — |
| `minimax` | MiniMax-M3 | MiniMax-M2.7 | — |
| `glm` | glm-5.2 | glm-4.7 | — |
| `mix-am` | Anthropic | MiniMax-M2.7 | `mixed` |
| `mix-ag` | Anthropic | glm-4.7 | `anthropic-glm` |
| `mix-gm` | glm-5.2 | MiniMax-M2.7 | `glm-minimax` |

Fonte: `ROUTING_TABLE` in `src/role_routing.py` (funzione pura, coperta da 48 test).
Gli alias legacy sono accettati da `ai-mode`, che scrive **sempre** il nome canonico
nel file di stato.

### 1. `anthropic` — Claude puro

Tutto verso `api.anthropic.com`, sia il THINK sia l'ACT. Il router non riscrive il
nome del modello: Anthropic negozia la versione lato server.

**Uso:** quando serve Claude e basta.

### 2. `minimax` — MiniMax puro

Tutto verso `api.minimaxi.chat/anthropic` (endpoint **Anthropic-compat** di MiniMax).
M3 pensa, M2.7 esegue.

**Uso:** task semplici, budget limitato, nessun limite settimanale.

### 3. `mix-am` — Claude pensa, MiniMax esegue

Il THINK va ad Anthropic, l'ACT a MiniMax-M2.7. È la modalità mista di uso comune:
pianificazione e verifica su Claude, esecuzione a basso costo su MiniMax.

Accetta l'alias storico `mixed`.

**Uso:** produzione — qualità sul ragionamento, costo contenuto sull'esecuzione.

### 4. `glm` — GLM/z.ai puro con tiering

Il modello GLM lo decide il ruolo, non un classificatore di complessità:

| Ruolo | Modello | Note |
|-------|---------|------|
| THINK | `glm-5.2` | Orchestrazione: classifica, pianifica, verifica |
| ACT | `glm-4.7` | Esecuzione |

**Cost control peak:** fascia `14:00–18:00 Asia/Shanghai` (~08:00–12:00 Italia estate).
In peak `glm-5.2`/`glm-5-turbo` costano 3× e vengono declassati automaticamente a
`glm-4.7` dal router; in quella fascia quindi anche il THINK gira su `glm-4.7`.
Il declassamento riguarda le modalità `glm` e `mix-gm`, le uniche che instradano
`glm-5.2`. Viene registrato nel log con il prefisso `GLM peak-cap`.
Off-peak: nessun declassamento, prezzo 1× promo fino al 2026-09-30.

**Escalation:** resta sul ladder GLM. In questa modalità non intervengono né MiniMax
né Anthropic.

**Uso:** quando si vuole usare GLM come backend primario.

### 5. `mix-gm` — GLM pensa, MiniMax esegue

- **glm-5.2** fa il THINK e il VERIFY
- **MiniMax-M2.7** esegue

Accetta l'alias storico `glm-minimax`.
**Escalation esecuzione:** `M2.7 → M3 → GLM`, **mai** Anthropic.

**Uso:** reasoning GLM con la velocità e l'economia di MiniMax, tenendo Claude fuori dal flusso.

### 6. `mix-ag` — Claude pensa, GLM esegue

- **Claude** fa il THINK e il VERIFY
- **glm-4.7** esegue

Accetta l'alias storico `anthropic-glm`.

**Uso:** Claude come orchestratore, GLM per l'esecuzione a basso costo.

### 7. `qwen` — Qwen puro (Alibaba Model Studio)

- **qwen3.8-max** fa il THINK e il VERIFY
- **qwen3-coder-plus** esegue

Modalità PURA: THINK e ACT restano entrambi su Qwen, non intervengono né Anthropic né MiniMax né GLM.

Endpoint Anthropic-compatible sull'host dedicato del workspace, `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`, che autentica con `x-api-key`. La base URL termina con `/apps/anthropic` SENZA `/v1`. I servizi DashScope nativi, sotto `/api/v1/...`, vogliono invece `Authorization: Bearer`.

Chiave: `secrets.sh get qwen.api_key`. Porta fissa: `8778`. Guida dedicata: `docs/MODALITA-QWEN.md`.

**Uso:** contesti molto lunghi a basso costo — `qwen3-coder-plus` dichiara 1.048.576 token di input.

---

### 8. `mix-al` — Claude pensa, il modello locale esegue

- **Fable 5 / Opus 5 / Sonnet 5** fanno il THINK e il VERIFY
- **code-max**, in locale, esegue

THINK e VERIFY restano su Anthropic (Fable 5, Opus 5 o Sonnet 5, scelti dall'utente); solo l'ACT va al modello locale `code-max`. Il provider "local" non ha un modello THINK proprio, quindi in `mix-al` la fase cognitiva non può uscire da Anthropic.

Quando l'esecuzione locale fallisce, l'escalation risale lungo i tier Anthropic: Sonnet → Opus → Fable.

Porta fissa: `8774`. Era la porta di `inverse`, modalità rimossa il 2026-07-26; il 2026-08-04 è stata riassegnata a `mix-al`.

Il modello locale è esposto via LiteLLM, che parla il protocollo Anthropic nativo su `/v1/messages`. Chiave e base URL si leggono da `LOCAL_LLM_API_KEY` e `LOCAL_LLM_API_BASE`; in loro assenza il router carica `secrets/local-llm.env`. Timeout: `AIROUTER_LOCAL_TIMEOUT_SEC`, default 240 secondi. Retry: massimo 2.

**Uso:** esecuzione a costo zero con il codice che non lascia la macchina, orchestrazione e ragionamento su Claude.

---

### 9. `local` — modello locale puro

- **code-max** fa il THINK e il VERIFY
- **code-max** esegue

Modalità PURA: THINK e ACT vanno entrambi al modello locale `code-max`; non intervengono né Anthropic, né MiniMax, né GLM.

Porta fissa: `8779`. Stesse chiavi (`LOCAL_LLM_API_KEY` / `LOCAL_LLM_API_BASE`, fallback `secrets/local-llm.env`), stesso timeout (`AIROUTER_LOCAL_TIMEOUT_SEC`, default 240 s) e stessi 2 retry di `mix-al`.

Il router accetta solo `code-max`: qualsiasi altro modello richiesto viene ricondotto a quello. Prima dell'inoltro aggiunge un suggerimento di sistema al prompt.

**Uso:** lavorare completamente offline, senza che alcun dato esca dalla macchina.

---

## Cambiare Modalità

### Porta dinamica 8787

La porta `8787` legge il file `~/.claude/ai-router-mode` ad ogni richiesta.
Per cambiare modalità a caldo:

```bash
ai-mode anthropic
ai-mode minimax
ai-mode mix-am         # alias: mixam, mixed
ai-mode mix-ag         # alias: mixag, anthropic-glm
ai-mode mix-gm         # alias: mixgm, glm-minimax
ai-mode glm
ai-mode status
ai-mode log
```

Oppure manualmente:

```bash
echo "minimax" > ~/.claude/ai-router-mode
echo "anthropic" > ~/.claude/ai-router-mode
```

**Propagazione:** richiede ~2 secondi (aiohttp mantiene connessioni persistenti).

### Comandi In-Chat

Durante una conversazione è possibile inviare comandi **isolati per chat** (non globali).
Il proxy riconosce il fingerprint della conversazione dalla sessione Claude Code.

```
!router anthropic      # passa a Claude puro per questa chat
!router minimax        # passa a MiniMax per questa chat
!router mixam          # Claude pensa + MiniMax esegue
!router mixag          # Claude pensa + GLM esegue
!router mixgm          # GLM pensa + MiniMax esegue
!router glm            # GLM per questa chat
!router status         # mostra modalità corrente e stato backend
!router reset          # ripristina modalità globale da ai-mode
!router help           # help inline
```

**Argomenti accettati:** i 9 nomi canonici più gli alias `mixam`/`mixag`/`mixgm`.
Qualsiasi altro argomento — inclusi i legacy `mixed`, `glm-minimax`, `anthropic-glm`,
che pure `ai-mode` accetta — risponde con l'help **senza cambiare nulla**.

**Dal terminale, per una sola sessione**

```bash
scripts/router qwen   # imposta qwen solo per questa sessione
scripts/router        # elenca le modalità accettate
```

Il comando esiste perché il punto esclamativo a inizio riga viene intercettato dalla shell del CLI e `!router …` non raggiunge mai il proxy; la sessione è identificata dalla variabile d'ambiente `CLAUDE_CODE_SESSION_ID` e, se manca, il comando lo segnala suggerendo `ai-mode` per il cambio globale. A differenza di `ai-mode`, `scripts/router` non tocca il file di modalità globale, quindi non sposta le altre chat.

> **Switch a voce rimosso il 2026-07-26.** Il proxy riconosceva anche frasi in
> linguaggio naturale (*«usa solo claude»*), ma commutava la modalità **senza
> autorizzazione**: bastava un messaggio breve con un verbo comune più una
> parola-modalità ovunque nel testo, quindi frasi di lavoro normali come *«cambia il
> commento che cita glm»* cambiavano la chat. `!router` esplicito è ora l'unico
> switch disponibile da chat.

**Scope:** il comando cambia la modalità solo per quella conversazione.
**Importante:** `!router` è gestito dal proxy `:8787` — non devo rispondere a questi
messaggi, viaggiano fino al proxy che li intercetta.

### Porte fisse

Per forzare una modalità senza modificare file o usare comandi in-chat,
puntare direttamente alla porta fissa:

```bash
# Sessione Claude pura
export ANTHROPIC_BASE_URL=http://127.0.0.1:8771

# Sessione MiniMax pura
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772

# Sessione mista: Claude pensa, MiniMax esegue
export ANTHROPIC_BASE_URL=http://127.0.0.1:8773

# Sessioni GLM
export ANTHROPIC_BASE_URL=http://127.0.0.1:8775   # glm
export ANTHROPIC_BASE_URL=http://127.0.0.1:8776   # mix-gm
export ANTHROPIC_BASE_URL=http://127.0.0.1:8777   # mix-ag
```

---

## Health Check

```bash
# Endpoint principale
curl http://127.0.0.1:8787/__router_health

# Risposta esempio:
# {
#   "service": "ai-router-proxy",
#   "mode": "mix-am",
#   "port_role": "dynamic",
#   "version": "...",
#   "backends": { "anthropic": "up", "minimax": "up" }
# }

# Metriche Prometheus
curl http://127.0.0.1:8787/metrics
curl http://127.0.0.1:8787/stats

# Endpoints Kubernetes-compatibili
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/readyz
curl http://127.0.0.1:8787/livez
```

---

## Header e telemetria delle risposte

### `x-ai-actual-model` — chi ha risposto davvero

Il campo `model` del corpo della risposta riporta il modello **chiesto dal
client**, non quello che ha generato il testo: il router lo riscrive, perché i
client rifiutano un nome di modello che non riconoscono. Per sapere chi ha
effettivamente risposto c'è un header dedicato, presente su tutte e nove le
modalità:

```bash
curl -s -D - -o /dev/null http://127.0.0.1:8787/v1/messages \
  -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":8,
       "messages":[{"role":"user","content":"ciao"}]}' | grep -i x-ai-actual-model
# x-ai-actual-model: MiniMax-M2.7
```

Il valore coincide sempre con il campo `final` registrato nel sidecar
`~/.claude/logs/router-usage.jsonl`: è calcolato una volta sola. Quando il
modello richiesto non è riconosciuto, l'header dichiara il provider
(`minimax-mode:mix-am`) e mai il nome inventato dal client.

L'header è **additivo**: il campo `model` del corpo non è stato modificato, per
non rompere i client esistenti.

### `x-airouter-synthetic` — marcare sonde e test

Chi genera traffico di prova dovrebbe inviarlo:

```bash
curl -H 'x-airouter-synthetic: 1' ...
```

L'entry corrispondente nel sidecar riceve `synthetic: true`, e viene esclusa
dalle statistiche di `/debug/stats` e dall'apprendimento della policy di
self-healing. Senza questa marcatura le sonde falsano le metriche per modalità:
è già successo quattro volte in questo progetto — in un caso una modalità
risultava al 95,3% di errori mentre le richieste reali erano nove, tutte
riuscite. Valori accettati: `1`, `true`, `yes`.

### Contratto di risposta — cosa varia fra le modalità

Il router **non uniforma** il corpo della risposta: passa i byte come li riceve
dall'upstream (`auto_decompress=False`, scelta deliberata — la libreria Brotli
locale è rotta e decomprimere romperebbe ogni risposta `br`). Le differenze qui
sotto nascono quindi dai provider, non dal proxy. Misura del 2026-08-08, un
ciclo di `sviluppo/tools/probe_modes.py`, richieste identiche **senza**
`Accept-Encoding`:

| Modalità | Content-Encoding | Formato `id` | Provider dell'id |
|---|---|---|---|
| `anthropic` | gzip | `msg_…` | Anthropic |
| `minimax` | in chiaro | esadecimale nudo | MiniMax |
| `mix-am` | in chiaro | esadecimale nudo | MiniMax |
| `mix-gm` | in chiaro | esadecimale nudo | MiniMax |
| `glm` | gzip | `msg_…` | z.ai |
| `mix-ag` | gzip | `msg_…` | z.ai |
| `qwen` | gzip | `msg_…` | Alibaba |
| `mix-al` | in chiaro | `resp_<base64>` | LiteLLM locale |
| `local` | in chiaro | `resp_<base64>` | LiteLLM locale |

Conseguenze pratiche:

- Un client che **non negozia** la compressione riceve byte illeggibili da
  quattro modalità su nove. Gli strumenti diagnostici vanno scritti per
  entrambe le forme (o si invii sempre `Accept-Encoding: identity`).
- I formati di `id` sono **tre**, non due: chi assume il prefisso `msg_` o una
  lunghezza ragionevole va incontro a sorprese su `mix-al`/`local` (l'id
  LiteLLM incorpora metadati del provider in base64) e sulle tre modalità
  MiniMax (nessun prefisso).
- L'unica cosa uniforme su tutte e nove è l'header `x-ai-actual-model`.

Il comportamento è fissato da `sviluppo/tests/test_contratto_risposta.py`, che
è un test di **caratterizzazione**: se diventa rosso, il contratto è cambiato e
va deciso se il cambiamento è voluto — non è detto che sia un bug.

### Latenza per modalità

```bash
curl -s http://127.0.0.1:8787/debug/stats | python3 -m json.tool
```

Il campo `latenza` riporta i percentili p50/p90/p99 di `ttfb_ms` (primo byte) e
`total_ms` (fine dello stream) per ciascuna modalità, sulle sole richieste non
sintetiche delle ultime 24 ore. La finestra si cambia con `?finestra_s=`.
Il campo `policy_degraded` conta quante volte la policy di self-healing avrebbe
deviato una richiesta: il router **non** devia — l'attuazione è lato agente per
scelta — ma il conteggio rende misurabile quanto peserebbe.

---

## Esempi d'Uso

### Claude Code — base

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
```

Claude Code userà automaticamente la modalità impostata da `ai-mode`.

### Sessioni parallele con backend diversi

```bash
# Terminale 1 (VSCode): sempre Claude
export ANTHROPIC_BASE_URL=http://127.0.0.1:8771

# Terminale 2: sempre MiniMax
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772

# Terminale 3: sempre GLM
export ANTHROPIC_BASE_URL=http://127.0.0.1:8775
```

Le sessioni operano indipendentemente senza interferenze.

### Ragionamento su Claude, esecuzione economica

```bash
ai-mode mix-am
```

Il THINK e il VERIFY restano su Claude, l'esecuzione va a MiniMax-M2.7. Se
l'esecutore fallisce ripetutamente, l'escalation risale i tier Anthropic
(Sonnet → Opus → Fable) — il modello che pensa non cambia mai da sé.

### Tenere Claude fuori dal flusso

```bash
ai-mode mix-gm
```

glm-5.2 pensa e verifica, MiniMax-M2.7 esegue. L'escalation resta su
`M2.7 → M3 → GLM`: Anthropic non viene mai coinvolto.

---

## GLM — Chiave API

Le modalità `glm`, `mix-gm`, `mix-ag` richiedono una chiave z.ai.

```bash
export GLM_API_KEY=...
# oppure
secrets.sh set glm.api_key <valore>
```

Senza la chiave, le modalità GLM ritornano errore 500 con messaggio esplicito.
Le altre modalità continuano a funzionare normalmente.

---

## Hardening e Resilienza

### Tripla difesa

1. **systemd** — servizio `ai-router-proxy.service` con `Restart=always`,
   `OOMScoreAdjust=-900`, linger abilitato.

2. **Cron watchdog** — `scripts/ai-stack-guard.sh` eseguito ogni 60 secondi
   verifica che tutte le 10 porte siano in ascolto. Se una cade e systemd non
   la riavvia entro 4 secondi, la rilancia via nohup.

3. **SessionStart hook** — verifica che lo stack sia attivo all'avvio dell'IDE.

Testato: `kill -9` su tutti i servizi → ripristino completo in <10 secondi.

### Cosa NON fare

- **Non killare** il servizio senza piano di ripristino immediato
- **Non modificare** manualmente i file unit systemd senza capire le conseguenze
- **Endpoint diretti**: non puntare le applicazioni direttamente agli endpoint dei provider, ma sempre alla porta `8787` oppure a una delle porte fisse per modalità, altrimenti si salta il routing.
- **Non cambiare** modalità in produzione senza prima provarla su una porta fissa
- **Non ignorare** gli allarmi del watchdog

---

## Troubleshooting

| Sintomo | Causa | Fix |
|---------|-------|-----|
| Tutte le risposte 401 | Chiave Anthropic scaduta/assente | Passa a `minimax` o `glm`, oppure aggiorna i secrets |
| Modalità non cambia | Connessioni persistenti (~2s) | Aspetta 2 secondi |
| GLM mode ritorna 500 | `GLM_API_KEY` non impostata | `export GLM_API_KEY=...` |
| Proxy non risponde | Servizio non avviato | `systemctl --user start ai-router.service` |
| `!router <modo>` risponde con l'help | Argomento non riconosciuto (es. `inverse`, rimossa il 2026-07-26) | Usa uno dei nove nomi canonici, gli alias `mixam`/`mixag`/`mixgm`, oppure gli storici `mixed`/`glm-minimax`/`anthropic-glm`, accettati e normalizzati dal 2026-08-04 |
| Modalità scritta a mano non applicata | Il file di stato accetta solo i 9 nomi canonici | Usa `ai-mode`, che normalizza gli alias |

### Debug

```bash
# Status servizio
systemctl --user status ai-router-proxy.service

# Porte in ascolto
ss -tlnp | grep -E '877[1-7]|8787'

# Log recenti
journalctl --user -u ai-router-proxy.service -n 50

# Health endpoint
curl http://127.0.0.1:8787/__router_health
```

---

## Variabili d'Ambiente

| Variabile | Default | Descrizione |
|-----------|---------|------------|
| `AIROUTER_PORT` | `8787` | Porta base |
| `AIROUTER_LISTEN_HOST` | `127.0.0.1` | Interfaccia di ascolto |
| `AIROUTER_ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` | Endpoint Anthropic |
| `AIROUTER_MINIMAX_UPSTREAM` | `https://api.minimaxi.chat/anthropic` | Endpoint MiniMax |
| `AIROUTER_MINIMAX_MODEL` | `MiniMax-M3` | Modello MiniMax di destinazione predefinito |
| `AIROUTER_TRANSITION_FILTERS` | `0` | Filtri di transizione MiniMax; la unit systemd del progetto lo porta a 1 |
| `GLM_API_KEY` | — | Chiave z.ai per le modalità GLM |
| `AIROUTER_DEBUG_TOKEN` | — | Credenziali per le rotte /debug/ e /admin/, richieste solo se l'ascolto non è su loopback |
| `AIROUTER_MINIMAX_CONTEXT_LIMIT` | `750000` | Limite in BYTE del contesto della richiesta, non in token |
| `AIROUTER_NON_STREAM_SOCK_READ_SEC` | `600` | Tetto di lettura per le risposte non-streaming; lo streaming non lo usa |
| `AIROUTER_MINIMAX_SEMAPHORE` | `8` | Richieste concorrenti massime verso MiniMax (analoghe: `AIROUTER_GLM_SEMAPHORE`, `AIROUTER_QWEN_SEMAPHORE`) |
| `AIROUTER_LOCAL_TIMEOUT_SEC` | `240` | Timeout del backend locale, usato dalle modalità `local` e `mix-al` |
| `AIROUTER_TOOLS_TELEMETRY` | `0` | Misura il peso del blocco `tools` di ogni richiesta e lo scrive nel sidecar |
| `AIROUTER_CATALOG_PATH` | — | Sposta il `BUG-CATALOG.jsonl`; la suite di test lo punta a una tmpdir per non scrivere in quello di produzione |
| `AIROUTER_DEEP_DEBUG` | `0` | Diagnostica estesa sul percorso caldo |

**Rotte /debug/ ed esposizione di rete**: le rotte con prefisso `/debug/` restituiscono il contenuto delle richieste che attraversano il router, e in particolare `/debug/trace` include il corpo integrale dell'ultima richiesta inoltrata all'upstream, quindi system prompt e conversazione, mentre `/debug/errors` arriva a 2000 caratteri del corpo di errore dell'upstream. Finché `AIROUTER_LISTEN_HOST` resta su loopback quelle rotte non sono raggiungibili dalla rete e restano libere. Appena viene impostato un indirizzo non-loopback, il router pretende `AIROUTER_DEBUG_TOKEN`: senza token configurato ogni rotta `/debug/` risponde 404, con il token configurato lo si presenta nell'header `X-Airouter-Debug-Token`, come `Authorization: Bearer <token>`, oppure come parametro `?token=`. La rotta `/__router_health` non è interessata. Lo stesso guard copre anche le rotte con prefisso `/admin/`, fra cui `/admin/mode/<modo>` che riscrive la modalità globale del router; senza il guard, un router esposto in rete permetterebbe a chiunque di dirottare la modalità di tutte le chat. Il guard è un middleware aiohttp in `src/router_debug.py`, coperto da `sviluppo/tests/test_debug_auth.py`.

Verificate una per una contro il codice il 2026-08-06, leggendo i valori dal
modulo invece che dalla memoria: tre default erano invecchiati (i due upstream
puntavano ancora a `127.0.0.1:8791` e `127.0.0.1:8790`, che nel codice non
compaiono più, e il modello MiniMax era fermo a `MiniMax-M2.7`). Per
`AIROUTER_TRANSITION_FILTERS` la colonna diceva `1`, che è il valore imposto
dalla unit systemd, non il default del codice, che è `0`.

Rimosse dalla tabella `AIROUTER_MIXED_PRIMARY`, `AIROUTER_VERIFY_MODEL` e, dal
2026-08-06, `AIROUTER_NEW_PIPELINE`: **nessuna delle tre ha un lettore nel
sorgente**, erano residui di pipeline non più esistenti.

Dal 2026-08-07 è caduta anche `AIROUTER_MIXED_EXECUTOR`, per lo stesso motivo:
l'esecutore delle modalità miste lo decide `role_routing.resolve_route`, e la
costante che leggeva quella variabile era rimasta senza lettori. La tabella elenca
le variabili di uso comune, non tutte: l'elenco esaustivo è il codice, dove si
trovano con `grep -rn AIROUTER_ src/`.

---

## File rilevanti

```
src/
  ai-router-proxy.py     # Proxy principale
  glm_backend.py         # Backend GLM (importato difensivamente)
  peak_scheduler.py      # Scheduler peak per GLM

scripts/
  ai-mode                # Helper CLI per cambio modalità
  ai-stack-guard.sh      # Watchdog cron

sviluppo/
  tests/
    test_glm_modes.sh    # Test isolamento modalità GLM
```

---

## Supporto

Per segnalare problemi, includere:

1. Output di `systemctl --user status ai-router-proxy.service`
2. Output di `curl http://127.0.0.1:8787/__router_health`
3. Ultime 50 righe di `journalctl --user -u ai-router-proxy.service`
4. Contenuto di `~/.claude/ai-router-mode`
5. Variabili d'ambiente rilevanti (escludere chiavi API)
