# Loop-breaker su GLM ACT e shrink dedicato all'esecutore — 2026-09-19

## Sintomo

L'utente riceve:

```
API Error: 400 Loop rilevato: 129 turni consecutivi identici in modalita' 'glm'.
Il router ha interrotto il giro invece di inoltrarne un altro.
```

## Causa radice

- Il loop-breaker (`src/loop_breaker.py`) è generico a tutte le 14 modalità: calcola SHA1 della firma dell'ultimo messaggio assistant (testo + tool_use) e interrompe se la firma è identica per ≥4 turni (`AIROUTER_LOOP_BREAKER_N`, default 4) **e** il contesto ha superato l'80% (`LOOP_BREAKER_MIN_CTX_PCT`) della finestra dichiarata per il modello in `MODEL_CONTEXT_MAP` (`src/model_context_map.py`).
- In modalità `glm`, il THINK è `glm-5.3` (finestra 1.000.000 token, `model_context_map.py:43`) ma l'ACT/esecutore — quello che fa il grosso dei turni (coding, tool-call, lavoro dei subagent) — è `glm-4.7`/`glm-5-turbo`, dichiarati da z.ai a **200.000 token** (`model_context_map.py:44-45`, verificato contro la doc ufficiale il 2026-07-27). Ogni turno reinvia l'intero contesto della chat indipendentemente da quale modello lo gestisce: è l'esecutore a 200K a saturare per primo in conversazioni lunghe, non il THINK da 1M.
- Fattore aggravante documentato in `BUG-CATALOG.md`: GLM a volte esaurisce tutto il budget di `max_tokens` nel blocco `thinking` nativo e risponde `stop_reason:"max_tokens"` senza testo/tool_use azionabile. Claude Code, non ricevendo nulla da eseguire, ripropone lo stesso turno → stessa firma SHA1 → il contatore sale (129 nel caso osservato) finché il contesto supera l'80% e il breaker interrompe.
- L'infrastruttura di compressione (`context_shrink.py`, `context_rewrite.py`, `trim_smart.py`) esiste già ed è agganciata in modo preventivo in `glm_backend.py` tramite `glm_shrink_target_for()` (righe ~415-466 prima della modifica), chiamata in `forward_glm` (~600-607). La funzione derivava correttamente (dal 2026-08-16) il target dalla finestra reale del modello, ma aveva un solo override globale (`AIROUTER_GLM_CONTEXT_SHRINK_TARGET`) che comprimeva allo stesso modo glm-4.7 e glm-5.3: impossibile stringere solo l'esecutore piccolo senza rovinare il THINK grande.

## Opzioni valutate e decisione

1. Tarare le soglie esistenti (knob dedicato).
2. Shrink proattivo permanente.
3. Escalation automatica ACT→glm-5.3 sopra soglia.
4. Solo consiglio operativo.

L'utente ha scelto l'opzione 1. La 3 è stata scartata perché contraddice la regola esplicita del progetto (CLAUDE.md): «il modello GLM lo decide il RUOLO, non la complessità: glm-5.3 per il THINK, glm-4.7 per l'ACT».

## Fix — commit `75c77d4` (pushato su `origin/main`)

In `src/glm_backend.py`, 12 righe dopo la costante `GLM_SHRINK_TARGET_BYTES` esistente:

```python
GLM_ACT_SHRINK_TARGET_BYTES = os.environ.get("AIROUTER_GLM_ACT_CONTEXT_SHRINK_TARGET")
_GLM_ACT_MODELS = {"glm-4.7", "glm-5-turbo"}
```

Dentro `glm_shrink_target_for(model)`, subito dopo il check dell'override globale esistente:

```python
if GLM_ACT_SHRINK_TARGET_BYTES and model in _GLM_ACT_MODELS:
    return int(GLM_ACT_SHRINK_TARGET_BYTES)
```

Retrocompatibile: senza la env, comportamento identico a prima. Verificato che `model` ricevuto da `forward_glm` è già il nome canonico esatto (`upstream_model`, risolto da `canonical_glm_model`/`GLM_MODEL_FOR_TIER`) — letterali `"glm-4.7"`/`"glm-5-turbo"`, nessun adattamento. Sintassi verificata con `ast.parse`.

## Scoperta operativa

Il file `~/.secrets/glm.env`, referenziato come `EnvironmentFile=%h/.secrets/glm.env` nell'unit base di systemd, **non esiste e non viene mai caricato**: il drop-in `10-secret-vault.conf` (`~/.config/systemd/user/ai-router.service.d/`) azzera `EnvironmentFile=` ed `ExecStart=` e li sostituisce con `ExecStart=/home/mrxxx/.local/bin/secret run minimax glm -- /home/mrxxx/.claude/scripts/ai-router-proxy-wrapper.sh`. Le credenziali GLM/MiniMax arrivano dal vault TPM via `secret run`; il servizio era `active` anche prima che quel file esistesse.

**Conseguenza per futuri tuning**: ogni nuova env var di configurazione (non credenziale) va aggiunta come drop-in systemd in `~/.config/systemd/user/ai-router.service.d/*.conf` con `Environment=CHIAVE=valore` — stessa convenzione di `agent-loop-flags.conf` e `tools-telemetry.conf` — MAI in un file `~/.secrets/*.env`, morto/ignorato per questo servizio.

## Deploy e verifica

Creato `~/.config/systemd/user/ai-router.service.d/glm-act-shrink.conf`:

```ini
[Service]
Environment=AIROUTER_GLM_ACT_CONTEXT_SHRINK_TARGET=400000
```

400.000 byte ≈ 100k token, ampio margine sotto il vero limite di glm-4.7 (200k token / ~800KB).

Eseguito `systemctl --user daemon-reload && systemctl --user restart ai-router`. Verificato:

- `systemctl --user is-active ai-router` → `active`
- `grep AIROUTER_GLM_ACT_CONTEXT_SHRINK_TARGET /proc/<PID>/environ` → presente, valore `400000`

## Follow-up aperto

- Nessun test end-to-end dal vivo con una chat GLM lunga che confermi che il loop-breaker non scatta più.
- Se 400000 risulta troppo aggressivo (compressione troppo frequente) o non abbastanza (loop ancora presente), si ritara il valore nel drop-in e si riavvia: nessuna modifica di codice necessaria.
