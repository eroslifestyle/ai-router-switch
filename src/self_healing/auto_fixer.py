import json
import re
import pathlib
import argparse
import hashlib
import paths

# Fonte reale dei bug: debug-errors.jsonl (240+ entry). BUG-CATALOG.jsonl non viene generato.
DEFAULT_BUG_CATALOG = paths.log_file("debug-errors.jsonl")


def load_bug_catalog(path=DEFAULT_BUG_CATALOG):
    """Load bug catalog from a JSONL file.

    Returns a list of dicts. Tolerates missing files and malformed lines.
    """
    if not path.exists():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip lines that are not valid JSON
                continue
    return entries


def _normalize(snippet):
    """Normalize an error snippet to create a stable grouping key.

    Lowercases, replaces sequences of digits with a placeholder '#',
    and collapses whitespace.
    """
    s = snippet.lower()
    s = re.sub(r'\d+', '#', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


# Campi del catalogo che possono contenere il testo dell'errore, in ordine di
# preferenza. `upstream_error` e' quello che le entry reali usano davvero: le
# 240 entry di debug-errors.jsonl non hanno mai ne' `snippet` ne' `error`
# (verificato il 2026-08-08 contando le chiavi). Cercando solo quei due la
# signature restava sempre la stringa vuota, e i ticket finivano tutti con
# l'md5 del nulla — d41d8cd9. Gli altri nomi restano per i cataloghi di test e
# per eventuali produttori futuri.
CAMPI_TESTO_ERRORE = ("snippet", "error", "upstream_error", "note")

# Campi che descrivono la richiesta e non il testo dell'errore: servono come
# discriminante di riserva quando il testo manca del tutto. Senza, due bug
# diversi ma entrambi senza testo collasserebbero sullo stesso ticket,
# sovrascrivendone storia e tentativi.
CAMPI_DISCRIMINANTI = ("mode", "path", "stage", "status", "upstream_status")


def _signature_entry(entry):
    """Ricava la signature di raggruppamento di una entry del catalogo.

    Prende il primo campo di testo disponibile; se sono tutti vuoti, ripiega su
    una combinazione dei campi che descrivono la richiesta, cosi' la signature
    non e' mai vuota e bug diversi restano distinti.
    """
    for campo in CAMPI_TESTO_ERRORE:
        valore = entry.get(campo)
        if valore:
            normalizzata = _normalize(str(valore))
            if normalizzata:
                return normalizzata
    coppie = [f"{c}={entry.get(c)}" for c in CAMPI_DISCRIMINANTI if entry.get(c)]
    if coppie:
        return _normalize("|".join(coppie))
    # Nemmeno un campo utile: si marca esplicitamente invece di produrre una
    # stringa vuota, che darebbe di nuovo l'md5 del nulla.
    return "senza-testo-e-senza-contesto"


def recurring_bugs(catalog, min_count=3):
    """Find groups of recurring bugs in the catalog.

    Groups by (kind, normalized snippet). Returns list of dicts with
    kind, signature, count, sample (first entry), and last_ts,
    sorted by count descending.
    """
    groups = {}
    for entry in catalog:
        kind = entry.get("kind")
        signature = _signature_entry(entry)
        key = (kind, signature)
        if key not in groups:
            groups[key] = {"entries": [], "last_ts": ""}
        groups[key]["entries"].append(entry)
        ts = entry.get("ts", "")
        if ts > groups[key]["last_ts"]:
            groups[key]["last_ts"] = ts

    result = []
    for (kind, signature), group in groups.items():
        count = len(group["entries"])
        if count >= min_count:
            result.append({
                "kind": kind,
                "signature": signature,
                "count": count,
                "sample": group["entries"][0],
                "last_ts": group["last_ts"],
            })
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


# The "mode" field holds router modes (e.g. anthropic, mix-am), not languages.
# The previous typical_by_mode was keyed by language and never matched.
# Three legacy mode names are preserved for backward compatibility with historical logs.
TYPICAL_FILES_BY_MODE = {
    "anthropic":    ["src/pipeline_anthropic.py", "src/forward_anthropic.py"],
    "minimax":      ["src/pipeline_minimax.py", "src/forward_minimax.py"],
    "glm":          ["src/glm_backend.py"],
    "qwen":         ["src/qwen_backend.py"],
    "mix-am":       ["src/role_routing.py", "src/pipeline_minimax.py", "src/forward_anthropic.py"],
    "mix-ag":       ["src/role_routing.py", "src/glm_backend.py", "src/forward_anthropic.py"],
    "mix-gm":       ["src/role_routing.py", "src/glm_backend.py", "src/pipeline_minimax.py"],
}
# Legacy aliases (mapped to the same list objects to avoid duplication)
TYPICAL_FILES_BY_MODE["mixed"] = TYPICAL_FILES_BY_MODE["mix-am"]
TYPICAL_FILES_BY_MODE["glm-minimax"] = TYPICAL_FILES_BY_MODE["mix-gm"]
TYPICAL_FILES_BY_MODE["anthropic-glm"] = TYPICAL_FILES_BY_MODE["mix-ag"]


def _suspected_files(sample):
    """Heuristically extract suspected source files from a bug sample.

    Looks for file paths with common code extensions in snippet, error,
    and traceback fields. Falls back to typical router-mode modules
    if no paths are found.
    Returns up to five unique file paths.
    """
    # Combine relevant text fields
    text = " ".join(
        sample.get(field, "")
        for field in ("snippet", "error", "traceback")
    )
    # Regex for file paths with code extensions
    pattern = re.compile(r'[\w\-./]+\.(?:py|js|ts|go|rs|sql)\b')
    matches = pattern.findall(text)
    # Preserve order and remove duplicates
    unique = list(dict.fromkeys(matches))
    if unique:
        return unique[:5]

    # Fallback: typical files based on router mode
    mode = sample.get("mode")
    return TYPICAL_FILES_BY_MODE.get(mode, [])[:5]


def make_ticket(bug):
    """Generate a structured fix ticket for a recurring bug.

    The ticket contains an id, kind, symptom, occurrence count, mode,
    suspected files, reproduction hint, suggested branch, and status.
    """
    signature = bug["signature"]
    if not signature:
        # Guardia esplicita: una signature vuota darebbe l'md5 del nulla
        # (d41d8cd9) per QUALUNQUE bug, e i ticket si sovrascriverebbero a
        # vicenda perdendo storia e tentativi. E' successo davvero: i tre
        # ticket in ~/.claude/self-fix/ avevano tutti quel suffisso.
        # Il fallback usa il campione, cosi' bug diversi restano distinti.
        signature = _signature_entry(bug.get("sample") or {})
    hash8 = hashlib.md5(signature.encode()).hexdigest()[:8]
    kind = bug["kind"]
    ticket_id = f"{kind}-{hash8}"
    branch = f"fix/{kind}-{hash8}"
    ticket = {
        "id": ticket_id,
        "kind": kind,
        # Stessa catena di campi della signature: cercando solo snippet/error
        # il sintomo restava vuoto su ogni ticket reale, perche' le entry usano
        # upstream_error.
        "symptom": next(
            (str(bug["sample"].get(c)) for c in CAMPI_TESTO_ERRORE if bug["sample"].get(c)),
            "",
        ),
        "count": bug["count"],
        "mode": bug["sample"].get("mode"),
        "suspected_files": _suspected_files(bug["sample"]),
        "reproduction": (
            "Cerca nel log la signature; riproduci con un fake upstream "
            "che emette l'errore"
        ),
        "branch": branch,
        "status": "open",
    }
    return ticket


def main():
    """Command-line interface for the auto-fixer."""
    parser = argparse.ArgumentParser(
        description="Offline auto-fixer analysis tool. Generates fix tickets "
        "for recurring bugs without performing any merges or automatic execution."
    )
    parser.add_argument(
        "--catalog",
        type=pathlib.Path,
        default=DEFAULT_BUG_CATALOG,
        help="Path to the bug catalog JSONL file.",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=3,
        help="Minimum number of occurrences to consider a bug recurring.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List top recurring bugs (kind | count | signature | suspected files).",
    )
    parser.add_argument(
        "--ticket",
        metavar="KIND_OR_ID",
        help="Generate and print a fix ticket for the first matching bug.",
    )
    parser.add_argument(
        "--plan",
        metavar="KIND_OR_ID",
        help="Print a fix plan (ticket + branch + suspected files + suggested commands). "
        "No automatic execution is performed.",
    )
    args = parser.parse_args()

    # Load catalog
    catalog = load_bug_catalog(args.catalog)

    if args.list:
        bugs = recurring_bugs(catalog, min_count=args.min_count)
        if not bugs:
            print("No recurring bugs found.")
            return
        for bug in bugs:
            sig = bug["signature"]
            sig80 = sig[:80] + ("..." if len(sig) > 80 else "")
            files = _suspected_files(bug["sample"])
            print(f"{bug['kind']} | {bug['count']} | {sig80} | {', '.join(files)}")

    elif args.ticket:
        # Try to find a matching bug among recurring ones (allow any count)
        bugs = recurring_bugs(catalog, min_count=1)
        ticket = None
        for bug in bugs:
            t = make_ticket(bug)
            if bug["kind"] == args.ticket or t["id"] == args.ticket:
                ticket = t
                break
        # Fallback: search directly by kind in the raw catalog
        if ticket is None:
            for entry in catalog:
                if entry.get("kind") == args.ticket:
                    sig = _normalize(entry.get("snippet") or entry.get("error") or "")
                    bug = {
                        "kind": entry.get("kind"),
                        "signature": sig,
                        "count": 1,
                        "sample": entry,
                        "last_ts": entry.get("ts", ""),
                    }
                    ticket = make_ticket(bug)
                    break
        if ticket is None:
            print(f"No bug found for '{args.ticket}'.")
            return
        print(json.dumps(ticket, indent=2))

    elif args.plan:
        bugs = recurring_bugs(catalog, min_count=1)
        target_bug = None
        for bug in bugs:
            t = make_ticket(bug)
            if bug["kind"] == args.plan or t["id"] == args.plan:
                target_bug = bug
                break
        # Fallback: search directly by kind in the raw catalog
        if target_bug is None:
            for entry in catalog:
                if entry.get("kind") == args.plan:
                    sig = _normalize(entry.get("snippet") or entry.get("error") or "")
                    target_bug = {
                        "kind": entry.get("kind"),
                        "signature": sig,
                        "count": 1,
                        "sample": entry,
                        "last_ts": entry.get("ts", ""),
                    }
                    break
        if target_bug is None:
            print(f"No bug found for '{args.plan}'.")
            return

        ticket = make_ticket(target_bug)
        print("=== FIX PLAN (GATED) ===")
        print("Ticket:")
        print(json.dumps(ticket, indent=2))
        print(f"Branch: {ticket['branch']}")
        print(f"Suspected files: {', '.join(ticket['suspected_files'])}")
        print("Suggested commands (apply manually after review + test):")
        print("  # Create a new worktree (optional):")
        print(f"  # git worktree add ../fix-wt-{ticket['id']} -b {ticket['branch']}")
        print("  # Edit suspected files, run tests, then merge via PR after review.")
        print(
            "GATED: nessuna esecuzione automatica. "
            "Applica manualmente in worktree dopo review + test."
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    # Self-test: create a fake in-memory catalog and verify core functions.
    fake_catalog = [
        {
            "ts": "2023-01-01T10:00:00",
            "kind": "ValueError",
            "mode": "python",
            "snippet": "division by zero",
            "status": "fixed",
        },
        {
            "ts": "2023-01-01T11:00:00",
            "kind": "ValueError",
            "mode": "python",
            "snippet": "division by zero",
            "status": "fixed",
        },
        {
            "ts": "2023-01-01T12:00:00",
            "kind": "ValueError",
            "mode": "python",
            "snippet": "division by zero",
            "status": "fixed",
        },
        {
            "ts": "2023-01-01T13:00:00",
            "kind": "TypeError",
            "mode": "python",
            "snippet": "can't concat str to int",
            "status": "open",
        },
    ]

    bugs = recurring_bugs(fake_catalog, min_count=3)
    assert len(bugs) == 1, f"Expected 1 bug group, got {len(bugs)}"
    bug = bugs[0]
    ticket = make_ticket(bug)
    assert "id" in ticket
    assert "branch" in ticket
    assert "suspected_files" in ticket
    assert ticket["id"].startswith(bug["kind"] + "-")
    print("OK")
