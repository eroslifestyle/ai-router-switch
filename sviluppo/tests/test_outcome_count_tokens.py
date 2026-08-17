"""Classificazione dell'outcome per le richieste a /v1/messages/count_tokens.

Il 2026-08-16 è stato scoperto che le richieste a /v1/messages/count_tokens non
generano nulla per contratto (niente stop_reason, niente blocchi di contenuto) e
venivano classificate outcome="unknown" (misura interrotta), facendole sembrare
generazioni perse. Ora la funzione log_router_usage discrimina il path e assegna
outcome="count_tokens" esplicitamente, in modo che l'analisi possa distinguerle
dalle reali interruzioni di misurazione.
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


def _ultima_entry(sidecar):
    """Rilegge l'ultima riga scritta nel sidecar."""
    righe = sidecar.read_text(encoding="utf-8").strip().split("\n")
    return json.loads(righe[-1])


def _logga(ru, sidecar, **extra):
    """Chiama log_router_usage con gli argomenti minimi realmente richiesti.

    La firma è (chat_id, orig, final, usage, mode, ...): usage è un dict di
    contatori, non una stringa di esito. Se in extra c'è un 'usage', lo usa
    al posto di quello di default.
    """
    # Default usage, sostituibile via extra
    usage_default = {"input_tokens": 10, "output_tokens": 5, "tool_use_blocks": 2}
    usage_override = extra.pop("usage", None)

    ru.log_router_usage(
        chat_id="sid:test",
        orig="claude-haiku-4-5-20251001",
        final="claude-3-5-sonnet-20241022",
        usage=usage_override if usage_override is not None else usage_default,
        mode="anthropic",
        **extra,
    )
    return _ultima_entry(sidecar)


class TestOutcomeCountTokens:
    """Verifica che il fix di src/router_utils.py discrimini correttamente le
    richieste count_tokens dall'outcome "unknown".
    """

    @pytest.fixture
    def ambiente(self, tmp_path, monkeypatch):
        """router_utils con il sidecar dirottato in tmp_path.

        USAGE_SIDECAR deve restare un Path: il codice ci chiama .parent.mkdir().
        """
        sidecar = tmp_path / "usage.jsonl"
        ru = _import_router_utils()
        monkeypatch.setattr(ru, "USAGE_SIDECAR", sidecar)
        return ru, sidecar

    def test_path_count_tokens_outcome_count_tokens(self, ambiente):
        """Test 1: path="/v1/messages/count_tokens" → outcome=="count_tokens"."""
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar, path="/v1/messages/count_tokens")
        assert entry["outcome"] == "count_tokens"

    def test_path_messages_outcome_unknown(self, ambiente):
        """Test 2: braccio di controllo. Stesso path base ma senza count_tokens
        deve produrre outcome=="unknown".

        Per ottenere "unknown" da classify_outcome servono: usage con
        thinking_blocks>=1 ma text_blocks=0 e tool_use_blocks=0, e stop_reason
        assente. Senza questo test, un fix che marcasse tutto "count_tokens"
        passerebbe comunque.
        """
        ru, sidecar = ambiente
        entry = _logga(
            ru, sidecar,
            path="/v1/messages",
            usage={"input_tokens": 10, "output_tokens": 0, "thinking_blocks": 1}
        )
        assert entry["outcome"] == "unknown"

    def test_path_assente_non_count_tokens(self, ambiente):
        """Test 3: path non passato (assente) non deve sollevare eccezioni e
        l'outcome NON deve essere "count_tokens".
        """
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar)
        assert "path" not in entry
        assert entry["outcome"] != "count_tokens"

    def test_path_contiene_count_tokens_ma_non_finisce(self, ambiente):
        """Test 4: path che contiene "count_tokens" ma non ci finisce (non
        endswith) NON deve essere classificato "count_tokens".
        """
        ru, sidecar = ambiente
        entry = _logga(ru, sidecar, path="/v1/messages/count_tokens/extra")
        assert entry["outcome"] != "count_tokens"
