# RENAME — 3 modalità mixed: MixAM / MixAG / MixGM

## Obiettivo
Rinominare le 3 modalità mixed e assicurare che la regola ferrea sia rispettata:
**Orchestrator = MAI esegue. Executor = esegue SEMPRE. Fallback = tier Anthropic completo.**

## Rinomine

| Vecchio nome | Nuovo nome | Regola |
|---|---|---|
| `mixed` | `mix-am` | Anthropic THINK → MiniMax ACT. 2 fail MiniMax → Anthropic rescue (Haiku→Sonnet→Opus) |
| `anthropic-glm` | `mix-ag` | Anthropic THINK → GLM ACT. Fall GLM → Anthropic rescue |
| `glm-minimax` | `mix-gm` | GLM THINK → MiniMax ACT. Fall MiniMax → Anthropic rescue |

## File da modificare
`ai-router-proxy.py`

## 1. VALID_MODES (r 393-394)
```python
VALID_MODES = ("anthropic", "minimax",
               "mix-am", "mix-ag", "mix-gm",
               "glm")
```
Vecchi nomi rimossi: `"mixed"`, `"anthropic-glm"`, `"glm-minimax"`.

## 2. Commento intestazione (r 392)
Aggiornare il commento:
```
Modalità:
  - anthropic   : tutto diretto a api.anthropic.com
  - minimax     : tutto diretto a api.minimaxi.chat
  - mix-am      : Anthropic THINK + MiniMax ACT (2 fail MiniMax → Anthropic rescue)
  - mix-ag      : Anthropic THINK + GLM ACT
  - mix-gm      : GLM THINK + MiniMax ACT
  - glm         : GLM tiered (5.2→4.7→4)
```

## 3. Commenti descrittivi (r 3922-3915, r 3626-3645)
Aggiornare i commenti di sezione in `handle()` per riflettere i nuovi nomi.

## 4. _handle_glm_mode dispatch (r 3627-3645)
```python
if mode == "glm":
    return await _glm.glm_think_act_verify(...)

# mix-ag: Anthropic THINK → GLM ACT → Anthropic VERIFY (task complessi)
if mode == "mix-ag":
    return await _anthropic_glm_think_act_verify(...)

# mix-gm: GLM-5.2 THINK → MiniMax ACT → Anthropic rescue
if mode == "mix-gm":
    return await _glm_minimax_think_act_verify(...)
```

## 5. MixGM — fix fallback Anthropic (r 3476-3535)
La chain `_glm_minimax_only_chain` è **ORFANA** e va eliminata.
La chain corretta (`_glm_minimax_think_act_verify`) non include il fallback Anthropic.
Sostituire il fallback attuale (solo 502):
```python
# Tutti ko → 502 (NO Anthropic)
return _err_response(f"glm-minimax chain exhausted", status=502)
```
Con fallback Anthropic completo:
```python
# 3) Fallback Anthropic rescue (Haiku → Sonnet → Opus)
return await _anthropic_rescue(request, orig, session, chat_fp, relay)
```

NOTA: `relay` è disponibile come parametro nella chain.

## 6. MixAM — aggiornare commenti e log
Tutti i commenti `mixed` → `mix-am`, i log `mixed` → `mix-am`.
Il codice funzionale NON cambia (già corretto: Anthropic solo THINK, MiniMax ACT).

## 7. MixAG — già corretta
`_anthropic_glm_think_act_verify` già ha Anthropic come THINK e GLM come ACT.
Aggiornare commenti da `anthropic-glm` → `mix-ag`.

## 8. Reset chat modes (difensivo)
Dopo aver rinominato, le chat esistenti con il vecchio nome nel store
(`ai-router-chats.json`) avranno una modalità non più in VALID_MODES.
In `get_mode()` aggiungere fallback:
```python
def get_mode(request=None, fp: str = None) -> str:
    ...
    if mode not in VALID_MODES:
        log(f"mode '{mode}' non valido → default 'mix-am'")
        mode = "mix-am"
    return mode
```

## 9. Elimina orfani
Rimuovere le 2 funzioni orfane:
- `_anthropic_glm_only_chain` (r 3310-3538, ~229 righe)
- `_glm_minimax_only_chain` (r 3476-3535, dentro la funzione sopra — eliminata con la parent)

## 10. Aggiornare _send_sse_message se usato con mode
Verificare che `_send_sse_message` non abbia riferimenti ai vecchi nomi.

## Test
- Ogni modo risponde con header `x-ai-verified` corretto
- MixAM: skip THINK su richiesta MiniMax (fast-path)
- MixAG: fallback Anthropic quando GLM fallisce
- MixGM: fallback Anthropic quando MiniMax fallisce (FIX principale)
