# AQ-REF1 — StreamingRelay estratta come classe

## Obiettivo
Estrarre la closure `relay` inline in `handle()` (riga 3784–4056) → classe stand-alone `StreamingRelay` in `streaming_relay.py`.

## File target
- **Creato**: `streaming_relay.py` (accanto a `ai-router-proxy.py`, nella root del progetto)
- **Modificato**: `ai-router-proxy.py` (solo la closure `relay` in `handle()`)

## Dipendenze da handle() (outer scope)
La closure `relay` legge queste variabili definite PRIMA della closure in `handle()`:

| Variabile | Tipo | Uso in relay |
|---|---|---|
| `body` | `bytes` | usage extraction + trim |
| `mode` | `str` | log_router_usage final model |
| `orig` | `dict\|None` | debug_capture |
| `_request_orig_model` | `dict` | pop orig_model per rewrite |
| `HOP_HEADERS` | `set[str]` | filtra headers |
| `SIDECAR` | `Path` | rimap index |
| `MINIMAX_MODEL` | `str` | log_router_usage |

**Funzioni globali** (già importate/moduli esistenti):
- `log()`, `debug_capture()`, `log_router_usage()`, `_trim_context_after_response()` — passate al costruttore
- `MINIMAX_MODEL` — passato come costante
- `HOP_HEADERS`, `SIDECAR` — passati come costanti/config
- `_request_orig_model` — dict mutabile passato al costruttore (riferimento)

## Interfaccia classe

```python
# streaming_relay.py

class StreamingRelay:
    """Relay SSE/streaming/non-streaming con riscrittura model + usage tracking."""

    def __init__(
        self,
        body: bytes,
        mode: str,
        orig: dict | None,
        request_orig_model: dict,  # riferimento al dict globale
        hop_headers: set[str],
        sidecar_path: Path,
        minimax_model: str,
        log_fn,
        debug_capture_fn,
        log_router_usage_fn,
        trim_context_fn,
    ):
        ...

    async def relay(
        self,
        upstream: ClientResponse,
        chat_fp: str = "default",
        extra_headers: dict | None = None,
    ) -> web.StreamResponse:
        """Propaga risposta upstream al client con rewrite + usage."""
        ...
```

## Logica relay (testo codice da preservare)

### 1. Orig model extraction (r 3784-3795)
```
- pop da _request_orig_model con chat_fp
- fallback: se esattamente 1 chiave pending (no __remap__), usa quella
```

### 2. Error path 4xx/5xx non-429 (r 3796-3829)
```
- leggi body upstream con upstream.read()
- debug_capture con status, raw, encoding, orig
- forward headers (skip HOP_HEADERS + content-length)
- aggiungi extra_headers
- return web.Response
```

### 3. SSE detection + StreamResponse setup (r 3830-3857)
```
- is_sse = "text/event-stream" in content-type
- StreamResponse(upstream.status)
- forward headers (skip HOP_HEADERS, skip content-length se SSE)
- extra_headers injection
- SSE headers: content-type, cache-control, connection, x-accel-buffering
- await resp.prepare(request)
```

### 4. Streaming loop (r 3858-3911)
```
- upstream.content.iter_any()  # NON iter_chunked
- chunk_count, total_bytes counters
- model_rewrite_done flag
- _acc_buf bytearray (cap 16384)
- SSE model rewrite: sse_model_pat.sub("model":"{orig_model}") sul primo evento
- JSON model rewrite: json.loads/dumps per risposte non-SSE
- await resp.write(chunk) + await resp.drain() per SSE
- log error su exception
```

### 5. finally: upstream release (r 3912-3915)
```
- if not upstream.closed: upstream.release()
```

### 6. Usage extraction (r 3916-3984)
```
- decodifica _acc_buf UTF-8
- SSE: regex "data: (.+)$" → parse JSON → message_start (input/cache) + message_delta (output)
- non-SSE: json.loads → usage dict
- fallback: max(1, total_bytes // 4) per output_tokens se non estratto
- input_tokens fallback: stima da body request (messages content + system)
- D41: delta-correction TPM sul limiter entry se presente
```

### 7. log_router_usage (r 3985-4038)
```
- determina _final_model da mode + orig_model + remap index + sidecar
- chiama log_router_usage con chat_id, orig, final, usage, mode, UA, status, path
```

### 8. Final log + drain + write_eof (r 4039-4054)
```
- log relay done: chunks + bytes + SSE flag
- await resp.drain()
- await resp.write_eof()
- TRIM: _trim_context_after_response(body, chat_fp)
```

### 9. Return (r 4056)
```
- return resp
```

## Modifiche a handle()
1. Importare `StreamingRelay` da `streaming_relay`
2. Sostituire la closure `relay` inline (r 3784-4056) con:
   ```python
   _relay = StreamingRelay(
       body=body,
       mode=mode,
       orig=orig,
       request_orig_model=_request_orig_model,
       hop_headers=HOP_HEADERS,
       sidecar_path=SIDECAR,
       minimax_model=MINIMAX_MODEL,
       log_fn=log,
       debug_capture_fn=debug_capture,
       log_router_usage_fn=log_router_usage,
       trim_context_fn=_trim_context_after_response,
   )
   relay = _relay.relay  # bound method
   ```
3. Tutti i call site `relay(...)` funzionano identici (bound method = async function)
4. Rimuovere le variabili `orig` (già inizializzata a None a r 3782 — ma `orig` non è più necessaria come free var se passiamo `orig=None` al costruttore)

## Note implementative
- `import re` fatto dentro il metodo (già così nel codice attuale) — OK
- `import json` già disponibile nel modulo
- `web` (aiohttp) va importato o passato come riferimento — preferibile importarlo nel modulo nuovo
- `ClientResponse` da `aiohttp`
- Tutti i parametri passati sono serializzabili come riferimenti (dict mutabile OK)
- `mode` è una stringa, `body` è bytes — immutabili, OK

## Cosa NON cambiare
- Comportamento funzionale identico (ogni branch, ogni log, ogni rewrite)
- Ordine operazioni invariato
- Tutti i call site `relay(...)` invariati
- `_send_sse_message` e `_prepare_sse_response` restano nel proxy (già stand-alone)

## Test minimo
1. Avvia proxy con nuovo file — nessun ImportError
2. Richiesta non-streaming → risposta OK
3. Richiesta streaming SSE → rewrite model + drain + write_eof OK
4. Richiesta 4xx error → error relay path OK
5. log_router_usage chiamato con parametri corretti
