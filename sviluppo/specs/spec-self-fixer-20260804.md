# SPEC: src/self_healing/fixer.py — self-fixer Livello 2 (auto-merge con auto-revert)

Genera UN SOLO file Python: `fixer.py`, stdlib puro, ~400 righe max. Niente dipendenze esterne.
Il file vive in `src/self_healing/` ed è invocato come `python3 -m self_healing.fixer` (cwd=src).

## Missione

Prende un ticket di bug ricorrente (dal catalogo), fa generare la patch a un agente AI in un
git worktree isolato, corre i gate di verifica, e se TUTTI passano: merge su main, restart
sicuro del servizio, healthcheck. Se qualcosa fallisce DOPO il merge: revert automatico,
restart, healthcheck. Fail-open OVUNQUE: ogni errore inatteso abortisce senza toccare main.

## Contratti esterni ESATTI (non inventare firme diverse)

```python
# Import dal package (stessa directory):
from self_healing.auto_fixer import load_bug_catalog, recurring_bugs, make_ticket
# load_bug_catalog(path=DEFAULT_BUG_CATALOG) -> list[dict]   (da ~/.claude/logs/debug-errors.jsonl)
# recurring_bugs(catalog, min_count=3) -> list[dict] ordinati per count desc
#   ogni bug: {"kind": str, "signature": str, "count": int, "sample": dict}
# make_ticket(bug) -> dict con chiavi: id, kind, symptom, count, mode,
#   suspected_files (list[str]), reproduction, branch, status

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # .resolve() OBBLIGATORIO (symlink deploy)
STATE_DIR = Path.home() / ".claude" / "self-fix"            # JSON stato per ticket
USAGE_SIDECAR = Path.home() / ".claude" / "logs" / "router-usage.jsonl"
LOG_FILE = Path.home() / ".claude" / "logs" / "self-fix.log"
PORTS = (8787, 8771, 8772, 8773, 8775, 8776, 8777, 8778)    # healthcheck GET /health -> 200
WORKTREE_BASE = REPO_ROOT / ".claude" / "worktrees"

# Zona proibita (prefissi relativi alla repo root): il fixer non deve MAI lasciare che una
# patch tocchi questi path, ne' mergiare diff che li contengono:
FORBIDDEN = ("src/self_healing/", "src/router_policy.py", ".claude/", ".git",
             "sviluppo/systemd/")   # + qualsiasi file che finisce con .service o .timer
```

## Costanti nominate (unita' nel nome)

MAX_ATTEMPTS_PER_TICKET = 2 · COOLDOWN_S = 6*3600 · ROUTER_BUSY_WINDOW_S = 600 ·
AGENT_TIMEOUT_S = 1800 · HEALTH_ROUNDS_S = (30, 120, 300) · PYTEST_TIMEOUT_S = 900

## Struttura richiesta (funzioni, in quest'ordine)

1. `_log(msg)`: stampa `[fixer] <msg>` e appende riga datata a LOG_FILE (fail-open).
2. `load_state(ticket_id) -> dict` / `save_state(ticket_id, state)`: JSON in STATE_DIR
   (mkdir parents ok). Stato: {"id", "attempts", "last_ts", "last_outcome", "history":[...]}
   File assente -> stato vuoto con attempts=0. Scrittura atomica via tmp+os.replace.
3. `router_is_busy() -> bool`: leggi SOLO gli ultimi 8KB di USAGE_SIDECAR (seek da fine),
   ultima riga JSON valida, campo "ts" float; True se time.time()-ts < ROUTER_BUSY_WINDOW_S.
   File assente/errore -> False (fail-open).
4. `_touches_forbidden(changed_files: list[str]) -> bool`: True se un path (normalizzato,
   senza ../) ricade in FORBIDDEN (prefissi) o finisce con .service/.timer.
5. `pick_ticket(min_count) -> tuple[dict|None, str]`: catalogo -> recurring_bugs -> primo
   per count che passa i vincoli: attempts<MAX, cooldown scaduto, suspected_files non
   vietati. Ritorna (ticket, motivo) — motivo vuoto se scelto, altrimenti spiegazione skip.
6. `create_worktree(ticket_id) -> Path`: `git worktree add <WORKTREE_BASE>/<id> -b fix/<id>`
   (subprocess ARRAY, cwd=REPO_ROOT, check=True). Se il branch esiste gia': riusa
   `git worktree add <path> fix/<id>`. Ritorna il path.
7. `run_fix_agent(worktree: Path, ticket: dict) -> bool`: subprocess array
   ["claude", "-p", "--dangerously-skip-permissions", PROMPT] con cwd=worktree,
   timeout=AGENT_TIMEOUT_S, stdout/stderr catturati (troncati a 2KB nel log). PROMPT:
   contratto che dice: sei un agente di fix; il bug (symptom, file sospetti, reproduction);
   modifica SOLO file strettamente necessari; VIETATO toccare i path FORBIDDEN;
   aggiorna o aggiungi un test che prova il fix; NON fare git commit (ci pensa l'orchestratore);
   niente dipendenze nuove. Exit code 0 e diff non vuoto -> True.
8. `run_gates(worktree: Path) -> tuple[bool, str]`: in ordine, TUTTI devono passare:
   a. `git diff --name-only HEAD` + untracked (git status --porcelain) non vuoti;
   b. `_touches_forbidden` sui file cambiati -> False;
   c. import reale di tutti i moduli: python3 -c che importa ogni src/*.py senza dash nel
      nome + self_healing.* (importlib, exit 0);
   d. `ruff check <file cambiati>` exit 0 (se ruff manca, skip con warning nel motivo);
   e. `python3 -m pytest -q` cwd=worktree, timeout=PYTEST_TIMEOUT_S, exit 0.
   Ritorna (ok, "gateX: <riga rilevante dell'output>" per il log).
9. `commit_patch(worktree, ticket) -> str`: git add -A + commit con messaggio
   `fix(<kind>): <id> (self-healing L2)`; ritorna lo sha.
10. `merge_to_main(branch) -> str`: da REPO_ROOT, `git merge --no-ff <branch> -m "merge: <branch> (self-healing L2)"`; ritorna lo sha del merge. Conflitto -> raise (gestito dal chiamante come pre-merge-fail: niente da revertare, pulisci worktree).
11. `restart_and_health() -> bool`: sequenza OBBLIGATORIA:
    `systemctl --user is-active ai-router` == "active" ALTRIMENTI ritorna False senza toccare;
    verifica output di `systemctl --user cat ai-router` contenga "Restart=";
    `systemctl --user restart ai-router`; sleep 3; is-active di nuovo;
    poi per ogni round in HEALTH_ROUNDS_S: sleep(round), is-active active E tutte le PORTS
    rispondono 200 su GET http://127.0.0.1:<port>/health (urllib, timeout 5, una fallita = round fallito).
    Tutti i round ok -> True.
12. `rollback(merge_sha) -> bool`: `git revert --no-edit -m 1 <merge_sha>` da REPO_ROOT,
    poi di nuovo restart_and_health() (un solo passaggio porte, senza round lunghi).
13. `cleanup_worktree(ticket_id)`: git worktree remove --force del path; lo stato del
    ticket RESTA (storico tentativi). Fail-open.
14. `process_ticket(ticket, dry_run=False) -> int`: orchestra tutto: stato attempts+=1;
    se dry_run stampa il piano e ritorna 0. Poi: create_worktree -> run_fix_agent ->
    run_gates -> commit_patch -> merge_to_main -> restart_and_health.
    Fallimento PRE-merge (agente/gate/conflitto): cleanup, esito "failed:<motivo>", exit 1.
    Fallimento POST-merge (healthcheck): rollback -> se rollback ok esito "reverted" exit 1,
    se rollback FALLISCE: _log di ALLARME ed exit 2 (intervento umano).
    Successo: `git push` (fail-open sul push: loggalo ma esito resta "merged"),
    cleanup, exit 0. Ogni fase wrappata: eccezione inattesa pre-merge = failed; post-merge
    = tenta rollback.
15. `main() -> int`: argparse con --auto, --ticket KIND_OR_ID, --dry-run, --min-count (3).
    --auto: vincolo router_is_busy() -> skip con log e exit 0 (non e' errore); poi pick_ticket.
    --ticket: cerca in recurring_bugs per id (`<kind>-<hash8>`) o kind; se assente exit 2.
    Nessun argomento = help. Ritorna l'exit code di process_ticket.

## Regole di stile (tassative)

- subprocess SOLO con array, MAI shell=True.
- Early return, guard clauses, max 4 livelli di nesting, funzioni ~30 righe.
- Nessun numero magico: costanti nominate sopra.
- Commenti che spiegano il PERCHE' (fail-open, sicurezza restart), non il cosa.
- In fondo: `if __name__ == "__main__": raise SystemExit(main())`.
- Niente print fuori da _log (tranne --dry-run che stampa il piano).

## NON fare

- Non scrivere test: li scrive l'orchestratore umano separatamente.
- Non importare nulla del proxy (ai-router-proxy) ne' chiamare le sue porte.
- Non usare os.system, eval, exec, pickle.
- Non gestire SIGTERM/lock: ci pensa il timer systemd (uno alla volta).
