# ~440 lines
"""Anthropic pipeline and body builders extracted from ai-router-proxy.py (~lines 1705-2796)."""
import asyncio
import json
import os

from router_constants import (
    MINIMAX_CONTEXT_BYTE_LIMIT, ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT,
    SUMMARY_BUDGET,
)
from router_utils import (
    log, _analyze_body_structure,
    debug_capture, _repair_message_sequence,
)
from trim_smart import SHRINK_KEEP_TAIL, build_shrink_summary
from forward_anthropic import forward_anthropic, forward_anthropic_direct
# Ri-esportato: pipeline_minimax importa _body_has_images DA QUI, non da
# providers.base. Rimuoverlo come "import inutilizzato" rompe la gestione
# immagini di MiniMax con un ImportError latente, visibile solo quando
# arriva una richiesta con immagini (regressione 2026-07-25, rilevata da
# analisi AST degli import incrociati).
from providers.base import _body_has_images  # noqa: F401  (re-export)

# THINK su Sonnet (budget aumentato da 4s Haiku → 15s Sonnet).
# ACT_MINIMAX_TIMEOUT_SEC resta 12s per evitare retry-storm lato client.
MIX_AM_THINK_FAST_SEC = float(os.environ.get("AIROUTER_MIX_AM_THINK_FAST_SEC", "15"))
MIX_AM_ACT_TIMEOUT_SEC = float(os.environ.get("AIROUTER_MIX_AM_ACT_TIMEOUT_SEC", "12"))

# ── THINK backoff state per chat_fp ──────────────────────────────────────────
_think_lock = __import__("threading").Lock()
_think_count: dict[str, int] = {}
_THINK_TIMEOUT_SEQUENCE = [15, 20, 25]   # Sonnet ci sta in 15s sulla maggior parte
_THINK_SKIP_AFTER = 2






# ── Text extraction ────────────────────────────────────────────────────────────
def _text_from_message(j: dict) -> str:
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



# ── Helpers ─────────────────────────────────────────────────────────────────────
async def _retry_forward(forward_fn, request, body, session, attempts: int = 2):
    last_exc = None
    for i in range(attempts):
        try:
            return await forward_fn(request, body, session)
        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                log(f"[_retry] attempt {i+1}/{attempts} EXC={e}, retrying...")
    log(f"[_retry] all {attempts} attempts failed: {last_exc}")
    raise last_exc

# ── Context helpers ─────────────────────────────────────────────────────────────
def _is_context_too_large_for_minimax(body_bytes: bytes) -> bool:
    return len(body_bytes) > MINIMAX_CONTEXT_BYTE_LIMIT


async def _is_context_exceed_400(up) -> tuple:
    if up.status != 400:
        return (False, b"")
    try:
        raw = await up.read()
    except Exception:
        return (False, b"")
    low = raw.lower()
    is_ctx = (b"context window" in low or b"exceeds limit" in low
              or b"2013" in low or b"context_length" in low
              or b"too long" in low or b"maximum context" in low
              or b"context_exceeded" in low)
    return (is_ctx, raw)

# _has_web_search_tool / _web_search_blocked_response RIMOSSE (2026-07-26):
# servivano solo al gate 400 di pipeline_minimax, eliminato perche' rifiutava
# richieste che forward_minimax ripulisce gia' da solo. Nessun altro chiamante
# (verificato via AST su tutto il repo).

# ── Shrink ─────────────────────────────────────────────────────────────────────
async def _try_shrink_body_haiku(orig: dict, target_bytes: int):
    """Shrink body per stare in target_bytes (versione inline per rescue)."""
    try:
        msgs = orig.get("messages", []) or []
        if not msgs:
            return None
        # Immagini NON ridimensionate per shrink (fix 2026-07-22)

        # BUG 2 fix: system serializzato correttamente (non come JSON grezzo)
        system_val = orig.get("system", "")
        if isinstance(system_val, list):
            parts = []
            for v in system_val:
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, dict):
                    if v.get("type") == "text":
                        parts.append(v.get("text", ""))
            system_str = "\n\n".join(parts)
        elif isinstance(system_val, str):
            system_str = system_val
        else:
            system_str = ""

        # BUG 1 fix: scala di budget - il summary NON va MAI perso
        BUDGETS = [SUMMARY_BUDGET, 280_000, 140_000, 70_000, 35_000, 16_000, 8_000]
        for budget in BUDGETS:
            # BUG 3 fix: riparazione PRIMA di assegnare
            tail_msgs = _repair_message_sequence(msgs[-SHRINK_KEEP_TAIL:])
            summary_content = build_shrink_summary(msgs, budget)
            system_content = (system_str + "\n\n" + summary_content) if system_str else summary_content
            shrunk = dict(orig)
            shrunk["messages"] = tail_msgs
            if system_content:
                shrunk["system"] = system_content
            shrunk.pop("thinking", None)
            shrunk_bytes = json.dumps(shrunk).encode()
            if len(shrunk_bytes) <= target_bytes:
                return shrunk_bytes

        # Ultimo tentativo: coda ridotta a 2 messaggi, budget 8000, summary mantenuto
        tail_final = _repair_message_sequence(msgs[-2:] if len(msgs) >= 2 else msgs)
        summary_final = build_shrink_summary(msgs, 8_000)
        system_final = (system_str + "\n\n" + summary_final) if system_str else summary_final
        shrunk_final = dict(orig)
        shrunk_final["messages"] = tail_final
        if system_final:
            shrunk_final["system"] = system_final
        shrunk_final.pop("thinking", None)
        shrunk_final_bytes = json.dumps(shrunk_final).encode()
        if len(shrunk_final_bytes) <= target_bytes:
            return shrunk_final_bytes

        return None
    except Exception as e:
        log(f"try_shrink_body_haiku EXC: {e}")
        return None


# ── Mixed rescue ───────────────────────────────────────────────────────────────
async def _escalate_anthropic(request, orig: dict, session, chat_fp: str, relay,
                              anthropic_leads: bool = False):
    """Catena escalation Anthropic dopo fallimento MiniMax.

    anthropic_leads=True: MiniMax down da 2+ turni → catena Anthropic completa.
      Haiku (digest compatto) → VERIFY Sonnet (se gated) → 502 se tutto fallisce.
    anthropic_leads=False (default): fallback dopo singolo fail MiniMax nel turno.
      Prova modello utente → Haiku → 502.
    """
    # Solo l'alias _log resta locale: gli altri nomi sono già importati a livello
    # modulo e re-importarli qui li renderebbe locali all'intera funzione (ogni uso
    # precedente diventerebbe UnboundLocalError).
    from router_utils import log as _log
    tr = getattr(request, "transport", None)
    if tr is None or tr.is_closing():
        # Relay al client già iniziato e rotto (o client sparito): ogni rescue
        # scriverebbe su un transport chiuso -> "Cannot write to closing
        # transport". Inutile spendere chiamate user-model + Haiku.
        _log(f"mix-am ACT rescue SKIP: transport client chiuso fp={chat_fp}")
        raise ConnectionResetError("client transport closing, rescue impossibile")
    _log(f"mix-am ACT: Haiku rescue fp={chat_fp}")
    body_bytes_rescue = json.dumps(dict(orig)).encode()
    if len(body_bytes_rescue) > MINIMAX_CONTEXT_BYTE_LIMIT:
        shrunk = await _try_shrink_body_haiku(orig, MINIMAX_CONTEXT_BYTE_LIMIT)
        if shrunk is not None and shrunk != body_bytes_rescue:
            body_bytes_rescue = shrunk
            _log(f"mix-am ACT rescue: shrink OK -> {len(body_bytes_rescue)}b fp={chat_fp}")
    user_status = None
    user_raw = b""
    haiku_status = None
    haiku_raw = b""
    try:
        up = await forward_anthropic_direct(request, body_bytes_rescue, session)
        user_status = up.status
        if up.status < 400:
            from fail_tracker import mixed_fail_reset
            mixed_fail_reset(chat_fp)
            _log(f"mix-am ACT rescue: modello utente {up.status} OK fp={chat_fp}")
            return await relay(up)
        if up.status == 400:
            try:
                user_raw = await up.read()
            except Exception:
                pass
            try:
                await up.release()
            except Exception:
                pass
        elif up.status == 429:
            should_retry = str(up.headers.get("x-should-retry", "")).lower() == "true"
            if not should_retry:
                _log(f"mix-am ACT rescue: modello utente 429 Rate Limit -> relay subito fp={chat_fp}")
                return await relay(up)
            # Retry certificato SDK (2026-07-22): stessa logica del path anthropic
            # puro (backoff esponenziale + jitter, onora retry-after) invece dei
            # delay hardcoded [1.5, 3.0] che ignoravano retry-after. Esauriti i
            # retry → Haiku. Riuso pipeline_common.
            from pipeline_common import (parse_retry_after as _parse_ra,
                                         backoff_sleep_sec as _backoff,
                                         ANTHROPIC_MAX_RETRIES as _MAXR)
            last_exc = None
            for i in range(_MAXR):
                retry_after = _parse_ra(up.headers.get("retry-after", ""))
                delay = _backoff(i, retry_after)
                try:
                    await up.release()
                except Exception:
                    pass
                _log(f"mix-am ACT rescue: modello utente 429 retry {i+1}/{_MAXR} "
                     f"retry-after={retry_after} sleep={delay:.2f}s fp={chat_fp}")
                await asyncio.sleep(delay)
                try:
                    up = await forward_anthropic_direct(request, body_bytes_rescue, session)
                    user_status = up.status
                    if up.status < 400:
                        from fail_tracker import mixed_fail_reset
                        mixed_fail_reset(chat_fp)
                        _log(f"mix-am ACT rescue: modello utente retry {i+1} {up.status} OK fp={chat_fp}")
                        return await relay(up)
                    if up.status == 429:
                        if str(up.headers.get("x-should-retry", "")).lower() == "false":
                            _log(f"mix-am ACT rescue: modello utente 429 x-should-retry=false -> Haiku fp={chat_fp}")
                            break
                        _log(f"mix-am ACT rescue: modello utente retry {i+1} ancora 429 -> continua fp={chat_fp}")
                        last_exc = None
                        continue
                    # non-429, non-2xx: esci dal retry loop e vai a Haiku
                    _log(f"mix-am ACT rescue: modello utente retry {i+1} {up.status} -> Haiku fp={chat_fp}")
                    break
                except Exception as e:
                    last_exc = e
                    _log(f"mix-am ACT rescue modello utente retry {i+1} EXC: {e}")
            # tutti i retry esauriti o eccezione: prosegue a Haiku
            if last_exc:
                user_status = None
                _log(f"mix-am ACT rescue: modello utente retry esauriti EXC -> Haiku fp={chat_fp}")
        else:
            try:
                await up.release()
            except Exception:
                pass
    except Exception as e:
        user_status = None
        _log(f"mix-am ACT rescue modello utente EXC: {e} -> Haiku")

    # Haiku fallback
    try:
        haiku_body_dict = dict(orig)
        # Rescue finale: usa il modello che il client ha scelto (config globale).
        # Il router NON impone un modello proprio (gerarchia = solo config globale).
        haiku_body_dict["model"] = (orig.get("model") or "").strip() or haiku_body_dict.get("model")
        haiku_body_bytes = json.dumps(haiku_body_dict).encode()
        if len(haiku_body_bytes) > ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT:
            shrunk_h = await _try_shrink_body_haiku(haiku_body_dict, MINIMAX_CONTEXT_BYTE_LIMIT)
            if shrunk_h is None:
                from aiohttp import web
                _log(f"mix-am ACT rescue: body > Haiku limit, skip fp={chat_fp}")
                return web.json_response(
                    {"type": "error", "error": {"type": "context_exceeded",
                     "message": f"body troppo grande anche per shrink."}},
                    status=400)
            haiku_body_bytes = shrunk_h
        # Retry certificato SDK anche sulla leg Haiku (bucket separato ma 429/5xx
        # transienti possibili): backoff + retry-after invece di rimbalzo immediato.
        from pipeline_common import anthropic_call_with_retry as _acr
        up_h, _h_exhausted = await _acr(forward_anthropic, request, haiku_body_bytes,
                                        session, log_fn=_log, tag="mix-am Haiku-rescue")
        haiku_status = up_h.status
        if up_h.status < 400:
            from fail_tracker import mixed_fail_reset
            mixed_fail_reset(chat_fp)
            _log(f"mix-am ACT rescue Haiku OK fp={chat_fp}")
            return await relay(up_h, extra_headers={"x-ai-verified": "haiku-rescue-act"})
        haiku_raw = b""
        if up_h.status == 400:
            try:
                haiku_raw = await up_h.read()
            except Exception:
                pass
            try:
                await up_h.release()
            except Exception:
                pass
        elif up_h.status == 429:
            # 429 persistito dopo i retry certificati = rate-limit reale del piano.
            # Relay onesto del 429 (con retry-after) al client, NON loop.
            _log(f"mix-am ACT rescue Haiku 429 PERSISTENTE dopo retry -> relay onesto fp={chat_fp}")
            return await relay(up_h, extra_headers={"x-ai-verified": "mix-am-ratelimit-exhausted"})
        elif up_h.status >= 500:
            _log(f"mix-am ACT rescue: Haiku {up_h.status}, relay upstream body fp={chat_fp}")
            return await relay(up_h)
        else:
            try:
                await up_h.release()
            except Exception:
                pass
        haiku_status = up_h.status
    except Exception as e:
        _log(f"mix-am ACT rescue Haiku EXC: {e} -> 502")

    # Debug capture
    orig_analysis = _analyze_body_structure(orig)
    user_sent_analysis = _analyze_body_structure(body_bytes_rescue)
    haiku_sent_analysis = _analyze_body_structure(haiku_body_bytes)
    debug_capture(kind="mixed_rescue_502", request=request, fp=chat_fp,
                  client_model=orig.get("model", ""), status=502, stage="user_model",
                  upstream_status=user_status or 0, upstream_raw=user_raw,
                  sent_bytes=len(body_bytes_rescue), orig=orig,
                  sent_analysis={"orig": orig_analysis, "sent": user_sent_analysis},
                  note=f"haiku_stage={haiku_status}")
    err_parts = [f"Haiku rescue failed: user_model={user_status}, Haiku={haiku_status}."]
    def _safe_text(raw: bytes) -> str:
        """Decodifica un body upstream per il messaggio d'errore, gzip incluso.

        Sostituisce `from router_utils import _decompress_upstream`: quella
        funzione non è mai esistita in router_utils, quindi l'import sollevava
        ImportError proprio qui — nel ramo che costruisce il 502 — mascherando
        la causa reale del fallimento (bug preesistente, rilevato 2026-07-25).
        """
        try:
            if raw[:2] == b"\x1f\x8b":
                import gzip
                raw = gzip.decompress(raw)
        except Exception:
            pass
        return raw.decode("utf-8", "replace")

    if user_raw:
        err_parts.append("user_model: " + _safe_text(user_raw)[:300])
    if haiku_raw:
        err_parts.append("haiku: " + _safe_text(haiku_raw)[:300])
    err_parts.append("Dettagli: /debug/last")
    from aiohttp import web
    return web.json_response({"type": "error", "error": {"type": "router_error",
             "message": " | ".join(err_parts)}}, status=502)



def _shrink_images_in_messages(orig: dict, max_side: int = 1024, jpeg_quality: int = 70):
    """Ridimensiona immagini base64 nei content blocks per ridurre body. ponytail:
    solo Anthropic image blocks (source.type=base64), lazy import PIL, fallback
    silenzioso se non installato. max_side=1024 + JPEG q70 -> tipicamente 5-10x
    riduzione su PNG foto-realistiche. Non applica a tool_result immagini
    ricevute dal modello (il flusso non riutilizza immagini ricevute)."""
    try:
        from PIL import Image
        import io, base64
    except Exception:
        return
    try:
        for msg in orig.get("messages", []) or []:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for blk in content:
                if not isinstance(blk, dict) or blk.get("type") != "image":
                    continue
                src = blk.get("source") or {}
                if src.get("type") != "base64":
                    continue
                data_b64 = src.get("data")
                if not data_b64 or len(data_b64) < 4000:  # ponytail: skip icone
                    continue
                try:
                    raw = base64.b64decode(data_b64)
                    img = Image.open(io.BytesIO(raw))
                    img.load()
                    if max(img.size) > max_side:
                        img.thumbnail((max_side, max_side), Image.LANCZOS)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=jpeg_quality)
                    new_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    if len(new_b64) < len(data_b64):
                        src["data"] = new_b64
                        src["media_type"] = "image/jpeg"
                except Exception:
                    continue
    except Exception:
        return


async def _serve_minimax_vision(request, orig: dict, session, chat_fp: str, relay):
    """Stub: la logica vision è ora nel flusso THINK→ACT di mix-am/mix-gm.
    Le immagini arrivano a THINK (Anthropic/GLM) che le analizza e produce un piano.
    L'ACT (MiniMax) riceve solo testo+piano, mai immagini raw.
    Ritorna sempre None (nessun bypass diretto a M3)."""
    return None

