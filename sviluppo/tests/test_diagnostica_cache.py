"""
Test diagnostica cache estesa a tutti i provider (FIX 2026-08-20).

Prima: la diagnostica era attiva SOLO per "claude-direct" (Anthropic).
Dopo: attiva per TUTTI i provider (GLM, MiniMax, qwen, local, etc).

Stesso pattern del bug b4ed405: "GLM non cacha mai" era il misuratore cieco.
"""
import json
import pytest


def conta_cache_control(body_bytes: bytes | str) -> tuple[int, int, int, int]:
    """
    Conta i breakpoint cache_control nel body della request.
    
    Ritorna: (totale, system, messages, tools)
    
    Il body e quello INVIATO all'upstream - il router non lo riscrive.
    Le riscritture avvengono solo sul response stream.
    """
    _cc = _cc_s = _cc_m = _cc_t = 0
    try:
        _bj = json.loads(body_bytes.decode("utf-8", errors="replace")) if isinstance(body_bytes, bytes) else (json.loads(body_bytes) if isinstance(body_bytes, str) else {})
        if isinstance(_bj, dict):
            if "system" in _bj and isinstance(_bj["system"], list):
                for _item in _bj["system"]:
                    if isinstance(_item, dict) and "cache_control" in _item:
                        _cc += 1
                        _cc_s += 1
            if "messages" in _bj and isinstance(_bj["messages"], list):
                for _msg in _bj["messages"]:
                    if isinstance(_msg, dict):
                        if "cache_control" in _msg:
                            _cc += 1
                            _cc_m += 1
                        if "content" in _msg and isinstance(_msg["content"], list):
                            for _ct in _msg["content"]:
                                if isinstance(_ct, dict) and "cache_control" in _ct:
                                    _cc += 1
                                    _cc_m += 1
            if "tools" in _bj and isinstance(_bj["tools"], list):
                for _tool in _bj["tools"]:
                    if isinstance(_tool, dict) and "cache_control" in _tool:
                        _cc += 1
                        _cc_t += 1
    except Exception:
        pass
    return _cc, _cc_s, _cc_m, _cc_t


def genera_riga_cache(body_bytes: bytes, usage: dict, final_model: str) -> str:
    """
    Genera la riga di log cache per un dato body e usage.
    """
    _cc, _cc_s, _cc_m, _cc_t = conta_cache_control(body_bytes)
    _ch = int(usage.get("cache_read_input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
    if _cc > 0 and _ch == 0:
        return f"cache: MISS TOTALE [{final_model}] bp={_cc} input={usage['input_tokens']} (nessun cache_read/creation nella risposta)"
    elif _cc == 0:
        return f"cache: nessun breakpoint [{final_model}] input={usage['input_tokens']}"
    else:
        return f"cache: OK [{final_model}] bp=s{_cc_s}/m{_cc_m}/t{_cc_t} read={usage['cache_read_input_tokens']} creation={usage['cache_creation_input_tokens']} input={usage['input_tokens']}"


class TestDiagnosticaCacheEstesa:
    """Verifica che la diagnostica cache funzioni per TUTTI i provider."""

    def test_la_diagnostica_vale_anche_per_i_provider_non_anthropic(self):
        """
        Il cuore del fix: con un provider non-Anthropic e status 200,
        la riga cache: DEVE essere emessa.
        """
        body = json.dumps({
            "model": "glm-4.7",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1024
        }).encode()
        
        usage = {
            "input_tokens": 100,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 0
        }
        
        for provider in ["glm-4.7", "MiniMax-M2.7", "qwen3-72b", "code-max"]:
            riga = genera_riga_cache(body, usage, provider)
            assert riga is not None, f"Provider {provider}: riga None"
            assert "cache:" in riga, f"Provider {provider}: manca 'cache:'"
            assert provider in riga, f"Provider {provider}: manca il provider nella riga"

    def test_la_riga_dichiara_il_provider(self):
        """La riga emessa deve contenere il modello/provider per attribuzione."""
        body = json.dumps({
            "model": "test",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]
        }).encode()
        
        usage = {"input_tokens": 10, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 0}
        
        riga = genera_riga_cache(body, usage, "glm-5.3")
        assert "glm-5.3" in riga, f"Provider mancante: {riga}"
        
        riga = genera_riga_cache(body, usage, "MiniMax-M3")
        assert "MiniMax-M3" in riga, f"Provider mancante: {riga}"

    def test_i_tre_rami_restano_distinti(self):
        """
        Tre scenari -> tre righe diverse:
        1. body con breakpoint E cache nella risposta -> OK
        2. body con breakpoint MA nessun cache_read/creation -> MISS TOTALE
        3. body senza breakpoint -> nessun breakpoint
        """
        body_con_breakpoint = json.dumps({
            "model": "test",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]
        }).encode()
        
        body_senza_breakpoint = json.dumps({
            "model": "test",
            "messages": [{"role": "user", "content": "hello"}]
        }).encode()
        
        base_usage = {"input_tokens": 10}
        
        usage_ok = {**base_usage, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 3}
        riga_ok = genera_riga_cache(body_con_breakpoint, usage_ok, "claude-direct")
        assert "OK" in riga_ok, f"Atteso OK: {riga_ok}"
        assert "bp=" in riga_ok, f"Manca bp=: {riga_ok}"
        
        usage_miss = {**base_usage, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        riga_miss = genera_riga_cache(body_con_breakpoint, usage_miss, "glm-4.7")
        assert "MISS TOTALE" in riga_miss, f"Atteso MISS TOTALE: {riga_miss}"
        assert "bp=1" in riga_miss, f"Manca bp=1: {riga_miss}"
        
        riga_no_bp = genera_riga_cache(body_senza_breakpoint, usage_ok, "MiniMax-M2.7")
        assert "nessun breakpoint" in riga_no_bp, f"Atteso nessun breakpoint: {riga_no_bp}"
    
    def test_body_bytes_e_string_producono_stesso_risultato(self):
        """Il tipo del body (bytes o str) non deve cambiare il risultato."""
        body_dict = {
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}]
        }
        body_bytes = json.dumps(body_dict).encode()
        body_str = json.dumps(body_dict)
        
        usage = {"input_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        
        riga_bytes = genera_riga_cache(body_bytes, usage, "test")
        riga_str = genera_riga_cache(body_str, usage, "test")
        
        assert riga_bytes == riga_str, f"Diversi: {riga_bytes} vs {riga_str}"


        assert riga_bytes == riga_str, f"Diversi: {riga_bytes} vs {riga_str}"

    def test_un_usage_senza_campi_cache_non_rompe_il_relay(self):
        """
        MiniMax restituisce usage con SOLO input_tokens e output_tokens,
        senza campi cache. Verificato con probe reale oggi (2026-08-20).
        La diagnostica deve non sollevare e produrre una riga con zeri.
        """
        body = json.dumps({
            "model": "MiniMax-M2.7",
            "messages": [{"role": "user", "content": "hi"}]
        }).encode()

        # Usage MiniMax: solo input e output, niente cache
        usage_minimax = {"input_tokens": 50, "output_tokens": 30}

        # Non deve sollevare
        riga = genera_riga_cache(body, usage_minimax, "MiniMax-M2.7")

        # Deve produrre riga sensata: nessun errore, provider presente
        assert riga is not None, "La riga non deve essere None"
        assert "cache:" in riga, f"Manca 'cache:': {riga}"
        assert "MiniMax-M2.7" in riga, f"Manca il provider: {riga}"
        # Il body non ha breakpoint, quindi il ramo e' "nessun breakpoint"
        # (read/creation appaiono solo nel ramo OK, non qui)
        assert "nessun breakpoint" in riga, f"Atteso 'nessun breakpoint': {riga}"
        # inputTokens deve essere preservato dal usage.get con default
        assert "input=50" in riga, f"inputTokens non preservato: {riga}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
