"""Sonda periodica di disponibilita' (audit A8).

Le modalita' poco usate degradano in silenzio: mix-ag aveva 15 richieste in
sette giorni e l'80% falliva con 429, ma nessuno se ne accorgeva finche' non la
usava.

Due vincoli che questi test difendono:
- la sonda NON manda nulla senza AIROUTER_PROBE_ENABLED=1 (accenderla consuma
  quota su sette provider a pagamento);
- ogni richiesta porta 'x-airouter-synthetic: 1', altrimenti la sonda stessa
  falserebbe le metriche che dovrebbe sorvegliare.
"""
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RADICE / "src"))
sys.path.insert(0, str(RADICE / "sviluppo" / "tools"))

import probe_modes  # noqa: E402


class TestConfigurazione:

    def test_nove_modalita(self):
        assert len(probe_modes.PORTE) == 9

    def test_porte_corrispondono_a_quelle_del_router(self):
        """Le porte devono essere quelle vere, non una copia invecchiata."""
        import router_constants
        attese = dict(router_constants.PORT_MODE)
        for modo, porta in probe_modes.PORTE.items():
            assert attese.get(porta) == modo, (
                f"porta {porta}: la sonda dice {modo}, il router {attese.get(porta)}"
            )

    def test_non_sonda_la_porta_dinamica(self):
        """8787 segue la modalita' corrente: una riga di significato variabile."""
        assert 8787 not in probe_modes.PORTE.values()

    def test_richiesta_minima(self):
        """Il costo di un ciclo deve restare trascurabile a cadenza di 30 min."""
        assert probe_modes.MAX_TOKENS_SONDA <= 16


class TestInterruttore:

    def test_spenta_non_manda_nulla(self, monkeypatch):
        monkeypatch.delenv("AIROUTER_PROBE_ENABLED", raising=False)
        chiamate = []
        monkeypatch.setattr(probe_modes, "sonda",
                            lambda *a, **k: chiamate.append(a) or {})
        assert probe_modes.esegui(scrivi=False) == []
        assert chiamate == [], "la sonda ha contattato le modalita' da spenta"

    def test_valore_diverso_da_uno_resta_spenta(self, monkeypatch):
        monkeypatch.setenv("AIROUTER_PROBE_ENABLED", "true")
        monkeypatch.setattr(probe_modes, "sonda",
                            lambda *a, **k: pytest.fail("non doveva partire"))
        assert probe_modes.esegui(scrivi=False) == []

    def test_accesa_sonda_tutte(self, monkeypatch):
        monkeypatch.setenv("AIROUTER_PROBE_ENABLED", "1")
        visti = []

        def finta(modo, porta, timeout=None):
            visti.append(modo)
            return {"modo": modo, "http": 200, "raggiungibile": True}

        monkeypatch.setattr(probe_modes, "sonda", finta)
        esiti = probe_modes.esegui(scrivi=False)
        assert len(esiti) == 9
        assert sorted(visti) == sorted(probe_modes.PORTE)

    def test_force_ignora_l_interruttore(self, monkeypatch):
        monkeypatch.delenv("AIROUTER_PROBE_ENABLED", raising=False)
        monkeypatch.setattr(probe_modes, "sonda",
                            lambda m, p, timeout=None: {"modo": m})
        assert len(probe_modes.esegui(forza=True, scrivi=False)) == 9

    def test_selezione_di_una_sola_modalita(self, monkeypatch):
        monkeypatch.setattr(probe_modes, "sonda",
                            lambda m, p, timeout=None: {"modo": m})
        esiti = probe_modes.esegui(modi=["local"], forza=True, scrivi=False)
        assert [e["modo"] for e in esiti] == ["local"]


class TestMarcaturaSintetica:
    """Senza l'header la sonda sarebbe l'ennesima fonte di contaminazione."""

    def test_header_presente_nella_richiesta(self, monkeypatch):
        catturate = []

        class FintaRisposta:
            status = 200
            headers = {"Content-Encoding": "identity", "x-ai-actual-model": "x"}

            def read(self):
                return b'{"id":"msg_1","model":"m"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def finta_urlopen(richiesta, timeout=None):
            catturate.append(dict(richiesta.headers))
            return FintaRisposta()

        monkeypatch.setattr(probe_modes.urllib.request, "urlopen", finta_urlopen)
        probe_modes.sonda("local", 8779)

        assert catturate, "nessuna richiesta effettuata"
        # urllib normalizza i nomi degli header in Capitalized-Case.
        chiavi = {k.lower() for k in catturate[0]}
        assert "x-airouter-synthetic" in chiavi
        valore = next(v for k, v in catturate[0].items()
                      if k.lower() == "x-airouter-synthetic")
        assert str(valore) == "1"


class TestEsito:

    def test_stati_raggiungibili(self):
        """401 e 403 significano catena viva, non modalita' giu'.

        Succede sulla modalita' anthropic, l'unica che valida la chiave del
        CLIENT: la sonda non ha un token OAuth e non deve averlo. Trattarli
        come indisponibilita' darebbe un allarme falso ogni mezz'ora.
        """
        assert probe_modes._e_raggiungibile(200)
        assert probe_modes._e_raggiungibile(401)
        assert probe_modes._e_raggiungibile(403)

    @pytest.mark.parametrize("stato", [0, 429, 500, 502, 503])
    def test_stati_non_raggiungibili(self, stato):
        assert not probe_modes._e_raggiungibile(stato)

    def test_errore_di_rete_non_solleva(self, monkeypatch):
        def esplode(*a, **k):
            raise OSError("connessione rifiutata")

        monkeypatch.setattr(probe_modes.urllib.request, "urlopen", esplode)
        esito = probe_modes.sonda("local", 8779)
        assert esito["http"] == 0
        assert esito["raggiungibile"] is False
        assert "OSError" in esito["errore"]

    def test_corpo_gzip_viene_letto(self):
        """glm, mix-ag e qwen rispondono compresso anche senza negoziazione."""
        import gzip
        esito = {"errore": ""}
        probe_modes._analizza_corpo(
            esito, gzip.compress(b'{"id":"msg_abc","model":"glm-4.7"}')
        )
        assert esito["id_prefisso"] == "msg_"
        assert esito["modello_dichiarato"] == "glm-4.7"

    def test_corpo_illeggibile_e_un_dato_non_un_errore(self):
        esito = {"errore": ""}
        probe_modes._analizza_corpo(esito, b"\x00\x01 non json")
        assert esito["errore"], "il fatto va registrato"

    def test_id_senza_prefisso(self):
        """MiniMax usa un esadecimale nudo: e' un terzo formato, oltre a
        msg_ (Anthropic/GLM/Qwen) e resp_ (LiteLLM locale)."""
        esito = {"errore": ""}
        probe_modes._analizza_corpo(esito, b'{"id":"06c60ca0deadbeef","model":"MiniMax-M2.7"}')
        assert esito["id_prefisso"] == "06c60ca0"
