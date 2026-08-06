# PIANO — Repo autonomo, portabile e pubblicabile (2026-08-06)

Obiettivo: chi clona `github.com/eroslifestyle/ai-router-switch` ottiene un router
funzionante su Linux, macOS o Windows senza dipendere da niente che viva solo su
questa macchina. Il repo è già pubblico e allineato (`main == origin/main`, `cfc54b9`),
quindi il lavoro non è pubblicare: è rendere clonabile-e-funzionante ciò che è già lì.

## Decisioni prese (2026-08-06)

| Tema | Scelta |
|---|---|
| Cloud locale | docker-compose + config template, **senza** i modelli GGUF |
| Multi-OS | Linux first-class; macOS e Windows supportati davvero (blocchi rimossi) |
| Auto-update | `ai-mode update`: pull → test → restart → rollback su health fail. Timer off |
| Autofix L2 | Incluso, **off di default**, **PR-only** (nessun merge automatico) |
| Pulizia repo | `git rm --cached`, **storia intatta**, nessun force-push |
| Note interne | Via `.claude/TODO.md`, `.claude/WIKI.md`, `docs/sessions/`, `docs/articles/`. `sviluppo/` resta |
| Installazione viva | Refactor + migrazione verificata su questa macchina |

## Fase 0 — Rete di sicurezza (prima di toccare qualsiasi cosa)

1. Tag `pre-portabilita-20260806` sul commit corrente.
2. Copia di `~/.config/systemd/user/ai-router.service` e del drop-in `ai-router.service.d/`.
3. Inventario dei symlink attivi in `~/.claude/scripts/` (elenco su file, serve alla migrazione).
4. Baseline misurata e trascritta: suite (303 test collezionati), nove porte a 200,
   `systemctl --user is-active ai-router` = `active`, `Restart=always` confermato.

Precondizione di restart già verificata oggi: unit `active`, `Restart=always`, `RestartSec=2`.

## Fase 1 — Far rientrare nel repo i componenti mancanti

| Cosa | Da | A |
|---|---|---|
| `ai_router_resilience.py` (21 KB) | `~/.claude/scripts/` | `src/` |
| `ai-router-proxy-wrapper.sh` | `~/.claude/scripts/` | generato da `install.py` (Fase 3) |
| `ai-router-watchdog.sh`, `ai-router-freeze-watchdog.sh`, `ensure-ai-router.sh`, `ai-router-relogin-helper.sh` | `~/.claude/scripts/` | `scripts/` |

Contestualmente:

- Tolgo il `sys.path.insert(~/.claude/scripts)` da `src/ai-router-proxy.py:114`:
  l'import diventa normale. Il `try/except` resta ma il fallimento viene **loggato come
  errore**, non come nota su stderr — oggi un modulo assente degrada il router in silenzio.
- **`minimax_rate_limiter`**: l'import a `src/context_manager.py:168` è dentro
  `except: pass` e il modulo non esiste da nessuna parte, quindi il rate limiter MiniMax
  non ha mai funzionato. **Scelta: rimuovo l'import morto e lo documento**, non lo
  implemento. Motivo: implementarlo cambierebbe il comportamento in produzione (pacing
  sulle richieste MiniMax) e non è portabilità — è una feature nuova, da valutare a parte.
  Le costanti `MINIMAX_RATE_LIMITS` restano dove sono, già usate altrove.
- Rimuovo i due symlink rotti in `~/.claude/scripts/`: `hhem_gate.py`, `summarizer.py`
  (target `src/` cancellati da tempo).

## Fase 2 — Portabilità del codice

**`src/paths.py` (nuovo).** Un solo punto che risolve la config dir, in quest'ordine:
`$AIROUTER_HOME` → `~/.claude` se esiste (retrocompatibilità con questa macchina) →
`~/.config/ai-router-switch` su Linux/macOS, `%APPDATA%\ai-router-switch` su Windows.
Passano da qui tutti i moduli che oggi scrivono `Path.home()/".claude"` a mano:
`router_constants`, `router_auth`, `local_backend`, `context_alert`, `ornith_warmer`,
`self_healing/{fixer,watcher,auto_fixer,m3_source}`.

**`src/secrets_provider.py` (nuovo).** Sostituisce le tre chiamate
`subprocess(["bash", secrets.sh, "get", …])` di `src/router_auth.py:78`,
`src/glm_backend.py:142` e `src/qwen_backend.py:157`. Catena: env var → file `.env`
nella config dir → `secrets.sh` se presente (retrocompat) → keyring OS se installato.
È questo che sblocca Windows: oggi senza `bash` non si risolve nessuna chiave.

**Blocchi da rimuovere:**

- `src/ai-router-proxy.py:1014` — `loop.add_signal_handler` solleva `NotImplementedError`
  su Windows: fallback a `signal.signal`.
- Auth Anthropic: oggi solo `~/.claude/.credentials.json`. Aggiungo env var come primo
  canale e Keychain su macOS (`security find-generic-password`), dove Claude Code non
  scrive quel file.
- `TRIM_STATE_DIR`: da `/tmp/ai-router-trim` a `tempfile.gettempdir()`.

**Dipendenze dichiarate.** `pyproject.toml` + `requirements.txt`: `aiohttp`, `brotli`,
`multidict`, `Pillow`. Extra `gui` (PySide6, per `router-mode/card.py`) e `dev`
(pytest, ruff). Oggi non esiste alcun file di dipendenze.

## Fase 3 — Installazione e servizi

- **`install.py`** cross-platform: crea la config dir, scrive `.env` dal template,
  registra il servizio (systemd user su Linux, launchd plist su macOS, istruzioni NSSM
  o Task Scheduler su Windows), stampa la riga `ANTHROPIC_BASE_URL` da incollare.
- **`systemd/ai-router.service.in`**: template con `@PROJECT_ROOT@` / `@PYTHON@`.
  La unit reale e il wrapper li genera `install.py` — così il path con lo spazio
  (`1 Programmazione`) resta gestito senza essere cablato nel repo.
- **`config/settings.anthropic.example.json`**: contiene **solo** `env.ANTHROPIC_BASE_URL`
  e i timeout. Non replico `dangerouslySkipPermissions`, `autoApprove`,
  `trustAllWorkspaces` del settings reale: sono scelte personali, pericolose come default
  pubblico.
- **`ai-mode` riscritto in Python** (`src/cli.py`) con shim bash per retrocompatibilità:
  oggi accetta ancora `interactive` (rimossa il 2026-07-26) e **non** accetta `mix-al`
  né `local`, che invece esistono in `src/router_constants.py:104`.
  Aggiunge il sottocomando `update` (Fase 5).

## Fase 4 — Cloud locale (`local-stack/`)

- `docker-compose.yml`: LiteLLM + Postgres, la forma in cui gira davvero qui.
- `litellm.config.example.yaml`: estratto dal container in esecuzione e **anonimizzato**;
  espone `code-max` verso llama.cpp.
- `llama-server.service.in`: la unit `llama-qcnext` parametrizzata (path del GGUF, porta,
  `-ngl`, dimensione contesto), senza le assunzioni hardware di questa macchina.
- `local-llm.env.example` + `README.md`: quale GGUF scaricare, da dove, e quanto pesa.

I pesi restano fuori dal repo. Le modalità `local` e `mix-al` restano opzionali: senza
lo stack alzato, il router deve dirlo con un errore comprensibile, non con un timeout.

## Fase 5 — Auto-aggiornamento

`src/updater.py` + `ai-mode update`:

1. Rifiuta se il working tree è sporco (nessuno stash automatico).
2. `git fetch` → se non c'è nulla di nuovo, esce.
3. `git pull --ff-only` → `pytest -q`.
4. Restart del servizio con la procedura sicura (is-active → restart → sleep → is-active).
5. Health check sulle porte in ascolto.
6. Se test o health falliscono: `git reset --hard <commit_precedente>` + restart + log.

Timer `ai-router-update.timer` fornito ma **non installato** di default.
Log in `logs/update.log`.

## Fase 6 — Autofix L2 in modalità PR-only

Oggi `src/self_healing/fixer.py:189` lancia `claude -p --dangerously-skip-permissions`
e poi fa `git merge --no-ff` su main. Il servizio è **`failed` adesso** (corsa delle
15:21 del 2026-08-06, exit 1 dopo 20 minuti) e ha lasciato tre branch residui:
`fix/relay_error_502-d41d8cd9`, `fix/relay_error_404-d41d8cd9`,
`fix/minimax_context_exceed-d41d8cd9`.

- Flag `--merge`, **default off**: senza, si ferma al branch e apre una PR con `gh` se c'è.
- `AIROUTER_SELF_FIX_ENABLED` default `0`; il timer non viene installato da `install.py`.
- Diagnosi dell'exit 1 e chiusura dei tre branch residui, dopo aver verificato che non
  contengano lavoro utile.
- README esplicito su cosa comporta accenderlo: un LLM che scrive nel tuo repo.

## Fase 7 — Pulizia e documentazione

**Fuori dal tracking** (`git rm --cached`, storia intatta):

- `sys` — 5,9 MB di PostScript generato da ImageMagick il 19/07, il file più grande del repo.
- `graphify-out` — symlink tracciato verso una directory gitignored: rotto per chiunque cloni.
- `.claude/TODO.md`, `.claude/WIKI.md`, `docs/sessions/`, `docs/articles/`.

`.claude/plans/` resta tracciata (contiene i piani di design, non lo stato operativo).
`sviluppo/` resta interamente, test compresi.

**Documentazione allineata al codice.** Le derive misurate oggi:

- README e `CLAUDE.md` dicono 7 modalità, il codice ne ha **9** (`mix-al`, `local`).
- `CLAUDE.md` afferma che «la 8774 non è in ascolto»: ora è `mix-al`. La 8779 (`local`)
  non è documentata da nessuna parte.
- README dice `qwen3.8-max`, la config globale dice `qwen3.7-max` — verifico quale è vero
  prima di scrivere.
- `router-mode/README.md` dice "7 modes" e indica `~/.claude/router-mode/` come location.

Più: `.env.example` con tutte le `AIROUTER_*` documentate, e una sezione «cosa NON è
incluso» (modelli, chiavi API, account).

## Fase 8 — Migrazione della macchina viva

Prima a freddo, poi in produzione:

1. Clone pulito in `/tmp`, `python install.py`, avvio su porte alternative via
   `AIROUTER_PORT_MODE_JSON` — **senza toccare :8787**.
2. Solo se il freddo passa: migrazione dell'installazione attiva (unit rigenerata,
   symlink sostituiti), con la procedura di restart del `CLAUDE.md` di progetto.
3. Verifica finale: nove porte a 200, suite verde, `ai-mode status`, un giro reale
   in una modalità.

## Fase 9 — Pubblicazione

Commit atomici per fase (Conventional), push su `main`, tag `v1.0.0`, description e
topics del repo aggiornati.

## Do NOT

- Niente force-push né riscrittura della storia: deciso esplicitamente.
- Non replicare nel template pubblico i flag pericolosi del `settings.json` personale.
- Non cancellare `sviluppo/`.
- Non scaricare né committare modelli.
- Non riavviare il router fuori dalla procedura del `CLAUDE.md` di progetto.

## Rischi noti

Il refactor dei path (Fase 2) tocca il modulo più caldo di un servizio in produzione.
Mitigazione: la Fase 8 prova tutto a freddo su istanza isolata prima di migrare, e il
tag della Fase 0 permette il rientro immediato.
