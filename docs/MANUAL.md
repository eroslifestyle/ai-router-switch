# Manuale Router Stack - Documentazione Operativa

## Panoramica

Questo documento descrive il router stack per la gestione del traffico API verso i provider AI (Anthropic Claude e MiniMax). Il sistema fornisce fallback automatico, compressione del contesto, e modalità di routing flessibili per ottimizzare costi e resilienza.

Lo stack è composto da 3 servizi systemd e un proxy aiohttp che si mette in ascolto su 5 porte diverse.

## Architettura di Rete

```
[Applicazione] → http://127.0.0.1:8787  (porta dinamica)
        ↓
        [ai-router-proxy]
        ├── → http://127.0.0.1:8791  (headroom-proxy)
        │   ↓ (compressione context)
        │   → https://api.anthropic.com
        │
        └── → http://127.0.0.1:8790  (headroom-minimax)
            ↓ (compressione context)
            → https://api.minimax.io/anthropic
```

Le porte in ascolto su 127.0.0.1:

| Porta | Servizio | Modalità |
|-------|----------|----------|
| 8787 | ai-router-proxy | Dinamica (legge file mode) |
| 8791 | headroom-proxy | Forward ad Anthropic |
| 8790 | headroom-minimax | Forward a MiniMax |
| 8771 | ai-router-proxy | Forzata: anthropic |
| 8772 | ai-router-proxy | Forzata: minimax |
| 8773 | ai-router-proxy | Forzata: mixed |
| 8774 | ai-router-proxy | Forzata: interactive |

## Le Quattro Modalità di Routing

### 1. Modalità Anthropic (Pura)

Tutto il traffico viene inoltrato esclusivamente a Claude tramite headroom-proxy. Nessun fallback. Se Claude non risponde, la richiesta fallisce con errore.

**Uso tipico**: sviluppo che richiede qualità massima, nessuna tolleranza per alternative.

### 2. Modalità MiniMax (Pura)

Tutto il traffico viene inoltrato a MiniMax tramite headroom-minimax. Utilizza il modello M3 (Claude-compatibile). Nessun fallback.

**Uso tipico**: task semplici, budget limitato, ambienti di test.

### 3. Modalità Mixed (Fallback Bidirezionale)

Il router tenta il backend primario (configurabile, default: anthropic). Se riceve uno status di errore retryable, ritenta automaticamente sul backend secondario.

**Status che triggerrano fallback**: 401, 403, 408, 409, 413, 429, 500, 502, 503, 504, 529.

**Circuit breaker**: se un backend fallisce 3 volte consecutive, viene messo in cooldown per 120 secondi prima di essere ritentato.

**Esclusi dal fallback**: 400 (errore client), 404 (risorsa non trovata). Questi indicano problemi della richiesta, non del backend.

**Uso tipico**: produzione con tolleranza ai fallback.

### 4. Modalità Interactive (Intelligente)

Classifica automaticamente i task e li instrada al backend più appropriato:

- **Task T0/T1** (semplici, non critici): riassunti, refactor meccanici, traduzioni → MiniMax
- **Task T2** (complessi, critici): analisi sicurezza, audit, spiegazioni dettagliate → MiniMax per bozza → Claude Opus per verifica

**Classificazione T2** (euristica sul system prompt):
- Parole chiave: "critically", "security", "audit", "vulnerability", "analyze", "explain in detail", "find issues", "architect", "review"

**Resilienza**: se Claude Opus non è disponibile, la bozza MiniMax viene comunque restituita con flag "unverified".

**Uso tipico**: workflow di sviluppo giornaliero con ottimizzazione costi/qualità.

## Cambiare Modalità Runtime

### Porta Dinamica (8787)

La porta 8787 è **dinamica**: legge il file `~/.claude/ai-router-mode` ad ogni richiesta. Per cambiare modalità senza riavviare servizi:

```bash
echo "minimax" > ~/.claude/ai-router-mode
echo "anthropic" > ~/.claude/ai-router-mode
echo "mixed" > ~/.claude/ai-router-mode
echo "interactive" > ~/.claude/ai-router-mode
```

**Nota**: la propagazione richiede ~2 secondi (le connessioni aiohttp persistenti mantengono il vecchio mode in cache).

Se disponibile, usare lo script helper:

```bash
ai-mode minimax
ai-mode anthropic
ai-mode mixed
ai-mode interactive
ai-mode status
```

### Porte Fisse (8771-8774)

Per forzare una modalità specifica senza modificare file, puntare direttamente:

```bash
# Sessione Claude pura
export ANTHROPIC_BASE_URL=http://127.0.0.1:8771

# Sessione MiniMax pura
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772

# Sessione con fallback
export ANTHROPIC_BASE_URL=http://127.0.0.1:8773

# Sessione intelligente
export ANTHROPIC_BASE_URL=http://127.0.0.1:8774
```

## Comandi In-Chat

Durante una conversazione è possibile inviare comandi diretti al proxy:

```
!router anthropic    # passa a Claude puro per questa chat
!router minimax      # passa a MiniMax puro per questa chat
!router mixed        # passa a fallback per questa chat
!router interactive  # passa a modalità intelligente per questa chat
!router status       # mostra modalità corrente e backend
!router reset        # ripristina modalità da file
!router help         # lista comandi disponibili
```

Il proxy riconosce anche frasi naturali come "usa solo claude" o "torna a minimax".

**Scope**: il comando cambia la modalità solo per quella conversazione (basato su chat fingerprint), non globalmente.

## Health Check

Verificare lo stato dei servizi:

```bash
# Proxy principale
curl http://127.0.0.1:8787/__router_health

# Output esempio:
# {"service":"ai-router-proxy","mode":"mixed","port_role":"dynamic","version":"1.4.2"}

# Headroom (formato Prometheus)
curl http://127.0.0.1:8791/metrics
curl http://127.0.0.1:8790/metrics

# Endpoint Kubernetes-compatibili
curl http://127.0.0.1:8791/health
curl http://127.0.0.1:8791/readyz
curl http://127.0.0.1:8791/livez
curl http://127.0.0.1:8790/health
curl http://127.0.0.1:8790/readyz
```

## Esempi d'Uso

### Claude Code

Lo script di setup imposta automaticamente:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
```

Claude Code userà la modalità correntemente impostata nel file mode.

### Sessioni Multiple

**Scenario**: due terminali che devono usare backend diversi.

```bash
# Terminale 1 (VSCode) - sempre Claude
export ANTHROPIC_BASE_URL=http://127.0.0.1:8771

# Terminale 2 - sempre MiniMax
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772
```

Le due sessioni operano indipendentemente senza interferenze.

### Failover Automatico

Per garantire continuità di servizio:

```bash
ai-mode mixed
```

Se Claude è down, la subscription è scaduta, o il rate limit è raggiunto, il router passa automaticamente a MiniMax con modello M3.

**Attenzione**: la qualità della risposta può variare.

### Risparmio Intelligente

Configurazione ottimale per workflow di sviluppo:

```bash
ai-mode interactive
```

- Riassunti, refactor semplici, domande veloci → MiniMax (gratis/cheap)
- Analisi sicurezza, architettura, review complesse → Claude (verificato)

## Troubleshooting Rapido

| Sintomo | Causa | Risoluzione |
|---------|-------|-------------|
| Tutte le risposte 401 | Chiave Anthropic scaduta/removal | Usa `mixed` mode o aggiorna secrets |
| Latenza alta su 8772 | headroom#2 in cooldown | `systemctl --user restart headroom-minimax.service` |
| Modalità non cambia | Cache connessioni (~2s) | Attendi 2 secondi, riprova |
| /readyz troppi log | (risolto 2026-06-23) | Aggiorna ai-router-proxy |
| mixed non fa fallback | Status non in FALLBACK_STATUSES | Verifica codice, probabilmente errore client |
| Connessione rifiutata 8787 | Servizio non avviato | `systemctl --user start ai-router-proxy.service` |
| Errori randomici | headroom in crash loop | `systemctl --user status headroom-proxy.service` |

### Comandi di Diagnostica

```bash
# Status servizi
systemctl --user status ai-router-proxy.service
systemctl --user status headroom-proxy.service
systemctl --user status headroom-minimax.service

# Log recenti
journalctl --user -u ai-router-proxy.service -n 50

# Porte in ascolto
ss -tlnp | grep -E '8787|8790|8791|877[1-4]'

# Riavvio completo stack
systemctl --user restart headroom-proxy.service headroom-minimax.service ai-router-proxy.service
```

## Hardening e Resilienza

### Difesa a Tre Livelli

**Livello 1 - systemd**:
- Servizi `enabled` per auto-start al boot
- `Restart=always` per riavvio automatico
- `RestartSec=2` per delay tra restart

**Livello 2 - Watchdog Cron**:
- Script `ai-stack-guard.sh` eseguito ogni 60 secondi
- Verifica che tutte le 5 porte siano in ascolto
- Se una porta manca e systemd non ha riavviato entro 4s, rilancia il servizio via nohup

**Livello 3 - Hook PreToolUse**:
- Policy `AI-ROUTER-POLICY` blocca comandi pericolosi:
  - `kill` sui processi dei servizi
  - `systemctl stop` sui servizi
  - `systemctl disable` sui servizi

### Cose da NON Fare

- **Non killare** i 3 servizi senza piano di ripristino immediato
- **Non modificare** manualmente i file unit systemd senza capire le conseguenze
- **Non puntare** direttamente a :8790 o :8791 dalle applicazioni
- **Non cambiare** modalità in produzione senza testare prima in `mixed`
- **Non ignorare** i warning del watchdog

## Variabili d'Ambiente

### Proxy ai-router

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `AIROUTER_PORT` | 8787 | Porta base |
| `AIROUTER_ANTHROPIC_UPSTREAM` | http://127.0.0.1:8791 | Backend Anthropic |
| `AIROUTER_MINIMAX_UPSTREAM` | http://127.0.0.1:8790 | Backend MiniMax |
| `AIROUTER_MIXED_PRIMARY` | anthropic | Backend primario in mixed |
| `AIROUTER_MINIMAX_MODEL` | MiniMax-M3 | Modello MiniMax |
| `AIROUTER_VERIFY_MODEL` | claude-opus-4-8 | Modello verifica interactive |

### Headroom

| Variabile | Servizio | Descrizione |
|-----------|----------|-------------|
| `ANTHROPIC_API_KEY` | headroom-proxy | Chiave Anthropic |
| `MINIMAX_API_KEY` | headroom-minimax | Chiave MiniMax |

## Requisiti di Sistema

- Linux con systemd (user services)
- Python 3.11+ con aiohttp
- Headroom CLI installato e configurato
- 3 unit file systemd in `~/.config/systemd/user/`
- Script watchdog in `~/bin/` o in PATH

## File di Configurazione

Struttura tipica:

```
~/.config/systemd/user/
  ├── ai-router-proxy.service
  ├── headroom-proxy.service
  └── headroom-minimax.service

~/.claude/
  └── ai-router-mode           # contiene: anthropic|minimax|mixed|interactive

~/bin/
  └── ai-stack-guard.sh        # watchdog
  └── ai-mode                  # helper CLI
```

## Supporto e Debug

Per segnalare problemi, includere:

1. Output di `systemctl --user status` per tutti e 3 i servizi
2. Output di `curl http://127.0.0.1:8787/__router_health`
3. Log recenti: `journalctl --user -u ai-router-proxy.service -n 100`
4. Contenuto di `~/.claude/ai-router-mode`
5. Variabili d'ambiente rilevanti (escludere chiavi API)
