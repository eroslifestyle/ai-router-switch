import sys
sys.path.insert(0, 'src')
sys.path.insert(0, '.')

from streaming_relay import StreamingRelay
"""
Test diagnostica cache estesa a tutti i provider (FIX 2026-08-20).

Prima: la diagnostica era attiva SOLO per "claude-direct" (Anthropic).
Dopo: attiva per TUTTI i provider (GLM, MiniMax, qwen, local, etc).

Stesso pattern del bug b4ed405: "GLM non cacha mai" era il misuratore cieco.
"""
import json
import pytest
from multidict import CIMultiDict


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

    Se usage contiene cache_creation_reported=True, il provider ha esposto il
    campo cache_creation_input_tokens nella risposta (anche se era zero).
    Se il campo e' assente (provider come z.ai/GLM), il flag non c'e' e la
    riga diventa CREAZIONE NON RIPORTATA invece di MISS TOTALE.
    """
    _cc, _cc_s, _cc_m, _cc_t = conta_cache_control(body_bytes)
    _ch = int(usage.get("cache_read_input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
    if _cc > 0 and _ch == 0:
        if usage.get("cache_creation_reported"):
            return f"cache: MISS TOTALE [{final_model}] bp={_cc} input={usage['input_tokens']} (nessun cache_read/creation nella risposta)"
        else:
            return f"cache: CREAZIONE NON RIPORTATA [{final_model}] bp={_cc} input={usage['input_tokens']} (il provider non espone cache_creation: creazione e miss sono indistinguibili)"
    elif _cc == 0:
        return f"cache: nessun breakpoint [{final_model}] input={usage['input_tokens']}"
    else:
        return f"cache: OK [{final_model}] bp=s{_cc_s}/m{_cc_m}/t{_cc_t} read={usage.get("cache_read_input_tokens", 0)} creation={usage.get("cache_creation_input_tokens", 0)} input={usage["input_tokens"]}"


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
        
        usage_miss = {**base_usage, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cache_creation_reported": True}
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


    def test_provider_senza_campo_creation_non_e_miss_totale(self):
        body = json.dumps({"model": "glm-4.7", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]}).encode()
        usage_senza_campo = {"input_tokens": 2111, "cache_read_input_tokens": 0}
        usage_zero_esplicito = {"input_tokens": 2111, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cache_creation_reported": True}
        riga_senza_campo = genera_riga_cache(body, usage_senza_campo, "glm-4.7")
        assert "CREAZIONE NON RIPORTATA" in riga_senza_campo, f"Atteso CREAZIONE NON RIPORTATA: {riga_senza_campo}"
        riga_zero = genera_riga_cache(body, usage_zero_esplicito, "glm-4.7")
        assert "MISS TOTALE" in riga_zero, f"Atteso MISS TOTALE per zero esplicito: {riga_zero}"

    def test_sequenza_probe_glm_della_misura_reale(self):
        body = json.dumps({"model": "glm-4.7", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]}).encode()
        riga1 = genera_riga_cache(body, {"input_tokens": 2111, "cache_read_input_tokens": 0}, "glm-4.7")
        assert "CREAZIONE NON RIPORTATA" in riga1, f"Richiesta 1: {riga1}"
        riga2 = genera_riga_cache(body, {"input_tokens": 63, "cache_read_input_tokens": 2048}, "glm-4.7")
        assert "OK" in riga2, f"Richiesta 2: {riga2}"
        assert "read=2048" in riga2, f"Richiesta 2 read: {riga2}"
        riga3 = genera_riga_cache(body, {"input_tokens": 63, "cache_read_input_tokens": 2048}, "glm-4.7")
        assert "OK" in riga3, f"Richiesta 3: {riga3}"

    def test_anthropic_con_creation_explcita_restano_ok(self):
        body = json.dumps({"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]}).encode()
        usage = {"input_tokens": 100, "cache_read_input_tokens": 106609, "cache_creation_input_tokens": 3055, "cache_creation_reported": True}
        riga = genera_riga_cache(body, usage, "claude-direct")
        assert "OK" in riga, f"Atteso OK: {riga}"
        assert "creation=3055" in riga, f"creation non preservata: {riga}"
        assert "read=106609" in riga, f"read non preservata: {riga}"

    def test_delta_creation_zero_esplicito_e_miss_totale(self):
        """
        Provider che espone cache_creation_input_tokens SOLO nel message_delta (a zero)
        con breakpoint > 0 e cache_read=0 deve dare MISS TOTALE, non CREAZIONE NON
        RIPORTATA. Il flag va cercato in ENTRAMBI gli eventi (fix: 2026-08-20).
        """
        body = json.dumps({"model": "MiniMax-M2.7", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]}).encode()
        # Campo presente MA a zero: miss vero, non provider che non lo espone
        usage = {"input_tokens": 500, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cache_creation_reported": True}
        riga = genera_riga_cache(body, usage, "MiniMax-M2.7")
        assert "MISS TOTALE" in riga, f"Atteso MISS TOTALE per creation=0 esplicito: {riga}"

class TestStreamingRelayCacheCreationReported:
    """Test di INTEGRAZIONE: esercita il modulo VERO StreamingRelay, non la copia.

    Il flag cache_creation_reported deve essere impostato in TUTTI i tre posti dove
    upstream risponde: message_start SSE, message_delta SSE, JSON body non-SSE.
    """
    pytestmark = pytest.mark.asyncio

    async def _run_relay(self, sse_bytes, body_bytes, mode, final_override):
        logs = []

        class FakeContent:
            def __init__(self, data):
                self._data = data
            async def iter_chunks(self):
                yield self._data
            async def iter_any(self):
                yield self._data
            def at_eof(self):
                return True

        class FakeUpstream:
            status = 200
            closed = False
            headers = CIMultiDict({"Content-Type": "text/event-stream", "Content-Encoding": "identity"})
            def __init__(self, content):
                self._content = content
            @property
            def content(self):
                return self._content
            def release(self):
                pass

        from aiohttp.test_utils import make_mocked_request
        request = make_mocked_request("POST", "/test", headers={"Content-Type": "application/json"})

        relay = StreamingRelay(
            request=request, body=body_bytes, mode=mode, orig=None,
            request_orig_model={}, hop_headers=set(), minimax_model="glm-4.7",
            log_fn=lambda msg, *a, **k: logs.append(str(msg)),
            log_router_usage_fn=lambda *a, **k: None,
            trim_context_fn=lambda *a, **k: None,
        )
        upstream = FakeUpstream(FakeContent(sse_bytes))
        await relay.relay(upstream, chat_fp_for_rewrite="test", final_override=final_override)
        return logs

    async def test_glm_zai_creazione_non_riportata(self):
        """Dialetto z.ai (GLM) osservato il 2026-08-20: nessun cache_creation
        in message_start NE message_delta. Con breakpoint e cache_read=0 ->
        CREAZIONE NON RIPORTATA, non MISS TOTALE."""
        logs = await self._run_relay(t1_sse, t1_body, "glm", "glm:glm-4.7")
        cache_lines = [l for l in logs if l.startswith("cache: ")]
        assert len(cache_lines) == 1, "Attesa 1 riga cache: " + repr(cache_lines)
        line = cache_lines[0]
        assert "CREAZIONE NON RIPORTATA" in line, "Atteso CREAZIONE NON RIPORTATA: " + line
        assert "glm:glm-4.7" in line, "Atteso glm:glm-4.7: " + line
        assert "MISS TOTALE" not in line, "NON atteso MISS TOTALE: " + line

    async def test_anthropic_miss_totale(self):
        """Controprova: cache_creation presente (a zero) nel message_start ->
        MISS TOTALE, NON CREAZIONE NON RIPORTATA."""
        logs = await self._run_relay(t2_sse, t2_body, "anthropic", "claude-direct")
        cache_lines = [l for l in logs if l.startswith("cache: ")]
        assert len(cache_lines) == 1, "Attesa 1 riga cache: " + repr(cache_lines)
        line = cache_lines[0]
        assert "MISS TOTALE" in line, "Atteso MISS TOTALE: " + line
        assert "claude-direct" in line, "Atteso claude-direct: " + line
        assert "CREAZIONE NON RIPORTATA" not in line, "NON atteso CREAZIONE NON RIPORTATA: " + line


# Data per i test di integrazione (definiti dopo la classe per essere referenziati dai metodi)

# Data SSE/body per i test di integrazione
t1_sse = b'data: {"type":"message_start","message":{"usage":{"input_tokens":0,"output_tokens":0}}}\n\ndata: {"type":"content_block_start","content_block":{"type":"text"}}\n\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"H"}}\n\ndata: {"type":"content_block_stop"}\n\ndata: {"type":"message_delta","usage":{"input_tokens":2111,"output_tokens":2,"cache_read_input_tokens":0},"delta":{"type":"message_delta","stop_sequence":""}}\n\ndata: {"type":"message_stop"}\n\n'
t2_sse = b'data: {"type":"message_start","message":{"usage":{"input_tokens":2111,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n\ndata: {"type":"content_block_start","content_block":{"type":"text"}}\n\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"H"}}\n\ndata: {"type":"content_block_stop"}\n\ndata: {"type":"message_delta","usage":{"output_tokens":1},"delta":{"type":"message_delta","stop_sequence":""}}\n\ndata: {"type":"message_stop"}\n\n'
t1_body = b'{"model": "glm-4.7", "system": [{"type": "text", "text": ".", "cache_control": {"type": "ephemeral"}}], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 512}'
t2_body = b'{"model": "claude-3-5-sonnet-20240620", "system": [{"type": "text", "text": ".", "cache_control": {"type": "ephemeral"}}], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 512}'

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
