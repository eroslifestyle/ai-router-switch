import sys
import json
import pytest
from pathlib import Path

# Aggiunge src al path: il file sta in sviluppo/tests/, il modulo in src/
src_dir = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(src_dir))

import local_backend as lb


class FakeContent:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._index = 0

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk
        self._index = len(self._chunks)

    def at_eof(self):
        # sincrono come in aiohttp: lo stub deve restare fedele all'originale
        return self._index >= len(self._chunks)


class FakeResponse:
    def __init__(self, chunks, content_type):
        self.content = FakeContent(chunks)
        self.status = 200
        self.closed = False
        self.headers = {
            "content-type": content_type,
            "Content-Length": "999",
            "x-custom": "keep"
        }


async def collect(resp):
    chunks = []
    async for chunk in resp.content.iter_any():
        chunks.append(chunk)
    return b"".join(chunks)


class TestRequestedMaxTokens:
    def test_estrazione_valore_77(self):
        body = json.dumps({"max_tokens": 77, "model": "test"})
        assert lb.requested_max_tokens(body) == 77

    def test_body_non_json(self):
        assert lb.requested_max_tokens("not json") is None

    def test_json_senza_chiave_max_tokens(self):
        body = json.dumps({"model": "test"})
        assert lb.requested_max_tokens(body) is None


class TestStopReasonFixed:
    @pytest.mark.asyncio
    async def test_sse_message_delta_saturo(self):
        # output_tokens >= max_tokens -> end_turn diventa max_tokens
        sse = 'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 100}}\n\n'
        resp = FakeResponse([sse.encode()], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        assert b"max_tokens" in output
        assert b"end_turn" not in output

    @pytest.mark.asyncio
    async def test_sse_message_delta_non_saturo(self):
        # output_tokens < max_tokens -> end_turn resta invariato
        sse = 'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 30}}\n\n'
        resp = FakeResponse([sse.encode()], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        assert b"end_turn" in output

    @pytest.mark.asyncio
    async def test_sse_evento_spezzato_tra_chunk(self):
        # Evento diviso a meta: la correzione funziona comunque
        sse = 'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 100}}\n\n'
        encoded = sse.encode()
        mid = len(encoded) // 2
        chunks = [encoded[:mid], encoded[mid:]]
        resp = FakeResponse(chunks, "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        assert b"max_tokens" in output

    @pytest.mark.asyncio
    async def test_sse_solo_eventi_testo_normali(self):
        # Eventi senza message_delta: output identico a input
        events = [
            'data: {"type": "content_block", "text": "ciao"}\n\n',
            'data: {"type": "content_block", "text": " mondo"}\n\n'
        ]
        resp = FakeResponse([e.encode() for e in events], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        expected = b"".join(e.encode() for e in events)
        assert output == expected

    @pytest.mark.asyncio
    async def test_sse_tool_use_saturo_non_alterato(self):
        # tool_use non viene toccato anche se saturo
        sse = 'data: {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 100}}\n\n'
        resp = FakeResponse([sse.encode()], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        assert b"tool_use" in output
        assert b"max_tokens" not in output

    @pytest.mark.asyncio
    async def test_json_non_stream_saturo(self):
        body = {
            "id": "test-id",
            "stop_reason": "end_turn",
            "usage": {"output_tokens": 100}
        }
        resp = FakeResponse([json.dumps(body).encode()], "application/json")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        parsed = json.loads(output)
        assert parsed["stop_reason"] == "max_tokens"

    @pytest.mark.asyncio
    async def test_json_non_stream_non_saturo(self):
        body = {
            "id": "test-id",
            "stop_reason": "end_turn",
            "usage": {"output_tokens": 30}
        }
        resp = FakeResponse([json.dumps(body).encode()], "application/json")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        parsed = json.loads(output)
        assert parsed["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_json_corpo_non_parsabile(self):
        invalid = b'not valid json {'
        resp = FakeResponse([invalid], "application/json")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        output = await collect(fixed)
        assert output == invalid

    @pytest.mark.asyncio
    async def test_headers_nessun_content_length(self):
        sse = 'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 100}}\n\n'
        resp = FakeResponse([sse.encode()], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        for key in fixed.headers:
            assert key.lower() != "content-length"
        assert fixed.headers["x-custom"] == "keep"

    @pytest.mark.asyncio
    async def test_delegazione_attributi_status_e_closed(self):
        sse = 'data: test\n\n'
        resp = FakeResponse([sse.encode()], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda x: None)
        assert fixed.status == 200
        assert fixed.closed is False

    @pytest.mark.asyncio
    async def test_log_fn_invocato_quando_correzione_avviene(self):
        log_calls = []
        sse = 'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 100}}\n\n'
        resp = FakeResponse([sse.encode()], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda msg: log_calls.append(msg))
        await collect(fixed)
        assert len(log_calls) > 0

    @pytest.mark.asyncio
    async def test_log_fn_non_invocato_quando_non_saturo(self):
        log_calls = []
        sse = 'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 30}}\n\n'
        resp = FakeResponse([sse.encode()], "text/event-stream")
        fixed = lb._StopReasonFixed(resp, max_tokens=50, log_fn=lambda msg: log_calls.append(msg))
        await collect(fixed)
        assert len(log_calls) == 0


class TestInjectSystemHint:
    """Test per lb.inject_system_hint: inietta hint nel campo system del JSON."""
    
    def test_system_assente(self):
        """Body senza campo system: viene aggiunto con LOCAL_SYSTEM_HINT."""
        body = json.dumps({"messages": []}).encode()
        result = lb.inject_system_hint(body)
        data = json.loads(result)
        assert data["system"] == lb.LOCAL_SYSTEM_HINT
        assert "vision_local" in data["system"]
    
    def test_system_stringa(self):
        """System gia' presente come stringa: hint accodato."""
        body = json.dumps({"system": "Sei Claude.", "messages": []}).encode()
        result = lb.inject_system_hint(body)
        data = json.loads(result)
        assert data["system"].startswith("Sei Claude.")
        assert "vision_local" in data["system"]
    
    def test_idempotente_stringa(self):
        """Chiamata ripetuta non duplica il hint."""
        body = json.dumps({"system": "Sei Claude.", "messages": []}).encode()
        result = lb.inject_system_hint(lb.inject_system_hint(body))
        assert result.decode().count("vision_local") == 1
    
    def test_system_lista(self):
        """System come lista di blocchi: hint aggiunto come nuovo blocco."""
        body = json.dumps({"system": [{"type": "text", "text": "You are X"}], "messages": []}).encode()
        result = lb.inject_system_hint(body)
        data = json.loads(result)
        assert len(data["system"]) == 2
        assert data["system"][1]["text"] == lb.LOCAL_SYSTEM_HINT
    
    def test_idempotente_lista(self):
        """Idempotenza su lista: hint presente una sola volta."""
        body = json.dumps({"system": [{"type": "text", "text": "You are X"}], "messages": []}).encode()
        result = lb.inject_system_hint(lb.inject_system_hint(body))
        data = json.loads(result)
        count = sum(1 for b in data["system"] if "vision_local" in b.get("text", ""))
        assert count == 1
    
    def test_body_non_json(self):
        """Body non JSON: ritornato invariato."""
        body = b"non json"
        result = lb.inject_system_hint(body)
        assert result == body
    
    def test_system_dict_invariato(self):
        """System dict non viene modificato."""
        body = json.dumps({"system": {"weird": 1}, "messages": []}).encode()
        result = lb.inject_system_hint(body)
        assert result == body


class TestStripImagesWithNote:
    """Test per strip_images_with_note: rimozione immagini con nota sostitutiva."""

    def _body(self, content):
        return json.dumps({"messages": [{"role": "user", "content": content}]}).encode()

    def test_immagine_sostituita(self):
        """Un blocco image viene sostituito da una nota testuale."""
        body = self._body([
            {"type": "text", "text": "cosa vedi?"},
            {"type": "image", "source": {"type": "base64", "data": "xxx"}},
        ])
        c = json.loads(lb.strip_images_with_note(body))["messages"][0]["content"]
        assert not any(b.get("type") == "image" for b in c)
        assert any("vision_local" in b.get("text", "") for b in c)

    def test_due_immagini_una_nota(self):
        """Due blocchi image producono esattamente UNA nota."""
        body = self._body([
            {"type": "image", "source": {"data": "xxx"}},
            {"type": "image", "source": {"data": "yyy"}},
        ])
        c = json.loads(lb.strip_images_with_note(body))["messages"][0]["content"]
        assert sum("vision_local" in b.get("text", "") for b in c) == 1

    def test_nessuna_immagine_invariato(self):
        """Senza immagini non viene aggiunta alcuna nota."""
        body = self._body([{"type": "text", "text": "ciao"}])
        c = json.loads(lb.strip_images_with_note(body))["messages"][0]["content"]
        assert not any("vision_local" in b.get("text", "") for b in c)

    def test_content_stringa_invariato(self):
        """Il content stringa resta invariato."""
        body = self._body("ciao")
        assert json.loads(lb.strip_images_with_note(body))["messages"][0]["content"] == "ciao"

    def test_body_non_json(self):
        """Un body non-JSON torna identico."""
        assert lb.strip_images_with_note(b"xx") == b"xx"

    def test_tool_result_annidato(self):
        """Immagini dentro tool_result.content vengono sostituite ricorsivamente."""
        body = self._body([
            {"type": "tool_result", "tool_use_id": "x",
             "content": [{"type": "image", "source": {}}]},
        ])
        tr = json.loads(lb.strip_images_with_note(body))["messages"][0]["content"][0]
        assert any("vision_local" in b.get("text", "") for b in tr["content"])
