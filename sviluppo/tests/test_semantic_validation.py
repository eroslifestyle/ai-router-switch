"""Validazione semantica in sola osservazione (audit A2).

Copre i quattro casi misurati dall'audit del 2026-08-08, che ottenevano tutti
200 e una generazione a pagamento:

    model = "modello-inesistente-xyz"  -> 200 in 5,77 s, 519 token fatturati
    model = "../../etc/passwd"         -> 200 in 8,52 s
    max_tokens = -5                    -> 200 in 5,30 s
    role = "hacker"                    -> 200 in 9,88 s

Il vincolo di questo lavoro e' che il comportamento visto dal client resti
IDENTICO: si logga soltanto. I test che seguono verificano tanto il
rilevamento quanto il fatto che i modelli legittimi delle nove modalita' non
vengano segnalati — un falso positivo qui diventerebbe, il giorno in cui il
rifiuto venisse attivato, un blocco di traffico buono.
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import semantic_validation as sv  # noqa: E402


def _corpo(model="claude-haiku-4-5-20251001", max_tokens=1024, role="user"):
    return {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": role, "content": "ciao"}]}


class TestCasiMisuratiDallAudit:

    def test_modello_inesistente(self):
        r = sv.valuta(_corpo(model="modello-inesistente-xyz"))
        assert any(x["campo"] == "model" for x in r)

    def test_path_traversal_nel_modello(self):
        """Non e' sfruttabile — il valore finisce nel body JSON — ma prova
        che sul campo model non c'era alcuna allowlist."""
        r = sv.valuta(_corpo(model="../../etc/passwd"))
        assert any(x["campo"] == "model" for x in r)

    def test_max_tokens_negativo(self):
        r = sv.valuta(_corpo(max_tokens=-5))
        assert any(x["campo"] == "max_tokens" for x in r)

    def test_ruolo_non_ammesso(self):
        r = sv.valuta(_corpo(role="hacker"))
        assert any(x["campo"] == "messages[0].role" for x in r)


class TestNessunFalsoPositivo:
    """I modelli veri delle nove modalita' non devono essere segnalati."""

    @pytest.mark.parametrize("modello", [
        "claude-haiku-4-5-20251001", "claude-opus-5", "claude-sonnet-5",
        "claude-fable-5", "MiniMax-M2.7", "MiniMax-M3", "minimax-m2.7-hs",
        "glm-5.2", "glm-4.7", "qwen3-coder-plus", "qwen3.7-max",
        "code-max", "fast-max", "local:code-max",
    ])
    def test_modello_legittimo_non_segnalato(self, modello):
        assert sv.valuta(_corpo(model=modello)) == [], (
            f"{modello} verrebbe rifiutato: e' un falso positivo"
        )

    @pytest.mark.parametrize("ruolo", ["user", "assistant"])
    def test_ruoli_ammessi(self, ruolo):
        assert sv.valuta(_corpo(role=ruolo)) == []

    @pytest.mark.parametrize("mt", [1, 8, 1024, 200000])
    def test_max_tokens_validi(self, mt):
        assert sv.valuta(_corpo(max_tokens=mt)) == []


class TestCasiLimite:

    def test_max_tokens_assente_non_e_un_rilievo(self):
        corpo = _corpo()
        del corpo["max_tokens"]
        assert sv.valuta(corpo) == []

    def test_booleano_non_passa_per_intero(self):
        """True e' un int in Python: senza controllo esplicito passerebbe."""
        r = sv.valuta(_corpo(max_tokens=True))
        assert any(x["campo"] == "max_tokens" for x in r)

    def test_max_tokens_zero(self):
        r = sv.valuta(_corpo(max_tokens=0))
        assert any(x["campo"] == "max_tokens" for x in r)

    def test_modello_lunghissimo(self):
        r = sv.valuta(_corpo(model="claude-" + "a" * 500))
        assert any("oltre il limite" in x["motivo"] for x in r)

    def test_modello_assente_non_e_compito_nostro(self):
        """L'assenza la copre gia' la validazione sintattica, con un 400."""
        corpo = _corpo()
        del corpo["model"]
        assert sv.valuta(corpo) == []

    def test_body_non_dizionario(self):
        assert sv.valuta([1, 2, 3]) == []
        assert sv.valuta(None) == []

    def test_messaggio_non_oggetto(self):
        corpo = _corpo()
        corpo["messages"] = ["ciao"]
        r = sv.valuta(corpo)
        assert any(x["campo"] == "messages[0]" for x in r)

    def test_piu_rilievi_insieme(self):
        r = sv.valuta(_corpo(model="xyz", max_tokens=-1, role="hacker"))
        assert len(r) == 3

    def test_indice_del_messaggio_corretto(self):
        corpo = _corpo()
        corpo["messages"] = [{"role": "user", "content": "a"},
                             {"role": "sbagliato", "content": "b"}]
        r = sv.valuta(corpo)
        assert r[0]["campo"] == "messages[1].role"


class TestModalita:

    def test_off_disattiva_tutto(self):
        assert sv.valuta(_corpo(model="xyz", max_tokens=-1), modalita="off") == []

    def test_audit_e_il_default(self):
        assert sv.MODALITA_VALIDAZIONE in ("audit", "off")

    def test_enforce_non_esiste_come_comportamento(self):
        """Il vincolo di A2: nessun rifiuto, in nessuna modalita'.

        Il modulo espone solo `valuta`, che ritorna rilievi. Non esiste una
        funzione che risponda 400: attivare il rifiuto e' una decisione da
        prendere sui dati raccolti, non un interruttore gia' cablato.
        """
        pubbliche = {n for n in dir(sv) if not n.startswith("_")}
        assert "valuta" in pubbliche
        for sospetta in ("rifiuta", "enforce", "blocca", "reject"):
            assert not any(sospetta in n.lower() for n in pubbliche)

    def test_env_off_rispettata_al_reimport(self, monkeypatch):
        monkeypatch.setenv("AIROUTER_VALIDATE_SEMANTIC", "off")
        sys.modules.pop("semantic_validation", None)
        modulo = importlib.import_module("semantic_validation")
        try:
            assert modulo.MODALITA_VALIDAZIONE == "off"
            assert modulo.valuta(_corpo(model="xyz")) == []
        finally:
            monkeypatch.delenv("AIROUTER_VALIDATE_SEMANTIC", raising=False)
            sys.modules.pop("semantic_validation", None)
            importlib.import_module("semantic_validation")


class TestDescrizione:

    def test_vuota_senza_rilievi(self):
        assert sv.descrivi([]) == ""

    def test_contiene_campo_valore_e_motivo(self):
        testo = sv.descrivi(sv.valuta(_corpo(model="xyz")))
        assert "model" in testo and "xyz" in testo and "pattern" in testo
