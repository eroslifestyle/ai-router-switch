# ~160 lines
"""MiniMax pipeline (orchestrate/think/act) extracted from ai-router-proxy.py (~lines 2798-2994)."""
import json
import sys

from aiohttp import web  # usato nei rami d'errore (righe ~65 e ~98): senza questo
                         # import il gestore stesso sollevava NameError e aiohttp
                         # rispondeva 500 "Server got itself in trouble" opaco.

from router_constants import (
    MINIMAX_MODEL, MINIMAX_CONTEXT_BYTE_LIMIT,
)
from router_utils import log, log_router_usage, _request_orig_model
import debug_catalog

# Dead code rimosso 2026-07-22 (audit): 5 funzioni THINK/ACT legacy MiniMax
# (_build_minimax_think_body, _pick_minimax_executor, _build_minimax_act_body,
# _build_minimax_act_body_retry, _parse_think_json) — mai chiamate da nessuno.
# L'orchestrazione minimax pura è passthrough diretto dal redesign 2026-07-22
# (il parser cercava [PLAN] ma il prompt chiedeva JSON → piano sempre scartato).
# L'import _text_from_message serviva solo a queste funzioni.


def _effective_minimax_model(orig, model_override):
    """Restituisce il modello MiniMax effettivamente servito.

    Prima i due call site usavano 'model_override or "MiniMax-M3"', quindi con
    override None etichettavano MiniMax-M3 anche le richieste che il body
    mandava a MiniMax-M2.7 (834 casi in 7 giorni), falsando ogni analisi per
    modello. La regola vera è quella di remap_body_for_minimax: un nome già
    MiniMax resta intatto e tutto il resto va sul default.
    """
    if model_override:
        return model_override
    name = str((orig or {}).get('model') or '')
    if name.lower().startswith('minimax'):
        return name
    return MINIMAX_MODEL


async def _pipeline_minimax_orchestrate(request, body, session, orig: dict, relay, model_override: str | None = None):
    """mode=minimax: passthrough streaming diretto a MiniMax-M3.

    Parametri:
      - model_override: se non None, riscrive il modello nelle chiamate a forward_minimax
                       (es. "MiniMax-M2.7" per forzare l'ACT anche in modalità THINK).

    Redesign 2026-07-22 (perf/latenza): rimossa l'orchestrazione THINK/ACT/VERIFY.
    Root cause verificata dai log (giorni di "THINK: piano non valido -> executor
    diretto" al 100% dei turni): il prompt THINK chiedeva JSON mentre il parser
    _parse_think_json cercava tag [PLAN]...[/PLAN] — formati incompatibili, piano
    SEMPRE scartato. La pipeline non ha MAI orchestrato in produzione: THINK+VERIFY
    erano solo 3-8s di latenza morta prima di ogni risposta (baseline TTFB 3.8-8.4s),
    con VERIFY non-gated + up.read() bufferizzante che impediva lo streaming.

    Rimuovere l'orchestrazione morta NON cambia il comportamento osservabile
    (l'output veniva già sempre da forward_minimax diretto), elimina solo la latenza.
    Restano intatti i guard che servono davvero: context-too-large + shrink,
    immagini (vision), web-search block. Lo stream MiniMax passa al client via relay()
    senza bufferizzazione (primo byte non appena MiniMax risponde)."""
    # Lazy import to avoid circular dependencies
    from router_mode import _resolve_chat_fingerprint
    from forward_minimax import forward_minimax
    from pipeline_anthropic import (
        _is_context_too_large_for_minimax,
        _body_has_images, _serve_minimax_vision,
    )

    chat_fp = _resolve_chat_fingerprint(request)

    if _is_context_too_large_for_minimax(body):
        shrunk = await _try_shrink_body(orig, MINIMAX_CONTEXT_BYTE_LIMIT)
        if shrunk is not None and shrunk != body:
            try:
                up_pre = await forward_minimax(request, shrunk, session, model_override=model_override)
                if up_pre.status < 400:
                    log(f"minimax PRE shrunk OK {up_pre.status} fp={chat_fp}")
                    return await relay(up_pre, extra_headers={"x-ai-verified": "minimax-m3-shrunk"}, final_override=_effective_minimax_model(orig, model_override))
                try:
                    await up_pre.release()
                except Exception:
                    pass
            except Exception as e:
                log(f"minimax PRE shrunk EXC: {e}")
        if shrunk is None:
            return web.json_response(
                {"type": "error", "error": {"type": "context_exceeded",
                 "message": f"body {len(body)}b > limit e shrink non riesce."}},
                status=400)

    # GATE web_search RIMOSSO (2026-07-26). Rifiutava con 400 ogni richiesta che
    # avesse il tool web_search fra i tools: in mix-am basta che l'esecutore
    # delegato dal main Anthropic lo abbia in lista perche' il turno muoia con un
    # API error a schermo. Il rifiuto era gia' superfluo — a valle di questo punto
    # forward_minimax ripulisce tutto, sempre e incondizionatamente:
    #   - remap_body_for_minimax -> strip_server_tools_for_minimax() toglie le
    #     definizioni server-tool e converte in testo i blocchi server_tool_use /
    #     web_search_tool_result rimasti nella history (era il 400 "2013");
    #   - tool_isolation.filter_tools_for_backend() toglie i tool brandizzati di
    #     altri provider (WebSearch/WebFetch inclusi per nome esatto);
    #   - sanitize_tool_choice() riallinea tool_choice ai tool superstiti.
    # Effetto atteso: la richiesta passa, l'esecutore MiniMax semplicemente non
    # vede il tool WebSearch di Anthropic — coerente con la policy globale
    # "web search = MiniMax/m3-web, non Anthropic". Degrado di capacita', non
    # errore in faccia all'utente.

    if _body_has_images(orig):
        res = await _serve_minimax_vision(request, orig, session, chat_fp, relay)
        if res is not None:
            return res

    # Passthrough streaming diretto: primo byte appena MiniMax risponde, zero overhead.
    try:
        up = await forward_minimax(request, body, session, model_override=model_override)
    except Exception as e:
        log(f"minimax passthrough EXC: {e} fp={chat_fp}")
        debug_catalog.record_event(severity="error", category="minimax",
                                    kind="forward_exception", chat_fp=chat_fp, snippet=str(e))
        # Il 502 finiva SOLO in ai-router.log: nel sidecar non compariva (59 casi
        # fra il 26 e il 28/07, zero tracce). Un fallimento invisibile alla
        # telemetria non e' misurabile, quindi non e' correggibile con prove.
        log_router_usage(chat_id=chat_fp, orig=_request_orig_model.get(chat_fp, "?"),
                         final=model_override or MINIMAX_MODEL, usage={},
                         mode="minimax", client=request.headers.get("user-agent", "?"),
                         status=502, path=request.path)
        return web.json_response({"type": "error", "error": {"type": "router_error",
                                  "message": str(e)}}, status=502)
    log(f"minimax passthrough {up.status} {request.path} fp={chat_fp}")
    return await relay(up, extra_headers={"x-ai-verified": f"minimax-direct({MINIMAX_MODEL.lower()})"}, final_override=_effective_minimax_model(orig, model_override))


async def _try_shrink_body(orig: dict, target_bytes: int):
    """Prova a shrinkare il body per farlo stare in target_bytes, mantenendo sempre un riassunto.

    Interfaccia: dict -> bytes | None, con side-effect su orig per le immagini.

    L'orchestrazione del budget è delegata a shrink_body_to_budget(context_shrink.py),
    che preserva il prompt caching tramite taglio sticky (K quantizzato, prefisso identico
    fra turni consecutivi). La copia locale aveva il summary nel campo 'system' e una coda
    scorrevole, che cambiavano a ogni turno rifacendo il prefisso e uccidendo la cache.
    """
    try:
        msgs = orig.get("messages", []) or []
        if not msgs:
            return None

        # Side-effect: riduci le immagini in-place su orig
        try:
            from pipeline_anthropic import _shrink_images_in_messages
            _shrink_images_in_messages(orig)
        except Exception:
            pass

        # Serializza orig a bytes (corpo della richiesta JSON)
        orig_bytes = json.dumps(orig).encode()
        original_bytes_len = len(orig_bytes)

        # Delega a shrink_body_to_budget: orchestrazione del budget + sticky caching
        from context_shrink import shrink_body_to_budget
        shrunk_bytes = await shrink_body_to_budget(orig_bytes, target_bytes, shrink_images=False)

        if shrunk_bytes is None:
            return None

        # 2026-08-15: shrink eccessivo = probabile perdita di informazioni utili.
        # Se togliamo più del 50%, logghiamo warning visibile (cliente + log).
        shrunk_len = len(shrunk_bytes)
        if shrunk_len < original_bytes_len * 0.5:
            pct = 100 * (original_bytes_len - shrunk_len) // max(original_bytes_len, 1)
            warn_msg = (
                f"WARN pipeline_minimax: _try_shrink_body ha rimosso {pct}% del body "
                f"({original_bytes_len}->{shrunk_len} byte). Possibile perdita di contesto "
                f"inviato a MiniMax. Verifica spec/Intent."
            )
            print(warn_msg, file=sys.stderr)
            try:
                _log_warn(warn_msg)
            except NameError:
                pass

        return shrunk_bytes

    except Exception as e:
        log(f"try_shrink_body EXC: {e}")
        return None
