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
# ── Rimosse 2026-08-03 ─────────────────────────────────────────────────────────
# Qui vivevano _try_shrink_body_haiku e _escalate_anthropic, codice morto.
# _escalate_anthropic era un residuo della rimozione delle pipeline THINK/ACT/VERIFY
# del 2026-07-25: tenere quella funzione lasciava credere che il router gestisse
# l'escalation dopo fallimento, mentre la gerarchia vive nella configurazione
# globale dell'agente. _try_shrink_body_haiku e caduta in cascata avendo solo quei
# due chiamanti interni. Storia completa nel git log.
# ───────────────────────────────────────────────────────────────────────────────



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
