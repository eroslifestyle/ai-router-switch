"""
Self-healing fixer L2: auto-merge con auto-revert.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

import paths

# Contratti esterni
from self_healing.auto_fixer import load_bug_catalog, recurring_bugs, make_ticket

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = paths.config_home() / "self-fix"
USAGE_SIDECAR = paths.log_file("router-usage.jsonl")
LOG_FILE = paths.log_file("self-fix.log")
PORTS = (8787, 8771, 8772, 8773, 8775, 8776, 8777, 8778)
WORKTREE_BASE = REPO_ROOT / ".claude" / "worktrees"

FORBIDDEN = (
    "src/self_healing/",
    "src/router_policy.py",
    ".claude/",
    ".git/",
    "sviluppo/systemd/"
)

# Costanti
MAX_ATTEMPTS_PER_TICKET = 2
COOLDOWN_S = 6 * 3600
ROUTER_BUSY_WINDOW_S = 600
AGENT_TIMEOUT_S = 1800
HEALTH_ROUNDS_S = (30, 120, 300)
PYTEST_TIMEOUT_S = 900
IMPORT_TIMEOUT_S = 60


def _log(msg):
    """Logga messaggio con timestamp."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[fixer] {timestamp} {msg}"
    print(full_msg)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(full_msg + "\n")
    except Exception:
        pass  # fail-open


def load_state(ticket_id):
    """Carica stato del ticket da disco."""
    state_file = STATE_DIR / f"{ticket_id}.json"
    if not state_file.exists():
        return {"id": ticket_id, "attempts": 0, "last_ts": 0, "last_outcome": "", "history": []}
    
    try:
        with state_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"id": ticket_id, "attempts": 0, "last_ts": 0, "last_outcome": "", "history": []}


def save_state(ticket_id, state):
    """Salva stato del ticket in modo atomico."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_DIR / f".{ticket_id}.tmp"
    final_path = STATE_DIR / f"{ticket_id}.json"
    
    try:
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(temp_path, final_path)
    except Exception:
        pass  # fail-open


def router_is_busy():
    """Controlla se router è occupato recentemente."""
    try:
        with USAGE_SIDECAR.open("rb") as f:
            f.seek(0, 2)  # seek end
            size = f.tell()
            f.seek(max(0, size - 8192), 0)  # leggi ultimi 8KB
            content = f.read().decode("utf-8", errors="ignore")
        
        lines = content.strip().split("\n")
        for line in reversed(lines):
            if line.strip():
                try:
                    data = json.loads(line)
                    ts = data.get("ts", 0)
                    if time.time() - ts < ROUTER_BUSY_WINDOW_S:
                        return True
                    break
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass  # fail-open
    
    return False


def _touches_forbidden(changed_files):
    """Controlla se file modificati sono vietati."""
    for f in changed_files:
        norm_f = f.replace("../", "").replace("./", "")
        for forbidden_prefix in FORBIDDEN:
            if norm_f.startswith(forbidden_prefix):
                return True
        if norm_f.endswith((".service", ".timer")):
            return True
    return False


def pick_ticket(min_count):
    """Sceglie il primo ticket che passa i vincoli; ritorna (ticket, motivo).

    Il motivo dello skip e' specifico (max_attempts/cooldown/forbidden):
    finisce nel log e nei test, un motivo generico nascondeva il perche'.
    """
    catalog = load_bug_catalog()
    bugs = recurring_bugs(catalog, min_count)
    last_reason = "Nessun ticket valido trovato"

    for bug in bugs:
        ticket = make_ticket(bug)
        ticket_id = ticket["id"]
        state = load_state(ticket_id)

        if state["attempts"] >= MAX_ATTEMPTS_PER_TICKET:
            last_reason = f"max_attempts: {ticket_id}"
            continue

        if time.time() - state["last_ts"] < COOLDOWN_S:
            last_reason = f"cooldown: {ticket_id}"
            continue

        if _touches_forbidden(ticket.get("suspected_files", [])):
            last_reason = f"forbidden: {ticket_id}"
            continue

        return ticket, ""

    return None, last_reason


def create_worktree(ticket_id):
    """Crea worktree isolato per il ticket."""
    worktree_path = WORKTREE_BASE / ticket_id
    branch_name = f"fix/{ticket_id}"
    
    if worktree_path.exists():
        # Rimuovi worktree esistente se presente
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree_path)], 
                          cwd=REPO_ROOT, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass
    
    # Crea nuovo worktree
    cmd = ["git", "worktree", "add", str(worktree_path), "-b", branch_name]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)
    return worktree_path


def run_fix_agent(worktree, ticket):
    """Esegue agente AI per generare patch."""
    symptom = ticket.get("symptom", "")
    files = ", ".join(ticket.get("suspected_files", []))
    repro = ticket.get("reproduction", "")
    
    prompt = f"""
Sei un agente di fix automatico. Il bug è: {symptom}.
File sospetti: {files}.
Riproduzione: {repro}.
Modifica SOLO i file strettamente necessari per risolvere il problema.
VIETATO toccare i seguenti path: {', '.join(FORBIDDEN)}.
Aggiorna o aggiungi un test che verifica il fix.
NON fare git commit: ci penserà l'orchestratore.
Non aggiungere dipendenze nuove.
"""
    
    try:
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", prompt],
            cwd=worktree,
            timeout=AGENT_TIMEOUT_S,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            _log(f"Agente fallito: {result.stderr[:2048]}")
            return False
        
        # Controlla se ha prodotto diff (tracked modificati O untracked nuovi)
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree,
            capture_output=True,
            text=True
        )

        return bool(status_result.stdout.strip())
    
    except subprocess.TimeoutExpired:
        _log("Timeout agente")
        return False
    except FileNotFoundError:
        _log("Claude CLI non trovato")
        return False
    except Exception as e:
        _log(f"Errore esecuzione agente: {e}")
        return False


def run_gates(worktree):
    """Esegue controlli di sicurezza e qualità."""
    # Gate A: diff non vuoto
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=True
        )
        untracked_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=True
        )
        all_changed = diff_result.stdout.splitlines() + [
            line.split()[1] for line in untracked_result.stdout.splitlines()
        ]
        
        if not all_changed:
            return False, "gateA: nessun file modificato"
    except subprocess.CalledProcessError as e:
        return False, f"gateA: errore git: {e}"

    # Gate B: file non vietati
    if _touches_forbidden(all_changed):
        return False, "gateB: file vietati modificati"

    # Gate C: import REALE di tutti i moduli. py_compile NON basta: compila
    # anche codice che esplode di NameError all'import (trappola documentata).
    try:
        mods = sorted(p.stem for p in (worktree / "src").glob("*.py")
                      if "-" not in p.name and p.stem != "__init__")
        mods += ["self_healing.watcher", "self_healing.sensor",
                 "self_healing.auto_fixer", "self_healing.m3_source",
                 "self_healing.fixer"]
        code = ("import importlib, sys; sys.path.insert(0, 'src'); "
                + "".join(f"importlib.import_module('{m}'); " for m in mods)
                + "print('IMPORT OK')")
        import_res = subprocess.run(["python3", "-c", code], cwd=worktree,
                                    check=True, timeout=IMPORT_TIMEOUT_S,
                                    capture_output=True, text=True)
        if "IMPORT OK" not in import_res.stdout:
            return False, "gateC: output import inatteso"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        detail = getattr(e, "stderr", "") or getattr(e, "output", "") or str(e)
        return False, f"gateC: import fallito: {str(detail)[-200:]}"

    # Gate D: ruff check (solo file ancora esistenti: i file eliminati non si
    # lintano; se non resta nulla si passa oltre, il gate E pytest gira sempre).
    existing_changed = [f for f in dict.fromkeys(all_changed)
                        if (worktree / f).exists()]
    if existing_changed:
        try:
            ruff_result = subprocess.run(
                ["ruff", "check"] + existing_changed,
                cwd=worktree,
                capture_output=True,
                text=True
            )
            if ruff_result.returncode != 0:
                return False, f"gateD: ruff fallito: {ruff_result.stdout[:200]}"
        except FileNotFoundError:
            _log("Ruff non trovato, skip controllo")

    # Gate E: pytest
    try:
        pytest_result = subprocess.run(
            ["python3", "-m", "pytest", "-q"],
            cwd=worktree,
            timeout=PYTEST_TIMEOUT_S,
            capture_output=True,
            text=True
        )
        if pytest_result.returncode != 0:
            return False, f"gateE: pytest fallito: {pytest_result.stdout[:200]}"
    except subprocess.TimeoutExpired:
        return False, "gateE: pytest timeout"

    return True, ""


def commit_patch(worktree, ticket):
    """Committa la patch."""
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    msg = f"fix({ticket['kind']}): {ticket['id']} (self-healing L2)"
    subprocess.run(["git", "commit", "-m", msg], cwd=worktree, check=True)
    
    # Ottieni SHA del commit
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()


def merge_to_main(branch):
    """Fonde la patch su main."""
    merge_msg = f"merge: {branch} (self-healing L2)"
    cmd = ["git", "merge", "--no-ff", branch, "-m", merge_msg]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)
    
    # Ottieni SHA del merge
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()


def restart_and_health():
    """Riavvia servizio e controlla salute."""
    # Controlla stato iniziale
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "ai-router"],
        capture_output=True,
        text=True
    )
    if result.stdout.strip() != "active":
        _log("Servizio non attivo, impossibile procedere")
        return False

    # Controlla che abbia Restart=
    cat_result = subprocess.run(
        ["systemctl", "--user", "cat", "ai-router"],
        capture_output=True,
        text=True
    )
    if "Restart=" not in cat_result.stdout:
        _log("Servizio non configurato per riavvio automatico")
        return False

    # Riavvia
    subprocess.run(["systemctl", "--user", "restart", "ai-router"], check=True)
    time.sleep(3)

    # Controlla stato dopo riavvio
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "ai-router"],
        capture_output=True,
        text=True
    )
    if result.stdout.strip() != "active":
        _log("Servizio non attivo dopo riavvio")
        return False

    # Healthchecks sui round
    for round_time in HEALTH_ROUNDS_S:
        time.sleep(round_time)
        
        # Controlla stato servizio
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "ai-router"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip() != "active":
            _log("Servizio non attivo durante healthcheck")
            return False
        
        # Controlla porte
        for port in PORTS:
            try:
                url = f"http://127.0.0.1:{port}/health"
                response = urlopen(url, timeout=5)
                if response.status != 200:
                    _log(f"Porta {port} non risponde 200")
                    return False
            except URLError:
                _log(f"Errore connessione porta {port}")
                return False

    return True


def rollback(merge_sha):
    """Annulla il merge e riporta il servizio sul codice vecchio.

    Deve riprendersi ANCHE se il servizio e' in failed/crash-loop: dopo uno
    start-limit systemd richiede reset-failed (trappola nota di questo
    progetto), altrimenti il rollback lascerebbe il router GIU'.
    """
    subprocess.run(["git", "revert", "--no-edit", "-m", "1", merge_sha],
                   cwd=REPO_ROOT, check=True)

    # reset-failed e' best-effort: se non c'era start-limit non fa danni.
    subprocess.run(["systemctl", "--user", "reset-failed", "ai-router"],
                   capture_output=True)
    subprocess.run(["systemctl", "--user", "restart", "ai-router"], check=True)
    time.sleep(3)

    result = subprocess.run(["systemctl", "--user", "is-active", "ai-router"],
                            capture_output=True, text=True)
    if result.stdout.strip() != "active":
        _log("Servizio non attivo dopo rollback")
        return False

    # Un solo passaggio porte, senza i round lunghi.
    for port in PORTS:
        try:
            response = urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
            if response.status != 200:
                _log(f"Porta {port} non risponde 200 dopo rollback")
                return False
        except Exception:
            _log(f"Errore connessione porta {port} dopo rollback")
            return False

    return True


def cleanup_worktree(ticket_id):
    """Pulisce worktree."""
    worktree_path = WORKTREE_BASE / ticket_id
    try:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree_path)], 
                      cwd=REPO_ROOT, check=True)
    except Exception:
        pass  # fail-open


def process_ticket(ticket, dry_run=False):
    """Orchestra processo di fix completo."""
    ticket_id = ticket["id"]
    state = load_state(ticket_id)

    if dry_run:
        print(f"Piano per ticket {ticket_id}:")
        print(f"- Tentativo #{state['attempts'] + 1} (simulato, non consumato)")
        print(f"- File sospetti: {ticket.get('suspected_files', [])}")
        print("- Simulazione completa senza effetti reali")
        return 0

    # attempts/cooldown si consumano SOLO sull'esecuzione reale
    state["attempts"] += 1
    state["last_ts"] = time.time()
    
    try:
        worktree = create_worktree(ticket_id)
        
        # Fase 1: genera patch
        if not run_fix_agent(worktree, ticket):
            cleanup_worktree(ticket_id)
            outcome = "failed:agente"
            state["last_outcome"] = outcome
            state["history"].append(outcome)
            save_state(ticket_id, state)
            return 1
        
        # Fase 2: controlli di sicurezza
        success, gate_msg = run_gates(worktree)
        if not success:
            cleanup_worktree(ticket_id)
            outcome = f"failed:{gate_msg}"
            state["last_outcome"] = outcome
            state["history"].append(outcome)
            save_state(ticket_id, state)
            return 1
        
        # Fase 3: commit (lo sha del merge, non questo, serve per il revert)
        commit_patch(worktree, ticket)
        
        # Fase 4: merge
        try:
            merge_sha = merge_to_main(f"fix/{ticket_id}")
        except subprocess.CalledProcessError:
            cleanup_worktree(ticket_id)
            outcome = "failed:merge-conflict"
            state["last_outcome"] = outcome
            state["history"].append(outcome)
            save_state(ticket_id, state)
            return 1
        
        # Fase 5: riavvio e healthcheck
        try:
            if not restart_and_health():
                # Post-merge failure: tenta rollback
                try:
                    if rollback(merge_sha):
                        outcome = "reverted"
                        state["last_outcome"] = outcome
                        state["history"].append(outcome)
                        save_state(ticket_id, state)
                        cleanup_worktree(ticket_id)
                        return 1
                    else:
                        _log(f"ALLARME: rollback fallito per merge {merge_sha}, intervento umano richiesto!")
                        state['last_outcome'] = 'alarm:rollback-failed'
                        state['history'].append('alarm:rollback-failed')
                        save_state(ticket_id, state)
                        return 2
                except Exception as e:
                    _log(f"ALLARME: errore rollback {e}, intervento umano richiesto!")
                    state['last_outcome'] = 'alarm:rollback-failed'
                    state['history'].append('alarm:rollback-failed')
                    save_state(ticket_id, state)
                    return 2
        except Exception as e:
            # Post-merge exception: tenta rollback
            try:
                if rollback(merge_sha):
                    outcome = "reverted"
                    state["last_outcome"] = outcome
                    state["history"].append(outcome)
                    save_state(ticket_id, state)
                    cleanup_worktree(ticket_id)
                    return 1
                else:
                    _log(f"ALLARME: rollback fallito per eccezione {e}, intervento umano richiesto!")
                    state['last_outcome'] = 'alarm:rollback-failed'
                    state['history'].append('alarm:rollback-failed')
                    save_state(ticket_id, state)
                    return 2
            except Exception as e2:
                _log(f"ALLARME: errore rollback {e2}, intervento umano richiesto!")
                state['last_outcome'] = 'alarm:rollback-failed'
                state['history'].append('alarm:rollback-failed')
                save_state(ticket_id, state)
                return 2
        
        # Successo completo
        try:
            subprocess.run(["git", "push"], cwd=REPO_ROOT, timeout=30)
        except Exception:
            _log("Push fallito ma proseguo con successo")
        
        outcome = "merged"
        state["last_outcome"] = outcome
        state["history"].append(outcome)
        save_state(ticket_id, state)
        cleanup_worktree(ticket_id)
        return 0
    
    except Exception as e:
        # Pre-merge exception
        try:
            cleanup_worktree(ticket_id)
        except Exception:
            pass  # fail-open
        outcome = f"failed:exception-{type(e).__name__}"
        state["last_outcome"] = outcome
        state["history"].append(outcome)
        save_state(ticket_id, state)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Self-healing fixer L2")
    parser.add_argument("--auto", action="store_true", help="Modalità automatica con vincoli")
    parser.add_argument("--ticket", help="ID o tipo di ticket")
    parser.add_argument("--dry-run", action="store_true", help="Simula senza effetti")
    parser.add_argument("--min-count", type=int, default=3, help="Minimo conteggio bug")
    
    args = parser.parse_args()
    
    if not args.auto and not args.ticket:
        parser.print_help()
        return 1
    
    if args.auto:
        if router_is_busy():
            _log("Router occupato, skip automatico")
            return 0
        
        ticket, reason = pick_ticket(args.min_count)
        if not ticket:
            _log(f"Nessun ticket disponibile: {reason}")
            return 2
    else:
        catalog = load_bug_catalog()
        bugs = recurring_bugs(catalog, args.min_count)
        ticket = None
        
        for bug in bugs:
            tkt = make_ticket(bug)
            if tkt["id"] == args.ticket or tkt["kind"] == args.ticket:
                ticket = tkt
                break
        
        if not ticket:
            _log(f"Ticket {args.ticket} non trovato")
            return 2
    
    return process_ticket(ticket, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
