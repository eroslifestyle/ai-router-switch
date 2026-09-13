"""tool_reference della beta tool-search: chiave `tool_name`, non `name`.

Il tool-search di Claude Code referenzia i tool differiti con elementi
{"type": "tool_reference", "tool_name": ...} nell'array `tools`: senza leggere
`tool_name` il filtro per-brand non li riconosce e Anthropic risponde
400 "Tool reference '...' not found in available tools".
"""
import json
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, '.')
from tool_isolation import filter_tools_for_backend


def _body(tools, messages=None):
    payload = {
        'model': 'claude-opus-5',
        'tools': tools,
        'messages': messages or [{'role': 'user', 'content': 'ciao'}],
    }
    return json.dumps(payload).encode()


def _data(out):
    return json.loads(out)


def test_tool_reference_brand_zai_rimosso_verso_anthropic():
    tools = [
        {'name': 'Bash', 'input_schema': {'type': 'object'}},
        {'type': 'tool_reference', 'tool_name': 'mcp__zai__web_search_prime'},
    ]
    out = _data(filter_tools_for_backend(_body(tools), 'anthropic'))
    names = [t.get('name') or t.get('tool_name') for t in out['tools']]
    assert 'mcp__zai__web_search_prime' not in names, names


def test_tool_bash_sopravvive():
    tools = [
        {'name': 'Bash', 'input_schema': {'type': 'object'}},
        {'type': 'tool_reference', 'tool_name': 'mcp__zai__web_search_prime'},
    ]
    out = _data(filter_tools_for_backend(_body(tools), 'anthropic'))
    names = [t.get('name') or t.get('tool_name') for t in out['tools']]
    assert 'Bash' in names, names


def test_tool_reference_zai_non_rimosso_verso_glm():
    tools = [
        {'name': 'Bash', 'input_schema': {'type': 'object'}},
        {'type': 'tool_reference', 'tool_name': 'mcp__zai__web_search_prime'},
    ]
    out = _data(filter_tools_for_backend(_body(tools), 'glm'))
    names = [t.get('name') or t.get('tool_name') for t in out['tools']]
    assert 'mcp__zai__web_search_prime' in names, names


def test_server_tool_use_esterno_in_history_demotato():
    tools = [{'name': 'Bash', 'input_schema': {'type': 'object'}}]
    messages = [{'role': 'assistant', 'content': [
        {'type': 'server_tool_use', 'id': 'srv_1',
         'name': 'mcp__MiniMax__web_search', 'input': {'q': 'x'}},
    ]}]
    out = _data(filter_tools_for_backend(_body(tools, messages), 'glm'))
    blocks = out['messages'][0]['content']
    assert blocks[0]['type'] == 'text', blocks
    assert 'mcp__MiniMax__web_search' in blocks[0]['text'], blocks


def test_mcp_tool_use_esterno_in_history_demotato():
    tools = [{'name': 'Bash', 'input_schema': {'type': 'object'}}]
    messages = [{'role': 'assistant', 'content': [
        {'type': 'mcp_tool_use', 'id': 'mcp_1',
         'name': 'mcp__MiniMax__understand_image', 'input': {}},
    ]}]
    out = _data(filter_tools_for_backend(_body(tools, messages), 'anthropic'))
    blocks = out['messages'][0]['content']
    assert blocks[0]['type'] == 'text', blocks


def test_tool_reference_in_history_demotato():
    tools = [{'name': 'Bash', 'input_schema': {'type': 'object'}}]
    messages = [{'role': 'user', 'content': [
        {'type': 'tool_reference', 'tool_name': 'mcp__zai__web_search_prime'},
    ]}]
    out = _data(filter_tools_for_backend(_body(tools, messages), 'anthropic'))
    blocks = out['messages'][0]['content']
    # ponytail: il nome resta nel testo convertito (schema demote, contesto leggibile);
    # il bug e' il blocco tool_reference residuo che fa rispondere 400 all'API.
    assert blocks[0]['type'] == 'text', blocks
