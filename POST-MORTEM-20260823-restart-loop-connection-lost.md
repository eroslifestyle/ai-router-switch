# POST-MORTEM 2026-08-23 — Restart loop & "Connection lost mid-response"

**Sistema:** leobox (VPS) — AI Router Proxy `ai-router.service`, porte 8771-8788 su `127.0.0.1`
**Sintomo riportato dall'utente:** "il router entra in loop continuo e perde le API key" + errori frequenti `API Error: Connection lost mid-response. The response above may be incomplete.` in qualsiasi modalità.
**Esito:** ✅ risolto — causa radice trovata, verificata empiricamente, eliminata. 180s di stabilità con PID invariato e streaming SSE completo end-to-end.

---

## 1. Sintomi osservati

| Sintomo | Evidenza |
|---|---|
| Restart del proxy ogni ~10-20s | PID cambiato in continuazione (322490 → 333623 → 334209 → 335451 …), journal: `restart counter is at 18` |
| Troncamento risposte | `Connection lost mid-response` lato client (Claude Code / Pi) |
| systemd in outage totale | `Failed with result 'start-limit-hit'` → servizio `failed`, porte orfane |
| Risposte "vuote" in telemetria | `debug-errors.jsonl`: decine di `empty_response_anthropic` / `empty_response_mix-am-2` con `bytes=417-430, real_out_tokens=0` |
| 429 MiniMax | `minimax_429_rpm` con backoff 5/10/20/40s (effetto, non causa) |
| Crash dump di loop-stall | `loop-stall-*.txt` con MainThread fermo in `router_utils.py:305 in log` |

---

## 2. Diagnosi — prove effettuate (in ordine)

### Prova 1 — Health & porte
`ss -tlnp` + `curl /__router_health`: tutte le porte bindate correttamente, health 200, OAuth `max` valido ~7h. **Le API key NON erano perse** (era l'impressione causata dai fallimenti). Proxy attivo ma con PID in continuo ricambio.

### Prova 2 — Log telemetria
`debug-errors.jsonl`: pattern `empty_response_*` con body Brotli grezzo (~420 byte). Il buffer di telemetria non decomprimeva → **falso "vuoto"**. Scoperto bug API Brotli (vedi §3.2).

### Prova 3 — Stack dump del loop congelato
`SIGUSR1` → `faulthandler` dump: **MainThread bloccato dentro `router_utils.py:305 log()`** (open+write sincroni su file da 9MB, nell'event loop). Causa dei loop-stall >10s. Fix: log asincrono (§3.1).

### Prova 4 — Watchdog come killer
Log freeze-watchdog: `RATE LIMIT RAGGIUNTO (5/5 in 300s)` → il watchdog SIGKILLa il proxy durante gli stream → **troncamento diretto delle risposte**. Corretto il protocollo di kill (§3.3). Ma i restart continuavano → il watchdog non era il killer principale.

### Prova 5 — Verifica syscall della catena exec
`secret run` (vault TPM) usa `os.execvpe` → il MainPID di systemd È il python del proxy. Teoria fork/sostituzione processo scartata.

### Prova 6 — Trappola di morte (strumento diagnostico creato ex-novo)
Hook in `main()` che logga sincronamente su `/tmp/ai-router-death.log` ogni possibile uscita (excepthook, atexit, eccezioni attorno ad `asyncio.run`).
**Risultato decisivo:** `asyncio.run completato NORMALE` + log proxy `shutdown signal received` → il processo riceve **veri SIGTERM regolari ogni ~20s**. Nessun crash Python.

### Prova 7 — strace sui segnali
Bloccato da ptrace_scope (`Operazione non permessa`). Alternativa: journal systemd.

### Prova 8 — Journal targettato
`journalctl --user --since "08:42:50"` → riga `Stopping ai-router.service` = **qualcuno invoca systemctl stop/restart**. E soprattutto, prima riga del contesto:
```
ai-router-proxy.service: Scheduled restart job, restart counter is at 169.
```

## 🎯 CAUSA RADICE

Esisteva un **servizio systemd DOPPIONE** mai disattivato:

**File:** `~/.config/systemd/user/ai-router-proxy.service` (vecchia unit residua)
```ini
[Service]
ExecStart=/home/mrxxx/.claude/scripts/ai-router-proxy-wrapper.sh
Restart=always
RestartSec=10
ExecStartPre=-/bin/bash -c 'pkill -f "ai-router-proxy.py" 2>/dev/null || true'
```

**Il ciclo del disastro (ogni ~15-20s):**
1. `ai-router-proxy.service` (doppione) si attiva → `ExecStartPre` fa `pkill -f ai-router-proxy.py` → **uccide il proxy buono a metà stream** → `Connection lost mid-response`
2. `ai-router.service` (buono, `RestartSec=2`) riparte prima e riconquista le porte
3. Il doppione (`RestartSec=10`) ci riprova → torna al punto 1, all'infinito
4. Effetti collaterali: i watchdog vedono il caos e aggiungono SIGKILL propri; systemd va in `start-limit-hit`; le chat pinnate sembrano "perdere le chiavi"

Conferma empirica: dopo disattivazione del doppione → **zero morti, PID stabile 180s, SSE completo**.

---

## 3. Bug trovati e fix applicati

### 3.1 ⚠️ Log sincrono nell'event loop → freeze (FIX STRUTTURALE)
**File:** `src/router_utils.py` (funzione `log()`)
**Bug:** `open()+write` sincroni + `rotate_if_needed` nel MainThread asyncio. Stack dump: loop fermo >10s proprio lì. Ogni turno genera decine di chiamate log (relay, cache, D41…).
**Fix:** log write-behind — coda `queue.Queue(maxsize=10000)` + thread dedicato `ai-router-log-writer` con batching, flush periodico, riapertura file solo su rotazione, fallback stderr, drain garantito via `atexit`. `log()` ora è O(1) in memoria.
**Bonus:** il log file ora si popola correttamente (prima il redirect si perdeva nei restart).

### 3.2 API Brotli errata → falsi "empty_response" (FIX)
**File:** `streaming_relay.py` (telemetria nel `finally` del relay)
**Bug:** `brotli.Decompress().process(_raw)` — **API inesistente** (il modulo espone `Decompressor` e `decompress`).
**Fix:** `brotli.Decompressor().decompress(_raw)` con doppio fallback (brotlicffi, poi raw). Nota: il buffer parziale di 16KB può restare non decodificabile — accettato, è solo telemetria; il pass-through compresso al client è by design (`auto_decompress=False`, commento nel codice: la lib Brotli locale è rotta in aiohttp).

### 3.3 Watchdog che SIGKILLa stream vivi (FIX protocollo di kill)
**File:** `scripts/ai-router-freeze-watchdog.sh`, `~/.claude/scripts/ai-router-watchdog.sh`
**Bug:** `kill -9` immediato → troncava gli SSE a metà (causa diretta di molti Connection lost).
**Fix:** sequenza `SIGTERM → attesa drain (5-10s) → SIGKILL di sicurezza → systemctl restart`.

### 3.4 🎯 SERVIZIO DOPPIONE con pkill (CAUSA RADICE — ELIMINATO)
**File:** `~/.config/systemd/user/ai-router-proxy.service`
**Fix:**
1. `systemctl --user disable --now ai-router-proxy.service`
2. File rinominato in `ai-router-proxy.service.DEPRECATED-20260823` (backup conservato)
3. `systemctl --user mask ai-router-proxy.service` (symlink → `/dev/null`, riattivazione impossibile per errore)
4. `daemon-reload` + `reset-failed` + restart pulito del servizio buono

### 3.5 Trappola di morte (diagnostica permanente, innocua)
**File:** `src/ai-router-proxy.py` (`main()`)
Logga su `/tmp/ai-router-death.log` eccezioni non gestite, exit puliti e segnali. Lasciata attiva come sentinella; rimovibile a piacere.

---

## 4. Verifica finale (post-fix)

| Test | Esito |
|---|---|
| Monitoraggio 180s continuo | ✅ PID invariato (410988), zero restart |
| `/tmp/ai-router-death.log` | ✅ nessuna nuova morte dopo il fix |
| Streaming SSE reale haiku-4-5 | ✅ 13 eventi fino a `message_stop`, nessun troncamento |
| Richiesta non-stream | ✅ risposta completa ("Sì, tutto bene!") |
| OAuth resilience | ✅ `state=OK`, subscription `max`, ~7h residue |
| Porte | ✅ 15 porte su `127.0.0.1` (conformi alla regola loopback-only) |
| Watchdog | ✅ riattivati (freeze + health) con nuovo protocollo TERM→KILL |

---

## 5. Lezioni apprese

1. **Cerca sempre unità duplicate prima di incolpare il codice**: `systemctl --user list-units --all | grep <nome>` — un `ExecStartPre=pkill -f` in una unit gemella è una bomba a orologeria invisibile nei log del servizio vittima.
2. **Il journal delle VITTIME mente per omissione**: la vittima loggava "shutdown complete" senza mai nominare l'assassino; solo il journal globale (`--user`, senza filtro unit) ha rivelato `Stopping` + il counter del doppione.
3. **I/O sincrono nell'event loop è debito tecnico composto**: 9MB di log × decine di write/turno = stall >10s che i watchdog puniscono col kill.
4. **Strumenti diagnostici ad-hoc pagano subito**: la "trappola di morte" (excepthook+atexit su file separato sincrono) ha distinto in 60s crash da segnale esterno.
5. **API mai verificate al buio**: `brotli.Decompress().process()` girava da chissà quanto senza mai sollevare errore visibile (era dentro try/except generico).

## 6. Stato dei file modificati

| File | Modifica | Reversibilità |
|---|---|---|
| `src/router_utils.py` | log asincrono write-behind | rollback: ripristinare `log()` originale |
| `streaming_relay.py` | API brotli corretta | rollback: riga singola |
| `src/ai-router-proxy.py` | trappola di morte in `main()` | rimuovibile liberamente |
| `scripts/ai-router-freeze-watchdog.sh` | TERM→drain→KILL | rollback: blocco singolo |
| `~/.claude/scripts/ai-router-watchdog.sh` | TERM→drain→KILL | rollback: blocco singolo |
| `~/.config/systemd/user/ai-router-proxy.service` | deprecato + mask | `unmask` + rename per ripristinare (NON farlo) |

---
*Diagnosi e fix eseguiti il 2026-08-23 (08:00-08:55) via Pi su leobox.*
