"""Sanitizzazione di tool_choice quando lo strip porta via tutti i tool (audit H2).

La funzione sanitize_tool_choice riallinea il campo tool_choice all'array tools
dopo uno strip. Un tool_choice che punta a un tool rimosso o dichiara un obbligo
(type=any/none) senza offrire tool genera 400 all'upstream. Questa suite verifica
che il campo sia rimosso correttamente quando non rimane nessun tool, e che
il comportamento esistente di degradazione a "auto" valga SOLO per type=tool.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from tool_isolation import sanitize_tool_choice


class TestToolChoiceSenzaTools:
    """Rimozione di tool_choice quando l'array tools è vuoto."""

    def test_type_any_tools_vuoti_rimuove(self):
        """type=any con tools=[] -> tool_choice rimosso."""
        data = {
            "tools": [],
            "tool_choice": {"type": "any"},
        }
        sanitize_tool_choice(data)
        assert "tool_choice" not in data

    def test_type_auto_tools_vuoti_rimuove(self):
        """type=auto con tools=[] -> tool_choice rimosso."""
        data = {
            "tools": [],
            "tool_choice": {"type": "auto"},
        }
        sanitize_tool_choice(data)
        assert "tool_choice" not in data

    def test_type_none_tools_vuoti_rimuove(self):
        """type=none con tools=[] -> tool_choice rimosso."""
        data = {
            "tools": [],
            "tool_choice": {"type": "none"},
        }
        sanitize_tool_choice(data)
        assert "tool_choice" not in data

    def test_type_tool_tools_vuoti_rimuove(self):
        """type=tool con tools=[] -> tool_choice rimosso (comportamento preesistente)."""
        data = {
            "tools": [],
            "tool_choice": {"type": "tool", "name": "web_search"},
        }
        sanitize_tool_choice(data)
        assert "tool_choice" not in data

    def test_tools_assente_type_any_rimuove(self):
        """tools assente interamente, type=any -> tool_choice rimosso."""
        data = {
            "tool_choice": {"type": "any"},
            "messages": [],
        }
        sanitize_tool_choice(data)
        assert "tool_choice" not in data

    def test_tool_choice_assente_non_eccezione(self):
        """tool_choice mancante -> nessuna eccezione."""
        data = {"tools": []}
        sanitize_tool_choice(data)
        # Nessuna eccezione, la funzione termina regolarmente
        assert True

    def test_tool_choice_stringa_non_eccezione(self):
        """tool_choice = "auto" (stringa, non dict) -> nessuna modifica, nessuna eccezione."""
        data = {
            "tools": [],
            "tool_choice": "auto",
        }
        sanitize_tool_choice(data)
        # La stringa rimane: il controllo isinstance(tc, dict) la lascia passare
        assert data["tool_choice"] == "auto"


class TestToolChoiceConToolsPresenti:
    """Comportamento di tool_choice quando rimangono tool nell'array."""

    def test_type_any_con_tool_invariato(self):
        """BRACCIO DI CONTROLLO 1: type=any con un tool presente -> non toccato."""
        data = {
            "tools": [{"name": "web_search", "description": "search"}],
            "tool_choice": {"type": "any"},
        }
        originale = data["tool_choice"].copy()
        sanitize_tool_choice(data)
        assert data["tool_choice"] == originale
        # Conferma che NON è stato degradato ad auto
        assert data["tool_choice"]["type"] == "any"

    def test_type_tool_che_esiste_invariato(self):
        """BRACCIO DI CONTROLLO 2: type=tool che punta a un tool ANCORA PRESENTE."""
        data = {
            "tools": [
                {"name": "web_search", "description": "search"},
                {"name": "code_execution", "description": "exec"},
            ],
            "tool_choice": {"type": "tool", "name": "web_search"},
        }
        originale = data["tool_choice"].copy()
        sanitize_tool_choice(data)
        assert data["tool_choice"] == originale

    def test_type_tool_che_non_esiste_degrada(self):
        """BRACCIO DI CONTROLLO 3: type=tool che punta a un tool rimosso, altri presenti."""
        data = {
            "tools": [
                {"name": "code_execution", "description": "exec"},
            ],
            "tool_choice": {"type": "tool", "name": "web_search"},  # Non esiste
        }
        sanitize_tool_choice(data)
        # Deve degradare a auto
        assert data["tool_choice"] == {"type": "auto"}

    def test_type_none_con_tool_invariato(self):
        """type=none con tool presente -> non toccato (nessun tool sarà usato)."""
        data = {
            "tools": [{"name": "web_search", "description": "search"}],
            "tool_choice": {"type": "none"},
        }
        originale = data["tool_choice"].copy()
        sanitize_tool_choice(data)
        assert data["tool_choice"] == originale


class TestMutazioneInPlace:
    """Verifica che la funzione muti il dict sul posto e restituisca None."""

    def test_mutazione_in_place_rimosso(self):
        """La chiave tool_choice viene rimossa dal dict originale."""
        data = {
            "tools": [],
            "tool_choice": {"type": "any"},
        }
        id_data_prima = id(data)
        risultato = sanitize_tool_choice(data)
        assert id(data) == id_data_prima  # È lo stesso dict
        assert risultato is None  # Restituisce None
        assert "tool_choice" not in data

    def test_mutazione_in_place_degradato(self):
        """La chiave tool_choice viene modificata nel dict originale."""
        data = {
            "tools": [{"name": "bash"}],
            "tool_choice": {"type": "tool", "name": "web_search"},
        }
        id_data_prima = id(data)
        risultato = sanitize_tool_choice(data)
        assert id(data) == id_data_prima  # È lo stesso dict
        assert risultato is None  # Restituisce None
        assert data["tool_choice"] == {"type": "auto"}
