"""
Characterization test per il comportamento asimmetrico di categorizzazione mode.

Il modulo router_debug categorizza i mode per debug-events.jsonl (mix-* -> "mix"),
il modulo debug_catalog.record_event() scrive last_mode con la category passata.
Questa asimmetria e' design deliberato (aggregazione vs debug puntuale).

Anti-regressione: verificare che il comportamento resti quello documentato.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import importlib


def reimport_debug_catalog(monkeypatch, tmp_path):
    """Reimporta debug_catalog con AIROUTER_CATALOG_PATH dirottato su tmpdir."""
    monkeypatch.setenv("AIROUTER_CATALOG_PATH", str(tmp_path / "catalog.jsonl"))
    if "debug_catalog" in sys.modules:
        del sys.modules["debug_catalog"]
    return importlib.import_module("debug_catalog")


def reimport_router_debug():
    """Reimporta router_debug per resettare stato singleton."""
    if "router_debug" in sys.modules:
        del sys.modules["router_debug"]
    return importlib.import_module("router_debug")


class TestCategoryForMode:
    """Test _category_for_mode() in router_debug.py."""

    def test_category_for_mode_mix_am_2_collapses_to_mix(self):
        """mix-am-2 collassa su 'mix' per aggregazione debug-events."""
        router_debug = reimport_router_debug()
        assert router_debug._category_for_mode("mix-am-2") == "mix"

    def test_category_for_mode_mix_am_also_collapses(self):
        """mix-am collassa su 'mix' (coerenza con -2)."""
        router_debug = reimport_router_debug()
        assert router_debug._category_for_mode("mix-am") == "mix"

    def test_category_for_mode_anthropic_unchanged(self):
        """anthropic resta 'anthropic' (mode pura)."""
        router_debug = reimport_router_debug()
        assert router_debug._category_for_mode("anthropic") == "anthropic"

    def test_category_for_mode_empty_returns_unknown(self):
        """Mode vuota o None ritorna 'unknown'."""
        router_debug = reimport_router_debug()
        assert router_debug._category_for_mode("") == "unknown"
        assert router_debug._category_for_mode(None) == "unknown"

    def test_category_for_mode_minimax_unchanged(self):
        """minimax resta 'minimax' (mode pura)."""
        router_debug = reimport_router_debug()
        assert router_debug._category_for_mode("minimax") == "minimax"

    def test_category_for_mode_glm_unchanged(self):
        """glm resta 'glm' (mode pura)."""
        router_debug = reimport_router_debug()
        assert router_debug._category_for_mode("glm") == "glm"


class TestRecordEventModeHandling:
    """Test che record_event() gestisce correttamente category nel catalogo."""

    def test_record_event_with_specific_mix_mode(self, monkeypatch, tmp_path):
        """record_event scrive last_mode=<category> nel record."""
        debug_catalog = reimport_debug_catalog(monkeypatch, tmp_path)

        debug_catalog.record_event(
            category="mix-am-2",
            severity="info",
            kind="test_mix_am_2",
            snippet="test mix-am-2 specific",
            chat_fp="fp:test",
        )

        catalog_path = Path(tmp_path) / "catalog.jsonl"
        assert catalog_path.exists()

        with catalog_path.open() as f:
            record = json.loads(f.readline())

        assert record["last_mode"] == "mix-am-2"
        assert "mix-am-2" in record["categories"]

    def test_record_event_with_aggregated_mix_category(self, monkeypatch, tmp_path):
        """record_event con category='mix' (aggregato) scrive last_mode='mix'."""
        debug_catalog = reimport_debug_catalog(monkeypatch, tmp_path)

        debug_catalog.record_event(
            category="mix",
            severity="info",
            kind="test_aggregated",
            snippet="test aggregated mix",
            chat_fp="fp:test2",
        )

        catalog_path = Path(tmp_path) / "catalog.jsonl"

        with catalog_path.open() as f:
            record = json.loads(f.readline())

        assert record["last_mode"] == "mix"
        assert "mix" in record["categories"]
