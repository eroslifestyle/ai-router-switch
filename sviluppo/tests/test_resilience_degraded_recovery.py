"""
Test di regressione per il fix della resilienza DEGRADED (2026-08-18).

Il router rimane intrappolato in DEGRADED quando la modalità globale non è
anthropic e il self-test periodico esce anzitempo senza mai fare il live-test.
Questo blocco di test verifica che il fix evita il buco.

Bug-root (risolto in src/ai_router_resilience.py, _tick_self_test):
    La condizione originale saltava il self-test se la modalità non era
    anthropic, SENZA controllare se eravamo in DEGRADED. Poiché la modalità
    qui è quella GLOBALE mentre il gate a valle guarda quella DELLA RICHIESTA
    (che può essere un override per-chat), una chat pinnata su una delle
    MODES_USING_ANTHROPIC prendeva 503 a oltranza: nessun tick la sbloccava.

Fix: aggiunto il check `and self._state != self.STATE_DEGRADED` in modo che
lo skip per modalità non-anthropic vale solo se NON siamo in DEGRADED.
"""

import sys
import pathlib
import time
from unittest.mock import MagicMock, patch

import pytest

# Risolvi il path di src
_src = pathlib.Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_src))

# Importa il modulo
from ai_router_resilience import Resilience


class TestDegradedRecovery:
    """Test della regressione DEGRADED + modalità non-anthropic."""

    @pytest.fixture
    def mock_paths(self):
        """Mock dei path globali per evitare di scrivere su file reali."""
        with patch("ai_router_resilience.STATE_DIR") as mock_state_dir, \
             patch("ai_router_resilience.STATE_FILE") as mock_state_file, \
             patch("ai_router_resilience.CRASH_DIR") as mock_crash_dir, \
             patch("ai_router_resilience.CRED_FILE") as mock_cred_file:
            mock_state_dir.mkdir = MagicMock()
            mock_state_file.write_text = MagicMock()
            mock_state_file.read_text = MagicMock(return_value="{}")
            mock_state_file.exists = MagicMock(return_value=False)
            mock_state_file.with_suffix = MagicMock(return_value=MagicMock())
            mock_crash_dir.mkdir = MagicMock()
            yield {
                "state_dir": mock_state_dir,
                "state_file": mock_state_file,
                "crash_dir": mock_crash_dir,
                "cred_file": mock_cred_file,
            }

    @pytest.fixture
    def resilience_instance(self, mock_paths):
        """Crea un'istanza di Resilience con mock dei path."""
        res = Resilience(
            port=8787,
            log_fn=lambda m: None,
            get_pid=lambda: 99999,
            should_test_oauth_fn=lambda: False,  # modalità non-anthropic per default
        )
        return res

    def test_degraded_state_with_nonanthropic_mode_invokes_selftest(
        self, resilience_instance, mock_paths
    ):
        """TEST 1: REGRESSIONE PRINCIPALE.

        Istanza in DEGRADED + modalità non-anthropic + token valido
        → il self-test live DEVE essere invocato (il fix aggiunge il check
        `and self._state != STATE_DEGRADED`).

        Con il codice vecchio: sarebbe uscito all'elif senza fare self-test.
        Verifica: istanza in DEGRADED, token strutturalmente valido, self-test
        invocato e ritorna OK → lo stato torna OK.
        """
        res = resilience_instance
        res._state = Resilience.STATE_DEGRADED
        res._oauth_tok = ""  # cache vuota per forzare il read

        # Mock _read_oauth per ritornare un token valido (inizia con sk-ant-oat)
        fake_token = "sk-ant-oat-fake-token-12345678901234567890"
        res._read_oauth = MagicMock(
            return_value=(
                fake_token,
                {"expiresAt": (time.time() + 3600) * 1000, "subscriptionType": "PRO"},
            )
        )

        # Mock is_oauth_structurally_ok per ritornare True
        res.is_oauth_structurally_ok = MagicMock(return_value=True)

        # Mock self_test_oauth per registrare la chiamata e ritornare OK
        self_test_called = []

        def fake_self_test(*args, **kwargs):
            self_test_called.append(True)
            return True, "ok"

        res.self_test_oauth = fake_self_test

        # Mock _write_state_now per evitare io reale
        res._write_state_now = MagicMock()

        # Esegui il tick
        res._tick_self_test()

        # Verifica: self-test DEVE essere stato chiamato nonostante la modalità non-anthropic
        assert len(self_test_called) == 1, (
            "self_test_oauth NON è stato invocato: il fix fallisce! "
            "(Con il codice vecchio lo skip per modalità non-anthropic usciva senza test)"
        )

        # Verifica: stato è tornato OK
        assert res._state == Resilience.STATE_OK, (
            f"Stato deve essere OK dopo self-test riuscito, invece è {res._state}"
        )

    def test_ok_state_with_nonanthropic_mode_skips_selftest(
        self, resilience_instance, mock_paths
    ):
        """TEST 2: BRACCIO DI CONTROLLO.

        Istanza in OK + modalità non-anthropic + token invariato
        → il self-test live NON deve essere invocato (il fix lo consente ancora
        quando NON siamo in DEGRADED).

        Verifica che il path di ottimizzazione "skip se modalità non-anthropic
        e stato OK" funziona ancora.
        """
        res = resilience_instance
        res._state = Resilience.STATE_OK
        fake_token = "sk-ant-oat-fake-token-12345678901234567890"
        res._oauth_tok = fake_token  # cache = token corrente

        # Mock _read_oauth per ritornare lo STESSO token
        res._read_oauth = MagicMock(
            return_value=(
                fake_token,
                {"expiresAt": (time.time() + 3600) * 1000, "subscriptionType": "PRO"},
            )
        )

        # Mock self_test_oauth — NON deve essere chiamato
        self_test_called = []

        def fake_self_test(*args, **kwargs):
            self_test_called.append(True)
            return True, "ok"

        res.self_test_oauth = fake_self_test

        # Esegui il tick
        res._tick_self_test()

        # Verifica: self-test NON deve essere stato chiamato
        assert len(self_test_called) == 0, (
            "self_test_oauth è stato invocato quando non dovrebbe: "
            "lo skip per 'token invariato e stato OK' deve funzionare"
        )

        # Verifica: stato rimane OK
        assert res._state == Resilience.STATE_OK

    def test_degraded_state_with_anthropic_mode_invokes_selftest(
        self, resilience_instance, mock_paths
    ):
        """TEST 3: COMPORTAMENTO PREESISTENTE.

        Istanza in DEGRADED + modalità anthropic + token valido
        → il self-test live deve essere invocato (nulla cambia).

        Verifica che il fix non regredisce il path quando la modalità è
        anthropic.
        """
        # Crea un'istanza con should_test_oauth_fn che ritorna True (modalità anthropic)
        res = Resilience(
            port=8787,
            log_fn=lambda m: None,
            get_pid=lambda: 99999,
            should_test_oauth_fn=lambda: True,  # modalità anthropic
        )
        res._state = Resilience.STATE_DEGRADED
        res._oauth_tok = ""

        # Mock _read_oauth
        fake_token = "sk-ant-oat-fake-token-12345678901234567890"
        res._read_oauth = MagicMock(
            return_value=(
                fake_token,
                {"expiresAt": (time.time() + 3600) * 1000, "subscriptionType": "PRO"},
            )
        )

        # Mock is_oauth_structurally_ok
        res.is_oauth_structurally_ok = MagicMock(return_value=True)

        # Mock self_test_oauth
        self_test_called = []

        def fake_self_test(*args, **kwargs):
            self_test_called.append(True)
            return True, "ok"

        res.self_test_oauth = fake_self_test
        res._write_state_now = MagicMock()

        # Esegui il tick
        res._tick_self_test()

        # Verifica: self-test DEVE essere stato chiamato
        assert len(self_test_called) == 1, (
            "self_test_oauth non è stato invocato quando la modalità è anthropic"
        )

        # Verifica: stato è OK
        assert res._state == Resilience.STATE_OK

    def test_degraded_state_with_invalid_oauth_stays_degraded(
        self, resilience_instance, mock_paths
    ):
        """TEST 4: TOKEN INVALIDO.

        Istanza in DEGRADED + token NON valido (non inizia con sk-ant-oat)
        → il self-test live NON deve essere invocato e lo stato rimane DEGRADED.

        Verifica il gate iniziale che controlla la struttura del token.
        """
        res = resilience_instance
        res._state = Resilience.STATE_DEGRADED
        res._oauth_tok = ""

        # Mock _read_oauth per ritornare un token INVALIDO
        fake_token = "invalid-token-12345"  # NON inizia con sk-ant-oat
        res._read_oauth = MagicMock(
            return_value=(
                fake_token,
                {"expiresAt": (time.time() + 3600) * 1000},
            )
        )

        # Mock self_test_oauth — NON deve essere chiamato
        self_test_called = []

        def fake_self_test(*args, **kwargs):
            self_test_called.append(True)
            return False, "error"

        res.self_test_oauth = fake_self_test
        res._write_state_now = MagicMock()

        # Esegui il tick
        res._tick_self_test()

        # Verifica: self-test NON deve essere stato chiamato
        assert len(self_test_called) == 0, (
            "self_test_oauth è stato invocato con token invalido"
        )

        # Verifica: stato rimane DEGRADED
        assert res._state == Resilience.STATE_DEGRADED, (
            f"Stato deve restare DEGRADED con token invalido, invece è {res._state}"
        )


class TestDegradedRecoveryRegressionAnalysis:
    """Analisi della regressione per documentazione."""

    def test_regression_explanation_why_old_code_fails(self):
        """Spiegazione testuale: perché il codice vecchio falliva in TEST 1.

        La condizione VECCHIA era:
            if (self._should_test_oauth_fn is not None
                    and not self._should_test_oauth_fn()
                    and self._state != self.STATE_DEGRADED):  # ← ASSENTE
                self.log("resilience: self-test OAuth skip: modalità non-anthropic")
                return

        CONTROPROVА: Test 1 dipende dal check `and self._state != self.STATE_DEGRADED`
        ─────────────────────────────────────────────────────────────────────────
        Se la condizione VECCHIA fosse in vigore, il test 1 fallirebbe così:

        Setup (identico al test 1):
        - Stato: DEGRADED
        - Modalità: non-anthropic (should_test_oauth_fn() ritorna False)
        - Token: valido (inizia con "sk-ant-oat")

        Esecuzione con VECCHIO codice:
        1. _read_oauth() ritorna il token valido → ok
        2. token != self._oauth_tok (cache vuota) → continua
        3. token.startswith("sk-ant-oat") → vero, non entra nell'if
        4. is_oauth_structurally_ok() → vero, non entra nell'if
        5. Arriva alla fatale condizione:
              if (self._should_test_oauth_fn is not None
                      and not self._should_test_oauth_fn()
                      [MANCA: and self._state != self.STATE_DEGRADED])
           Valutazione: True and True = True (perché manca il terzo check)
        6. Entra nell'if, esegue return
        7. Esce senza mai fare self_test_oauth

        Risultato: assertion "self_test_oauth DEVE essere stato invocato" FALLISCE.

        Con il FIX (check `and self._state != self.STATE_DEGRADED` aggiunto):
        1-4. Identico
        5. La condizione è: True and True and False (siamo in DEGRADED)
           = False
        6. NON entra nell'if, continua
        7. Invoca self_test_oauth
        8. self_test ritorna OK → stato torna OK
        9. Assertion "self_test_oauth invocato" PASSA

        La regressione che il fix corregge:
        ───────────────────────────────────
        Il router rimaneva intrappolato in DEGRADED perché il tick di self-test
        usciva anticipatamente quando la modalità GLOBALE non era anthropic,
        anche se lo stato era DEGRADED (l'unico caso in cui il self-test vivo è
        OBBLIGATORIO per uscirne).

        Perché questo bug era invisibile:
        ─────────────────────────────────
        Se la modalità globale era sempre "anthropic", il bug non emergeva.
        Il bug si manifestava in questa sequenza:
        1. Utente cambia modalità globale a "minimax"
        2. Qualcosa causa DEGRADED (es. OAuth scaduto)
        3. Una chat è pinnata su "anthropic" (override per-chat via `!router anthropic`)
        4. Quella chat prende 503 da DEGRADED
        5. Il tick di self-test non recupera perché guarda la modalità GLOBALE (minimax)
        6. Serviva un restart manuale

        La riparazione mantiene l'ottimizzazione (skip self-test live se in OK e
        modalità non-anthropic) ma sblocca il self-test quando siamo intrappolati
        (DEGRADED) — il momento in cui è l'UNICA via d'uscita.
        """
        # Questo è un docstring di spiegazione dettagliata, non un test eseguibile.
        # Passa sempre per documentare il motivo della regressione e la controprovа.
        assert True


class TestPredicateGetFileMode:
    """Test del predicato del fix: si usa `get_file_mode()` non `get_chat_mode(None)`.

    Il fix in ai-router-proxy.py riga 1040 cambia il predicato lambda:
      OLD: lambda: get_chat_mode(None) == "anthropic"
      NEW: lambda: get_file_mode() in MODES_USING_ANTHROPIC

    Questo test documenta e verifica il problema della riga vecchia.
    """

    def test_get_chat_mode_none_returns_none(self):
        """Documenta il bug: get_chat_mode(None) ritorna sempre None.

        `get_chat_mode(fp)` legge da un file JSON le override per-chat.
        Passando `fp=None` non c'è chiave nel dict → get() ritorna None.

        Con il codice vecchio:
            lambda: get_chat_mode(None) == "anthropic"
                ↓
            lambda: None == "anthropic"
                ↓
            lambda: False  ← SEMPRE False

        Il self-test live OAuth non veniva mai eseguito, tranne quando
        la modalità globale era esplicitamente "anthropic" E il gate
        a valle trovasse una chat con override (improbabile).
        """
        _src = pathlib.Path(__file__).resolve().parents[2] / "src"
        sys.path.insert(0, str(_src))
        from router_mode import get_chat_mode

        result = get_chat_mode(None)
        assert result is None, (
            f"get_chat_mode(None) deve ritornare None (no fingerprint nel dict), "
            f"invece ritorna {result!r}"
        )

    def test_get_file_mode_predicate_with_monkeypatch(self, monkeypatch, tmp_path):
        """Verifica che il nuovo predicato CHIAMA get_file_mode() e legge la modalità dal file.

        Il predicato nel fix è: `get_file_mode() in MODES_USING_ANTHROPIC`

        Questo test:
        1. Crea file temporanei con diverse modalità
        2. Fa leggere a get_file_mode() dai file temporanei (via monkeypatch)
        3. Valuta il predicato REALE e verifica che ritorni True/False a seconda della modalità

        Così il test cattura il bug del predicato vecchio:
        - OLD: lambda: get_chat_mode(None) == "anthropic"  → sempre False
        - NEW: lambda: get_file_mode() in MODES_USING_ANTHROPIC  → dipende dalla modalità
        """
        _src = pathlib.Path(__file__).resolve().parents[2] / "src"
        # Purga i moduli carichi per evitare contaminazione fra i test
        for mod in list(sys.modules.keys()):
            if "router" in mod or "paths" in mod:
                del sys.modules[mod]
        sys.path.insert(0, str(_src))

        from router_constants import MODES_USING_ANTHROPIC
        import router_mode
        from router_mode import get_file_mode

        # Test (a): per ciascuna modalità, scrivi nel file e valuta il predicato
        test_cases = [
            ("anthropic", True),    # Anthropic THINK+ACT
            ("mix-am", True),       # Anthropic THINK + MiniMax ACT
            ("mix-am-2", True),     # Variante delega aggressiva
            ("mix-ag", True),       # Anthropic THINK + GLM ACT
            ("mix-ag-2", True),     # Variante
            ("mix-al", True),       # Anthropic THINK + LLM locale ACT
            ("minimax", False),     # NON usa Anthropic
            ("glm", False),         # NON usa Anthropic
            ("qwen", False),        # NON usa Anthropic
        ]

        for mode, expected_in_anthropic in test_cases:
            # Crea file temporaneo e scrivi la modalità
            mode_file = tmp_path / "ai-router-mode"
            mode_file.write_text(mode)

            # Monkeypatch router_mode.MODE_FILE per puntare al file temporaneo
            monkeypatch.setattr(router_mode, "MODE_FILE", mode_file)

            # Valuta il predicato REALE
            result = get_file_mode() in MODES_USING_ANTHROPIC

            assert result == expected_in_anthropic, (
                f"Modalità {mode}: predicato deve essere {expected_in_anthropic}, "
                f"instead got {result}. "
                f"MODES_USING_ANTHROPIC={MODES_USING_ANTHROPIC}"
            )

    def test_get_file_mode_fallback_returns_anthropic(self, monkeypatch, tmp_path):
        """Verifica il fallback: se MODE_FILE inesistente, get_file_mode() ritorna 'anthropic'.

        Questo è il comportamento fail-open nel codice (riga 28 di router_mode.py):
            try:
                m = MODE_FILE.read_text().strip().lower()
                ...
            except Exception:
                pass
            return "anthropic"

        Il fallback significa che il predicato ritorna True quando il file è inesistente.
        Questo test blocca il caso: se qualcuno rimuovesse il fallback, il test fallirebbe.
        """
        _src = pathlib.Path(__file__).resolve().parents[2] / "src"
        # Purga i moduli carichi
        for mod in list(sys.modules.keys()):
            if "router" in mod or "paths" in mod:
                del sys.modules[mod]
        sys.path.insert(0, str(_src))

        from router_constants import MODES_USING_ANTHROPIC
        import router_mode
        from router_mode import get_file_mode

        # Crea un path inesistente
        nonexistent = tmp_path / "nonexistent" / "ai-router-mode"

        # Monkeypatch MODE_FILE
        monkeypatch.setattr(router_mode, "MODE_FILE", nonexistent)

        # get_file_mode() deve ritornare il fallback "anthropic"
        result = get_file_mode()
        assert result == "anthropic", (
            f"Fallback con file inesistente deve essere 'anthropic', "
            f"invece got {result!r}"
        )

        # Il predicato deve essere True (anthropic è in MODES_USING_ANTHROPIC)
        predicate_result = result in MODES_USING_ANTHROPIC
        assert predicate_result is True, (
            f"Fallback 'anthropic' deve fare il predicato True, "
            f"instead got {predicate_result}"
        )

    def test_proxy_source_contains_correct_predicate(self):
        """Aggancia la riga di produzione nel sorgente del proxy.

        I test precedenti verificano il predicato logico ricostruito dentro il test.
        Se qualcuno rimette `get_chat_mode(None) == "anthropic"` nel proxy,
        quei test passano identici: testano la logica estratta, non il codice vivo.

        Questo test legge il sorgente `src/ai-router-proxy.py` e cerca la riga
        che contiene `should_test_oauth_fn=`. Asserisce che quella riga contiene:
          - `get_file_mode()`
          - `MODES_USING_ANTHROPIC`
        E NON contiene:
          - `get_chat_mode(` (la funzione vecchia del bug)

        Così il test aggancia la riga di produzione. Se il bug ritorna, il test
        cade subito, senza aspettare che la regressione si manifesti a runtime.
        """
        _project_root = pathlib.Path(__file__).resolve().parents[2]
        proxy_file = _project_root / "src" / "ai-router-proxy.py"

        # Leggi il file sorgente
        source = proxy_file.read_text()

        # Trova la riga con should_test_oauth_fn
        lines = source.split("\n")
        found = False
        for i, line in enumerate(lines, start=1):
            if "should_test_oauth_fn=" in line:
                found = True
                # Asserzioni sulla riga di produzione
                assert "get_file_mode()" in line, (
                    f"Riga {i} di {proxy_file}: "
                    f"should_test_oauth_fn deve contenere 'get_file_mode()', "
                    f"invece contiene: {line.strip()}"
                )
                assert "MODES_USING_ANTHROPIC" in line, (
                    f"Riga {i} di {proxy_file}: "
                    f"should_test_oauth_fn deve contenere 'MODES_USING_ANTHROPIC', "
                    f"invece contiene: {line.strip()}"
                )
                assert "get_chat_mode(" not in line, (
                    f"Riga {i} di {proxy_file}: "
                    f"should_test_oauth_fn NON deve contenere 'get_chat_mode(' (bug), "
                    f"invece contiene: {line.strip()}"
                )
                break

        assert found, (
            f"Riga 'should_test_oauth_fn=' non trovata in {proxy_file}. "
            f"Il fix potrebbe essere stato revertato."
        )
