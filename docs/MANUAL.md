# AI Router Proxy — Guida Operativa Completa

> Versione documento: 2026-07-26 · Progetto: [ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)

---

## Panoramica

AI Router Proxy è un proxy **self-hosted** che si pone davanti a Claude Code (e qualunque client
Anthropic-format) e instrada il traffico verso **Claude**, **MiniMax**, o **GLM/z.ai** scegliendo
il backend a seconda della modalità attiva.

Il router è un **singolo processo Python/aiohttp** in ascolto su 7 porte:

| Porta | Ruolo |
|-------|-------|
| `8787` | Dinamica — segue `ai-mode` |
| `8771` | Forzata: `anthropic` |
| `8772` | Forzata: `minimax` |
| `8773` | Forzata: `mix-am` |
| `8775` | Forzata: `glm` |
| `8776` | Forzata: `mix-gm` |
| `8777` | Forzata: `mix-ag` |

*(la `8774` esisteva per la modalità `inverse`, rimossa il 2026-07-26)*

**Regola aurea:** il router seleziona il backend. Non tocca impostazioni, skills,
agenti, MCP, tools o system prompt del modello.

**Il router è un tunnel trasparente.** Non orchestra fasi e non tiene stato: guarda
quale modello è richiesto e quale modalità è attiva, riscrive il campo `model` e
inoltra. La gerarchia THINK/ACT/VERIFY e l'escalation vivono nella configurazione
del client, non qui. La mappa è una tabella-dati in `src/role_routing.py`.

---

## Le Sette Modalità

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

**Argomenti accettati:** i 6 nomi canonici più gli alias `mixam`/`mixag`/`mixgm`.
Qualsiasi altro argomento — inclusi i legacy `mixed`, `glm-minimax`, `anthropic-glm`,
che pure `ai-mode` accetta — risponde con l'help **senza cambiare nulla**.

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
   verifica che tutte le 8 porte siano in ascolto. Se una cade e systemd non
   la riavvia entro 4 secondi, la rilancia via nohup.

3. **SessionStart hook** — verifica che lo stack sia attivo all'avvio dell'IDE.

Testato: `kill -9` su tutti i servizi → ripristino completo in <10 secondi.

### Cosa NON fare

- **Non killare** il servizio senza piano di ripristino immediato
- **Non modificare** manualmente i file unit systemd senza capire le conseguenze
- **Non puntare** direttamente a `:8790` o `:8791` — usare sempre `:8787` o le porte fisse
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
| `!router <modo>` risponde con l'help | Argomento non riconosciuto (es. `inverse`, rimossa il 2026-07-26) | Usa uno dei sette nomi canonici, gli alias `mixam`/`mixag`/`mixgm`, oppure gli storici `mixed`/`glm-minimax`/`anthropic-glm`, accettati e normalizzati dal 2026-08-04 |
| Modalità scritta a mano non applicata | Il file di stato accetta solo i 7 nomi canonici | Usa `ai-mode`, che normalizza gli alias |

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
| `AIROUTER_ANTHROPIC_UPSTREAM` | `http://127.0.0.1:8791` | Backend Anthropic |
| `AIROUTER_MINIMAX_UPSTREAM` | `http://127.0.0.1:8790` | Backend MiniMax |
| `AIROUTER_LISTEN_HOST` | `127.0.0.1` | Interfaccia di ascolto |
| `AIROUTER_MINIMAX_MODEL` | `MiniMax-M2.7` | Modello MiniMax per l'ACT |
| `AIROUTER_NEW_PIPELINE` | `1` | Abilita il path di routing corrente |
| `AIROUTER_TRANSITION_FILTERS` | `1` | Filtri di transizione MiniMax (drop-in systemd) |
| `GLM_API_KEY` | — | Chiave z.ai per modalità GLM |
| `AIROUTER_DEBUG_TOKEN` | — | credenziali per le rotte `/debug/`, richieste solo se l'ascolto non è su loopback |

**Rotte /debug/ ed esposizione di rete**: le rotte con prefisso `/debug/` restituiscono il contenuto delle richieste che attraversano il router, e in particolare `/debug/trace` include il corpo integrale dell'ultima richiesta inoltrata all'upstream, quindi system prompt e conversazione, mentre `/debug/errors` arriva a 2000 caratteri del corpo di errore dell'upstream. Finché `AIROUTER_LISTEN_HOST` resta su loopback quelle rotte non sono raggiungibili dalla rete e restano libere. Appena viene impostato un indirizzo non-loopback, il router pretende `AIROUTER_DEBUG_TOKEN`: senza token configurato ogni rotta `/debug/` risponde 404, con il token configurato lo si presenta nell'header `X-Airouter-Debug-Token`, come `Authorization: Bearer <token>`, oppure come parametro `?token=`. La rotta `/__router_health` non è interessata. Il guard è un middleware aiohttp in `src/router_debug.py`, coperto da `sviluppo/tests/test_debug_auth.py`.

Verificate una per una contro il codice il 2026-07-26. Sono state rimosse dalla
tabella `AIROUTER_MIXED_PRIMARY` e `AIROUTER_VERIFY_MODEL`: **nessuna delle due ha
un lettore nel sorgente**, erano residui di pipeline non più esistenti.

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
