"""Marcatura delle richieste sintetiche nel sidecar (audit A7) e filtro nel
watcher del self-healing (audit D5).

Il 6,3% delle entry storiche di router-usage.jsonl (1.280 su 20.348 in sette
giorni) veniva da sonde manuali e dalla suite di test, e falsava ogni analisi
per modalita': senza filtro la modalita' minimax risultava al 95,3% di errori
mentre le richieste reali erano 9, con zero errori. Sui fallimenti attribuiti a
MiniMax-M3 la misura del 2026-08-08 e' anche piu' netta: 425 delle 434 fail
erano sonde, e il tasso reale scende dal 68,7% al 4,7%.
"""
import importlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))


def _import_router_utils():
    """Ricarica router_utils: la conftest purga i moduli di src/ da sys.modules."""
    sys.modules.pop("router_utils", None)
    return importlib.import_module("router_utils")


def _import_watcher():
    """Ricarica il watcher, per lo stesso motivo di _import_router_utils."""
    sys.modules.pop("self_healing.watcher", None)
    return importlib.import_module("self_healing.watcher")


def _write_jsonl(path, entries):
    """Scrive una lista di dict come file JSONL."""
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _ultima_entry(sidecar):
    """Rilegge l'ultima riga scritta nel sidecar."""
    righe = sidecar.read_text(encoding="utf-8").strip().split("\n")
    return json.loads(righe[-1])


def _logga(ru, sidecar, **extra):
    """Chiama log_router_usage con gli argomenti minimi realmente richiesti.

    La firma e' (chat_id, orig, final, usage, mode, ...): usage e' un dict di
    contatori, non una stringa di esito.
    """
    ru.log_router_usage(
        chat_id="sid:test",
        orig="claude-haiku-4-5-20251001",
        final="MiniMax-M2.7",
        usage={"input_tokens": 10, "output_tokens": 5, "tool_use_blocks": 2},
        mode="mix-am",
        **extra,
    )
    return _ultima_entry(sidecar)


class TestCampoSynthetic:
    """A7: il campo synthetic finisce nel sidecar solo quando e' vero."""

    @pytest.fixture
    def ambiente(self, tmp_path, monkeypatch):
        """router_utils con il sidecar dirottato in tmp_path.

        USAGE_SIDECAR deve restare un Path: il codice ci chiama .parent.mkdir().
        """
        sidecar = tmp_path / "usage.jsonl"
        ru = _import_router_utils()
        monkeypatch.setattr(ru, "USAGE_SIDECAR", sidecar)
        return ru, sidecar

    def test_synthetic_true_scrive_il_campo(self, ambiente):
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar, synthetic=True)
        assert entry.get("synthetic") is True

    def test_synthetic_false_non_scrive_il_campo(self, ambiente):
        """Requisito di retrocompatibilita': le entry normali restano identiche."""
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar, synthetic=False)
        assert "synthetic" not in entry

    def test_default_e_non_sintetico(self, ambiente):
        """Chi non conosce il parametro continua a scrivere entry normali."""
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar)
        assert "synthetic" not in entry

    def test_campo_in_coda(self, ambiente):
        """I campi nuovi vanno dopo quelli storici: c'e' chi li legge per posizione."""
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar, synthetic=True)
        chiavi = list(entry.keys())
        assert chiavi.index("synthetic") > chiavi.index("tool_use_blocks")

    def test_convive_con_la_latenza(self, ambiente):
        """synthetic e i campi di latenza (A9) non si escludono a vicenda."""
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar, synthetic=True, ttfb_ms=12.34, total_ms=56.78)
        assert entry["synthetic"] is True
        assert entry["ttfb_ms"] == 12.3
        assert entry["total_ms"] == 56.8


class TestHeaderRiconosciuto:
    """A7: contratto di riconoscimento dell'header x-airouter-synthetic.

    La funzione replica la regola di streaming_relay.py. Va tenuta allineata:
    una variazione qui deve riflettersi nel relay e viceversa.
    """

    @staticmethod
    def riconosce(valore):
        return str(valore).strip().lower() in ("1", "true", "yes")

    @pytest.mark.parametrize("valore", ["1", "true", "TRUE", "yes", " 1 ", "Yes"])
    def test_valori_veri(self, valore):
        assert self.riconosce(valore) is True

    @pytest.mark.parametrize(
        "valore", ["", "0", "false", "no", "si", "synthetic", "2", "vero"]
    )
    def test_valori_falsi(self, valore):
        assert self.riconosce(valore) is False


class TestWatcherEsclude:
    """D5: il watcher non impara dai fallimenti delle sonde."""

    CHIAVE = "MiniMax-M3|coding"

    @pytest.fixture
    def learner(self):
        watcher = _import_watcher()
        return watcher, watcher.OutcomeLearner(alpha=0.1, half_life_seconds=200)

    @staticmethod
    def _entry(indice, outcome="ok", synthetic=None):
        e = {
            "final": "MiniMax-M3",
            "task_class": "coding",
            "outcome": outcome,
            "ts": 1700000000 + indice,
        }
        if synthetic is not None:
            e["synthetic"] = synthetic
        return e

    def test_entry_sintetiche_non_contano(self, tmp_path, learner):
        """Tre richieste reali e cinque sonde fallite: contano solo le tre."""
        watcher, ln = learner
        entries = [self._entry(i) for i in range(3)]
        entries += [self._entry(10 + i, "empty", True) for i in range(5)]
        path = tmp_path / "synthetic.jsonl"
        _write_jsonl(path, entries)

        watcher.process_file(str(path), ln, 0)

        stats = ln._stats.get(self.CHIAVE)
        assert stats is not None
        assert stats["total"] == 3
        assert stats["fails"] == 0

    def test_entry_reali_contano_ancora(self, tmp_path, learner):
        """Braccio di controllo: senza il campo, tutte e otto vengono contate.

        Senza questo test, un filtro che scartasse tutto passerebbe comunque.
        """
        watcher, ln = learner
        entries = [self._entry(i) for i in range(3)]
        entries += [self._entry(10 + i, "empty") for i in range(5)]
        path = tmp_path / "all_real.jsonl"
        _write_jsonl(path, entries)

        watcher.process_file(str(path), ln, 0)

        stats = ln._stats.get(self.CHIAVE)
        assert stats is not None
        assert stats["total"] == 8
        assert stats["fails"] == 5

    def test_synthetic_falso_esplicito_conta(self, tmp_path, learner):
        """synthetic=False e' traffico reale, non una sonda."""
        watcher, ln = learner
        entries = [self._entry(i) for i in range(2)]
        entries.append(self._entry(9, "empty", False))
        path = tmp_path / "explicit_false.jsonl"
        _write_jsonl(path, entries)

        watcher.process_file(str(path), ln, 0)

        stats = ln._stats.get(self.CHIAVE)
        assert stats is not None
        assert stats["total"] == 3
        assert stats["fails"] == 1

    def test_offset_avanza_comunque(self, tmp_path, learner):
        """Il continue non deve bloccare l'offset.

        Se lo bloccasse, il watcher rileggerebbe le stesse righe per sempre.
        """
        watcher, ln = learner
        entries = [self._entry(i, "empty", True) for i in range(4)]
        path = tmp_path / "all_synthetic.jsonl"
        _write_jsonl(path, entries)
        dimensione = path.stat().st_size

        offset = watcher.process_file(str(path), ln, 0)

        assert offset == dimensione
        stats = ln._stats.get(self.CHIAVE)
        assert stats is None or stats["total"] == 0
