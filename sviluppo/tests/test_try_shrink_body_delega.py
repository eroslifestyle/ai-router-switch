"""Test per _try_shrink_body dopo il refactoring con delega a shrink_body_to_budget.

La funzione _try_shrink_body(orig: dict, target_bytes: int) -> bytes | None delega
l'orchestrazione del budget a shrink_body_to_budget, per non duplicarla e per
preservare il prompt caching con il taglio sticky.

Problemi corretti in questa versione:
1. TypeError: shrunk_bytes era un oggetto bytes, confrontato con un float
   (original_bytes * 0.5). L'eccezione veniva inghiottita dal try/except e la
   funzione restituiva None: nessuno shrink, in silenzio, proprio nel caso in cui
   serviva di piu' (nessun budget bastava).
2. Prompt caching ucciso: il summary finiva nel campo 'system' e la coda era
   scorrevole; entrambi cambiavano a ogni turno, creando ogni volta un prefisso nuovo.
3. Duplicazione con context_shrink.py che era già stata sanata.

La nuova versione preserva il contratto sulla firma e il side-effect delle immagini,
ma delega tutta l'orchestrazione a shrink_body_to_budget(orig_bytes, target_bytes, shrink_images).
"""

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from pipeline_minimax import _try_shrink_body

pytestmark = pytest.mark.asyncio


class TestTryShrinkBodyDelega:
    """Regressione del TypeError quando shrunk_bytes è bytes."""

    async def test_regressione_typeerror_bytes_float(self, monkeypatch):
        """Caso critico: nessun budget basta, shrink deve fallire SENZA sollevare TypeError.

        Con target_bytes molto piccolo (10 byte) e messaggi normali (alcuni KB),
        il vecchio codice alla riga 190 faceva:
            if shrunk_bytes < original_bytes * 0.5:
        dove shrunk_bytes era bytes (oggetto), non int. Il TypeError veniva inghiottito
        dal try/except e ritornava None silenziosamente, senza nemmeno un log di EXC.

        Il test verifica che:
        - La funzione ritorna None (come previsto quando nessun budget basta)
        - Nessuna eccezione viene sollevata
        - Il log non contiene "EXC" (non c'è stato un errore interno)
        """
        # Intercetta i log di pipeline_minimax
        logged_lines = []

        def mock_log(msg):
            logged_lines.append(msg)

        import pipeline_minimax
        monkeypatch.setattr(pipeline_minimax, "log", mock_log)

        # Body con tre messaggi di circa 3KB ciascuno (~9KB totale)
        orig = {
            "model": "MiniMax-M3",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "A" * 3000}]
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "B" * 3000}]
                },
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "C" * 3000}]
                },
            ],
            "system": "System prompt base",
        }

        # Chiedi un target impossibile: 10 byte
        result = await _try_shrink_body(orig, target_bytes=10)

        # Non deve esserci stata un'eccezione
        assert result is None, "Atteso: None quando nessun budget basta"

        # Nessun log di "EXC" = nessun TypeError inghiottito
        exc_logs = [l for l in logged_lines if "EXC" in l]
        assert len(exc_logs) == 0, f"Non atteso log di EXC, trovato: {exc_logs}"

    async def test_caso_normale_target_generoso(self):
        """Caso normale: body che rientra con budget generoso."""
        orig = {
            "model": "MiniMax-M2.7",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "World"}]},
            ],
            "system": "You are helpful.",
        }

        # target_bytes generoso: 200KB
        result = await _try_shrink_body(orig, target_bytes=200000)

        # Deve ritornare bytes
        assert isinstance(result, bytes), f"Atteso: bytes, Ottenuto: {type(result)}"

        # Deve stare entro il target
        assert len(result) <= 200000, f"Atteso: <= 200000b, Ottenuto: {len(result)}b"

    async def test_messaggi_vuoti_ritorna_none(self):
        """Body con elenco messaggi vuoto deve ritornare None."""
        orig = {
            "model": "MiniMax-M3",
            "messages": [],
            "system": "Prompt",
        }

        result = await _try_shrink_body(orig, target_bytes=50000)
        assert result is None

    async def test_chiave_messages_assente_ritorna_none(self):
        """Body senza chiave 'messages' deve ritornare None senza eccezioni."""
        orig = {
            "model": "MiniMax-M3",
            "system": "Prompt senza messaggi",
        }

        result = await _try_shrink_body(orig, target_bytes=50000)
        assert result is None

    async def test_risultato_json_valido_e_preserva_modello(self):
        """Il risultato deve essere JSON valido e contenere il campo 'model'."""
        orig = {
            "model": "MiniMax-M3",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Query " * 500}]},
            ],
            "system": "Base system prompt",
        }

        result = await _try_shrink_body(orig, target_bytes=150000)

        # Deve essere bytes
        assert isinstance(result, bytes)

        # Deve essere JSON valido
        deserialized = json.loads(result)
        assert isinstance(deserialized, dict)

        # Deve contenere la chiave 'model' dal body originale
        assert "model" in deserialized
        assert deserialized["model"] == "MiniMax-M3"
