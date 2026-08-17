"""Propagazione del flag sintetico nel catalogo bug/block/error.

Quando streaming_relay.py chiama dl.capture(..., synthetic=True), il sidecar
marca l'entry come sintetica. Il catalogo aggregato BUG-CATALOG.jsonl deve
distinguere quante occorrenze della stessa firma vengono da sonde rispetto al
traffico reale, mantenendo la retrocompatibilità: il campo synthetic_count
va scritto SOLO quando è maggiore di zero, esattamente come il campo synthetic
del sidecar (vedi router_utils.py, regola YAGNI).
"""
import importlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))


def _import_debug_catalog():
    """Ricarica debug_catalog: la conftest purga i moduli di src/ da sys.modules."""
    sys.modules.pop("debug_catalog", None)
    return importlib.import_module("debug_catalog")


def _ultima_entry_catalogo(catalogo_path):
    """Rilegge l'ultima riga scritta nel catalogo."""
    righe = catalogo_path.read_text(encoding="utf-8").strip().split("\n")
    return json.loads(righe[-1]) if righe else None


def _registra_evento(dc, **kwargs):
    """Chiama record_event con argomenti minimi obbligatori."""
    defaults = {
        "severity": "error",
        "category": "test-mode",
        "kind": "test_kind",
        "chat_fp": "fp:test",
        "code": 123,
        "snippet": "test snippet",
        "detail": {},
    }
    defaults.update(kwargs)
    return dc.record_event(**defaults)


class TestSyntheticNelCatalogo:
    """Campo synthetic_count nel catalogo BUG-CATALOG.jsonl."""

    @pytest.fixture
    def ambiente_catalogo(self, tmp_path, monkeypatch):
        """debug_catalog con il catalogo dirottato in tmp_path."""
        catalogo = tmp_path / "catalog.jsonl"
        dc = _import_debug_catalog()
        monkeypatch.setattr(dc, "CATALOG_JSONL", catalogo)
        # Azzera la cache dopo il monkeypatch
        dc._catalog_cache["data"] = None
        return dc, catalogo

    def test_synthetic_true_scrive_synthetic_count(self, ambiente_catalogo):
        """Una entry nuova con synthetic=True crea synthetic_count=1."""
        dc, catalogo = ambiente_catalogo
        _registra_evento(dc, synthetic=True)
        entry = _ultima_entry_catalogo(catalogo)
        assert entry.get("synthetic_count") == 1

    def test_synthetic_false_non_scrive_synthetic_count(self, ambiente_catalogo):
        """Retrocompatibilità: synthetic=False NON scrive il campo."""
        dc, catalogo = ambiente_catalogo
        _registra_evento(dc, synthetic=False)
        entry = _ultima_entry_catalogo(catalogo)
        assert "synthetic_count" not in entry

    def test_default_non_scrive_synthetic_count(self, ambiente_catalogo):
        """Chi non passa synthetic (default False) non scrive il campo."""
        dc, catalogo = ambiente_catalogo
        _registra_evento(dc)
        entry = _ultima_entry_catalogo(catalogo)
        assert "synthetic_count" not in entry

    def test_due_sintetiche_stessa_firma_incrementa_count(self, ambiente_catalogo):
        """Due event uguali con synthetic=True: count=2, synthetic_count=2."""
        dc, catalogo = ambiente_catalogo
        _registra_evento(dc, synthetic=True)
        _registra_evento(dc, synthetic=True)
        entry = _ultima_entry_catalogo(catalogo)
        assert entry["count"] == 2
        assert entry["synthetic_count"] == 2

    def test_una_sintetica_e_una_reale_stessa_firma(self, ambiente_catalogo):
        """Una sintetica + una reale: count=2, synthetic_count=1."""
        dc, catalogo = ambiente_catalogo
        _registra_evento(dc, synthetic=True)
        _registra_evento(dc, synthetic=False)
        entry = _ultima_entry_catalogo(catalogo)
        assert entry["count"] == 2
        assert entry["synthetic_count"] == 1

    def test_una_reale_e_una_sintetica_ordine_inverso(self, ambiente_catalogo):
        """Ordine inverso: reale prima, sintetica dopo: count=2, synthetic_count=1."""
        dc, catalogo = ambiente_catalogo
        _registra_evento(dc, synthetic=False)
        _registra_evento(dc, synthetic=True)
        entry = _ultima_entry_catalogo(catalogo)
        assert entry["count"] == 2
        assert entry["synthetic_count"] == 1

    def test_braccio_di_controllo_solo_reale(self, ambiente_catalogo):
        """Braccio di controllo: solo traffico reale NON ha synthetic_count."""
        dc, catalogo = ambiente_catalogo
        _registra_evento(dc, synthetic=False)
        _registra_evento(dc, synthetic=False)
        entry = _ultima_entry_catalogo(catalogo)
        assert entry["count"] == 2
        assert "synthetic_count" not in entry

    def test_campo_non_zero_solo_quando_necessario(self, ambiente_catalogo):
        """synthetic_count va scritto solo se > 0 (retrocompatibilità YAGNI)."""
        dc, catalogo = ambiente_catalogo
        # Firma 1: tre reali, zero sintetiche
        _registra_evento(dc, kind="kind1", synthetic=False)
        _registra_evento(dc, kind="kind1", synthetic=False)
        _registra_evento(dc, kind="kind1", synthetic=False)
        entry1 = _ultima_entry_catalogo(catalogo)
        assert "synthetic_count" not in entry1
        # Firma 2: due reali, due sintetiche
        _registra_evento(dc, kind="kind2", synthetic=False)
        _registra_evento(dc, kind="kind2", synthetic=True)
        _registra_evento(dc, kind="kind2", synthetic=True)
        entry2 = _ultima_entry_catalogo(catalogo)
        assert entry2.get("synthetic_count") == 2
