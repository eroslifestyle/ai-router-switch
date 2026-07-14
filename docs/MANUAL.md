# AI Router Proxy — Guida Operativa Completa

> Versione documento: 2026-07-14 · Progetto: [ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)

---

## Panoramica

AI Router Proxy è un proxy **self-hosted** che si pone davanti a Claude Code (e qualunque client
Anthropic-format) e instrada il traffico verso **Claude**, **MiniMax**, o **GLM/z.ai** scegliendo
il backend a seconda della modalità attiva.

Il router è un **singolo processo Python/aiohttp** in ascolto su 8 porte:

| Porta | Ruolo |
|-------|-------|
| `8787` | Dinamica — segue `ai-mode` |
| `8771` | Forzata: `anthropic` |
| `8772` | Forzata: `minimax` |
| `8773` | Forzata: `mixed` |
| `8774` | Forzata: `inverse` |
| `8775` | Forzata: `glm` |
| `8776` | Forzata: `glm-minimax` |
| `8777` | Forzata: `anthropic-glm` |

**Regola aurea:** il router seleziona il backend. Non tocca impostazioni, skills,
agenti, MCP, tools o system prompt del modello.

---

## Le Sette Modalità

### 1. `anthropic` — Claude puro

Tutto verso `api.anthropic.com`. Nessun fallback. Se il backend risponde con errore,
l'errore viene restituito al client senza intervento.

**Uso:** quando serve Claude e basta.

### 2. `minimax` — MiniMax puro

Tutto verso `api.minimaxi.chat/anthropic` (endpoint **Anthropic-compat** di MiniMax).
Nessun fallback. MiniMax-M3 orchestra, M2.7 esegue.

**Uso:** task semplici, budget limitato, nessun limite settimanale.

### 3. `mixed` — Fallback bidirezionale

Tenta il backend primario (`ai-mode` o env `AIROUTER_MIXED_PRIMARY`, default: `anthropic`).
Se la risposta ha status in `FALLBACK_STATUSES`, ritenta automaticamente sul backend secondario.

**Status che triggerrano il fallback su Anthropic→MiniMax:**
`401, 403, 408, 409, 413, 429, 500, 502, 503, 504, 529`

**Status che triggerrano il fallback su MiniMax→Anthropic:**
`401, 403, 408, 409, 413, 500, 502, 503, 504, 529`
*(429 è escluso perché il rate limit è gestito internamente da MiniMax)*

**Nota:** status `400` e `404` non causano fallback (errori del client, non del backend).

**Uso:** produzione, continuità di servizio anche se una subscription scade.

### 4. `inverse` — MiniMax genera, Claude verifica

Generazione sempre da MiniMax. Per task **T2** (classificati come critici/complessi)
viene eseguita una verifica aggiuntiva da Claude Opus prima di restituire la risposta.

**Classificazione T2** (euristica sul system prompt):
parole chiave come `"critically"`, `"security"`, `"audit"`, `"vulnerability"`,
`"analyze"`, `"explain in detail"`, `"find issues"`, `"architect"`, `"review"`.

Se Claude Opus non è disponibile, la risposta MiniMax viene comunque restituita
con flag `"unverified"`.

**Uso:** risparmio intelligente con verifica su task critici.

### 5. `glm` — GLM/z.ai puro con tiering

GLM-5.2 classifica la complessità del task → instrada al tier più appropriato:

| Tier | Modello | Condizione |
|------|---------|------------|
| Alto | `glm-5-turbo` | Task complessi, peak-off |
| Basso | `glm-4.7` | Task semplici |
| Top | `glm-5.2` | Peak-off, task complessi non risolti |

**Cost control peak:** fascia `14:00–18:00 Asia/Shanghai` (~08:00–12:00 Italia estate).
In peak `glm-5.2`/`glm-5-turbo` costano 3× e sono bloccati → task complessi
ricadono su Claude, task semplici usano `glm-4.7`.
Off-peak: tiering completo, prezzo 1× promo fino al 2026-09-30.

**Fallback chain:** GLM → MiniMax → Claude.

**Uso:** quando si vuole usare GLM come backend primario.

### 6. `glm-minimax` — GLM pensa, MiniMax esegue

- **GLM-5.2** genera il ragionamento (THINK)
- **MiniMax** esegue (ACT, streaming)
- Per task complessi/agentici: **GLM verifica** il risultato

**Uso:** combinazione del reasoning GLM con la velocità/economicità di MiniMax.

### 7. `anthropic-glm` — Claude orchestra, GLM esegue, Claude verifica T2

- **Claude** (model dell'utente) orchestra e classifica
- **GLM** esegue con tiering (`glm-5-turbo` → `glm-4.7` → `glm-5.2`)
- Task **T2** (critici/complessi): **Claude verifica** prima della consegna

**Fallback chain:** GLM → MiniMax → Claude.

**Uso:** quando Claude è l'orchestratore primario ma si vuole sfruttare GLM per l'esecuzione.

---

## Cambiare Modalità

### Porta dinamica 8787

La porta `8787` legge il file `~/.claude/ai-router-mode` ad ogni richiesta.
Per cambiare modalità a caldo:

```bash
ai-mode minimax        # modo più comodo
ai-mode anthropic
ai-mode mixed
ai-mode inverse
ai-mode glm
ai-mode glm-minimax
ai-mode anthropic-glm
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
!router minimax        # passa a MiniMax per questa chat
!router anthropic      # passa a Claude puro per questa chat
!router mixed          # fallback per questa chat
!router inverse        # modalità inverse per questa chat
!router glm            # GLM per questa chat
!router glm-minimax
!router anthropic-glm
!router status         # mostra modalità corrente e stato backend
!router reset          # ripristina modalità globale da ai-mode
!router help           # help inline
```

Il proxy riconosce anche frasi naturali come `"usa solo claude"` o `"torna a minimax"`.

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

# Sessione con fallback
export ANTHROPIC_BASE_URL=http://127.0.0.1:8773

# Sessione inverse
export ANTHROPIC_BASE_URL=http://127.0.0.1:8774

# Sessioni GLM
export ANTHROPIC_BASE_URL=http://127.0.0.1:8775   # glm
export ANTHROPIC_BASE_URL=http://127.0.0.1:8776   # glm-minimax
export ANTHROPIC_BASE_URL=http://127.0.0.1:8777   # anthropic-glm
```

---

## Health Check

```bash
# Endpoint principale
curl http://127.0.0.1:8787/__router_health

# Risposta esempio:
# {
#   "service": "ai-router-proxy",
#   "mode": "mixed",
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

### Failover automatico

```bash
ai-mode mixed
```

Se Claude è down, la subscription è scaduta, o il rate limit è raggiunto,
il router passa automaticamente a MiniMax.

### Risparmio intelligente con verifica

```bash
ai-mode inverse
```

MiniMax genera per tutti i task. Task critici (T2) vengono verificati da Claude Opus
prima della consegna.

---

## GLM — Chiave API

Le modalità `glm`, `glm-minimax`, `anthropic-glm` richiedono una chiave z.ai.

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
- **Non cambiare** modalità in produzione senza prima testare in `mixed`
- **Non ignorare** gli allarmi del watchdog

---

## Troubleshooting

| Sintomo | Causa | Fix |
|---------|-------|-----|
| Tutte le risposte 401 | Chiave Anthropic scaduta/assente | Usa `mixed` o aggiorna secrets |
| Modalità non cambia | Connessioni persistenti (~2s) | Aspetta 2 secondi |
| GLM mode ritorna 500 | `GLM_API_KEY` non impostata | `export GLM_API_KEY=...` |
| Proxy non risponde | Servizio non avviato | `systemctl --user start ai-router-proxy.service` |
| `mixed` non fa fallback | Status è 400 o 404 (errori client, non backend) | Verifica la richiesta |

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
| `AIROUTER_MIXED_PRIMARY` | `anthropic` | Backend primario in mixed |
| `AIROUTER_MINIMAX_MODEL` | `MiniMax-M3` | Modello MiniMax |
| `AIROUTER_VERIFY_MODEL` | `claude-opus-4-8` | Modello verifica inverse |
| `GLM_API_KEY` | — | Chiave z.ai per modalità GLM |

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
