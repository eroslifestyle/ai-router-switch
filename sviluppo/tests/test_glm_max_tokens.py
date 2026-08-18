"""max_tokens verso z.ai: tetto e pavimento (F5, 2026-08-16).

Il tetto esisteva da luglio (z.ai rifiuta oltre 32.768 con 400 [1210]). Mancava il
pavimento: GLM emette un blocco `thinking` prima del testo, e quel blocco consuma
output_tokens. Con un max_tokens basso il budget finisce nel ragionamento e il
client riceve un 200 con `stop_reason=max_tokens` e nessun blocco text.

Misurato il 2026-08-16 con uno stress a max_tokens=128: 11 risposte su 40 senza
testo, tutte su GLM (mode glm e mix-gm-2). Sulla distribuzione reale di 450
risposte GLM concluse, un tetto di 128 ne avrebbe troncate il 46,9%.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


@pytest.fixture
def glm(monkeypatch):
    for var in ("AIROUTER_GLM_MIN_MAX_TOKENS", "AIROUTER_GLM_MAX_TOKENS_LIMIT"):
        monkeypatch.delenv(var, raising=False)
    sys.modules.pop("glm_backend", None)
    mod = importlib.import_module("glm_backend")
    yield mod
    sys.modules.pop("glm_backend", None)


def _mt(body):
    return json.loads(body)["max_tokens"]


def test_sotto_il_minimo_viene_innalzato(glm):
    """Il caso che il fix esiste per risolvere: 128 non basta al thinking."""
    out = glm.clamp_glm_max_tokens(json.dumps({"max_tokens": 128}).encode())
    assert _mt(out) == glm.GLM_MIN_MAX_TOKENS


def test_sopra_il_massimo_viene_abbassato(glm):
    """Il tetto storico non deve essere stato perso nel modificare la funzione."""
    out = glm.clamp_glm_max_tokens(json.dumps({"max_tokens": 999_999}).encode())
    assert _mt(out) == glm.GLM_MAX_TOKENS_LIMIT


def test_tetto_per_modello(glm):
    """glm-5.3 e glm-4.7 reggono 128K di output (doc chat-completion, e verificato
    a runtime il 2026-08-18: max_tokens=131072 -> HTTP 200 su entrambi). Il tetto
    unico a 32.768 li tagliava a un quarto. Un modello ignoto resta prudente."""
    corpo = json.dumps({"max_tokens": 100_000}).encode()
    assert glm.clamp_glm_max_tokens(corpo, model="glm-5.3") is corpo
    assert glm.clamp_glm_max_tokens(corpo, model="glm-4.7") is corpo
    # glm-4.6V ha 32K di output: lo stesso valore va abbassato.
    assert _mt(glm.clamp_glm_max_tokens(corpo, model="glm-4.6V")) == 32_768
    # Senza modello (e con uno sconosciuto) vale il tetto storico.
    assert _mt(glm.clamp_glm_max_tokens(corpo)) == glm.GLM_MAX_TOKENS_LIMIT
    assert _mt(glm.clamp_glm_max_tokens(corpo, model="glm-ignoto")) == glm.GLM_MAX_TOKENS_LIMIT
    # Il modello si legge anche dal body, che e' come arriva da set_body_model.
    dal_body = json.dumps({"max_tokens": 100_000, "model": "glm-5.3"}).encode()
    assert glm.clamp_glm_max_tokens(dal_body) is dal_body


def test_valore_nell_intervallo_non_si_tocca(glm):
    corpo = json.dumps({"max_tokens": 8000}).encode()
    assert glm.clamp_glm_max_tokens(corpo) is corpo


def test_minimo_sotto_il_massimo(glm):
    """Controprova di coerenza: se qualcuno alzasse il minimo oltre il tetto,
    ogni richiesta verrebbe prima innalzata e poi rifiutata da z.ai."""
    assert glm.GLM_MIN_MAX_TOKENS < glm.GLM_MAX_TOKENS_LIMIT


def test_campo_assente_o_non_intero(glm):
    for corpo in (b'{}', b'{"max_tokens": null}', b'{"max_tokens": "molti"}', b'non-json'):
        assert glm.clamp_glm_max_tokens(corpo) is corpo


def test_zero_e_negativi_finiscono_nel_minimo(glm):
    """Prima finivano a 1, che e' dentro il range di z.ai ma da' una risposta
    troncata al primo token."""
    for v in (0, -5):
        out = glm.clamp_glm_max_tokens(json.dumps({"max_tokens": v}).encode())
        assert _mt(out) == glm.GLM_MIN_MAX_TOKENS


def test_override_ambiente(monkeypatch):
    monkeypatch.setenv("AIROUTER_GLM_MIN_MAX_TOKENS", "512")
    sys.modules.pop("glm_backend", None)
    mod = importlib.import_module("glm_backend")
    try:
        assert _mt(mod.clamp_glm_max_tokens(json.dumps({"max_tokens": 10}).encode())) == 512
    finally:
        sys.modules.pop("glm_backend", None)


def test_idempotente(glm):
    """Due passaggi consecutivi danno lo stesso body: nessuna deriva fra turni."""
    a = glm.clamp_glm_max_tokens(json.dumps({"max_tokens": 100}).encode())
    b = glm.clamp_glm_max_tokens(a)
    assert b is a or json.loads(b) == json.loads(a)
