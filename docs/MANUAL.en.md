# Manuale Operativo - AI Router Stack

## Panoramica

Questo documento descrive il router stack che gestisce il traffico API verso
i backend Anthropic (Claude) e MiniMax (modello M3). Il sistema è composto
da un proxy principale e due servizi di compressione context.

## Architettura

```
[Applicazione]
      |
      v
  :8787 ai-router (proxy switcher, modalità dinamica)
      |
      +---> :8791 headroom-proxy ---> api.anthropic.com
      |
      +---> :8790 headroom-minimax ---> api.minimax.io/anthropic

Porte fixed:
  :8771 ai-router (FORCED: anthropic)
  :8772 ai-router (FORCED: minimax)
  :8773 ai-router (FORCED: mixed)
  :8774 ai-router (FORCED: interactive)
```

Il proxy ai-router è un singolo processo aiohttp in ascolto su 5 porte.
Le porte 8771-8774 hanno la modalità preimpostata nel bind, permettendo
di usare backend diversi in sessioni parallele.

---

## Le Quattro Modalità

### 1. Anthropic (Pura)

Tutto il traffico viene inoltrato esclusivamente a `:8791` (headroom-proxy
verso api.anthropic.com). Nessun fallback. Se il backend risponde con
errore, l'errore viene restituito tale e quale al client.

**Uso consigliato**: quando serve Claude e basta, senza compromessi.

### 2. MiniMax (Pura)

Tutto il traffico viene inoltrato esclusivamente a `:8790` (headroom-minimax
verso api.minimax.io/anthropic, modello M3). Nessun fallback.

**Uso consigliato**: task semplici dove il modello M3 è sufficiente.

### 3. Mixed (Fallback Bidirezionale)

Tenta il backend primario (variabile `AIROUTER_MIXED_PRIMARY`, default
anthropic). Se la risposta ha status in `FALLBACK_STATUSES`, ritenta
automaticamente sul backend secondario.

**Status che triggerrano il fallback**: 401, 403, 408, 409, 413, 429,
500, 502, 503, 504, 529.

**Nota**: status 400 e 404 non causano fallback (sono errori del client).

**Circuit breaker**: 3 fallimenti consecutivi su un backend mettono
quel backend in cooldown per 120 secondi.

**Uso consigliato**: produzione, quando vuoi continuità di servizio.

### 4. Interactive (Intelligente)

Classificazione automatica dei task basata su euristica del system prompt:

- **T0/T1** (task semplici, non critici): inoltrati a MiniMax
- **T2** (task complessi, critici): generati da MiniMax, poi verificati
  da Claude (opus)

Se Claude non è disponibile, la bozza MiniMax viene comunque restituita
marcata `"unverified"`.

**Parole chiave che classificano T2**: "critically", "security", "audit",
"vulnerability", "analyze", "explain in detail", "find issues",
"architectural", "refactor deep".

**Uso consigliato**: sviluppo giornaliero con risparmio intelligente.

---

## Cambiare Modalità Runtime

La porta `:8787` legge il file `~/.claude/ai-router-mode` ad ogni
richiesta. Per cambiare modalità a caldo:

```bash
echo "minimax" > ~/.claude/ai-router-mode
echo "anthropic" > ~/.claude/ai-router-mode
echo "mixed" > ~/.claude/ai-router-mode
echo "interactive" > ~/.claude/ai-router-mode
```

**Propagazione**: richiede ~2 secondi (nessuna cache, aiohttp mantiene
le connessioni esistenti).

**Script helper**: se disponibile, usa `ai-mode`:

```bash
ai-mode minimax
ai-mode anthropic
ai-mode mixed
ai-mode interactive
```

---

## Comandi in-Chat

Il proxy intercetta comandi nei messaggi chat. Sintassi:

```
!router <comando>
```

Oppure frasi naturali come "usa solo claude", "passa a minimax".

| Comando | Effetto |
|---|---|
| `!router anthropic` | Forza modalità anthropic per questa chat |
| `!router minimax` | Forza modalità minimax per questa chat |
| `!router mixed` | Forza modalità mixed per questa chat |
| `!router interactive` | Forza modalità interactive per questa chat |
| `!router status` | Restituisce modalità corrente e stato backend |
| `!router reset` | Ripristina modalità da file |
| `!router help` | Mostra help inline |

Il comando cambia la modalità solo per quella conversazione (identificata
da fingerprint della chat).

---

## Health Check

Ogni porta del proxy espone un endpoint pubblico (no auth):

```bash
curl http://127.0.0.1:8787/__router_health
```

Risposta esempio:

```json
{
  "service": "ai-router-proxy",
  "mode": "mixed",
  "port_role": "dynamic",
  "backends": {
    "anthropic": "up",
    "minimax": "up"
  }
}
```

Le porte headroom (8790, 8791) espongono:

- `GET /health`
- `GET /readyz`
- `GET /livez`
- `GET /stats`
- `GET /metrics` (formato Prometheus)

---

## Cose da NON Fare

- **Non killare** i servizi senza piano di ripristino immediato
- **Non modificare** manualmente i file unit systemd se non sai cosa
  fai (sono protetti da hook)
- **Non puntare** direttamente a `:8790` o `:8791` dalle app: passa
  sempre per `:8787` (o `8771-8774` per modalità fissa)
- **Non cambiare** modalità in produzione senza prima testare in
  `mixed` (il fallback garantisce continuità)
- **Non ignorare** allarmi del watchdog: indicano che qualcosa non
  funziona come previsto

---

## Hardening e Resilienza

### Triple Difesa

1. **systemd**: i servizi sono `enabled` con `Restart=always` e
   `RestartSec=2`

2. **Cron watchdog**: `ai-stack-guard.sh` controlla ogni 60 secondi
   che le 5 porte siano in ascolto. Se una cade e systemd non la
   riavvia entro 4 secondi, la rilancia via nohup come safety net

3. **Hook PreToolUse**: blocca comandi pericolosi su tutti i servizi
   (`kill`, `systemctl stop`, `systemctl disable`). Regola:
   `AI-ROUTER-POLICY`

### Servizi systemd

I tre servizi sono installati in `~/.config/systemd/user/`:

- `headroom-proxy.service` (porta 8791)
- `headroom-minimax.service` (porta 8790)
- `ai-router-proxy.service` (porte 8787, 8771-8774)

Per riavviare manualmente:

```bash
systemctl --user restart headroom-proxy.service
systemctl --user restart headroom-minimax.service
systemctl --user restart ai-router-proxy.service
```

---

## Esempi d'Uso

### 1. Claude Code di Base

Lo script di setup imposta:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
```

Claude Code userà automaticamente la modalità corrente del file
`~/.claude/ai-router-mode`.

### 2. Sessioni Parallele con Backend Diversi

```bash
# Terminale 1: VSCode, solo Claude
export ANTHROPIC_BASE_URL=http://127.0.0.1:8771

# Terminale 2: shell veloce, solo MiniMax
export ANTHROPIC_BASE_URL=http://127.0.0.1:8772
```

Nessuna interferenza: le due sessioni usano backend indipendenti.

### 3. Failover Automatico

```bash
echo "mixed" > ~/.claude/ai-router-mode
```

Se Claude è giù o la subscription è scaduta, il router passa
automaticamente a MiniMax. Latenza: un retry, circa 1-2 secondi
in più. Qualità: inferiore (modello M3 vs Claude).

### 4. Risparmio Intelligente in Sviluppo

```bash
echo "interactive" > ~/.claude/ai-router-mode
```

Durante il giorno:
- Refactor meccanici, riassunti, traduzioni -> MiniMax (gratis)
- Analisi sicurezza, refactor architetturale, audit -> Claude (verificato)

---

## Troubleshooting Rapido

| Sintomo | Causa Probabile | Fix |
|---|---|---|
| Tutte le risposte 401 | Chiave Anthropic scaduta/assente | Usa `mixed` mode o aggiorna secrets |
| Latenza alta su :8772 | headroom#2 in cooldown | `systemctl --user restart headroom-minimax.service` |
| Modalità non cambia | Cache connessioni (~2s) | Aspetta 2 secondi, riprova |
| `/readyz` rumoroso in log | Baco risolto il 2026-06-23 | Aggiorna ai-router-proxy |
| Mixed non fa fallback | Status non in FALLBACK_STATUSES | Verifica codice (status 400/404 esclusi) |
| Proxy non risponde | Servizio crashato | `systemctl --user restart ai-router-proxy.service` |
| Headroom connection refused | Headroom non avviato | `systemctl --user start headroom-proxy.service` |

### Debug Avanzato

```bash
# Verifica stato servizi
systemctl --user status ai-router-proxy.service
systemctl --user status headroom-proxy.service
systemctl --user status headroom-minimax.service

# Verifica porte in ascolto
ss -tlnp | grep -E '878[0-9]|877[1-4]'

# Log recenti
journalctl --user -u ai-router-proxy.service -n 50
journalctl --user -u headroom-proxy.service -n 50

# Test diretto health endpoint
curl -v http://127.0.0.1:8787/__router_health
```

---

## Requisiti di Sistema

- Linux con systemd (user services)
- Python 3.11+ con aiohttp
- Headroom CLI installato (compressione context)
- 3 file unit systemd in `~/.config/systemd/user/`
- Script watchdog in PATH o in `~/bin/`

---

## Variabili d'Ambiente

### Proxy Router

| Variabile | Default | Descrizione |
|---|---|---|
| `AIROUTER_PORT` | 8787 | Porta principale |
| `AIROUTER_ANTHROPIC_UPSTREAM` | http://127.0.0.1:8791 | Backend Claude |
| `AIROUTER_MINIMAX_UPSTREAM` | http://127.0.0.1:8790 | Backend MiniMax |
| `AIROUTER_MINIMAX_MODEL` | MiniMax-M3 | Modello MiniMax |
| `AIROUTER_VERIFY_MODEL` | claude-opus-4-8 | Modello verifica interactive |
| `AIROUTER_MIXED_PRIMARY` | anthropic | Backend primario in mixed |

### Headroom

| Variabile | Servizio | Descrizione |
|---|---|---|
| `ANTHROPIC_API_KEY` | headroom-proxy (:8791) | Chiave per api.anthropic.com |
| `MINIMAX_API_KEY` | headroom-minimax (:8790) | Chiave per api.minimax.io |

### Client

| Variabile | Valore Tipico |
|---|---|
| `ANTHROPIC_BASE_URL` | http://127.0.0.1:8787 |

---

## File Rilevanti

- `src/ai-router-proxy.py` - Proxy principale
- `src/headroom-proxy/` - Servizio compressione Anthropic
- `src/headroom-minimax/` - Servizio compressione MiniMax
- `docs/MANUAL.en.md` - Questo documento
- `scripts/ai-mode` - Script helper cambio modalità
- `scripts/ai-stack-guard.sh` - Watchdog

---

## Supporto

Per problemi non risolti da questa guida, consulta i log dei servizi
con `journalctl --user` o apri una issue con:

- Output di `ss -tlnp | grep -E '878[0-9]|877[1-4]'`
- Output di `curl http://127.0.0.1:8787/__router_health`
- Ultime 50 righe di journalctl per il servizio coinvolto
