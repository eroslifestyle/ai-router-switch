import hashlib
import json
import sys

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


# ── Fingerprint dei ticket (audit D2, 2026-08-08) ─────────────────────────────
# I tre ticket in ~/.claude/self-fix/ avevano tutti il suffisso d41d8cd9, che e'
# md5("")[:8]: la signature era sempre vuota perche' si cercava il testo
# dell'errore in "snippet"/"error", campi che le entry reali non hanno mai.

MD5_STRINGA_VUOTA = hashlib.md5(b"").hexdigest()[:8]


def _bug(kind, sample, count=5):
    """Costruisce un bug come lo produrrebbe recurring_bugs."""
    from self_healing.auto_fixer import _signature_entry
    return {"kind": kind, "signature": _signature_entry(sample),
            "count": count, "sample": sample, "last_ts": "2026-08-08T00:00:00Z"}


def test_entry_reale_non_produce_md5_della_stringa_vuota():
    """Il caso che ha generato i tre ticket ambigui: testo solo in upstream_error."""
    sample = {
        "kind": "relay_error_404", "mode": "mix-am", "path": "/v1/messages",
        "stage": "relay", "status": "404", "upstream_status": "404",
        "upstream_error": '{"type":"error","error":{"type":"not_found_error"}}',
    }
    ticket = make_ticket(_bug("relay_error_404", sample))
    assert not ticket["id"].endswith(MD5_STRINGA_VUOTA), (
        f"Il ticket ha di nuovo l'md5 del nulla: {ticket['id']}"
    )


def test_bug_diversi_producono_ticket_diversi():
    """Due errori distinti dello stesso kind non devono collassare.

    Se collassassero, il secondo sovrascriverebbe storia e tentativi del primo.
    """
    base = {"kind": "relay_error_502", "mode": "mix-am", "path": "/v1/messages",
            "stage": "relay", "status": "502"}
    uno = dict(base, upstream_error="upstream connection reset by peer")
    due = dict(base, upstream_error="gateway timeout waiting for backend")
    t1 = make_ticket(_bug("relay_error_502", uno))
    t2 = make_ticket(_bug("relay_error_502", due))
    assert t1["id"] != t2["id"], f"Stesso id per bug diversi: {t1['id']}"


def test_entry_senza_alcun_testo_resta_discriminata():
    """Senza testo dell'errore si ripiega sui campi della richiesta."""
    uno = {"kind": "x", "mode": "mix-am", "path": "/v1/messages", "stage": "relay"}
    due = {"kind": "x", "mode": "qwen", "path": "/v1/messages", "stage": "relay"}
    t1 = make_ticket(_bug("x", uno))
    t2 = make_ticket(_bug("x", due))
    assert t1["id"] != t2["id"], "Modalita' diverse devono dare ticket diversi"
    assert not t1["id"].endswith(MD5_STRINGA_VUOTA)


def test_signature_vuota_esplicita_non_passa():
    """Anche se un chiamante passa una signature vuota, la guardia interviene."""
    sample = {"kind": "y", "mode": "glm", "upstream_error": "boom"}
    bug = {"kind": "y", "signature": "", "count": 3, "sample": sample,
           "last_ts": "2026-08-08T00:00:00Z"}
    ticket = make_ticket(bug)
    assert not ticket["id"].endswith(MD5_STRINGA_VUOTA), (
        f"La guardia non ha funzionato: {ticket['id']}"
    )


def test_il_sintomo_non_e_piu_vuoto():
    """Il campo symptom cercava anch'esso solo snippet/error."""
    sample = {"kind": "z", "mode": "mix-am",
              "upstream_error": "context length exceeded"}
    ticket = make_ticket(_bug("z", sample))
    assert ticket["symptom"] == "context length exceeded"


def test_snippet_ha_ancora_la_precedenza():
    """I cataloghi che usano snippet continuano a funzionare come prima."""
    sample = {"kind": "w", "snippet": "timeout after 30s",
              "upstream_error": "altro testo", "mode": "anthropic"}
    ticket = make_ticket(_bug("w", sample))
    assert ticket["symptom"] == "timeout after 30s"


def test_raggruppamento_su_entry_reali(tmp_path):
    """Entry con lo stesso errore si raggruppano, entry diverse no."""
    percorso = tmp_path / "catalogo.jsonl"
    entries = (
        [{"kind": "relay_error_502", "mode": "mix-am", "stage": "relay",
          "upstream_error": f"connection reset after {i}ms", "ts": f"2026-08-0{i%9+1}"}
         for i in range(4)]
        + [{"kind": "relay_error_502", "mode": "mix-am", "stage": "relay",
            "upstream_error": "gateway timeout", "ts": "2026-08-08"}
           for _ in range(3)]
    )
    percorso.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    bugs = recurring_bugs(load_bug_catalog(percorso), min_count=3)
    # Le prime quattro condividono la signature: i numeri diventano '#'.
    assert len(bugs) == 2, f"Attesi 2 gruppi, ottenuti {len(bugs)}"
    ids = {make_ticket(b)["id"] for b in bugs}
    assert len(ids) == 2, f"I due gruppi devono dare id diversi: {ids}"
