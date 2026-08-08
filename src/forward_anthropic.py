# ~190 lines
"""Anthropic forwarding extracted from ai-router-proxy.py (~lines 1034-1703)."""
import json
import os
import time

import tool_isolation
import cache_optimizer

from router_constants import (
    ANTHROPIC_UPSTREAM, ANTHROPIC_DIRECT_URL, HOP_HEADERS,
    is_context_exceeded_body,
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
import anthropic_capabilities

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




# _log_original_model rimossa il 2026-08-07: nessun chiamante, sidecar fermo dal 2026-07-25


# _force_no_stream e stata rimossa il 2026-08-03 perche mai chiamata


# _text_from_message rimossa il 2026-08-07: copia morta, nessun chiamante. Fonte unica in providers/base.py


async def forward_anthropic(request, body, session):
    """Chiama api.anthropic.com con OAuth subscription Bearer."""
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

    # Whitelist PER MODELLO al posto dello strip cieco: i modelli recenti
    # supportano output_config.effort (misurato: -40% di token in uscita) e
    # thinking, i vecchi rispondono 400. In piu' garantisce il marker Claude
    # Code sui modelli che senza di esso rispondono 429 (opus/sonnet).
    if "/v1/messages" in request.path:
        # inject_marker=False: MISURATO il 2026-08-07, iniettare il marker sul
        # traffico del CLI cambia il system prompt e INVALIDA il prompt caching.
        # Effetto osservato su traffico reale: rapporto cache_read/cache_creation
        # crollato da 82,4 a 2,6 e cache_creation per richiesta salita da 4.167 a
        # 52.513 (12,6x). Il rimedio costava piu' del male: il CLI il marker ce
        # l'ha gia'. L'iniezione resta disponibile per le richieste sintetiche.
        # context_management: il CLI lo MANDA (301 richieste osservate nei log) e
        # finora il router lo cancellava. Misurato il 2026-08-08 su conversazione
        # con 12 giri di tool: clear_tool_uses toglie 31.644 token di input
        # (-72,4%, cleared_tool_uses=9). Resta DISATTIVATO di default perche'
        # cancellare invalida il prefisso in cache, e con 345k token di cache_read
        # per richiesta il rimedio potrebbe costare piu' del male: e' lo stesso
        # meccanismo che ha fatto danno con il marker. Serve una misura multi-turno
        # prima di accenderlo. Attivazione: AIROUTER_ALLOW_CONTEXT_MGMT=1
        _allow_cm = os.environ.get("AIROUTER_ALLOW_CONTEXT_MGMT", "") == "1"
        safe_body, _caps = anthropic_capabilities.prepare_body(
            body, allow_context_management=_allow_cm, inject_marker=False)
        if _allow_cm and _caps.get("has_context_management"):
            # Senza questo beta header l'API risponde 400 "Extra inputs are not
            # permitted": e' la ragione per cui strippare era la scorciatoia.
            _beta = headers.get("anthropic-beta") or headers.get("Anthropic-Beta") or ""
            if anthropic_capabilities.BETA_CONTEXT_MANAGEMENT not in _beta:
                headers.pop("Anthropic-Beta", None)
                headers["anthropic-beta"] = (
                    f"{_beta},{anthropic_capabilities.BETA_CONTEXT_MANAGEMENT}"
                    if _beta else anthropic_capabilities.BETA_CONTEXT_MANAGEMENT)
        if _caps["stripped"] or _caps["marker_added"]:
            log(f"[caps] {_caps['model']}: stripped={_caps['stripped']} "
                f"marker_added={_caps['marker_added']}")
    else:
        safe_body = body
    safe_body = tool_isolation.filter_tools_for_backend(safe_body, "anthropic")

    if "/v1/messages" in request.path:
        headers.setdefault("anthropic-version", "2023-06-01")
        try:
            body_dict = json.loads(safe_body)
            # Promozione PRIMA della riparazione: _repair_message_sequence
            # elimina i messaggi role=system, e fino al 2026-08-08 ne buttava il
            # contenuto (verificato: un system con "rispondi solo ANANAS" veniva
            # ignorato dalla risposta). Ora il testo passa nel campo `system`,
            # dove l'API Anthropic lo vuole. Il blocco si aggiunge in CODA e
            # senza cache_control: il prompt caching lavora per prefissi, quindi
            # la parte gia' in cache resta valida.
            # NB: questo blocco esiste in DUE gemelle, forward_anthropic e
            # forward_anthropic_direct. Modificarne una sola le fa divergere.
            from router_utils import promote_system_messages
            _promossi = promote_system_messages(body_dict)
            if _promossi:
                log(f"promossi {_promossi} messaggi role=system nel campo system (anthropic), {getattr(promote_system_messages, 'ultimi_caratteri', 0)} caratteri")
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
            # I marker vivevano qui in una copia che non conteneva "too long",
            # quindi l'errore più frequente di Anthropic, "prompt is too long:
            # N tokens > M maximum", non veniva riconosciuto e il retry senza
            # immagini non scattava mai. Ora la lista è unica, in router_constants.
            is_ctx = is_context_exceeded_body(raw_err)
            if is_ctx:
                from providers.base import strip_images_body
                stripped = strip_images_body(safe_body)
                if stripped != safe_body:
                    log("[forward_anthropic] ctx-exceed 400 -> retry with images stripped")
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

    # Whitelist PER MODELLO al posto dello strip cieco: i modelli recenti
    # supportano output_config.effort (misurato: -40% di token in uscita) e
    # thinking, i vecchi rispondono 400. In piu' garantisce il marker Claude
    # Code sui modelli che senza di esso rispondono 429 (opus/sonnet).
    if "/v1/messages" in request.path:
        # inject_marker=False: MISURATO il 2026-08-07, iniettare il marker sul
        # traffico del CLI cambia il system prompt e INVALIDA il prompt caching.
        # Effetto osservato su traffico reale: rapporto cache_read/cache_creation
        # crollato da 82,4 a 2,6 e cache_creation per richiesta salita da 4.167 a
        # 52.513 (12,6x). Il rimedio costava piu' del male: il CLI il marker ce
        # l'ha gia'. L'iniezione resta disponibile per le richieste sintetiche.
        # context_management: il CLI lo MANDA (301 richieste osservate nei log) e
        # finora il router lo cancellava. Misurato il 2026-08-08 su conversazione
        # con 12 giri di tool: clear_tool_uses toglie 31.644 token di input
        # (-72,4%, cleared_tool_uses=9). Resta DISATTIVATO di default perche'
        # cancellare invalida il prefisso in cache, e con 345k token di cache_read
        # per richiesta il rimedio potrebbe costare piu' del male: e' lo stesso
        # meccanismo che ha fatto danno con il marker. Serve una misura multi-turno
        # prima di accenderlo. Attivazione: AIROUTER_ALLOW_CONTEXT_MGMT=1
        _allow_cm = os.environ.get("AIROUTER_ALLOW_CONTEXT_MGMT", "") == "1"
        safe_body, _caps = anthropic_capabilities.prepare_body(
            body, allow_context_management=_allow_cm, inject_marker=False)
        if _allow_cm and _caps.get("has_context_management"):
            # Senza questo beta header l'API risponde 400 "Extra inputs are not
            # permitted": e' la ragione per cui strippare era la scorciatoia.
            _beta = headers.get("anthropic-beta") or headers.get("Anthropic-Beta") or ""
            if anthropic_capabilities.BETA_CONTEXT_MANAGEMENT not in _beta:
                headers.pop("Anthropic-Beta", None)
                headers["anthropic-beta"] = (
                    f"{_beta},{anthropic_capabilities.BETA_CONTEXT_MANAGEMENT}"
                    if _beta else anthropic_capabilities.BETA_CONTEXT_MANAGEMENT)
        if _caps["stripped"] or _caps["marker_added"]:
            log(f"[caps] {_caps['model']}: stripped={_caps['stripped']} "
                f"marker_added={_caps['marker_added']}")
    else:
        safe_body = body
    safe_body = tool_isolation.filter_tools_for_backend(safe_body, "anthropic")

    if "/v1/messages" in request.path:
        try:
            body_dict = json.loads(safe_body)
            # Promozione PRIMA della riparazione: _repair_message_sequence
            # elimina i messaggi role=system, e fino al 2026-08-08 ne buttava il
            # contenuto (verificato: un system con "rispondi solo ANANAS" veniva
            # ignorato dalla risposta). Ora il testo passa nel campo `system`,
            # dove l'API Anthropic lo vuole. Il blocco si aggiunge in CODA e
            # senza cache_control: il prompt caching lavora per prefissi, quindi
            # la parte gia' in cache resta valida.
            # NB: questo blocco esiste in DUE gemelle, forward_anthropic e
            # forward_anthropic_direct. Modificarne una sola le fa divergere.
            from router_utils import promote_system_messages
            _promossi = promote_system_messages(body_dict)
            if _promossi:
                log(f"promossi {_promossi} messaggi role=system nel campo system (anthropic), {getattr(promote_system_messages, 'ultimi_caratteri', 0)} caratteri")
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
