# Windows resilience — current gap and a ready-to-paste implementation prompt

## Current state (verified, not assumed)

The process-supervision layer — auto-restart on crash, health watchdog, freeze
detection, periodic probes — exists only for Linux today, built entirely on
systemd:

- `systemd/ai-router.service.in` — the unit itself: `Restart=always`,
  `RestartSec=2`, `TimeoutStopSec=20` with `KillMode=mixed` (SIGTERM then
  SIGKILL, tuned so in-flight SSE streams aren't truncated), `ExecStartPre`
  frees the fixed ports before start, `OOMScoreAdjust=-900` /
  `OOMPolicy=continue`.
- `scripts/ai-router-watchdog.sh` — polls `/__router_health` every ~30s
  (via `ai-router-watchdog.timer`), forces a restart after 2 consecutive
  failures, skips the restart if `/__resilience` reports `DEGRADED` (missing
  OAuth is not something a restart fixes).
- `scripts/ai-router-freeze-watchdog.sh` — detects true freezes (not just
  down) via a heartbeat file freshness check plus the health endpoint, with a
  30s boot-grace window, a 5-restarts-per-300s rate limit followed by a 300s
  cooldown, and a crash dump written before the kill. On Linux it also reads
  `/proc/<pid>/wchan` for deadlock signals — that check is Linux-specific and
  has no direct Windows equivalent (see the prompt below).
- `sviluppo/systemd/ai-router-probe.service` / `ai-router-self-fix.service` —
  periodic probe (~30 min) and self-fix (~6h), both opt-in via env var,
  off by default.

`src/ai_router_resilience.py` (the `DEGRADED`/OAuth state machine behind
`/__resilience`) is plain Python with no systemd dependency — it already runs
correctly on any OS. **Only the process-supervision scripts above are
Linux-only.**

On Windows, `install.py`'s `setup_windows_service()` (around line 263) only
generates a `start-router.bat` and prints manual instructions for
`schtasks /create /sc onlogon` (start-at-logon, no crash-restart) or a
suggestion to install NSSM by hand. **There is currently no automatic
restart-on-crash, no watchdog, and no freeze detection on Windows.** A crashed
router on Windows stays down until someone notices.

## Goal

Bring Windows to behavioral parity with the Linux resilience stack, using
Windows-native primitives, and wire it into `install.py` so it's automatic
for anyone who runs the installer — not a manual step.

## Implementation prompt (ready to paste into a fresh coding session)

```
Ruolo: stai lavorando su un fork Windows del repo `ai-router-switch`
(clonato da GitHub). Il tuo compito è portare su Windows nativo il sistema
di resilienza/restart/watchdog che oggi esiste solo per Linux (systemd),
mantenendo lo stesso comportamento osservabile, non l'implementazione.

LEGGI PRIMA, come riferimento vincolante (non indovinare comportamento
non documentato lì dentro):
- systemd/ai-router.service.in — unit systemd sorgente
- scripts/ai-router-watchdog.sh — health probe ogni ~30s
- scripts/ai-router-freeze-watchdog.sh — freeze/deadlock detect
- src/ai_router_resilience.py — modulo DEGRADED/OAuth, GIÀ cross-platform
  (puro Python, non toccarlo se non necessario)
- sviluppo/systemd/ai-router-probe.service + ai-router-self-fix.service
- install.py, funzione setup_windows_service() (riga ~263) — stato attuale:
  genera solo start-router.bat + istruzioni manuali, nessuna automazione
- docs/DEPLOYMENT-RULES.md

MAPPATURA da replicare 1:1 (comportamento, non sintassi):

| Meccanismo Linux | Comportamento da preservare | Equivalente Windows da valutare |
|---|---|---|
| `Restart=always`, `RestartSec=2` | il processo riparte da solo su crash | Windows Service con Recovery Actions (`sc.exe failure` o NSSM `AppRestartDelay`) |
| `ExecStartPre` libera le porte | niente bind-fail su restart sporco | script pre-start che libera le porte occupate (`Get-NetTCPConnection` + `Stop-Process`) |
| `KillMode=mixed`, SIGTERM→20s→SIGKILL | shutdown pulito, stream SSE non troncati | serve un modo per notificare shutdown "gentile" al processo Python (named pipe / file-signal / `CTRL_BREAK_EVENT` su un processo console) prima di un kill duro |
| `OOMScoreAdjust=-900` | non è il primo killato sotto pressione memoria | Windows non ha OOM killer identico — valuta Job Object con limiti di memoria, o documenta la differenza come N/A invece di fingere un equivalente |
| watchdog ogni 30s: 2 fail consecutivi su `/__router_health` → force restart | stesso identico endpoint, stessa soglia | Scheduled Task con trigger a ripetizione ogni 1 minuto (minimo nativo) che chiama lo stesso `/__router_health` |
| freeze-watchdog: heartbeat file freshness, rate-limit 5 restart/300s poi cooldown 300s, crash dump prima del kill, boot-grace 30s, skip se `/__resilience` = DEGRADED | stessa logica di rate-limit/cooldown/boot-grace/skip-in-DEGRADED | il controllo `/proc/<pid>/wchan` è Linux-specifico e va SOSTITUITO, non emulato — usa solo heartbeat file + health endpoint; per il crash dump valuta Sysinternals `procdump.exe` (se disponibile) o `Get-Process | Format-List *` come fallback nativo |
| probe ogni 30 min, self-fix ogni 6h (entrambi opt-in via env var) | stesso ciclo, stesso default OFF | Scheduled Task con trigger a intervallo |
| `/__resilience` DEGRADED (OAuth mancante) → nessun restart forzato | invariato: è già in `ai_router_resilience.py`, puro Python, funziona su Windows senza modifiche | nessuna azione, solo verificarlo |

VINCOLI:
- Preferisci primitive Windows native (Task Scheduler, `sc.exe`, Windows
  Service via `pywin32`) a dipendenze esterne. Se proponi NSSM, gestisci
  ESPLICITAMENTE il caso "NSSM non installato" con fallback nativo — non
  dare per scontato che l'utente lo installi.
- Zero segreti, zero path assoluti personali, zero riferimenti a
  infrastruttura che non sia quella pubblica del repo (nessun vault TPM2,
  nessun hook di gerarchia personale).
- Idempotente: rilanciare l'installer non deve duplicare Scheduled Task o
  servizi.
- Deve esistere un disinstallatore pulito (rimuove servizio + task +
  file di stato).
- Non toccare la logica applicativa in `src/ai-router-proxy.py` se non
  strettamente necessario per esporre un hook di shutdown pulito.

DELIVERABLE richiesti, espliciti:
1. Script/modulo che registra il router come Windows Service con
   restart-on-crash (equivalente di `Restart=always`).
2. Script di pre-start che libera le porte (equivalente `ExecStartPre`).
3. Scheduled Task "watchdog" (health probe) e "freeze-watchdog"
   (heartbeat+health, rate-limit, cooldown, boot-grace, skip su DEGRADED).
4. Scheduled Task "probe" e "self-fix", entrambi OFF di default
   (stessa scelta del repo Linux — è una decisione utente, non
   un'omissione).
5. Aggiornamento di `install.py` → `setup_windows_service()` per
   automatizzare i punti 1-4 invece di stampare istruzioni manuali.
6. Un disinstallatore.
7. Aggiornamento della sezione Windows in `docs/DEPLOYMENT-RULES.md`
   che descriva il nuovo comportamento (sostituendo la frase attuale
   "a startup-file plus instructions on Windows").

CRITERI DI ACCETTAZIONE (verifica reale, non dichiarata):
- Kill manuale del processo → il servizio riparte da solo entro pochi
  secondi (osservato, non presunto).
- Simulazione di freeze (es. processo che smette di aggiornare
  l'heartbeat file ma resta vivo) → il freeze-watchdog lo rileva e
  forza il restart, rispettando rate-limit/cooldown.
- Stato DEGRADED (OAuth mancante) → NESSUN restart forzato dai watchdog,
  verificato leggendo `/__resilience`.
- Riavvio della macchina → il servizio riparte da solo.
- Rilancio dell'installer → nessun duplicato in Task Scheduler / servizi.

Applica evidence-gate su ogni claim: nessun "fatto/funziona" senza
output letterale (`sc query`, `schtasks /query`, log del watchdog,
risposta HTTP reale dell'health endpoint prima/dopo il test).
```

## Status

Not implemented yet. This document is the spec + prompt for whoever picks it
up (human or AI session) — implementing it is a separate piece of work with
real design decisions (NSSM dependency vs. pure `pywin32`, how to signal
graceful shutdown without POSIX signals) that should be made explicitly
rather than assumed.
