"""
StreamingRelay — extracted relay closure from ai-router-proxy.py handle().

Byte-for-byte identical relay logic, no behavioral changes.
"""
import json
import re as re_module
import time

from aiohttp import web
from router_debug import dl
from router_utils import collect_tools_stats
import tool_isolation


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
        # sidecar_path rimosso il 2026-08-07: il relay lo leggeva per ricostruire
        # una mappa orig->final da un file fermo dal 2026-07-25. Ora l'instradamento
        # lo dà resolve_route, che è la fonte di verità.
        self.minimax_model = minimax_model
        self.log_fn = log_fn
        self.log_router_usage_fn = log_router_usage_fn
        self.trim_context_fn = trim_context_fn

    def _modelli_orig_e_finale(self, final_override, orig_model):
        """Ritorna (modello chiesto dal client, modello che ha davvero risposto).

        Estratto dal blocco finally di relay() il 2026-08-08 per l'audit A3: lo
        stesso valore serve ora in due momenti diversi — negli header, che
        partono PRIMA di leggere la risposta, e nel sidecar, che si scrive dopo.
        Duplicare la catena di casi avrebbe garantito che le due copie
        divergessero: e' gia' successo in questo repo piu' di una volta.
        La logica e' invariata rispetto a prima.
        """
        try:
            _body_j = json.loads(self.body.decode("utf-8", errors="replace"))
            _body_model = (_body_j.get("model") or "").strip()
        except Exception:
            _body_model = ""
        _orig = orig_model or _body_model or "?"
        # FIX bug 2026-07-01: per mode=mixed il final NON è "?" — è il modello
        # rimappato (MiniMax-M3 se orig è nel remap index) oppure "claude-direct"
        # se mixed è caduto in fallback Anthropic.
        if final_override:
            _final = final_override
        elif self.mode == "minimax":
            _final = self.minimax_model
        elif self.mode == "anthropic":
            _final = "claude-direct"
        elif self.mode in ("glm", "glm-minimax", "anthropic-glm", "mix-gm", "mix-ag"):
            # Il modello GLM effettivo è nell'header x-ai-verified (glm(<model>)).
            # Registriamo il mode; il modello reale + moltiplicatore costo sono
            # già loggati inline da _glm_execute_with_chain (x-glm-cost-mult).
            _final = f"glm-mode:{self.mode}"
        else:
            # mix-am/mixed e tutte le modalita' senza un ramo dedicato (qwen,
            # mix-al, local, e qualunque altra si aggiunga): resolve_route e' la
            # fonte di verita' dell'instradamento, quindi non serve un caso per
            # ciascuna. Prima qwen, mix-al e local cadevano nel ramo finale
            # `_final = "?"` e, senza un final_override dal call site, il sidecar
            # le registrava come "router-internal" (2026-08-08, audit A3).
            #
            # Storia del ramo mix-am: il sidecar dei modelli originali non viene
            # piu' scritto dal 2026-07-25, e l'indice ricostruito aveva 25 voci
            # su 75.683 righe, con attribuzioni errate (claude-opus-5 ->
            # MiniMax-M2.7) e un fallback a 'claude-direct' che marcava male
            # claude-haiku 6 volte su 31.
            try:
                from role_routing import resolve_route
                _mode_norm = 'mix-am' if self.mode == 'mixed' else self.mode
                _provider, _override = resolve_route(_mode_norm, _orig if _orig != "?" else "")
                if _provider == 'anthropic':
                    _final = 'claude-direct'
                elif _override:
                    _final = _override
                elif _provider == 'minimax':
                    _final = self.minimax_model
                else:
                    # Modello non riconosciuto: si dichiara il provider, mai il
                    # nome chiesto dal client — sarebbe di nuovo la bugia che
                    # questo lavoro elimina.
                    _final = f"{_provider}-mode:{_mode_norm}"
            except Exception:
                _final = "?"
        return _orig, _final

    async def relay(
        self,
        upstream,
        chat_fp_for_rewrite: str = "default",
        extra_headers: dict | None = None,
        final_override: str | None = None,
    ):
        # final_override: il modello che ha REALMENTE eseguito, quando il call
        # site lo conosce (es. mix-am sa che l'ACT è andato a MiniMax o al
        # rescue Anthropic). Senza questo, _final veniva dedotto da un indice
        # di remap costruito dal sidecar, che per mix-am ripiegava sul default
        # "claude-direct" anche quando l'esecutore era MiniMax: la telemetria
        # riportava il provider sbagliato (fix 2026-07-25).
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
        # x-ai-actual-model (audit A3, 2026-08-08): il campo `model` del corpo
        # dice al client quello che il client ha chiesto, non quello che ha
        # risposto — il relay lo riscrive con orig_model poche righe piu' sotto,
        # perche' i client rifiutano un nome di modello che non riconoscono. E
        # il comportamento differiva fra modalita': con un modello ignoto in
        # mix-am la risposta dichiarava quel nome mentre il sidecar registrava
        # MiniMax-M2.7. Si aggiunge un header invece di cambiare il campo:
        # additivo, nessun client esistente si rompe, e chi vuole sapere chi ha
        # risposto davvero ha un posto solo dove guardare.
        try:
            _, _actual_model = self._modelli_orig_e_finale(final_override, orig_model)
            if _actual_model and _actual_model != "?":
                resp.headers["x-ai-actual-model"] = str(_actual_model)
        except Exception:
            pass  # la telemetria non deve mai impedire una risposta
        # FIX #6: NON usare enable_chunked_encoding() - aiohttp lo fa automaticamente
        # quando Transfer-Encoding non è in headers (già skippato da HOP_HEADERS).
        # Evita doppia codifica/conflitto chunked.
        await resp.prepare(self.request)
        # FIX #2: usa iter_any() anche per SSE - iter_chunked(N) può bloccare aspettando N bytes
        # mentre SSE invia eventi piccoli (<200 byte). iter_any() yielda appena disponibile.
        iterator = upstream.content.iter_any()
        chunk_count = 0
        total_bytes = 0
        # Latenza (2026-08-08, audit A9). t_start e' marcato dal middleware
        # all'ingresso della richiesta nel router: partire da qui perderebbe la
        # connessione upstream, cioe' la parte lenta. Il fallback su monotonic()
        # serve ai test, che costruiscono request finte senza t_start.
        _t_start = None
        try:
            _t_start = self.request.get("t_start")
        except Exception:
            pass
        if _t_start is None:
            _t_start = time.monotonic()
        _ttfb_ms = None
        model_rewrite_done = orig_model is None  # se non c'è orig_model, skip subito
        # FIX F: accumula chunks per estrarre usage reale dai record SSE/JSON
        _acc_buf = bytearray()
        _buf_str = ""
        _acc_limit = 16384  # massimo 16KB per evitare OOM su risposte enormi
        # Il buffer di testa vede solo i primi 16KB; nelle risposte lunghe che iniziano con un blocco thinking il message_delta finale (stop_reason + output_tokens) non viene mai letto, quindi la richiesta risulta erroneamente 'empty' in telemetria. Il buffer di coda tiene gli ultimi 16KB per recuperarlo.
        _tail_buf = bytearray()
        _tail_limit = 16384
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
                    _ttfb_ms = (time.monotonic() - _t_start) * 1000.0
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
                _tail_buf.extend(chunk)
                if len(_tail_buf) > _tail_limit:
                    del _tail_buf[:len(_tail_buf) - _tail_limit]
                await resp.write(chunk)
                if is_sse:
                    # FIX #5: drain senza try/except - se fallisce vogliamo saperlo
                    await resp.drain()
        except Exception as e:
            # FIX #3: log esplicito eccezioni nel loop streaming
            # DIAG stall 2026-08-01: distingue "client sparito" (effetto) da rottura
            # lato server (causa). Il calcolo e' difensivo: qualunque suo fallimento
            # non deve impedire il log originale ne' il raise (vedi regressione detector).
            try:
                _tr = getattr(self.request, "transport", None)
                _diag = (f" [client_closing={_tr is None or _tr.is_closing()}"
                         f" upstream_eof={upstream.content.at_eof()}"
                         f" exc={type(e).__name__} sse={is_sse}]")
            except Exception:
                _diag = " [diag=NA]"
            self.log_fn(f"relay loop ERROR after {chunk_count} chunks ({total_bytes}B): {e}{_diag}")
            raise
        finally:
            # FIX B2.3: garantisce chiusura upstream su client disconnect/cancel/exception
            if not upstream.closed:
                upstream.release()
            # FIX F: log per-request usage. Estrai token reali da _acc_buf.
            try:
                _usage = {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
                # Decompress because auto_decompress=False; buffer may be truncated.
                _enc = (upstream.headers.get("content-encoding", "") or "").lower().strip()
                _raw = bytes(_acc_buf)
                try:
                    if "gzip" in _enc:
                        import zlib
                        _raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(_raw)
                    elif "deflate" in _enc:
                        import zlib
                        _raw = zlib.decompressobj().decompress(_raw)
                    elif "br" in _enc:
                        try:
                            import brotli
                            _raw = brotli.Decompress().process(_raw)
                        except ImportError:
                            import brotlicffi as brotli
                            _raw = brotli.Decompress().process(_raw)
                except Exception:
                    # Decompression failed; fall back to raw compressed bytes.
                    pass
                _buf_str = _raw.decode("utf-8", errors="replace")
                if is_sse:
                    def _scan(text):
                        # Lo spazio dopo "data:" e' OPZIONALE per lo standard SSE: il
                        # gateway Qwen/Alibaba emette "data:{...}" senza spazio, quindi il
                        # vecchio r"^data: " non matchava NULLA e ogni risposta qwen
                        # risultava con 0 blocchi e stop_reason vuoto (2026-08-04).
                        for _data in re_module.findall(r"^data:\s*(.+)$", text, re_module.MULTILINE):
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
                                    _sr = (_ev.get("delta") or {}).get("stop_reason")
                                    if _sr:
                                        _usage["stop_reason"] = _sr
                                elif _ev.get("type") == "content_block_start":
                                    _bt = (_ev.get("content_block") or {}).get("type")
                                    if _bt == "text":
                                        _usage["text_blocks"] = _usage.get("text_blocks", 0) + 1
                                    elif _bt == "thinking":
                                        _usage["thinking_blocks"] = _usage.get("thinking_blocks", 0) + 1
                                    elif _bt == "tool_use":
                                        _usage["tool_use_blocks"] = _usage.get("tool_use_blocks", 0) + 1
                            except Exception:
                                pass
                    _scan(_buf_str)
                    _compressed = any(x in _enc for x in ("gzip", "deflate", "br"))
                    if not _compressed and total_bytes > _acc_limit and _tail_buf:
                        try:
                            overlap = len(_acc_buf) + len(_tail_buf) - total_bytes
                            if overlap >= 0:
                                _tail_bytes = bytes(_tail_buf)[overlap:]
                                _tail_str = _tail_bytes.decode("utf-8", errors="replace")
                                _scan(_tail_str)
                            else:
                                _tail_raw = bytes(_tail_buf)
                                _nl = _tail_raw.find(b"\n")
                                if _nl >= 0:
                                    _tail_str = _tail_raw[_nl + 1:].decode("utf-8", errors="replace")
                                else:
                                    _tail_str = _tail_raw.decode("utf-8", errors="replace")
                                _scan(_tail_str)
                                _usage["measure_partial"] = True
                        except Exception:
                            pass
                    elif _compressed and total_bytes > _acc_limit:
                        _usage["measure_partial"] = True
                    if _usage["output_tokens"] == 0:
                        _usage["output_tokens"] = max(1, total_bytes // 4)
                else:
                    def _scan_json(txt):
                        try:
                            _j = json.loads(txt)
                            if not isinstance(_j, dict):
                                return False
                            _u = _j.get("usage") or {}
                            _usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                            _usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                            _usage["cache_read_input_tokens"] = int(_u.get("cache_read_input_tokens", 0) or 0)
                            _usage["cache_creation_input_tokens"] = int(_u.get("cache_creation_input_tokens", 0) or 0)
                            _sr = _j.get("stop_reason")
                            if _sr:
                                _usage["stop_reason"] = _sr
                            for _blk in (_j.get("content") or []):
                                if isinstance(_blk, dict):
                                    _btype = _blk.get("type")
                                    if _btype == "text":
                                        _usage["text_blocks"] = _usage.get("text_blocks", 0) + 1
                                    elif _btype == "thinking":
                                        _usage["thinking_blocks"] = _usage.get("thinking_blocks", 0) + 1
                                    elif _btype == "tool_use":
                                        _usage["tool_use_blocks"] = _usage.get("tool_use_blocks", 0) + 1
                            return True
                        except Exception:
                            return False
                    _ok = _scan_json(_buf_str)
                    if not _ok:
                        try:
                            _compressed_nb = any(x in _enc for x in ('gzip', 'deflate', 'br'))
                        except Exception:
                            _compressed_nb = False
                        if not _compressed_nb and _tail_buf:
                            overlap = len(_acc_buf) + len(_tail_buf) - total_bytes
                            if overlap >= 0:
                                try:
                                    full_txt = _buf_str + bytes(_tail_buf)[overlap:].decode('utf-8', errors='replace')
                                    _ok = _scan_json(full_txt)
                                except Exception:
                                    pass
                    if not _ok:
                        _usage["measure_partial"] = True
                        if _usage["output_tokens"] == 0:
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
                _orig, _final = self._modelli_orig_e_finale(final_override, orig_model)
                try:
                    if _final == "claude-direct" and upstream.status == 200:
                        _cc = 0
                        _cc_s = _cc_m = _cc_t = 0  # breakpoint per sezione: system/messages/tools
                        try:
                            _bj = json.loads(self.body.decode("utf-8", errors="replace")) if isinstance(self.body, bytes) else (json.loads(self.body) if isinstance(self.body, str) else {})
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
                        _ch = int(_usage.get("cache_read_input_tokens", 0)) + int(_usage.get("cache_creation_input_tokens", 0))
                        if _cc > 0 and _ch == 0:
                            self.log_fn(f"cache: MISS TOTALE breakpoints={_cc} input={_usage['input_tokens']} (nessun cache_read/creation nella risposta)")
                        elif _cc == 0:
                            self.log_fn(f"cache: nessun breakpoint cache_control nel body inviato input={_usage['input_tokens']}")
                        else:
                            self.log_fn(f"cache: OK bp=s{_cc_s}/m{_cc_m}/t{_cc_t} read={_usage['cache_read_input_tokens']} creation={_usage['cache_creation_input_tokens']} input={_usage['input_tokens']}")
                except Exception:
                    pass
                _is_synthetic = False
                try:
                    _is_synthetic = (
                        self.request.headers.get("x-airouter-synthetic", "").strip().lower()
                        in ("1", "true", "yes")
                    )
                except Exception:
                    pass
                self.log_router_usage_fn(
                    chat_id=chat_fp_for_rewrite,
                    orig=_orig,
                    final=_final,
                    usage=_usage,
                    mode=self.mode,
                    client=self.request.headers.get("User-Agent", "?")[:40] or "?",
                    status=upstream.status,
                    path=self.request.path,
                    # Peso del blocco `tools` della richiesta: None quando la
                    # telemetria e' spenta (default), cosi' l'entry del sidecar
                    # resta identica a prima.
                    tools=collect_tools_stats(self.body),
                    body=self.body,
                    # Latenza: ttfb_ms puo' essere None se lo stream non ha mai
                    # prodotto un chunk (errore prima del primo byte); in quel caso
                    # il campo semplicemente non compare nell'entry.
                    ttfb_ms=_ttfb_ms,
                    total_ms=(time.monotonic() - _t_start) * 1000.0,
                    # Sonde e test: chi genera traffico sintetico manda
                    # 'x-airouter-synthetic: 1' e l'entry viene marcata, cosi' le
                    # analisi per modalita' possono escluderla invece di doverla
                    # indovinare dal fingerprint del client.
                    synthetic=_is_synthetic,
                )
            except Exception:
                pass
        # GUARD pseudo-toolcall (2026-07-22): se la request aveva `tools` ma la
        # risposta (primi 16KB) contiene tool call TESTUALI e nessun tool_use
        # strutturato, l'esecutore ha degradato (tools ignorati/persi upstream).
        # Non blocca né modifica il flusso: logga + cataloga per diagnosi.
        try:
            _guard_txt = _acc_buf.decode("utf-8", errors="replace")
            if b'"tools"' in self.body and '"tool_use"' not in _guard_txt:
                _markers = ("minimax:tool_call", "[TOOL_CALL]", "[/TOOL_CALL]",
                            "<tool_call>", "<invoke name=")
                _hit = next((_m for _m in _markers if _m in _guard_txt), None)
                if _hit:
                    self.log_fn(f"PSEUDO-TOOLCALL testuale (marker={_hit}) mode={self.mode} "
                                f"fp={chat_fp_for_rewrite} status={upstream.status} — executor ha ignorato i tools")
                    dl.capture(
                        kind="pseudo_toolcall_text", request=self.request,
                        fp=chat_fp_for_rewrite, client_model=orig_model or "",
                        status=upstream.status, stage="relay",
                        upstream_status=upstream.status,
                        upstream_raw=bytes(_acc_buf[:8192]),
                        upstream_encoding=upstream.headers.get("Content-Encoding", ""),
                        orig=self.orig, mode=self.mode,
                        note=f"marker={_hit}",
                    )
        except Exception:
            pass
        # GUARD isolamento response-side (2026-07-26): il filtro request-side rimuove
        # le DEFINIZIONI dei tool stranieri, ma non impedisce al modello di imitare
        # dalla history il NOME di un tool di un altro provider. Qui si RILEVA soltanto,
        # senza toccare la risposta: riscrivere uno stream SSE a valle spezzerebbe la
        # numerazione dei content_block, e il fenomeno non e' mai stato osservato.
        try:
            _bk = tool_isolation.backend_from_final(
                final_override or ("claude-direct" if self.mode == "anthropic" else ""))
            if _bk:
                _txt = _buf_str or _acc_buf.decode("utf-8", errors="replace")
                _foreign = tool_isolation.detect_foreign_tool_use(_txt, _bk)
                if _foreign:
                    self.log_fn(f"FOREIGN-TOOLUSE {_foreign} backend={_bk} "
                                f"mode={self.mode} fp={chat_fp_for_rewrite}")
                    dl.capture(
                        kind="foreign_tool_use_response", request=self.request,
                        fp=chat_fp_for_rewrite, client_model=orig_model or "",
                        status=upstream.status, stage="relay",
                        upstream_status=upstream.status,
                        upstream_raw=bytes(_acc_buf[:4096]),
                        upstream_encoding=upstream.headers.get("Content-Encoding", ""),
                        orig=self.orig, mode=self.mode,
                        note=f"foreign={_foreign} backend={_bk}",
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
