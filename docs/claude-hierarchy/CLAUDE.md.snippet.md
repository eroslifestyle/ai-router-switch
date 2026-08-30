## Gerarchia THINK/ACT del router (ai-router-switch)

Questo progetto usa [ai-router-switch](https://github.com/eroslifestyle/ai-router-switch)
come backend. Il router instrada le richieste in base al NOME del modello
richiesto, non decide da solo: chiedere Opus/Sonnet/Fable ottiene il ruolo
THINK (pianifica, verifica, applica), chiedere Haiku ottiene il ruolo ACT
(esegue) — la mappatura ruolo→provider per la modalità attiva è in
`src/role_routing.py::ROUTING_TABLE`.

Regole:
1. **Il modello THINK lo sceglie l'utente** tramite `/model` (Opus/Sonnet/Fable).
   Non cambia mai da solo, non lo decide il codice.
2. **Ogni `Agent()`/Task() deve avere `model` esplicito.** Un subagent senza
   `model` eredita il ruolo del chiamante: non delega niente, aggira solo un
   giro di rete. Per delegare l'esecuzione: `model="claude-haiku-4-5-20251001"`.
3. **Il THINK non scrive codice di progetto direttamente** nelle modalità dove
   THINK e ACT sono provider diversi (mix-am*, mix-ag*, mix-gm*, mix-al, ultra):
   pianifica, delega la scrittura a un subagent Haiku, poi verifica il diff
   (Read/git diff) prima di accettarlo. Eccezione: micro-edit (~15 righe) o file
   di configurazione/tooling — l'overhead della delega non vale la pena.
4. **L'escalation riguarda solo l'esecuzione**, mai il THINK: se il subagent
   fallisce ripetutamente, prova un modello di esecuzione più capace; il
   modello THINK cambia solo se l'utente lo cambia a mano con `/model`.

Verifica quale modalità è attiva prima di assumere il routing:
```
python3 -c "import sys; sys.path.insert(0,'src'); import paths; print(paths.mode_file())"
```
oppure, se installato, `ai-mode status`.
