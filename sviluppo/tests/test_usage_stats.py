"""Percentili di latenza per modalita' (audit D6) e contatore policy (D4).

Le rotte /debug/ esistevano ma non rispondevano alla domanda piu' semplice:
«questa richiesta quanto e' durata?». Da quando il sidecar registra ttfb_ms e
total_ms (A9) i dati ci sono; qui si verifica che l'aggregazione li legga
correttamente, escluda le sonde e non si faccia ingannare da un file enorme.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import usage_stats  # noqa: E402

ADESSO = 1786000000.0


def _scrivi(percorso, entries):
    percorso.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
    )
    return percorso


def _entry(mode="mix-am", ttfb=100.0, total=200.0, ts=ADESSO, **extra):
    e = {"ts": ts, "mode": mode, "final": "MiniMax-M2.7", "status": 200}
    if ttfb is not None:
        e["ttfb_ms"] = ttfb
    if total is not None:
        e["total_ms"] = total
    e.update(extra)
    return e


class TestPercentile:
    """La funzione di percentile e' scritta a mano: va verificata a mano."""

    def test_lista_vuota(self):
        assert usage_stats._percentile([], 50) is None

    def test_valore_unico(self):
        assert usage_stats._percentile([42.0], 99) == 42.0

    def test_mediana_di_dieci_valori(self):
        valori = [float(v) for v in range(1, 11)]  # 1..10
        # Rango piu' vicino: indice round(0.5 * 9) = 4 (0-based) -> il quinto
        assert usage_stats._percentile(valori, 50) == 5.0

    def test_p99_su_cento_valori(self):
        """Col rango piu' vicino il p99 di 1..100 e' 99, non 100.

        Indice = round(0.99 * (100-1)) = 98, cioe' il novantanovesimo valore.
        Il massimo lo restituisce solo il p100. E' il comportamento voluto:
        un percentile che coincide sempre col massimo nasconderebbe la coda.
        """
        valori = [float(v) for v in range(1, 101)]
        assert usage_stats._percentile(valori, 99) == 99.0
        assert usage_stats._percentile(valori, 100) == 100.0

    def test_p50_minore_o_uguale_a_p90(self):
        valori = sorted([3.0, 1.0, 9.0, 4.0, 7.0, 2.0, 8.0])
        p50 = usage_stats._percentile(valori, 50)
        p90 = usage_stats._percentile(valori, 90)
        assert p50 <= p90


class TestLatenzaPerModalita:

    def test_file_assente_non_solleva(self, tmp_path):
        r = usage_stats.latenza_per_modalita(tmp_path / "manca.jsonl")
        assert r["modalita"] == {}
        assert r["campioni"] == 0

    def test_aggrega_per_modalita(self, tmp_path):
        f = _scrivi(tmp_path / "u.jsonl", [
            _entry("mix-am", 100.0, 1000.0),
            _entry("mix-am", 200.0, 2000.0),
            _entry("qwen", 50.0, 500.0),
        ])
        r = usage_stats.latenza_per_modalita(f, adesso=ADESSO)
        assert set(r["modalita"]) == {"mix-am", "qwen"}
        assert r["modalita"]["mix-am"]["n"] == 2
        assert r["modalita"]["qwen"]["n"] == 1

    def test_esclude_le_sintetiche(self, tmp_path):
        """Il motivo per cui esiste il campo synthetic (A7/D5)."""
        f = _scrivi(tmp_path / "u.jsonl", [
            _entry("mix-am", 100.0, 100.0),
            _entry("mix-am", 99999.0, 99999.0, synthetic=True),
        ])
        r = usage_stats.latenza_per_modalita(f, adesso=ADESSO)
        assert r["modalita"]["mix-am"]["n"] == 1
        assert r["modalita"]["mix-am"]["total_ms"]["p99"] == 100.0
        assert r["scartate_sintetiche"] == 1

    def test_puo_includerle_su_richiesta(self, tmp_path):
        """Braccio di controllo: senza il filtro la sonda rientra."""
        f = _scrivi(tmp_path / "u.jsonl", [
            _entry("mix-am", 100.0, 100.0),
            _entry("mix-am", 99999.0, 99999.0, synthetic=True),
        ])
        r = usage_stats.latenza_per_modalita(
            f, adesso=ADESSO, includi_sintetiche=True
        )
        assert r["modalita"]["mix-am"]["n"] == 2

    def test_finestra_temporale(self, tmp_path):
        f = _scrivi(tmp_path / "u.jsonl", [
            _entry("mix-am", 100.0, 100.0, ts=ADESSO),
            _entry("mix-am", 100.0, 100.0, ts=ADESSO - 200000),  # oltre 24h
        ])
        r = usage_stats.latenza_per_modalita(f, finestra_s=86400, adesso=ADESSO)
        assert r["modalita"]["mix-am"]["n"] == 1

    def test_entry_senza_latenza_non_creano_modalita(self, tmp_path):
        """Il traffico anteriore ad A9 non ha i campi: non deve comparire."""
        f = _scrivi(tmp_path / "u.jsonl", [
            _entry("glm", ttfb=None, total=None),
            _entry("mix-am", 10.0, 20.0),
        ])
        r = usage_stats.latenza_per_modalita(f, adesso=ADESSO)
        assert "glm" not in r["modalita"]
        assert "mix-am" in r["modalita"]

    def test_righe_malformate_ignorate(self, tmp_path):
        f = tmp_path / "u.jsonl"
        f.write_text(
            json.dumps(_entry("mix-am", 10.0, 20.0)) + "\n"
            + "non-json{{{\n\n"
            + json.dumps(_entry("mix-am", 30.0, 40.0)) + "\n",
            encoding="utf-8",
        )
        r = usage_stats.latenza_per_modalita(f, adesso=ADESSO)
        assert r["modalita"]["mix-am"]["n"] == 2

    def test_legge_solo_la_coda(self, tmp_path):
        """Su un file grande si leggono solo gli ultimi max_bytes.

        Il sidecar reale supera i 45 MB e l'endpoint deve restare istantaneo.
        """
        vecchie = [_entry("vecchia", 1.0, 1.0) for _ in range(2000)]
        nuove = [_entry("nuova", 2.0, 2.0) for _ in range(5)]
        f = _scrivi(tmp_path / "u.jsonl", vecchie + nuove)
        dimensione = f.stat().st_size

        r = usage_stats.latenza_per_modalita(f, adesso=ADESSO, max_bytes=1500)

        assert dimensione > 100_000, "il file di prova deve essere grande"
        assert "nuova" in r["modalita"], "le entry recenti devono esserci tutte"
        assert r["modalita"]["nuova"]["n"] == 5
        # Qualche entry vecchia rientra nella finestra di byte: e' normale. Il
        # punto e' che siano poche, non che siano zero — altrimenti il test
        # dipenderebbe dalla lunghezza esatta di una riga JSON.
        lette_vecchie = r["modalita"].get("vecchia", {}).get("n", 0)
        assert lette_vecchie < 50, f"lette troppe entry vecchie: {lette_vecchie}"

    def test_prima_riga_tagliata_non_rompe(self, tmp_path):
        """Leggendo dal mezzo, la prima riga e' quasi sempre monca."""
        f = _scrivi(tmp_path / "u.jsonl",
                    [_entry("mix-am", float(i), float(i)) for i in range(1, 200)])
        r = usage_stats.latenza_per_modalita(f, adesso=ADESSO, max_bytes=500)
        assert r["modalita"]["mix-am"]["n"] >= 1

    def test_percentili_ordinati(self, tmp_path):
        f = _scrivi(tmp_path / "u.jsonl", [
            _entry("mix-am", float(i * 10), float(i * 100)) for i in range(1, 101)
        ])
        m = usage_stats.latenza_per_modalita(f, adesso=ADESSO)["modalita"]["mix-am"]
        assert m["total_ms"]["p50"] <= m["total_ms"]["p90"] <= m["total_ms"]["p99"]
        assert m["ttfb_ms"]["p50"] <= m["ttfb_ms"]["p90"] <= m["ttfb_ms"]["p99"]


class TestContatorePolicy:
    """D4: l'ACTUATOR logga soltanto, ma ora si conta quanto devierebbe."""

    def _logger(self):
        sys.modules.pop("router_debug", None)
        import importlib
        rd = importlib.import_module("router_debug")
        return rd.DebugLogger()

    def test_conta_per_chiave(self):
        dl = self._logger()
        dl.note_policy_degraded("MiniMax-M3", "coding", "mix-am")
        dl.note_policy_degraded("MiniMax-M3", "coding", "mix-am")
        dl.note_policy_degraded("MiniMax-M3", "chat", "mix-am")
        assert dl._policy_degraded["MiniMax-M3|coding|mix-am"] == 2
        assert dl._policy_degraded["MiniMax-M3|chat|mix-am"] == 1

    def test_parte_vuoto(self):
        assert self._logger()._policy_degraded == {}

    def test_modalita_diverse_non_si_sommano(self):
        """Sommare due firme perche' compaiono vicine e' correlazione."""
        dl = self._logger()
        dl.note_policy_degraded("MiniMax-M3", "coding", "mix-am")
        dl.note_policy_degraded("MiniMax-M3", "coding", "mix-gm")
        assert len(dl._policy_degraded) == 2
