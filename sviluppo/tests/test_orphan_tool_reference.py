import json
import pathlib
import sys
import pytest
sys.path.insert(0, 'src')
sys.path.insert(0, '.')
from tool_isolation import drop_orphan_tool_references


def _tool(name):
    return {
        'name': name,
        'description': 'test tool',
        'input_schema': {'type': 'object'}
    }


def _body(tools, messages):
    return json.dumps({
        'model': 'glm-4.7',
        'tools': tools,
        'messages': messages
    }).encode()


def test_rimuove_tool_reference_orfani_da_tool_search():
    """Rimuove tool_reference orfani da tool_search_tool_result.content.tool_references."""
    tools = [_tool('Read')]
    messages = [
        {
            'role': 'user',
            'content': [{'type': 'text', 'text': 'help'}]
        },
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_search_tool_result',
                    'content': {
                        'type': 'tool_search_tool_search_result',
                        'tool_references': [
                            {'type': 'tool_reference', 'tool_name': 'Read'},
                            {'type': 'tool_reference', 'tool_name': 'mcp__claude_ai_Google_Drive__search_files'}
                        ]
                    }
                }
            ]
        }
    ]
    body = _body(tools, messages)
    result = json.loads(drop_orphan_tool_references(body))
    refs = result['messages'][1]['content'][0]['content']['tool_references']
    tool_names = [ref.get('tool_name') for ref in refs if ref.get('type') == 'tool_reference']
    assert tool_names == ['Read'], f"Expected only ['Read'], got {tool_names}"


def test_tool_search_con_tutti_orfani_rimane_vuoto():
    """Se tutti i tool_reference sono orfani, il campo tool_references resta una lista vuota."""
    tools = [_tool('Read')]
    messages = [
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_search_tool_result',
                    'content': {
                        'type': 'tool_search_tool_search_result',
                        'tool_references': [
                            {'type': 'tool_reference', 'tool_name': 'mcp__claude_ai_Google_Drive__search_files'},
                            {'type': 'tool_reference', 'tool_name': 'Gmail'}
                        ]
                    }
                }
            ]
        }
    ]
    body = _body(tools, messages)
    result = json.loads(drop_orphan_tool_references(body))
    # Dopo la rimozione, tool_references diventa una lista vuota (verificato contro l'API il 2026-09-14)
    refs = result['messages'][0]['content'][0]['content']['tool_references']
    assert refs == [], f"Expected empty tool_references, got {refs}"


def test_body_senza_tool_reference_rimane_invariato():
    """Body senza tool_reference è ritornato identico."""
    tools = [_tool('Read'), _tool('Write')]
    messages = [
        {
            'role': 'user',
            'content': [{'type': 'text', 'text': 'hello'}]
        }
    ]
    body = _body(tools, messages)
    result = drop_orphan_tool_references(body)
    assert result is body, "Body without tool_reference should be returned as-is (identity)"


def test_tool_search_tool_result_con_tool_references_annidati():
    """Rimuove tool_reference orfani da dentro tool_search_tool_result.content.tool_references."""
    tools = [_tool('Read')]
    messages = [
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_search_tool_result',
                    'content': {
                        'type': 'tool_search_tool_search_result',
                        'tool_references': [
                            {'type': 'tool_reference', 'tool_name': 'Read'},
                            {'type': 'tool_reference', 'tool_name': 'mcp__claude_ai_Google_Drive__search_files'}
                        ]
                    }
                }
            ]
        }
    ]
    body = _body(tools, messages)
    result = json.loads(drop_orphan_tool_references(body))
    refs = result['messages'][0]['content'][0]['content']['tool_references']
    tool_names = [ref.get('tool_name') for ref in refs if ref.get('type') == 'tool_reference']
    assert tool_names == ['Read'], f"Expected only ['Read'], got {tool_names}"


def _orfani(data):
    presenti = {t.get("name") for t in data.get("tools") or []}
    trovati = []

    def visita(nodo):
        if isinstance(nodo, dict):
            if nodo.get("type") == "tool_reference" and nodo.get("tool_name") not in presenti:
                trovati.append(nodo.get("tool_name"))
            for v in nodo.values():
                visita(v)
        elif isinstance(nodo, list):
            for v in nodo:
                visita(v)

    visita(data.get("messages"))
    return trovati


def test_tool_result_lista_con_orfani():
    """Forma reale dei 1210: tool_reference nella LISTA content di un tool_result."""
    body = json.dumps({"tools": [{"name": "Read"}], "messages": [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "tool_reference", "tool_name": "Read"},
            {"type": "tool_reference", "tool_name": "mcp__claude_ai_Google_Drive__search_files"}]}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": [
            {"type": "tool_reference", "tool_name": "mcp__claude_ai_Gmail__x"}]}]},
    ]}).encode()
    out = json.loads(drop_orphan_tool_references(body))
    assert out["messages"][0]["content"][0]["content"] == [{"type": "tool_reference", "tool_name": "Read"}]
    assert out["messages"][1]["content"][0]["content"] == [{"type": "text", "text": "Tool loaded."}]


def test_integrazione_dump_reali_1210():
    """I 5 body reali presi 400 [1210] passano 200 dopo la pulizia (verificato a mano
    contro z.ai il 2026-10-01): qui si controlla che non resti nessun orfano."""
    dumps = sorted((pathlib.Path(__file__).resolve().parents[2] / "logs" / "glm-1210-bodies").glob("*.json"))
    if not dumps:
        pytest.skip("nessun dump in logs/glm-1210-bodies")
    for p in dumps:
        raw = json.load(open(p))["body"].encode()
        assert _orfani(json.loads(raw)), f"{p.name}: il dump dovrebbe contenere orfani"
        assert _orfani(json.loads(drop_orphan_tool_references(raw))) == [], p.name
