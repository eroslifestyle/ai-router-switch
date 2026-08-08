"""Soglia minima di max_tokens verso MiniMax (audit A4).

MINIMAX_MIN_MAX_TOKENS = 1024 esisteva senza una riga di commento, in un file
per il resto densamente commentato, e innalzava max_tokens in silenzio: con
max_tokens=8 si generano 1024 token.

Non viene rimosso — e' un rimedio alle risposte vuote da troncamento nel
thinking, e la misura sul traffico reale ha smentito l'ipotesi dello spreco
(8 risposte su 17.468 arrivano a 1024, il 71,9% sta sotto). Ma ora
l'innalzamento viene loggato, quindi e' contabile.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from minimax_body import (  # noqa: E402
    MINIMAX_MIN_MAX_TOKENS,
    remap_body_for_minimax,
)


def _rimappa(max_tokens, **extra):
    """Rimappa un body e ritorna (body risultante, righe di log)."""
    righe = []
    corpo = {"model": "claude-haiku-4-5-20251001",
             "messages": [{"role": "user", "content": "ciao"}]}
    if max_tokens is not None:
        corpo["max_tokens"] = max_tokens
    corpo.update(extra)
    risultato = remap_body_for_minimax(
        json.dumps(corpo).encode(), log_fn=righe.append,
    )
    return json.loads(risultato), righe


class TestSogliaMinima:

    def test_valore_e_1024(self):
        assert MINIMAX_MIN_MAX_TOKENS == 1024

    @pytest.mark.parametrize("richiesto", [1, 8, 100, 1023])
    def test_valori_sotto_soglia_vengono_innalzati(self, richiesto):
        corpo, _ = _rimappa(richiesto)
        assert corpo["max_tokens"] == MINIMAX_MIN_MAX_TOKENS

    @pytest.mark.parametrize("richiesto", [1024, 2048, 32000])
    def test_valori_sopra_soglia_non_cambiano(self, richiesto):
        corpo, _ = _rimappa(richiesto)
        assert corpo["max_tokens"] == richiesto

    def test_zero_non_viene_toccato(self):
        """La condizione e' 0 < mt < soglia: lo zero resta com'e'.

        Un max_tokens a zero non e' una richiesta piccola, e' una richiesta
        malformata: innalzarla nasconderebbe l'errore al client.
        """
        corpo, _ = _rimappa(0)
        assert corpo["max_tokens"] == 0

    def test_assente_resta_assente(self):
        corpo, _ = _rimappa(None)
        assert "max_tokens" not in corpo

    def test_valore_non_numerico_non_rompe(self):
        corpo, _ = _rimappa("otto")
        assert corpo["max_tokens"] == "otto"


class TestLogDellInnalzamento:
    """A4: l'innalzamento era invisibile, ora si conta con un grep."""

    def test_logga_quando_innalza(self):
        _, righe = _rimappa(8)
        pertinenti = [r for r in righe if "max_tokens innalzato" in r]
        assert pertinenti, f"nessun log dell'innalzamento: {righe}"
        assert "8 -> 1024" in pertinenti[0]

    def test_non_logga_quando_non_innalza(self):
        """Braccio di controllo: nessun rumore sulle richieste normali."""
        _, righe = _rimappa(4096)
        assert not [r for r in righe if "max_tokens innalzato" in r]

    def test_il_log_spiega_il_perche(self):
        """Chi legge il log deve capire il motivo senza aprire il sorgente."""
        _, righe = _rimappa(16)
        riga = next(r for r in righe if "max_tokens innalzato" in r)
        assert "thinking" in riga.lower()
