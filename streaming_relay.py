"""
StreamingRelay — extracted relay closure from ai-router-proxy.py handle().

Byte-for-byte identical relay logic, no behavioral changes.
"""
import json
import re as re_module

from aiohttp import web
from router_debug import dl


class StreamingRelay:
    """Relay SSE/streaming/non-streaming con riscrittura model + usage tracking."""

    def __init__(
        self,
        request,                    # aiohttp web.Request — free var from handle()
        body: bytes,                # request body bytes
        mode: str,                  # router mode
        orig: dict | None,          # debug capture dict
        request_orig_model: dict,  # riferimento al dict globale (mutabile)
        hop_headers: set[str],
        sidecar_path,
        minimax_model: str,
        log_fn,
        log_router_usage_fn,
        trim_context_fn,
    ):
        self.request = request
        self.body = body
        self.mode = mode
        self.orig = orig
        self.request_orig_model = request_orig_model
        self.hop_headers = hop_headers
        self.sidecar_path = sidecar_path
        self.minimax_model = minimax_model
        self.log_fn = log_fn
        self.log_router_usage_fn = log_router_usage_fn
        self.trim_context_fn = trim_context_fn

    async def relay(
        self,
        upstream,
        chat_fp_for_rewrite: str = "default",
        extra_headers: dict | None = None,
    ):
        # FIX E: leggi e rimuovi orig_model da riscrivere nello SSE/non-stream
        # NB: i call site passano spesso chat_fp sbagliato (es 'default' vs IP reale).
        # Soluzione: prova la chiave esplicita; se manca e c'è esattamente UN orig
        # pending in _request_orig_model, usa quello (single-user loopback tipico).
        # FIX D38 2026-07-02: escludi la chiave interna '__remap__' (indice remap, dict)
        # dal fallback single-entry — altrimenti il dict finisce riscritto in body['model'].
        orig_model = self.request_orig_model.pop(chat_fp_for_rewrite, None)
        if orig_model is None:
            _pending = [k for k in self.request_orig_model if k != "__remap__"]
            if len(_pending) == 1:
                orig_model = self.request_orig_model.pop(_pending[0])
        # DEBUG: per errori 4xx/5xx (NON 429 rate-limit), cattura body in chiaro.
        # Per gli errori il body è piccolo e non streaming — lo logghiamo e poi
        # lo mandiamo diretto senza passare dal loop iter_any() (che consumerebbe
        # il body già letto). Il 200 OK prosegue normalmente nel loop streaming.
        if upstream.status >= 400 and upstream.status not in {429}:
            try:
                _raw = await upstream.read()
            except Exception:
                _raw = b""
            _enc = upstream.headers.get("Content-Encoding", "")
            # FIX diagnostica 2026-07-19: cattura server/cf-ray/via per capire se
            # il 404/errore arriva davvero dall'upstream atteso (Cloudflare/ALB)
            # o da un middlebox di rete locale (nginx generico non riconducibile
            # né ad Anthropic né a MiniMax) — vedi relay_error_404 intermittenti.
            _diag_headers = {
                k: upstream.headers.get(k) for k in
                ("server", "cf-ray", "via", "x-served-by", "alb_receive_time")
                if upstream.headers.get(k)
            }
            dl.capture(
                kind=f"relay_error_{upstream.status}",
                request=self.request, fp=chat_fp_for_rewrite,
                client_model=orig_model or "",
                status=upstream.status, stage="relay",
                upstream_status=upstream.status,
                upstream_raw=_raw,
                upstream_encoding=_enc,
                orig=self.orig, mode=self.mode,
                note=f"extra_headers={list((extra_headers or {}).keys())} upstream_headers={_diag_headers} url={getattr(upstream, 'url', '')}",
            )
            # Invia l'errore direttamente: body già letto, costruisci web.Response
            upstream.release()
            err_headers = {}
            for k, v in upstream.headers.items():
                lk = k.lower()
                if lk in self.hop_headers:
                    continue
                if lk == "content-length":
                    continue
                err_headers[k] = v
            if extra_headers:
                err_headers.update(extra_headers)
            return web.Response(body=_raw, status=upstream.status, headers=err_headers)
        # FIX SSE: rileva text/event-stream per applicare flush immediato + no-buffering
        is_sse = "text/event-stream" in (upstream.headers.get("content-type") or "").lower()
        resp = web.StreamResponse(status=upstream.status)
        for k, v in upstream.headers.items():
            lk = k.lower()
            if lk in self.hop_headers:
                continue
            # FIX #8: Forward Content-Encoding (br/gzip) so client can decode.
            # We use auto_decompress=False in ClientSession to pass through as-is.
            # Evita Content-Length su SSE: rompe chunked streaming
            if is_sse and lk == "content-length":
                continue
            resp.headers[k] = v
        # FIX redesign 2026-07-01: header extra iniettati dal caller (es x-ai-verified).
        # Evidenzia la pipeline gerarchica mixed per audit downstream.
        if extra_headers:
            for k, v in extra_headers.items():
                resp.headers[k] = v
        if is_sse:
            # Header SSE-corretti: nessun buffering downstream, keep-alive
            resp.headers.setdefault("content-type", "text/event-stream")
            resp.headers["cache-control"] = "no-cache, no-transform"
            resp.headers["connection"] = "keep-alive"
            resp.headers["x-accel-buffering"] = "no"
        # FIX #6: NON usare enable_chunked_encoding() - aiohttp lo fa automaticamente
        # quando Transfer-Encoding non è in headers (già skippato da HOP_HEADERS).
        # Evita doppia codifica/conflitto chunked.
        await resp.prepare(self.request)
        # FIX #2: usa iter_any() anche per SSE - iter_chunked(N) può bloccare aspettando N bytes
        # mentre SSE invia eventi piccoli (<200 byte). iter_any() yielda appena disponibile.
        iterator = upstream.content.iter_any()
        chunk_count = 0
        total_bytes = 0
        model_rewrite_done = orig_model is None  # se non c'è orig_model, skip subito
        # FIX F: accumula chunks per estrarre usage reale dai record SSE/JSON
        _acc_buf = bytearray()
        _acc_limit = 16384  # massimo 16KB per evitare OOM su risposte enormi
        # Precompila pattern per SSE message_start rewrite
        sse_model_pat = re_module.compile(rb'"model":"[^"]*"')
        try:
            async for chunk in iterator:
                if not chunk:
                    continue
                chunk_count += 1
                total_bytes += len(chunk)
                # FIX #4: log primo chunk per debug
                if chunk_count == 1:
                    self.log_fn(f"relay first chunk {len(chunk)}B (SSE={is_sse})")
                # FIX E: riscrivi il campo 'model' nello stream SSE (solo primo chunk rilevante)
                if not model_rewrite_done and orig_model:
                    if is_sse:
                        # cerca il pattern "model":"<qualsiasi>" e sostituisci SOLO nel primo evento message_start
                        new_chunk = sse_model_pat.sub(
                            f'"model":"{orig_model}"'.encode(), chunk, count=1
                        )
                        if new_chunk != chunk:
                            self.log_fn(f"FIX E: SSE model rewritten to '{orig_model}'")
                            chunk = new_chunk
                            model_rewrite_done = True
                    else:
                        # non-streaming JSON response: parsifica e riscrivi
                        try:
                            j = json.loads(chunk)
                            if isinstance(j, dict) and "model" in j:
                                j["model"] = orig_model
                                chunk = json.dumps(j).encode()
                                self.log_fn(f"FIX E: JSON model rewritten to '{orig_model}'")
                            model_rewrite_done = True
                        except Exception:
                            pass  # non-JSON body, skip
                # FIX F: accumulazione parziale per usage extraction
                if len(_acc_buf) < _acc_limit:
                    _acc_buf.extend(chunk[:(_acc_limit - len(_acc_buf))])
                await resp.write(chunk)
                if is_sse:
                    # FIX #5: drain senza try/except - se fallisce vogliamo saperlo
                    await resp.drain()
        except Exception as e:
            # FIX #3: log esplicito eccezioni nel loop streaming
            self.log_fn(f"relay loop ERROR after {chunk_count} chunks ({total_bytes}B): {e}")
            raise
        finally:
            # FIX B2.3: garantisce chiusura upstream su client disconnect/cancel/exception
            if not upstream.closed:
                upstream.release()
            # FIX F: log per-request usage. Estrai token reali da _acc_buf.
            try:
                _usage = {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
                _buf_str = _acc_buf.decode("utf-8", errors="replace")
                if is_sse:
                    # Cerca message_start (input) e message_delta (output) nei chunk SSE
                    for _data in re_module.findall(r"^data: (.+)$", _buf_str, re_module.MULTILINE):
                        try:
                            _ev = json.loads(_data)
                            if _ev.get("type") == "message_start":
                                _u = (_ev.get("message") or {}).get("usage") or {}
                                _usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                                _usage["cache_read_input_tokens"] = int(_u.get("cache_read_input_tokens", 0) or 0)
                                _usage["cache_creation_input_tokens"] = int(_u.get("cache_creation_input_tokens", 0) or 0)
                            elif _ev.get("type") == "message_delta":
                                _u = _ev.get("usage") or {}
                                _usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                        except Exception:
                            pass
                    if _usage["output_tokens"] == 0:
                        _usage["output_tokens"] = max(1, total_bytes // 4)
                else:
                    try:
                        _j = json.loads(_buf_str)
                        if isinstance(_j, dict):
                            _u = _j.get("usage") or {}
                            _usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                            _usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                            _usage["cache_read_input_tokens"] = int(_u.get("cache_read_input_tokens", 0) or 0)
                            _usage["cache_creation_input_tokens"] = int(_u.get("cache_creation_input_tokens", 0) or 0)
                    except Exception:
                        _usage["output_tokens"] = max(1, total_bytes // 4)
                # Input: estrai dal body richiesta (non compresso) se non già noto.
                # È una stima sicura perché il body request è sempre in chiaro.
                if _usage["input_tokens"] == 0:
                    try:
                        _req_j = json.loads(self.body.decode("utf-8", errors="replace"))
                        # Stima da prompt: somma len(c["content"]) per tutti i messaggi
                        _chars = 0
                        for _m in (_req_j.get("messages") or []):
                            c = _m.get("content", "")
                            if isinstance(c, str):
                                _chars += len(c)
                            elif isinstance(c, list):
                                for _b in c:
                                    if isinstance(_b, dict) and isinstance(_b.get("text"), str):
                                        _chars += len(_b["text"])
                        _sys = _req_j.get("system", "")
                        if isinstance(_sys, str):
                            _chars += len(_sys)
                        _usage["input_tokens"] = max(1, _chars // 4)
                    except Exception:
                        _usage["input_tokens"] = max(1, len(self.body) // 4)
                # D41: delta-correction TPM — riconcilia la stima del rate limiter con
                # i token reali (input+output). _lim_entry è la STESSA lista mutabile
                # nella finestra del limiter; correggerla aggiusta il TPM percepito.
                # Clamp alla stima già prenotata (evita sforo del budget validato in acquire).
                try:
                    _lim_entry = getattr(upstream, "_airouter_limiter_entry", None)
                    if _lim_entry is not None:
                        _real_total = int(_usage.get("input_tokens", 0)) + int(_usage.get("output_tokens", 0))
                        _est_reserved = getattr(upstream, "_airouter_limiter_est", _real_total)
                        if _real_total > 0:
                            _lim_entry[1] = min(_real_total, _est_reserved)
                            self.log_fn(f"D41 TPM delta-correct: est={_est_reserved} real={_real_total} -> {_lim_entry[1]}")
                except Exception as _e:
                    self.log_fn(f"D41 TPM delta-correct skip: {_e}")
                # FIX bug stats: passa il FINAL reale (risolto da remap) + fallback al
                # model nel body della request se orig_model (chat_fp-mismatch) è vuoto.
                try:
                    _body_j = json.loads(self.body.decode("utf-8", errors="replace"))
                    _body_model = (_body_j.get("model") or "").strip()
                except Exception:
                    _body_model = ""
                _orig = orig_model or _body_model or "?"
                # FIX bug 2026-07-01: per mode=mixed il final NON è "?" — è il modello
                # rimappato (MiniMax-M3 se orig è nel remap index) oppure "claude-direct"
                # se mixed è caduto in fallback Anthropic.
                if self.mode == "minimax":
                    _final = self.minimax_model
                elif self.mode == "anthropic":
                    _final = "claude-direct"
                elif self.mode in ("glm", "glm-minimax", "anthropic-glm"):
                    # Il modello GLM effettivo è nell'header x-ai-verified (glm(<model>)).
                    # Registriamo il mode; il modello reale + moltiplicatore costo sono
                    # già loggati inline da _glm_execute_with_chain (x-glm-cost-mult).
                    _final = f"glm-mode:{self.mode}"
                elif self.mode == "mixed":
                    try:
                        _remap_idx = self.request_orig_model.get("__remap__") or {}
                        if not _remap_idx:
                            # costruisci al volo dal sidecar (cache 60s gia' presente altrove)
                            _idx = {}
                            try:
                                with open(self.sidecar_path, "r") as _sf:
                                    for _sl in _sf:
                                        _so = json.loads(_sl) if _sl.strip() else None
                                        if _so and _so.get("orig") and _so.get("final"):
                                            _so_o = _so["orig"]
                                            if _so_o not in _idx:
                                                _idx[_so_o] = _so["final"]
                            except Exception:
                                pass
                            self.request_orig_model["__remap__"] = _idx
                            _remap_idx = _idx
                        _final = _remap_idx.get(_orig, "claude-direct") if _orig != "?" else "?"
                    except Exception:
                        _final = "?"
                else:
                    _final = "?"
                self.log_router_usage_fn(
                    chat_id=chat_fp_for_rewrite,
                    orig=_orig,
                    final=_final,
                    usage=_usage,
                    mode=self.mode,
                    client=self.request.headers.get("User-Agent", "?")[:40] or "?",
                    status=upstream.status,
                    path=self.request.path,
                )
            except Exception:
                pass
        # FIX #4: log bytes totali inoltrati
        if is_sse or total_bytes > 0:
            self.log_fn(f"relay done: {chunk_count} chunks, {total_bytes} bytes (SSE={is_sse})")
        # FIX #5: drain finale prima di write_eof per garantire flush completo
        await resp.drain()
        await resp.write_eof()

        # ── TRIM: dopo relay OK, salva trimmed state per la prossima iterazione ──
        try:
            _d = json.loads(self.body.decode("utf-8", errors="replace"))
            if _d.get("messages"):
                self.trim_context_fn(self.body, chat_fp_for_rewrite)
        except Exception:
            pass

        return resp
