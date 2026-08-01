import json
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, '.')
from tool_isolation import filter_tools_for_backend

def _tool(name, cc=False):
    tool = {
        'name': name,
        'description': 'x',
        'input_schema': {'type': 'object'}
    }
    if cc:
        tool['cache_control'] = {'type': 'ephemeral'}
    return tool

def _body(tools):
    payload = {
        'model': 'claude-opus-5',
        'tools': tools,
        'messages': [{'role': 'user', 'content': 'ciao'}]
    }
    return json.dumps(payload).encode()

def _tools_out(out_bytes):
    return json.loads(out_bytes).get('tools', [])

def test_cache_control_trasferito_quando_il_tool_rimosso_lo_portava():
    tools = [_tool('Bash'), _tool('Read'), _tool('mcp__zai__web_search_prime', cc=True)]
    out = filter_tools_for_backend(_body(tools), 'anthropic')
    result = _tools_out(out)
    tool_names = [t['name'] for t in result]
    assert 'mcp__zai__web_search_prime' not in tool_names, \
        f"Tool should be removed, got tools: {tool_names}"
    cc_count = sum(1 for t in result if 'cache_control' in t)
    assert cc_count == 1, \
        f"Expected exactly 1 cache_control, got {cc_count}. Tools: {result}"
    assert 'cache_control' in result[-1], \
        f"Last tool should have cache_control, but tools: {result}"
    assert result[-1]['name'] == 'Read', \
        f"Last tool should be 'Read', got '{result[-1]['name']}'. Tools: {result}"

def test_cache_control_non_duplicato_se_gia_presente_tra_i_rimasti():
    tools = [_tool('Bash'), _tool('Read', cc=True), _tool('mcp__zai__web_search_prime', cc=True)]
    out = filter_tools_for_backend(_body(tools), 'anthropic')
    result = _tools_out(out)
    tool_names = [t['name'] for t in result]
    assert 'mcp__zai__web_search_prime' not in tool_names, \
        f"Tool should be removed, got tools: {tool_names}"
    cc_count = sum(1 for t in result if 'cache_control' in t)
    assert cc_count == 1, \
        f"Expected exactly 1 cache_control, got {cc_count}. Tools: {result}"
    assert any(t.get('cache_control') for t in result if t['name'] == 'Read'), \
        f"Tool 'Read' should have cache_control, but tools: {result}"
    for t in result:
        if t['name'] != 'Read':
            assert 'cache_control' not in t, \
                f"Tool {t['name']} should not have cache_control, but tools: {result}"

def test_nessuna_modifica_se_non_si_rimuove_nulla():
    tools = [_tool('Bash'), _tool('Read', cc=True)]
    body = _body(tools)
    out = filter_tools_for_backend(body, 'anthropic')
    assert out == body, \
        f"Output should be identical to input when no tool removed. Input: {body}, Output: {out}"

def test_strip_senza_cache_control_non_ne_inventa():
    tools = [_tool('Bash'), _tool('mcp__zai__web_search_prime')]
    out = filter_tools_for_backend(_body(tools), 'anthropic')
    result = _tools_out(out)
    tool_names = [t['name'] for t in result]
    assert 'mcp__zai__web_search_prime' not in tool_names, \
        f"Tool should be removed, got tools: {tool_names}"
    cc_count = sum(1 for t in result if 'cache_control' in t)
    assert cc_count == 0, \
        f"Expected no cache_control, got {cc_count}. Tools: {result}"
    assert len(result) == 1, \
        f"Expected 1 tool, got {len(result)}. Tools: {result}"
    assert result[0]['name'] == 'Bash', \
        f"Only 'Bash' should remain, got '{result[0]['name']}'"

def test_tutti_i_tool_rimossi_non_esplode():
    tools = [_tool('mcp__zai__web_search_prime', cc=True)]
    out = filter_tools_for_backend(_body(tools), 'anthropic')
    d = json.loads(out)
    if 'tools' in d:
        assert d['tools'] == [], \
            f"Expected 'tools' to be absent or empty list, got {d['tools']}"
    # No exception thrown: test passes
