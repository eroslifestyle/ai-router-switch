import sys
sys.path.insert(0, 'src')

import json
import importlib
import qwen_tool_trim


def test_rimuove_connettori_claude_ai():
    """Verifica che i connettori claude_ai vengano rimossi mantenendo solo Read."""
    tools = [
        {"name": "Read"},
        {"name": "mcp__claude_ai_Gmail__search"},
        {"name": "mcp__claude_ai_Google_Drive__read"}
    ]
    body = json.dumps({"tools": tools}).encode()
    
    result = qwen_tool_trim.strip_heavy_connectors(body)
    result_data = json.loads(result.decode())
    
    assert len(result_data["tools"]) == 1
    assert result_data["tools"][0]["name"] == "Read"


def test_trasferisce_cache_control():
    """Verifica che il cache_control dall'ultimo connettore rimosso venga trasferito al tool rimanente."""
    # Importiamo il modulo fresco perché dobbiamo manipolare STRIP_HEAVY_CONNECTORS
    mod = importlib.import_module('qwen_tool_trim')
    
    tools = [
        {"name": "Read"},
        {"name": "mcp__claude_ai_Gmail__search", "cache_control": {"type": "ephemeral"}}
    ]
    body = json.dumps({"tools": tools}).encode()
    
    result = mod.strip_heavy_connectors(body)
    result_data = json.loads(result.decode())
    
    # Dopo la rimozione del connettore, il cache_control dovrebbe essere sul tool rimanente
    # Senza questo trasferimento si perderebbe il breakpoint del prompt caching
    assert "cache_control" in result_data["tools"][0]
    assert result_data["tools"][0]["cache_control"]["type"] == "ephemeral"


def test_noop_senza_connettori():
    """Verifica che quando non ci sono connettori la funzione restituisca lo stesso oggetto."""
    tools = [{"name": "Read"}, {"name": "Bash"}]
    body = json.dumps({"tools": tools}).encode()
    
    result = qwen_tool_trim.strip_heavy_connectors(body)
    
    # Verifichiamo che sia lo stesso oggetto (identità, non uguaglianza)
    assert result is body


def test_name_none_non_solleva():
    """Verifica che un tool con name=None non causi eccezioni e venga conservato."""
    mod = importlib.import_module('qwen_tool_trim')
    
    tools = [
        {"name": None},
        {"name": "mcp__claude_ai_Gmail__search"}
    ]
    body = json.dumps({"tools": tools}).encode()
    
    # Non deve sollevare eccezioni
    result = mod.strip_heavy_connectors(body)
    result_data = json.loads(result.decode())
    
    # Il tool con name=None deve essere conservato
    assert len(result_data["tools"]) == 1
    assert result_data["tools"][0]["name"] is None


def test_elemento_non_dict_conservato():
    """Verifica che elementi non-dict vengano conservati insieme ai connettori rimossi."""
    mod = importlib.import_module('qwen_tool_trim')
    
    tools = ["stringa", {"name": "mcp__claude_ai_Gmail__search"}]
    body = json.dumps({"tools": tools}).encode()
    
    result = mod.strip_heavy_connectors(body)
    result_data = json.loads(result.decode())
    
    # La stringa deve sopravvivere
    assert "stringa" in result_data["tools"]
    assert len(result_data["tools"]) == 1


def test_rimuove_tools_e_tool_choice_se_resta_nulla():
    """Verifica che se rimuovendo connettori non resta nulla, si rimuovano anche tools e tool_choice."""
    mod = importlib.import_module('qwen_tool_trim')
    
    body = json.dumps({
        "tools": [{"name": "mcp__claude_ai_Gmail__search"}],
        "tool_choice": "auto"
    }).encode()
    
    result = mod.strip_heavy_connectors(body)
    result_data = json.loads(result.decode())
    
    # Non dovrebbero esserci né 'tools' né 'tool_choice'
    assert "tools" not in result_data
    assert "tool_choice" not in result_data


def test_body_non_json_invariato():
    """Verifica che body non JSON venga lasciato invariato."""
    body = b"non-json"
    
    result = qwen_tool_trim.strip_heavy_connectors(body)
    
    # Verifichiamo identità
    assert result is body


def test_nessun_doppio_breakpoint():
    """Verifica che se sia il connettore che il tool hanno cache_control, ne rimanga solo uno."""
    mod = importlib.import_module('qwen_tool_trim')
    
    tools = [
        {"name": "Read", "cache_control": {"type": "ephemeral"}},
        {"name": "mcp__claude_ai_Gmail__search", "cache_control": {"type": "ephemeral"}}
    ]
    body = json.dumps({"tools": tools}).encode()
    
    result = mod.strip_heavy_connectors(body)
    result_data = json.loads(result.decode())
    
    # Ci deve essere esattamente un tool con cache_control
    tools_with_cache = [t for t in result_data["tools"] if "cache_control" in t]
    assert len(tools_with_cache) == 1
    assert tools_with_cache[0]["name"] == "Read"


def test_flag_disattivato_e_noop(monkeypatch):
    """Verifica che con la variabile d'ambiente disabilitata la funzione faccia noop."""
    # Imposta la variabile d'ambiente
    monkeypatch.setenv("AIROUTER_QWEN_STRIP_HEAVY_CONNECTORS", "0")
    
    # Ricarica il modulo per leggere la nuova variabile
    mod = importlib.reload(importlib.import_module('qwen_tool_trim'))
    
    tools = [{"name": "Read"}, {"name": "mcp__claude_ai_Gmail__search"}]
    body = json.dumps({"tools": tools}).encode()
    
    result = mod.strip_heavy_connectors(body)
    
    # Deve restituire lo stesso oggetto
    assert result is body
    
    # Ripristina il modulo senza la variabile
    monkeypatch.delenv("AIROUTER_QWEN_STRIP_HEAVY_CONNECTORS", raising=False)
    importlib.reload(importlib.import_module('qwen_tool_trim'))
