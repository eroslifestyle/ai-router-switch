"""Thinking block con firma non-Anthropic declassati a text (F1, 2026-08-16).

Nelle modalita' miste l'ACT non-Anthropic emette blocchi ``thinking`` firmati a
modo suo; il client li conserva nella conversazione e al turno THINK successivo
Anthropic li rifiuta con 400 ``Invalid `signature` in `thinking` block``. Misura
sui 7 giorni precedenti: 1.001 turni utente persi.

Le firme dei provider sono state misurate con sonde dirette sulle porte di
modalita' il 2026-08-16: GLM (8775) 24 caratteri esadecimali, MiniMax (8781,
ACT di mix-am-2) 64. Sono i valori usati qui.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from anthropic_body import declassa_thinking_estranei, _firma_e_estranea

FIRMA_GLM = "9be54cbc3de7463699fb38fa"                                  # 24, hex
FIRMA_MINIMAX = "94a6f37d40128c61ca1dc32eb7f3ab880e5f891b" + "0" * 24   # 64, hex
# Anthropic firma con un blob crittografico base64: lungo e fuori dall'alfabeto hex.
FIRMA_ANTHROPIC = ("ErUBCkYIBRgCIkDvW3n+Yz9KpQm2XcRt5hLbNmA1sVfE8gK0xTzZ/Qc"
                   "RtYuOpLmN4wXhVbGdJeSaKfDcBnMqZrEwTyUiIoPlAg==")


def _body(*blocchi_assistant):
    return json.dumps({
        "model": "claude-opus-5",
        "messages": [
            {"role": "user", "content": "ciao"},
            {"role": "assistant", "content": list(blocchi_assistant)},
        ],
    }).encode()


def test_firma_glm_riconosciuta_estranea():
    assert _firma_e_estranea(FIRMA_GLM) is True


def test_firma_minimax_riconosciuta_estranea():
    assert _firma_e_estranea(FIRMA_MINIMAX) is True


def test_firma_assente_o_vuota_e_estranea():
    assert _firma_e_estranea(None) is True
    assert _firma_e_estranea("") is True


def test_firma_anthropic_non_e_estranea():
    assert _firma_e_estranea(FIRMA_ANTHROPIC) is False


def test_firma_hex_ma_lunga_non_e_estranea():
    """Il criterio richiede hex E corta: una firma hex di 65 caratteri non si tocca."""
    assert _firma_e_estranea("a" * 65) is False


def test_thinking_glm_declassato_a_text():
    body = _body({"type": "thinking", "thinking": "rifletto...", "signature": FIRMA_GLM})
    out, n = declassa_thinking_estranei(body)
    assert n == 1
    blocchi = json.loads(out)["messages"][1]["content"]
    assert blocchi == [{"type": "text", "text": "rifletto..."}]


def test_thinking_minimax_declassato_a_text():
    body = _body({"type": "thinking", "thinking": "penso", "signature": FIRMA_MINIMAX})
    out, n = declassa_thinking_estranei(body)
    assert n == 1
    assert json.loads(out)["messages"][1]["content"][0]["type"] == "text"


def test_thinking_anthropic_intatto():
    """Controprova: il blocco valido deve restare un thinking, firma compresa."""
    blocco = {"type": "thinking", "thinking": "ragiono", "signature": FIRMA_ANTHROPIC}
    body = _body(blocco)
    out, n = declassa_thinking_estranei(body)
    assert n == 0
    assert out is body  # nemmeno riserializzato
    assert json.loads(out)["messages"][1]["content"][0] == blocco


def test_tool_use_accanto_al_thinking_resta():
    """Il tool_use del turno non va perso: si tocca solo il blocco thinking."""
    body = _body(
        {"type": "thinking", "thinking": "devo leggere il file", "signature": FIRMA_GLM},
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"p": "/x"}},
    )
    out, n = declassa_thinking_estranei(body)
    assert n == 1
    blocchi = json.loads(out)["messages"][1]["content"]
    assert [b["type"] for b in blocchi] == ["text", "tool_use"]
    assert blocchi[1]["id"] == "toolu_1"


def test_messaggio_non_resta_mai_vuoto():
    """Un thinking senza testo diventa un segnaposto: content vuoto sarebbe un altro 400."""
    body = _body({"type": "thinking", "thinking": "", "signature": FIRMA_GLM})
    out, n = declassa_thinking_estranei(body)
    assert n == 1
    blocchi = json.loads(out)["messages"][1]["content"]
    assert len(blocchi) == 1 and blocchi[0]["text"]


def test_stabile_fra_turni_consecutivi():
    """La trasformazione e' deterministica: due passaggi danno lo stesso byte.

    E' il vincolo che protegge il prompt caching (la classe di bug da 670M token).
    """
    body = _body({"type": "thinking", "thinking": "x", "signature": FIRMA_GLM})
    a, _ = declassa_thinking_estranei(body)
    b, _ = declassa_thinking_estranei(body)
    assert a == b
    # e ri-applicarla sull'output non cambia piu' nulla (idempotente)
    c, n2 = declassa_thinking_estranei(a)
    assert n2 == 0 and c == a


def test_body_senza_thinking_non_viene_parsato():
    body = json.dumps({"messages": [{"role": "user", "content": "ciao"}]}).encode()
    out, n = declassa_thinking_estranei(body)
    assert n == 0 and out is body


def test_body_non_json_non_esplode():
    out, n = declassa_thinking_estranei(b'{"thinking" non-json')
    assert n == 0 and out == b'{"thinking" non-json'


def test_agganciata_a_entrambe_le_gemelle():
    """forward_anthropic e forward_anthropic_direct devono chiamarla entrambe.

    Il commento nel sorgente avverte che sono gemelle: un fix su una sola le fa
    divergere, ed e' gia' successo con la promozione dei role=system.
    """
    src = (Path(__file__).resolve().parents[2] / "src" / "forward_anthropic.py").read_text()
    assert src.count("declassa_thinking_estranei(safe_body)") == 2
