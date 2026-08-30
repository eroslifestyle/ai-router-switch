# Bootstrap della gerarchia THINK/ACT/VERIFY per Claude Code

## Perché serve

`ai-router-switch` instrada le richieste in base al **nome del modello richiesto**: chi chiama con un modello Haiku ottiene il ruolo di esecuzione (ACT), chi chiama con Opus/Sonnet/Fable ottiene il ruolo di pianificazione (THINK). La mappatura ruolo → provider per la modalità attiva vive in [`src/role_routing.py`](../../src/role_routing.py) (`ROUTING_TABLE`, `model_role()`).

Il router però è **un tunnel trasparente**: non orchestra fasi, non tiene stato, non impedisce a un agente di scrivere codice direttamente invece di delegarlo. Quella disciplina — "il THINK pianifica e verifica, l'ACT esegue, il THINK non cambia da solo" — deve vivere nel client (Claude Code), nel suo `CLAUDE.md` e nei suoi hook. Chi clona solo questo repository ottiene il proxy, non quella disciplina: senza di essa, un orchestratore Opus può continuare a scrivere codice di progetto direttamente anche in una modalità come `mix-am`, pensata apposta per farlo eseguire da MiniMax — e la modalità non serve più a niente.

Questa cartella fornisce il minimo per ricostruirla in qualunque progetto.

## Cosa contiene

| File | Cosa fa |
|---|---|
| `CLAUDE.md.snippet.md` | Sezione da appendere al `CLAUDE.md` del progetto: le 4 regole della gerarchia |
| `hooks/enforce_agent_model.py` | Hook `PreToolUse` su `Task`: nega uno spawn di subagent senza `model` esplicito |
| `hooks/enforce_delegate_write.py` | Hook `PreToolUse` su `Edit`/`Write`/`MultiEdit`: nella modalità attiva del router, se è una modalità "mista" (THINK e ACT su provider diversi) nega scritture di codice oltre una soglia di righe, a meno che siano micro-edit |
| `settings.snippet.json` | Le voci `hooks.PreToolUse` da registrare in `.claude/settings.json` |

## Come installarlo

Incolla questo prompt in una sessione Claude Code pulita, nel progetto dove vuoi applicare la gerarchia (può essere questo stesso repository o un altro):

```
Ho clonato ai-router-switch (https://github.com/eroslifestyle/ai-router-switch) e
lo uso come proxy per Claude Code. Il router instrada le richieste in base al NOME
del modello richiesto (vedi src/role_routing.py: ROUTING_TABLE, model_role()) —
Haiku = esecuzione (ACT), Opus/Sonnet/Fable = pianificazione (THINK) — ma questa
disciplina va applicata da ME (l'agente), non dal router: il router è un tunnel
trasparente e non orchestra nulla.

Voglio che tu configuri QUESTO progetto (non il router) per rispettare quella
gerarchia. Fai così:

1. Leggi docs/claude-hierarchy/CLAUDE.md.snippet.md nel repo di ai-router-switch
   (chiedimi il path se non sai dove l'ho clonato) e appendi il suo contenuto al
   file CLAUDE.md di questo progetto (crealo se non esiste). Non duplicare la
   sezione se è già presente.

2. Copia docs/claude-hierarchy/hooks/enforce_agent_model.py e
   docs/claude-hierarchy/hooks/enforce_delegate_write.py in .claude/hooks/ di
   questo progetto.

3. Leggi .claude/settings.json di questo progetto se esiste. Se esiste, fai il
   merge delle voci PreToolUse da docs/claude-hierarchy/settings.snippet.json
   SENZA sovrascrivere hook già presenti (accodale nell'array). Se non esiste,
   crealo copiando lo snippet.

4. Dimmi quali modalità del router uso davvero (anthropic/minimax/glm/qwen/
   mix-am/mix-ag/mix-gm/mix-al/local/gpt/ultra/opr, incluse le varianti -2), e
   se voglio l'hook 2 (blocca scritture dirette di codice sopra soglia nelle
   modalità miste) attivo o solo registrato-ma-disattivabile con una env var.

5. Verifica il risultato: mostrami il diff di CLAUDE.md, il contenuto di
   .claude/settings.json dopo il merge, e conferma che i due script hook siano
   eseguibili (`python3 .claude/hooks/enforce_agent_model.py < /dev/null` deve
   uscire con codice 0 senza errori).

Non installare nulla nella mia config GLOBALE (~/.claude/) a meno che te lo
chieda esplicitamente.
```

## Nota

Gli hook leggono la modalità corrente dal file che il router stesso usa (`ai-router-mode`, risolto con la stessa logica di `src/paths.py::config_home()`), duplicata qui perché l'hook gira in un processo separato da quello del router. Se cambi le porte/modalità del router, aggiorna `DELEGATING_MODES` in `enforce_delegate_write.py` di conseguenza.
