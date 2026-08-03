# ~190 lines
"""Anthropic forwarding extracted from ai-router-proxy.py (~lines 1034-1703)."""
import json
import os
import time
from pathlib import Path

import tool_isolation
import cache_optimizer

from router_constants import (
    ANTHROPIC_UPSTREAM, ANTHROPIC_DIRECT_URL, HOP_HEADERS,
    ANTHROPIC_UNSUPPORTED_FIELDS, CLAUDE_CODE_MARKER,
)
from router_utils import (
    _analyze_body_structure, SENT_ANALYSIS, _DEBUG_LAST_SENT, log,
    upstream_timeout_for,
)
from router_auth import _load_oauth_token, _reload_oauth_token
from router_debug import dl
# A livello modulo, MAI dentro le funzioni: un import locale renderebbe il nome
# locale all'intera funzione (vedi test_module_names_resolved.py).
from anthropic_body import sanitize_server_tool_ids

# Deep-debug (analyze struttura body + dump _DEBUG_LAST_SENT su disco) è overhead
# SINCRONO nel path caldo, eseguito ad ogni richiesta e scalante col body (deep-copy
# di ogni content block + json.dump indent=2 su disco). Serve solo per diagnosi:
# gated dietro flag, default OFF. Attiva con AIROUTER_DEEP_DEBUG=1 quando serve.
_DEEP_DEBUG = os.environ.get("AIROUTER_DEEP_DEBUG", "0") == "1"


def _readable_err(raw: bytes) -> str:
    """Rende leggibile un body d'errore upstream per il log.

    Anthropic risponde compresso: finora il log stampava i byte gzip grezzi
    (b'\\x1f\\x8b...'), inutili per capire cosa fosse andato storto. Prova
    gzip, poi zlib/deflate, poi testo semplice."""
    if not raw:
        return "(vuoto)"
    if raw[:2] == b"\x1f\x8b":
        try:
            import gzip
            return gzip.decompress(raw).decode("utf-8", "replace")
        except Exception:
            pass
    try:
        import zlib
        return zlib.decompress(raw).decode("utf-8", "replace")
    except Exception:
        pass
    return raw.decode("utf-8", "replace")


class _PreReadResponse:
    """Risposta upstream il cui body e' gia' stato letto, riproposta al relay.

    FIX 2026-07-26: sul 400 il codice legge il body per capire se e' un
    context-exceeded (e in quel caso ritenta senza immagini). Se NON lo e',
    faceva `return up` — ma lo stream era gia' consumato e la connessione
    rilasciata, quindi il relay inoltrava un corpo VUOTO: il client vedeva un
    "API Error" muto, senza il messaggio diagnostico di Anthropic (es.
    "messages.1.content.0.server_tool_use.id: String should match pattern
    '^srvtoolu_...'"), che invece finiva solo nel log del router — e li' pure
    compresso gzip, cioe' illeggibile. Questa classe ripropone status, header e
    body ESATTI gia' letti, cosi' l'errore arriva intero al client.

    Emula la superficie di ClientResponse usata da StreamingRelay:
    .status, .headers, .read(), .release(), .content.iter_any()."""

    def __init__(self, status: int, headers, body: bytes):
        self.status = status
        self.headers = dict(headers or {})
        self._body = body or b""

    async def read(self):
        return self._body

    async def json(self):
        return json.loads(self._body)

    async def release(self):
        return None

    @property
    def content(self):
        body = self._body

        class _OneShot:
            async def iter_any(self):
                yield body

        return _OneShot()


def _emit_deep_debug(fn: str, request, safe_body: bytes) -> None:
    """Analisi strutturale + dump ultimo body inviato. No-op se _DEEP_DEBUG off."""
    try:
        analysis = _analyze_body_structure(safe_body)
        SENT_ANALYSIS.append({
            # %z e non "Z": prima era ora locale con suffisso UTC (vedi debug_catalog)
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "fn": fn, "path": request.path,
            "sent_bytes": analysis["size_bytes"],
            "analysis": analysis,
        })
        try:
            body_dict = json.loads(safe_body)
            for m in body_dict.get("messages", []):
                c = m.get("content", [])
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "image":
                            d = b.get("data", "")
                            if len(d) > 200:
                                b["data"] = d[:200] + f"... [TRUNCATED {len(d) - 200} chars]"
            with open(_DEBUG_LAST_SENT, "w") as f:
                json.dump({"sent_body": body_dict, "analysis": analysis}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        if analysis["orphan_tool_results"] or analysis["role_system_in_messages"] > 0:
            log(f"[DEEP-DEBUG-WARN] {fn}: orphans={len(analysis['orphan_tool_results'])} "
                f"role_system_msgs={analysis['role_system_in_messages']}")
    except Exception:
        pass


def strip_unsupported_fields(raw: bytes, fields: tuple) -> bytes:
    """Rimuove campi non supportati dal body JSON. No-op se non è JSON."""
    try:
        data = json.loads(raw)
        changed = False
        for f in fields:
            if f in data:
                data.pop(f, None)
                changed = True
        return json.dumps(data).encode() if changed else raw
    except Exception:
        return raw


def _log_original_model(orig: str, final: str, chat_id: str) -> None:
    """Log modello originale prima del remap a MiniMax."""
    from router_constants import SIDECAR
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": int(time.time()), "chat": chat_id, "orig": orig, "final": final}
        with open(SIDECAR, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# _force_no_stream e stata rimossa il 2026-08-03 perche mai chiamata


def _text_from_message(j: dict) -> str:
    """Estrae testo puro da un message dict Anthropic."""
    out = []
    for b in (j or {}).get("content", []):
        if isinstance(b, dict):
            t = b.get("type", "")
            if t == "text":
                out.append(b.get("text", ""))
            elif t == "thinking":
                inner = b.get("thinking", {})
                if isinstance(inner, dict):
                    out.append(inner.get("thinking", ""))
                elif isinstance(inner, str):
                    out.append(inner)
    return "".join(out)


async def forward_anthropic(request, body, session):
    """Chiama api.anthropic.com con OAuth subscription Bearer."""
    from router_auth import get_minimax_key as _unused  # bridge import for tool isolation
    from router_utils import _repair_message_sequence

    url = ANTHROPIC_UPSTREAM + request.path  # strip query string (Anthropic API uses body/headers, not query params)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    auth = headers.get("Authorization", "") or headers.get("authorization", "")

    if auth.startswith("Bearer sk-ant-oat"):
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        _reload_oauth_token()
        tok = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        elif auth:
            pass

    # Strip 1m beta for Sonnet/Haiku (fork-subagent inherited it)
    beta = headers.get("anthropic-beta", "") or headers.get("Anthropic-Beta", "")
    if beta and "context-1m" in beta.lower():
        try:
            body_dict = json.loads(body)
            model_str = (body_dict.get("model") or "").lower()
            is_small = any(m in model_str for m in ("sonnet", "haiku")) and "opus" not in model_str
            if is_small:
                new_beta = ",".join(
                    tok.strip() for tok in beta.split(",")
                    if "context-1m" not in tok.lower()
                )
                if new_beta:
                    headers["anthropic-beta"] = new_beta
                else:
                    headers.pop("anthropic-beta", None)
                    headers.pop("Anthropic-Beta", None)
                log(f"[#68727] stripped 1m beta for {model_str}")
        except Exception:
            pass

    safe_body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS) \
        if "/v1/messages" in request.path else body
    safe_body = tool_isolation.filter_tools_for_backend(safe_body, "anthropic")

    if "/v1/messages" in request.path:
        headers.setdefault("anthropic-version", "2023-06-01")
        try:
            body_dict = json.loads(safe_body)
            msgs = body_dict.get("messages", [])
            role_sys = sum(1 for m in msgs if m.get("role") == "system")
            if role_sys > 0 or msgs:
                repaired = _repair_message_sequence(msgs)
                body_dict["messages"] = repaired
                safe_body = json.dumps(body_dict).encode()
        except Exception:
            pass

    # Anthropic rifiuta con 400 gli id server_tool_use non conformi a
    # ^srvtoolu_[a-zA-Z0-9_]+$ (in mix-am li produce l'esecutore MiniMax e
    # finiscono nella history). Fast path interno: se il body non contiene
    # "server_tool_use" non viene nemmeno parsato.
    safe_body, _n_srv = sanitize_server_tool_ids(safe_body)
    # Copre la coda della conversazione con il breakpoint di cache rimasto libero:
    # senza, un contesto lungo viene riprocessato a ogni turno (creation=0) e lo
    # stream rallenta fino a farsi chiudere dal client. No-op se i 4 slot sono pieni.
    safe_body = cache_optimizer.ensure_tail_cache_breakpoint(safe_body)
    if _n_srv:
        log(f"anthropic: sanificati {_n_srv} id server_tool_use non conformi")

    # Deep debug (gated: default OFF, vedi _emit_deep_debug)
    if _DEEP_DEBUG:
        _emit_deep_debug("forward_anthropic", request, safe_body)

    # Context window retry: 400 context -> strip images and retry
    _kw = dict(data=safe_body, headers=headers, allow_redirects=False)
    _to = upstream_timeout_for(safe_body)
    if _to is not None:
        _kw["timeout"] = _to  # non-streaming: vedi upstream_timeout_for
    try:
        up = await session.request(request.method, url, **_kw)
        if up.status == 400:
            err_headers = dict(up.headers)
            try:
                raw_err = await up.read()
            except Exception:
                raw_err = b""
            await up.release()
            log(f"[forward_anthropic] 400 body: {_readable_err(raw_err)[:300]}")
            low = raw_err.lower()
            is_ctx = (b"context window" in low or b"reached its context" in low
                      or b"context_exceeded" in low or b"context limit" in low
                      or b"exceeds limit" in low)
            if is_ctx:
                from providers.base import strip_images_body
                stripped = strip_images_body(safe_body)
                if stripped != safe_body:
                    log(f"[forward_anthropic] ctx-exceed 400 -> retry with images stripped")
                    _kw_s = dict(data=stripped, headers=headers,
                                 allow_redirects=False)
                    _to_s = upstream_timeout_for(stripped)
                    if _to_s is not None:
                        _kw_s["timeout"] = _to_s
                    up = await session.request(request.method, url, **_kw_s)
                    if up.status < 400:
                        return up
                    dl.capture(kind="forward_anthropic_ctx_exceed_retry_fail",
                             request=request, stage="forward",
                             upstream_status=up.status,
                             note="context exceed retry with images stripped failed", mode="anthropic",
                             severity="error")
                    retry_headers = dict(up.headers)
                    try:
                        retry_err = await up.read()
                    except Exception:
                        retry_err = b""
                    await up.release()
                    log(f"[forward_anthropic] ctx-retry fallito {up.status}: "
                        f"{_readable_err(retry_err)[:300]}")
                    # anche qui lo stream e' consumato: ripropone il body letto
                    return _PreReadResponse(up.status, retry_headers, retry_err)
            # 400 non-context: lo stream e' gia' stato consumato sopra, quindi
            # `return up` consegnerebbe al client un corpo VUOTO. Ripropone il
            # body reale, cosi' il messaggio di Anthropic arriva a schermo.
            return _PreReadResponse(400, err_headers, raw_err)
        return up
    except Exception:
        raise


async def forward_anthropic_direct(request, body, session):
    """Chiama api.anthropic.com diretto con OAuth Bearer. Usato dalle verify T2."""
    from router_utils import _repair_message_sequence
    global ANTHROPIC_OAUTH_TOKEN

    if not globals().get("ANTHROPIC_OAUTH_TOKEN"):
        _load_oauth_token()
    if _reload_oauth_token():
        ANTHROPIC_OAUTH_TOKEN = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")

    url = ANTHROPIC_DIRECT_URL + request.path  # strip query string
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    for h in list(headers):
        if h.lower() in ("authorization", "x-api-key"):
            headers.pop(h)
    tok = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    headers.setdefault("anthropic-version", "2023-06-01")

    safe_body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS) \
        if "/v1/messages" in request.path else body
    safe_body = tool_isolation.filter_tools_for_backend(safe_body, "anthropic")

    if "/v1/messages" in request.path:
        try:
            body_dict = json.loads(safe_body)
            msgs = body_dict.get("messages", [])
            role_sys = sum(1 for m in msgs if m.get("role") == "system")
            if role_sys > 0 or msgs:
                repaired = _repair_message_sequence(msgs)
                body_dict["messages"] = repaired
                safe_body = json.dumps(body_dict).encode()
        except Exception:
            pass

    # Stessa sanificazione del path principale: questa funzione è usata dalle
    # rescue chain delle modalità miste, dove la history contiene proprio i
    # blocchi prodotti dall'esecutore non-Anthropic.
    safe_body, _n_srv = sanitize_server_tool_ids(safe_body)
    # Copre la coda della conversazione con il breakpoint di cache rimasto libero:
    # senza, un contesto lungo viene riprocessato a ogni turno (creation=0) e lo
    # stream rallenta fino a farsi chiudere dal client. No-op se i 4 slot sono pieni.
    safe_body = cache_optimizer.ensure_tail_cache_breakpoint(safe_body)
    if _n_srv:
        log(f"anthropic-direct: sanificati {_n_srv} id server_tool_use non conformi")

    # Deep debug (gated: default OFF, vedi _emit_deep_debug)
    if _DEEP_DEBUG:
        _emit_deep_debug("forward_anthropic_direct", request, safe_body)

    _kw_d = dict(data=safe_body, headers=headers, allow_redirects=False)
    _to_d = upstream_timeout_for(safe_body)
    if _to_d is not None:
        _kw_d["timeout"] = _to_d  # non-streaming: vedi upstream_timeout_for
    return await session.request(request.method, url, **_kw_d)
