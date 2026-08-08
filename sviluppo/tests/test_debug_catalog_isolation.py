"""
Suite di test per il modulo debug_catalog.py.

Scritto il 2026-08-06 per proteggere l'isolamento del BUG-CATALOG di produzione.

PROBLEMA ORIGINALE: la precedente suite di test sovrascriveva il campo
example_snippet delle entry reali nel catalogo di produzione. Ad esempio,
un tool_isolation_strip che in produzione riportava "kept=72/73" veniva
sostituito con "kept=0/1" contenente esempi sintetici dei test.

CAUSA REALE: il percorso del catalogo era ancorato alla root del repo
(Path(__file__).parent.parent / "logs"), senza alcun modo di spostarlo. Qualunque
test che importasse il modulo scriveva quindi nel catalogo di produzione. Non
c'entrava sys.modules: il reimport serve a QUESTI test, perche' il modulo legge
AIROUTER_CATALOG_PATH una sola volta all'import e senza scaricarlo la copia gia'
caricata restituirebbe il percorso vecchio, rendendo il test un falso verde.

Ogni test in questa suite:
- Salva e ripristina l'ambiente con monkeypatch
- Rimuove debug_catalog da sys.modules prima del reimport
- Verifica che i test non tocchino mai logs/BUG-CATALOG.jsonl di produzione
"""

import sys
sys.path.insert(0, 'src')

import importlib
from pathlib import Path

import pytest


def reimporta_modulo():
    """
    Rimuove debug_catalog da sys.modules e lo reimporta.

    Necessario per testare entrambi i comportamenti (con e senza override
    della variabile d'ambiente), poiche' il modulo legge l'ambiente
    una sola volta all'import.
    """
    if 'debug_catalog' in sys.modules:
        del sys.modules['debug_catalog']
    return importlib.import_module('debug_catalog')


@pytest.fixture(autouse=True)
def _scarica_modulo_dopo_ogni_test():
    """Toglie debug_catalog da sys.modules a fine test.

    Senza questo, dopo `test_senza_override_usa_logs_del_repo` il modulo
    resterebbe caricato con il percorso di PRODUZIONE: monkeypatch ripristina
    l'ambiente ma non reimporta nulla, e il primo test successivo che chiamasse
    record_event scriverebbe nel catalogo reale. Esattamente il bug che questa
    suite deve impedire.
    """
    yield
    sys.modules.pop("debug_catalog", None)


class TestIsolamentoBugCatalog:
    """Test per garantire l'isolamento del catalogo di produzione."""

    def test_override_dirotta_il_percorso(self, monkeypatch, tmp_path):
        """
        Verifica che con AIROUTER_CATALOG_PATH impostata, il percorso
        del catalogo venga dirottato esattamente sul valore della variabile.
        """
        percorso_atteso = tmp_path / "catalogo_test.jsonl"
        monkeypatch.setenv("AIROUTER_CATALOG_PATH", str(percorso_atteso))

        modulo = reimporta_modulo()

        assert modulo.CATALOG_JSONL == percorso_atteso

    def test_senza_override_usa_logs_del_repo(self, monkeypatch):
        """
        Verifica che senza override, il percorso termini con
        logs/BUG-CATALOG.jsonl nella root del repo.

        Non verifica la root assoluta perche' dipende da dove gira il test.
        """
        monkeypatch.delenv("AIROUTER_CATALOG_PATH", raising=False)

        modulo = reimporta_modulo()

        percorso = Path(modulo.CATALOG_JSONL)
        assert percorso.parent.name == "logs"
        assert percorso.name == "BUG-CATALOG.jsonl"

    def test_record_event_scrive_nel_percorso_override(self, monkeypatch, tmp_path):
        """
        Verifica che con override attivo, record_event scriva SOLO nel
        percorso di override e NON in quello di produzione.

        Questo e' il test critico per l'isolamento: dimostra che i test
        non inquinano il catalogo reale.
        """
        percorso_override = tmp_path / "bug_catalog_test.jsonl"
        monkeypatch.setenv("AIROUTER_CATALOG_PATH", str(percorso_override))

        modulo = reimporta_modulo()

        # Salva dimensione attuale del catalogo di produzione
        # Ancorato alla root del repo (questo file sta in sviluppo/tests/):
        # con un percorso relativo al cwd il confronto diventerebbe 0 == 0,
        # cioe' un falso verde quando pytest gira da un'altra directory.
        radice_repo = Path(__file__).resolve().parents[2]
        percorso_produzione = radice_repo / "logs" / "BUG-CATALOG.jsonl"
        dimensione_prima = percorso_produzione.stat().st_size if percorso_produzione.exists() else 0

        # Chiama record_event con dati sintetici
        modulo.record_event(
            severity="info",
            category="test",
            kind="prova_isolamento",
            snippet="esempio sintetico"
        )

        # Verifica che il file di override esista e contenga la stringa cercata
        assert percorso_override.exists(), "Il file di override non e' stato creato"
        contenuto = percorso_override.read_text()
        assert "prova_isolamento" in contenuto, "La stringa non e' stata scritta nel file di override"

        # Verifica che il catalogo di produzione NON sia stato toccato
        dimensione_dopo = percorso_produzione.stat().st_size if percorso_produzione.exists() else 0
        assert dimensione_prima == dimensione_dopo, (
            f"La dimensione del catalogo di produzione e' cambiata: "
            f"prima={dimensione_prima}, dopo={dimensione_dopo}"
        )

    def test_directory_annidata_viene_creata(self, monkeypatch, tmp_path):
        """
        Verifica che se il percorso di override contiene directory non esistenti,
        queste vengano create automaticamente (parents=True).
        """
        # Crea percorso con due livelli di directory non esistenti
        percorso_annidato = tmp_path / "anni" / "2026" / "catalogo.jsonl"
        monkeypatch.setenv("AIROUTER_CATALOG_PATH", str(percorso_annidato))

        # Verifica che le directory non esistano ancora
        assert not percorso_annidato.parent.exists()

        modulo = reimporta_modulo()

        # Verifica che la directory padre sia stata creata
        assert percorso_annidato.parent.exists(), (
            "La directory padre non e' stata creata"
        )
        # E che il modulo punti davvero li': senza questo il test passerebbe
        # anche se la directory fosse stata creata da qualcun altro.
        assert modulo.CATALOG_JSONL == percorso_annidato


# ── last_mode: attribuzione dell'ultima occorrenza (audit D6, 2026-08-08) ──────

def _catalogo_isolato(tmp_path, monkeypatch):
    """Modulo debug_catalog con il catalogo dirottato in tmp_path.

    Serve l'override PRIMA del reimport: il percorso viene risolto all'import
    del modulo, non a ogni chiamata.
    """
    monkeypatch.setenv("AIROUTER_CATALOG_PATH", str(tmp_path / "catalogo.jsonl"))
    return reimporta_modulo()


def _sola_entry(modulo):
    """Ritorna l'unica entry del catalogo isolato."""
    catalogo = modulo._load_catalog()
    assert len(catalogo) == 1, f"attesa una sola firma, trovate {len(catalogo)}"
    return next(iter(catalogo.values()))

def test_last_mode_su_entry_nuova(tmp_path, monkeypatch):
    """Una firma nuova nasce con la modalita' che l'ha prodotta."""
    dc = _catalogo_isolato(tmp_path, monkeypatch)
    dc.record_event(severity="error", category="mix-am", kind="relay_error_502",
                    snippet="boom")
    entry = _sola_entry(dc)
    assert entry["last_mode"] == "mix-am"
    assert entry["categories"] == ["mix-am"]


def test_modalita_diverse_danno_firme_diverse(tmp_path, monkeypatch):
    """La modalita' entra nella firma: due modalita' = due entry distinte.

    Verificato il 2026-08-08: _signature(category, kind, ...) include la
    categoria, quindi `categories` non e' mai cumulativa e l'attribuzione a una
    modalita' era gia' univoca. last_mode non aggiunge informazione, la rende
    scalare.
    """
    dc = _catalogo_isolato(tmp_path, monkeypatch)
    dc.record_event(severity="error", category="mix-am", kind="k", snippet="s")
    dc.record_event(severity="error", category="qwen", kind="k", snippet="s")
    catalogo = dc._load_catalog()
    assert len(catalogo) == 2
    modi = sorted(e["last_mode"] for e in catalogo.values())
    assert modi == ["mix-am", "qwen"]
    for entry in catalogo.values():
        assert entry["categories"] == [entry["last_mode"]]


def test_last_mode_resta_allineato_sulle_occorrenze_successive(tmp_path, monkeypatch):
    """Il ramo di aggiornamento riscrive last_mode, non lo lascia al primo valore."""
    dc = _catalogo_isolato(tmp_path, monkeypatch)
    for _ in range(3):
        dc.record_event(severity="error", category="mix-am", kind="k", snippet="s")
    entry = _sola_entry(dc)
    assert entry["last_mode"] == "mix-am"
    assert entry["count"] == 3
