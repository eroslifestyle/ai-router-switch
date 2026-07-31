"""Test per il modulo router_utils, specificamente per le funzionalità
di telemetria degli strumenti (tools)."""
import json
import os
import sys
import importlib
from pathlib import Path
import pytest


def _import_router_utils(flag_acceso: bool):
    """Importa il modulo router_utils con il flag di telemetria specificato."""
    if flag_acceso:
        os.environ["AIROUTER_TOOLS_TELEMETRY"] = "1"
    else:
        os.environ.pop("AIROUTER_TOOLS_TELEMETRY", None)

    sys.modules.pop("router_utils", None)

    src_path = Path(__file__).resolve().parents[2] / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    return importlib.import_module("router_utils")


def test_flag_spento_ritorna_none():
    """Flag spento + body con tools deve ritornare None."""
    ru = _import_router_utils(False)

    body = json.dumps({"tools": [{"name": "Bash"}, {"name": "Read"}]}).encode()
    result = ru.collect_tools_stats(body)

    assert result is None, "Con flag spento deve ritornare None anche con tools"


def test_body_vuoto_e_malformato():
    """Body vuoto o malformato deve ritornare None."""
    ru = _import_router_utils(True)

    assert ru.collect_tools_stats(b"") is None, "Body vuoto deve dare None"
    assert ru.collect_tools_stats(b"non-json") is None, "Non JSON deve dare None"
    assert ru.collect_tools_stats(b'"stringa"') is None, "Stringa JSON deve dare None"
    assert ru.collect_tools_stats(b'{"model":"x"}') is None, "Senza tools deve dare None"
    assert ru.collect_tools_stats(b'{"tools":[]}') is None, "Tools vuoto deve dare None"


def test_conteggio_tool_semplici():
    """Verifica conteggio di 3 tool non MCP (Bash, Read, Edit)."""
    ru = _import_router_utils(True)

    body = json.dumps({
        "tools": [
            {"name": "Bash"},
            {"name": "Read"},
            {"name": "Edit"}
        ]
    }).encode()

    result = ru.collect_tools_stats(body)

    assert result is not None, "Non deve essere None con tools validi"
    assert result["tools_count"] == 3, "Tre tool semplici"
    assert result["tools_mcp_count"] == 0, "Nessun tool MCP"
    assert result["tools_mcp_servers"] == {}, "Nessun server MCP"
    assert result["tools_bytes"] > 0, "tools_bytes deve essere > 0"


def test_riconoscimento_server_mcp():
    """Verifica riconoscimento server MCP con 3 tool su 2 server."""
    ru = _import_router_utils(True)

    body = json.dumps({
        "tools": [
            {"name": "Bash"},
            {"name": "mcp__claude_ai_Gmail__create_draft"},
            {"name": "mcp__claude_ai_Gmail__get_message"},
            {"name": "mcp__zai__web_search_prime"}
        ]
    }).encode()

    result = ru.collect_tools_stats(body)

    assert result is not None, "Non deve essere None"
    assert result["tools_count"] == 4, "Quattro tool totali"
    assert result["tools_mcp_count"] == 3, "Tre tool MCP"

    servers_attesi = {"claude_ai_Gmail", "zai"}
    assert set(result["tools_mcp_servers"].keys()) == servers_attesi, \
        f"Server devono essere {servers_attesi}, ottenuto {set(result['tools_mcp_servers'].keys())}"

    somma_valori = sum(result["tools_mcp_servers"].values())
    diff = result["tools_mcp_bytes"] - somma_valori
    diff_attesa = 2 + (3 - 1)
    assert diff == diff_attesa, \
        f"Differenza tra bytes e somma deve essere {diff_attesa}, ottenuto {diff}"

    assert result["tools_mcp_bytes"] < result["tools_bytes"], \
        "tools_mcp_bytes deve essere minore di tools_bytes"


def test_nome_tool_mancante_o_non_stringa():
    """Verifica gestione tool con nome mancante, None o non dict."""
    ru = _import_router_utils(True)

    body = json.dumps({
        "tools": [
            {"name": None},
            {"nessun_nome": 1},
            "non-un-dict",
            {"name": "mcp__test__valid_tool"}
        ]
    }).encode()

    result = ru.collect_tools_stats(body)

    assert result is not None, "Non deve sollevare eccezioni"
    assert result["tools_count"] == 4, "Quattro elementi totali"
    assert result["tools_mcp_count"] == 1, "Un solo tool MCP valido"


def test_formato_mcp_irregolare():
    """Tool MCP senza separatore valido finisce in 'sconosciuto'."""
    ru = _import_router_utils(True)

    body = json.dumps({
        "tools": [
            {"name": "mcp__senzaseparatore"}
        ]
    }).encode()

    result = ru.collect_tools_stats(body)

    assert result is not None, "Non deve essere None"
    assert result["tools_mcp_count"] == 1, "Un tool MCP rilevato"
    assert "sconosciuto" in result["tools_mcp_servers"], \
        "Deve finire nel server 'sconosciuto'"


def test_sidecar_invariato_senza_tools(tmp_path):
    """Verifica che senza tools il sidecar contenga solo le chiavi originali."""
    ru = _import_router_utils(True)

    sidecar_file = tmp_path / "usage.jsonl"
    original_sidecar = ru.USAGE_SIDECAR
    ru.USAGE_SIDECAR = sidecar_file  # deve restare un Path: log_router_usage usa .parent.mkdir()

    try:
        ru.log_router_usage(
            chat_id="chat123",
            orig="model-a",
            final="model-b",
            usage={"input_tokens": 100, "output_tokens": 50, "cache_read": 10, "cache_creation": 5},
            mode="chat",
            client="test-client",
            status=200,
            path="/v1/chat"
        )

        content = sidecar_file.read_text()
        line = json.loads(content.strip())

        # Chiavi originali (backward-compat) + chiavi self-healing Fase 1 (sempre presenti).
        # La telemetria self-healing (outcome/stop_reason/blocks/task_class) e' attiva di
        # default: rileva i "valori zero" (200 con 0 text blocks). I campi tools_* restano
        # invece gated dal flag AIROUTER_TOOLS_TELEMETRY (qui non devono apparire).
        chiavi_originali = {
            "ts", "chat", "orig", "final", "mode", "client",
            "status", "input_tokens", "output_tokens", "cache_read", "cache_creation"
        }
        chiavi_self_healing = {"stop_reason", "text_blocks", "thinking_blocks",
                               "tool_use_blocks", "outcome", "task_class"}
        for k in chiavi_originali | chiavi_self_healing:
            assert k in line, f"Chiave {k} mancante: {set(line.keys())}"
        assert "tools_count" not in line, "Senza tools non deve contenere tools_count"
        assert "tools_bytes" not in line, "Senza tools non deve contenere tools_bytes"
    finally:
        ru.USAGE_SIDECAR = original_sidecar


def test_sidecar_arricchito_con_tools(tmp_path):
    """Verifica che con tools il sidecar contenga le chiavi arricchite."""
    ru = _import_router_utils(True)

    sidecar_file = tmp_path / "usage_tools.jsonl"
    original_sidecar = ru.USAGE_SIDECAR
    ru.USAGE_SIDECAR = sidecar_file  # deve restare un Path: log_router_usage usa .parent.mkdir()

    try:
        ru.log_router_usage(
            chat_id="chat456",
            orig="model-x",
            final="model-y",
            usage={"input_tokens": 200, "output_tokens": 100, "cache_read": 20, "cache_creation": 10},
            mode="completion",
            client="test-client-2",
            status=201,
            path="/v1/completions",
            tools={"tools_count": 5, "tools_bytes": 1234}
        )

        content = sidecar_file.read_text()
        line = json.loads(content.strip())

        chiavi_originali = {
            "ts", "chat", "orig", "final", "mode", "client",
            "status", "input_tokens", "output_tokens", "cache_read", "cache_creation"
        }
        for chiave in chiavi_originali:
            assert chiave in line, f"Chiave originale {chiave} deve essere presente"

        assert line["tools_count"] == 5, "tools_count deve essere 5"
        assert line["tools_bytes"] == 1234, "tools_bytes deve essere 1234"
    finally:
        ru.USAGE_SIDECAR = original_sidecar
