import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from token_counter import bytes_per_token, estimate_tokens_body, BYTES_PER_TOKEN


def test_classificazione_tokenizer_nuovo():
    """Verifica che i modelli Anthropic nuovi restituiscano il divisore corretto."""
    nuovi_modelli = [
        "claude-opus-5",
        "claude-opus-5[1m]",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-mythos-5"
    ]
    for modello in nuovi_modelli:
        assert bytes_per_token(modello) == BYTES_PER_TOKEN["anthropic_new"]


def test_classificazione_tokenizer_vecchio():
    """Verifica che i modelli Anthropic vecchi restituiscano il divisore corretto."""
    vecchi_modelli = [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-opus-4-6",
        "claude-opus-4-5"
    ]
    for modello in vecchi_modelli:
        assert bytes_per_token(modello) == BYTES_PER_TOKEN["anthropic_old"]


def test_classificazione_altri_provider():
    """Verifica classificazione per altri provider: MiniMax, GLM, sconosciuti."""
    # MiniMax
    assert bytes_per_token("MiniMax-M2.7") == BYTES_PER_TOKEN["minimax"]
    assert bytes_per_token("minimax-m2.7-hs") == BYTES_PER_TOKEN["minimax"]
    # GLM
    assert bytes_per_token("glm-4.7") == BYTES_PER_TOKEN["glm"]
    assert bytes_per_token("glm-5.3") == BYTES_PER_TOKEN["glm"]
    # Sconosciuti
    assert bytes_per_token("modello_sconosciuto") == 4.0
    assert bytes_per_token(None) == 4.0


def test_case_insensitive():
    """Verifica che la classificazione sia case-insensitive."""
    assert bytes_per_token("CLAUDE-OPUS-5") == bytes_per_token("claude-opus-5")
    assert bytes_per_token("MINIMAX-M2.7") == bytes_per_token("minimax-m2.7")
    assert bytes_per_token("GLM-4.7") == bytes_per_token("glm-4.7")


def test_stima_sovrastima_mai_sottostima():
    """Costruisce body JSON ~50000 byte e verifica che la stima non sottostimi mai."""
    # Crea un payload di circa 50000 byte con messaggio ripetuto
    testo_base = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 10
    body = {
        "model": "claude-opus-5",
        "messages": [
            {"role": "user", "content": testo_base * 50}
        ]
    }
    body_json = json.dumps(body)
    dimensione = len(body_json)

    # Divisori reali misurati il 2026-07-27
    divisori_reali = {
        "claude-opus-5": 2.55,
        "claude-haiku-4-5-20251001": 3.53,
        "MiniMax-M2.7": 3.85,
        "glm-4.7": 4.06
    }

    for modello, divisore in divisori_reali.items():
        token_minimi = dimensione / divisore
        stima = estimate_tokens_body(body_json.encode(), modello)
        assert stima >= token_minimi, (
            f"{modello}: stima {stima} < minimo atteso {token_minimi:.2f}"
        )


def test_retrocompatibilita_senza_modello():
    """Verifica retrocompatibilita': senza modello usa divisione per 4."""
    body = {"messages": [{"role": "user", "content": "test payload"}]}
    body_json = json.dumps(body)
    atteso_vecchio = len(body_json) // 4
    stima_nuova = estimate_tokens_body(body_json.encode())
    # Tolleranza di 1 token per arrotondamento
    assert abs(stima_nuova - atteso_vecchio) <= 1


def test_opus5_stima_piu_alta_di_haiku():
    """Il tokenizer nuovo produce piu' token a parita' di testo rispetto al vecchio."""
    body = {
        "messages": [
            {"role": "user", "content": "Questo e' un testo di test per verificare la stima dei token." * 100}
        ]
    }
    body_bytes = json.dumps(body).encode()
    stima_opus5 = estimate_tokens_body(body_bytes, "claude-opus-5")
    stima_haiku = estimate_tokens_body(body_bytes, "claude-haiku-4-5-20251001")
    assert stima_opus5 > stima_haiku
