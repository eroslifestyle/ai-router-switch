import hashlib
import json
import sys
import pytest
from pathlib import Path

sys.path.insert(0, 'src')
from self_healing.auto_fixer import (
    load_bug_catalog, _normalize, recurring_bugs, _suspected_files,
    make_ticket, TYPICAL_FILES_BY_MODE,
)


def test_catalogo_assente_ritorna_lista_vuota(tmp_path):
    result = load_bug_catalog(tmp_path / "manca.jsonl")
    assert result == [], (
        f"Atteso: [], Ottenuto: {result}"
    )


def test_catalogo_salta_righe_malformate(tmp_path):
    file_path = tmp_path / "mixed.jsonl"
    valid1 = {"kind": "timeout", "snippet": "timeout after 30s", "mode": "anthropic", "ts": "2026-08-01"}
    valid2 = {"kind": "crash", "error": "segmentation fault", "mode": "minimax", "ts": "2026-08-02"}
    file_path.write_text(
        json.dumps(valid1) + "\n\n" + "non-json{{{" + "\n" + json.dumps(valid2) + "\n"
    )
    result = load_bug_catalog(file_path)
    assert len(result) == 2, (
        f"Atteso: 2 entry, Ottenuto: {len(result)}"
    )
    assert result[0]["kind"] == "timeout", (
        f"Atteso kind timeout, Ottenuto: {result[0]['kind']}"
    )
    assert result[1]["kind"] == "crash", (
        f"Atteso kind crash, Ottenuto: {result[1]['kind']}"
    )


def test_normalize_stabilizza_numeri_e_spazi():
    r1 = _normalize("Errore 404 su   RIGA 12")
    r2 = _normalize("errore 999 su riga 7")
    assert r1 == r2, (
        f"Atteso: normalizzazioni uguali, Ottenuto: '{r1}' vs '{r2}'"
    )
    assert r1 == "errore # su riga #", (
        f"Atteso: 'errore # su riga #', Ottenuto: '{r1}'"
    )


def test_recurring_rispetta_min_count():
    catalog = [
        {"kind": "timeout", "snippet": "timeout after 5s", "mode": "anthropic", "ts": "2026-08-01"},
        {"kind": "timeout", "snippet": "timeout after 5s", "mode": "anthropic", "ts": "2026-08-02"},
        {"kind": "timeout", "snippet": "timeout after 5s", "mode": "anthropic", "ts": "2026-08-03"},
        {"kind": "crash", "error": "segfault", "mode": "minimax", "ts": "2026-08-01"},
        {"kind": "crash", "error": "segfault", "mode": "minimax", "ts": "2026-08-02"},
    ]
    result = recurring_bugs(catalog, min_count=3)
    assert len(result) == 1, (
        f"Atteso: 1 gruppo, Ottenuto: {len(result)}"
    )
    assert result[0]["kind"] == "timeout", (
        f"Atteso kind timeout, Ottenuto: {result[0]['kind']}"
    )
    assert result[0]["count"] == 3, (
        f"Atteso count 3, Ottenuto: {result[0]['count']}"
    )


def test_recurring_raggruppa_per_signature_normalizzata():
    catalog = [
        {"kind": "timeout", "snippet": "timeout dopo 5s", "mode": "anthropic", "ts": "2026-08-01"},
        {"kind": "timeout", "snippet": "timeout dopo 30s", "mode": "minimax", "ts": "2026-08-02"},
        {"kind": "timeout", "snippet": "timeout dopo 100s", "mode": "glm", "ts": "2026-08-03"},
    ]
    result = recurring_bugs(catalog, min_count=2)
    assert len(result) == 1, (
        f"Atteso: 1 gruppo, Ottenuto: {len(result)}"
    )
    assert result[0]["count"] == 3, (
        f"Atteso count 3, Ottenuto: {result[0]['count']}"
    )
    assert result[0]["sample"]["snippet"] == "timeout dopo 5s", (
        f"Atteso sample snippet 'timeout dopo 5s', Ottenuto: {result[0]['sample']['snippet']}"
    )


def test_recurring_ordina_per_count_decrescente():
    catalog = [
        {"kind": "timeout", "snippet": "timeout after 5s", "mode": "anthropic", "ts": "2026-08-01"},
        {"kind": "timeout", "snippet": "timeout after 5s", "mode": "anthropic", "ts": "2026-08-02"},
        {"kind": "timeout", "snippet": "timeout after 5s", "mode": "anthropic", "ts": "2026-08-03"},
        {"kind": "crash", "snippet": "crash", "mode": "minimax", "ts": "2026-08-01"},
        {"kind": "crash", "snippet": "crash", "mode": "minimax", "ts": "2026-08-02"},
        {"kind": "crash", "snippet": "crash", "mode": "minimax", "ts": "2026-08-03"},
        {"kind": "crash", "snippet": "crash", "mode": "minimax", "ts": "2026-08-04"},
        {"kind": "crash", "snippet": "crash", "mode": "minimax", "ts": "2026-08-05"},
    ]
    result = recurring_bugs(catalog, min_count=3)
    assert len(result) == 2, (
        f"Atteso: 2 gruppi, Ottenuto: {len(result)}"
    )
    assert result[0]["count"] == 5, (
        f"Atteso primo gruppo count 5, Ottenuto: {result[0]['count']}"
    )
    assert result[1]["count"] == 3, (
        f"Atteso secondo gruppo count 3, Ottenuto: {result[1]['count']}"
    )


def test_recurring_last_ts_e_il_massimo():
    catalog = [
        {"kind": "timeout", "snippet": "timeout", "mode": "anthropic", "ts": "2026-08-01"},
        {"kind": "timeout", "snippet": "timeout", "mode": "minimax", "ts": "2026-08-03"},
        {"kind": "timeout", "snippet": "timeout", "mode": "glm", "ts": "2026-08-02"},
    ]
    result = recurring_bugs(catalog, min_count=2)
    assert len(result) == 1, (
        f"Atteso: 1 gruppo, Ottenuto: {len(result)}"
    )
    assert result[0]["last_ts"] == "2026-08-03", (
        f"Atteso last_ts '2026-08-03', Ottenuto: '{result[0]['last_ts']}'"
    )
    assert result[0]["sample"]["ts"] == "2026-08-01", (
        f"Atteso sample ts '2026-08-01' (prima entry), Ottenuto: '{result[0]['sample']['ts']}'"
    )


def test_suspected_files_preferisce_i_path_del_testo():
    sample = {
        "kind": "timeout",
        "snippet": "timeout in src/glm_backend.py at line 12",
        "error": "timed out",
        "traceback": "...",
        "mode": "anthropic",
    }
    result = _suspected_files(sample)
    assert result == ["src/glm_backend.py"], (
        f"Atteso ['src/glm_backend.py'], Ottenuto: {result}"
    )
    fallback = TYPICAL_FILES_BY_MODE.get("anthropic", [])
    assert result != fallback, (
        "Il risultato non deve essere il fallback di anthropic"
    )


def test_suspected_files_fallback_per_modalita_router():
    canonical_modes = ["anthropic", "minimax", "glm", "qwen", "mix-am", "mix-ag", "mix-gm"]
    for mode in canonical_modes:
        sample = {
            "kind": "timeout",
            "snippet": "generic timeout error without any file path",
            "error": "timeout",
            "traceback": "no files here",
            "mode": mode,
        }
        result = _suspected_files(sample)
        expected = TYPICAL_FILES_BY_MODE[mode]
        assert result == expected, (
            f"Atteso fallback {expected} per mode '{mode}', Ottenuto: {result}"
        )
        assert len(result) > 0, (
            f"Atteso lista non vuota per mode '{mode}', Ottenuto: {result}"
        )


def test_suspected_files_alias_legacy():
    sample = {
        "kind": "timeout",
        "snippet": "no path here",
        "error": "timeout",
        "traceback": "niente",
        "mode": "dummy",
    }
    alias_map = {
        "mixed": "mix-am",
        "glm-minimax": "mix-gm",
        "anthropic-glm": "mix-ag",
    }
    for alias, canonical in alias_map.items():
        sample["mode"] = alias
        result = _suspected_files(sample)
        expected = TYPICAL_FILES_BY_MODE[canonical]
        assert result == expected, (
            f"Atteso per alias '{alias}' lo stesso fallback di '{canonical}': {expected}, Ottenuto: {result}"
        )


def test_suspected_files_modalita_ignota():
    sample = {
        "kind": "timeout",
        "snippet": "no path",
        "error": "timeout",
        "traceback": "",
        "mode": "non-esiste",
    }
    result = _suspected_files(sample)
    assert result == [], (
        f"Atteso: [], Ottenuto: {result}"
    )


def test_suspected_files_taglia_a_cinque():
    paths = [
        "src/a.py", "src/b.py", "src/c.py", "src/d.py",
        "src/e.py", "src/f.py", "src/g.py",
    ]
    sample = {
        "kind": "timeout",
        "snippet": "error in " + " ".join(paths),
        "error": "",
        "traceback": "",
        "mode": "anthropic",
    }
    result = _suspected_files(sample)
    assert len(result) == 5, (
        f"Atteso: 5 elementi, Ottenuto: {len(result)}"
    )
    assert result == paths[:5], (
        f"Atteso: {paths[:5]}, Ottenuto: {result}"
    )


def test_make_ticket_id_e_branch_derivano_dalla_signature():
    bug = {
        "kind": "timeout",
        "signature": "timeout after #s",
        "count": 5,
        "sample": {
            "kind": "timeout",
            "snippet": "timeout after 30s",
            "error": "",
            "traceback": "",
            "mode": "anthropic",
        },
        "last_ts": "2026-08-05",
    }
    sig = bug["signature"]
    expected_hash = hashlib.md5(sig.encode()).hexdigest()[:8]
    expected_id = f"timeout-{expected_hash}"
    expected_branch = f"fix/timeout-{expected_hash}"
    result = make_ticket(bug)
    assert result["id"] == expected_id, (
        f"Atteso id '{expected_id}', Ottenuto: '{result['id']}'"
    )
    assert result["branch"] == expected_branch, (
        f"Atteso branch '{expected_branch}', Ottenuto: '{result['branch']}'"
    )
    assert result["status"] == "open", (
        f"Atteso status 'open', Ottenuto: '{result['status']}'"
    )
    assert "suspected_files" in result, (
        "Atteso chiave 'suspected_files' presente nel ticket"
    )


def test_make_ticket_symptom_ricade_su_error():
    bug = {
        "kind": "crash",
        "signature": "segmentation fault",
        "count": 2,
        "sample": {
            "kind": "crash",
            "snippet": "",
            "error": "segmentation fault at 0x7fff5fbff8c0",
            "traceback": "",
            "mode": "minimax",
        },
        "last_ts": "2026-08-03",
    }
    result = make_ticket(bug)
    expected_symptom = "segmentation fault at 0x7fff5fbff8c0"
    assert result["symptom"] == expected_symptom, (
        f"Atteso symptom '{expected_symptom}', Ottenuto: '{result['symptom']}'"
    )
