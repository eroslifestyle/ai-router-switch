"""Coerenza del blocco `tools` dopo gli strip: i marcatori `defer_loading` non
devono mai restare senza il tool di ricerca che li espande.

Il tool deferral di Claude Code ha DUE ingredienti e vanno insieme: i tool
marcati `defer_loading` e un tool di ricerca che li risolve. Quest'ultimo non ha
`input_schema`, quindi ogni strip che rimuove i server-tool Anthropic se lo
porta via — e senza questa pulizia lascerebbe il body a dichiarare tool "da
caricare dopo" con nulla che possa caricarli.
"""

import json
import sys

import pytest

sys.path.insert(0, 'src')
sys.path.insert(0, '.')

import minimax_body
import qwen_tool_trim
from tool_isolation import filter_tools_for_backend, sanitize_defer_loading

TOOL_RICERCA = {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"}


def _tools(con_ricerca=True, con_connettore=False, con_estraneo=False):
    """Costruisce una lista di tool realistica. Le copie sono difensive: alcune
    funzioni sotto test mutano i dict sul posto."""
    tools = []
    if con_ricerca:
        tools.append(dict(TOOL_RICERCA))
    tools.append({"name": "Read", "input_schema": {"type": "object"}, "defer_loading": True})
    tools.append({"name": "Bash", "input_schema": {"type": "object"}, "defer_loading": True})
    if con_estraneo:
        tools.append({"name": "mcp__MiniMax__web_search", "input_schema": {"type": "object"}})
    tools.append({"name": "Agent", "input_schema": {"type": "object"},
                  "cache_control": {"type": "ephemeral"}})
    if con_connettore:
        tools.append({"name": "mcp__claude_ai_Gmail__search_threads",
                      "input_schema": {"type": "object"}, "defer_loading": True})
    return tools


def _body(tools, tool_choice=None):
    """Serializza il body in bytes, come lo riceve il proxy."""
    payload = {"model": "claude-opus-5", "max_tokens": 4096, "tools": tools,
               "messages": [{"role": "user", "content": "ciao"}]}
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return json.dumps(payload).encode()


def _nomi(tools):
    return [t.get("name") for t in tools if isinstance(t, dict)]


def _defer(tools):
    """Nomi dei tool che portano ancora il marcatore defer_loading."""
    return [t.get("name") for t in tools if isinstance(t, dict) and t.get("defer_loading")]


@pytest.mark.parametrize("backend", ["minimax", "glm", "qwen"])
def test_filter_rimuove_ricerca_e_marcatori(backend):
    """Sui tre backend che non conoscono il tool search, marcatori e tool di ricerca escono insieme."""
    out = filter_tools_for_backend(_body(_tools()), backend)
    data = json.loads(out)
    assert "tool_search_tool_regex" not in _nomi(data["tools"])
    assert _defer(data["tools"]) == []


def test_filter_anthropic_conserva_ricerca_e_marcatori():
    """Verso Anthropic il tool di ricerca resta, quindi i marcatori vanno conservati."""
    out = filter_tools_for_backend(_body(_tools()), "anthropic")
    data = json.loads(out)
    assert "tool_search_tool_regex" in _nomi(data["tools"])
    assert _defer(data["tools"]) == ["Read", "Bash"]


def test_filter_anthropic_conserva_marcatori_anche_dopo_uno_strip():
    """Controprova forte: anche quando lo strip rimuove davvero un tool, se il
    tool di ricerca sopravvive i marcatori non vengono toccati — la pulizia
    dipende dal tool di ricerca, non dall'aver strippato qualcosa."""
    out = filter_tools_for_backend(_body(_tools(con_estraneo=True)), "anthropic")
    data = json.loads(out)
    assert "mcp__MiniMax__web_search" not in _nomi(data["tools"])
    assert "tool_search_tool_regex" in _nomi(data["tools"])
    assert _defer(data["tools"]) == ["Read", "Bash"]


def test_ordine_di_produzione_qwen():
    """Nell'ordine reale di qwen_backend (filter, poi strip dei connettori) non resta alcun marcatore orfano."""
    out = filter_tools_for_backend(_body(_tools(con_connettore=True)), "qwen")
    data = json.loads(qwen_tool_trim.strip_heavy_connectors(out))
    assert "mcp__claude_ai_Gmail__search_threads" not in _nomi(data["tools"])
    assert _defer(data["tools"]) == []


def test_strip_heavy_connectors_da_sola_pulisce_i_marcatori():
    """Anche invocato da solo, lo strip dei connettori non lascia marcatori senza tool di ricerca."""
    out = qwen_tool_trim.strip_heavy_connectors(
        _body(_tools(con_ricerca=False, con_connettore=True)))
    data = json.loads(out)
    assert "mcp__claude_ai_Gmail__search_threads" not in _nomi(data["tools"])
    assert _defer(data["tools"]) == []


def test_strip_server_tools_for_minimax_non_regressione():
    """La deduplica non cambia il comportamento del path MiniMax: ricerca e marcatori spariscono insieme."""
    data = {"model": "claude-opus-5", "max_tokens": 4096, "tools": _tools(),
            "messages": [{"role": "user", "content": "ciao"}]}
    minimax_body.strip_server_tools_for_minimax(data)
    assert "tool_search_tool_regex" not in _nomi(data["tools"])
    assert _defer(data["tools"]) == []


def test_tool_choice_e_marcatori_sanati_insieme():
    """Le due sanificazioni convivono: tool_choice degrada ad auto e nessun marcatore resta orfano."""
    body = _body(_tools(con_connettore=True),
                 tool_choice={"type": "tool", "name": "mcp__claude_ai_Gmail__search_threads"})
    out = filter_tools_for_backend(body, "qwen")
    data = json.loads(qwen_tool_trim.strip_heavy_connectors(out))
    assert data.get("tool_choice") == {"type": "auto"}
    assert _defer(data["tools"]) == []


def test_sanitize_defer_senza_tools():
    """Senza il campo tools la funzione non fa nulla."""
    assert sanitize_defer_loading({"model": "claude-opus-5"}) is False


def test_sanitize_defer_tools_non_lista():
    """Un campo tools malformato non deve far sollevare la sanificazione."""
    assert sanitize_defer_loading({"tools": "non una lista"}) is False


def test_sanitize_defer_elementi_non_dict():
    """Elementi non-dict dentro tools vengono ignorati senza eccezioni."""
    assert sanitize_defer_loading({"tools": ["stringa", 123, None]}) is False


def test_sanitize_defer_con_ricerca_non_tocca_i_marcatori():
    """Con il tool di ricerca presente il marcatore resta dov'e'."""
    data = {"tools": [dict(TOOL_RICERCA), {"name": "Read", "defer_loading": True}]}
    assert sanitize_defer_loading(data) is False
    assert _defer(data["tools"]) == ["Read"]


def test_breakpoint_cache_control_non_perso():
    """La pulizia dei marcatori non deve far perdere il breakpoint del prompt caching."""
    out = filter_tools_for_backend(_body(_tools()), "glm")
    data = json.loads(out)
    assert any(t.get("cache_control") for t in data["tools"] if isinstance(t, dict))


# Il tool estraneo porta con se' il cache_control: rimuoverlo obbliga il
# trasferimento del breakpoint, che e' il punto dove si annidava il TypeError.
_ESTRANEO_CON_BREAKPOINT = {"name": "mcp__MiniMax__web_search", "input_schema": {},
                            "cache_control": {"type": "ephemeral"}}


def test_elemento_non_dict_in_coda_non_solleva():
    """Un elemento spurio non-dict in coda a tools non deve far sollevare il trasferimento del breakpoint."""
    out = filter_tools_for_backend(_body([dict(_ESTRANEO_CON_BREAKPOINT), "spuria"]), "glm")
    assert json.loads(out)["tools"] == ["spuria"]


def test_nessun_dict_superstite_non_solleva():
    """Se fra i tool superstiti non c'e' alcun dict, il breakpoint non viene posato e non si solleva."""
    out = filter_tools_for_backend(_body([dict(_ESTRANEO_CON_BREAKPOINT), "spuria", 123]), "glm")
    assert json.loads(out)["tools"] == ["spuria", 123]


def test_breakpoint_va_sull_ultimo_dict():
    """Non-regressione: il cache_control del tool rimosso finisce sull'ultimo tool superstite che sia un dict."""
    tools = [dict(_ESTRANEO_CON_BREAKPOINT), {"name": "Read", "input_schema": {}}]
    superstiti = json.loads(filter_tools_for_backend(_body(tools), "glm"))["tools"]
    assert _nomi(superstiti) == ["Read"]
    assert superstiti[-1]["cache_control"] == {"type": "ephemeral"}
