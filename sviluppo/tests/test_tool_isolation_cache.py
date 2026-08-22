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


# ---------------------------------------------------------------------------
# Il breakpoint va preservato da TUTTI i percorsi di strip, non solo da questo.
# Prima del 2026-08-16 la logica esisteva in tre copie: due divergenti e una
# assente (lo strip dei server tool verso MiniMax).
# ---------------------------------------------------------------------------

def test_strip_minimax_preserva_il_breakpoint():
    """Un server tool Anthropic in coda porta con se' il cache_control.

    Claude Code chiude `tools` con il breakpoint sull'ultimo elemento. Se quello
    e' un server tool (privo di input_schema), lo strip verso MiniMax lo rimuove:
    senza trasferimento il blocco tools resta senza breakpoint e il caching non si
    estende alla conversazione."""
    from minimax_body import strip_server_tools_for_minimax
    data = {
        'tools': [_tool('Bash'), _tool('Read'),
                  {'name': 'web_search_20250305', 'type': 'web_search_20250305',
                   'cache_control': {'type': 'ephemeral'}}],
        'messages': [],
    }
    strip_server_tools_for_minimax(data)
    rimasti = data['tools']
    assert len(rimasti) == 2, 'il server tool doveva essere rimosso'
    assert rimasti[-1].get('cache_control') == {'type': 'ephemeral'}, \
        'breakpoint perso: il caching non si estende oltre i tool'


def test_preserva_cache_control_con_elemento_spurio_in_coda():
    """Un `tools` con un elemento non-dict in coda non deve far perdere il
    breakpoint (ne' sollevare TypeError): va risalito all'ultimo dict."""
    from tool_isolation import preserva_cache_control
    originali = [_tool('Bash'), _tool('mcp__minimax__x', cc=True), 'spurio']
    kept = [_tool('Bash'), 'spurio']
    preserva_cache_control(originali, kept)
    assert kept[0].get('cache_control') == {'type': 'ephemeral'}


# ---------------------------------------------------------------------------
# Test strip_heavy_mcp_for_glm (2026-08-22): rimuove connettori Gmail/Calendar/Drive/Canva
# ---------------------------------------------------------------------------

def test_strip_heavy_mcp_rimuove_gmail():
    """Tool Gmail con maiuscole miste viene rimosso."""
    from tool_isolation import strip_heavy_mcp_for_glm
    tools = [
        {'name': 'mcp__claude_ai_Gmail__search_threads', 'description': 'x', 'input_schema': {'type': 'object'}},
        {'name': 'Bash', 'description': 'shell', 'input_schema': {'type': 'object'}},
    ]
    body = json.dumps({'model': 'glm-4.7', 'tools': tools, 'messages': []}).encode()
    out = strip_heavy_mcp_for_glm(body)
    result = json.loads(out).get('tools', [])
    names = [t['name'] for t in result]
    assert 'mcp__claude_ai_Gmail__search_threads' not in names, f"Tool should be removed, got {names}"
    assert 'Bash' in names, f"Bash should remain, got {names}"
    assert len(result) == 1


def test_strip_heavy_mcp_nessuna_modifica_senza_tool_pesanti():
    """Body senza tool pesanti passa byte-per-byte identico."""
    from tool_isolation import strip_heavy_mcp_for_glm
    tools = [
        {'name': 'Bash', 'description': 'shell', 'input_schema': {'type': 'object'}},
        {'name': 'Read', 'description': 'file', 'input_schema': {'type': 'object'}},
    ]
    body = json.dumps({'model': 'glm-4.7', 'tools': tools, 'messages': []}).encode()
    out = strip_heavy_mcp_for_glm(body)
    assert out == body, f"Body should be unchanged, got different bytes"


def test_strip_heavy_mcp_environ_disabled():
    """Con AIROUTER_GLM_MCP_FILTER=0 il filtro e' no-op."""
    import os
    from tool_isolation import strip_heavy_mcp_for_glm
    tools = [
        {'name': 'mcp__claude_ai_gmail__send_email', 'description': 'x', 'input_schema': {'type': 'object'}},
        {'name': 'Bash', 'description': 'shell', 'input_schema': {'type': 'object'}},
    ]
    body = json.dumps({'model': 'glm-4.7', 'tools': tools, 'messages': []}).encode()
    orig_env = os.environ.get('AIROUTER_GLM_MCP_FILTER')
    os.environ['AIROUTER_GLM_MCP_FILTER'] = '0'
    try:
        out = strip_heavy_mcp_for_glm(body)
        assert out == body, "With filter disabled, body should be unchanged"
    finally:
        if orig_env is None:
            os.environ.pop('AIROUTER_GLM_MCP_FILTER', None)
        else:
            os.environ['AIROUTER_GLM_MCP_FILTER'] = orig_env


def test_strip_heavy_mcp_preserva_cache_control():
    """Il breakpoint cache_control si sposta sull'ultimo tool rimasto."""
    from tool_isolation import strip_heavy_mcp_for_glm
    tools = [
        {'name': 'Read', 'description': 'f', 'input_schema': {'type': 'object'}},
        {'name': 'mcp__claude_ai_google_drive__list_files', 'description': 'x',
         'input_schema': {'type': 'object'}, 'cache_control': {'type': 'ephemeral'}},
    ]
    body = json.dumps({'model': 'glm-4.7', 'tools': tools, 'messages': []}).encode()
    out = strip_heavy_mcp_for_glm(body)
    result = json.loads(out).get('tools', [])
    assert len(result) == 1, f"Expected 1 tool, got {len(result)}"
    assert result[0].get('cache_control') == {'type': 'ephemeral'},         f"cache_control should transfer to remaining tool, got {result[0]}"
