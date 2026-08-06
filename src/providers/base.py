"""providers/base.py — Interfaccia e utilities condivise per i backend provider."""

import asyncio
import json
import re

# ── Status sets ──────────────────────────────────────────────────────────────
# Queste non sono definizioni ma re-export dalla fonte unica in router_constants.
# La copia locale di FALLBACK_STATUSES non conteneva il 404; reintrodurre quel
# valore significava reintrodurre un bug già corretto (quello del "selected model").
# router_constants non importa providers, quindi non si crea un ciclo.
from router_constants import FALLBACK_STATUSES, MINIMAX_FALLBACK_STATUSES  # noqa: F401

# ── T2 classification ─────────────────────────────────────────────────────────
T2_KEYWORDS = (
    "quant", "quando", "data", "prezzo", "costo", "percentual", "formula",
    "calcol", "converti", "differenza tra", "versione", "compatibil",
    "sicurezz", "security", "vulnerab", "password", "credenzial", "token",
    "produzione", "production", "deploy", "migrazione", "migration",
    "irreversibil", "cancell", "delete", "drop ", "rm -rf", "truncate",
    "legale", "medic", "fiscal", "contratt", "normativ",
    "esatt", "preciso", "accurat", "verifica", "corretto", "sicuro che",
    "sei sicuro", "è vero che", "dimostra", "prova che",
)

# ── Text extraction ──────────────────────────────────────────────────────────
def extract_last_user_text(data: dict) -> str:
    """Estrae l'ultimo messaggio user dal body request."""
    messages = data.get("messages", [])
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                for b in reversed(c):
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            return b.get("text", "")
    return ""

def _text_from_message(data: dict) -> str:
    """Estrae il testo da un message dict, inclusi i blocchi thinking.

    La versione precedente leggeva solo content[0]["text"] e restituiva stringa
    vuota quando la risposta cominciava con un blocco thinking: è la causa nota
    delle risposte vuote. Allineata alle copie vive di pipeline_anthropic e
    forward_anthropic, che sono identiche fra loro.
    """
    out = []
    for b in (data or {}).get("content", []):
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

# ── Context checks ────────────────────────────────────────────────────────────
def _is_context_too_large_for_minimax(body: bytes) -> bool:
    """True se il body supera la soglia MINIMAX_CONTEXT_BYTE_LIMIT in byte.

    Soglia default 750.000 byte, override con AIROUTER_MINIMAX_CONTEXT_LIMIT.
    Confronto su byte, non su token stimati.
    """
    # Mantieni allineata all'omonima in pipeline_anthropic.py (effettivamente usata da pipeline_minimax)
    from router_constants import MINIMAX_CONTEXT_BYTE_LIMIT
    return len(body) > MINIMAX_CONTEXT_BYTE_LIMIT

def _is_context_exceed_400(body: bytes) -> tuple[bool, str]:
    """Rileva errore context window 400 upstream. Ritorna (is_context, snippet).

    I marker erano una copia locale, una delle quattro divergenti trovate il
    2026-08-06: ora la lista è unica, in router_constants.
    """
    from router_constants import CONTEXT_EXCEED_MARKERS
    low = body.lower()
    for m in CONTEXT_EXCEED_MARKERS:
        idx = low.find(m)
        if idx >= 0:
            snippet = body[max(0, idx - 20):idx + 80].decode("utf-8", errors="replace")
            return True, snippet
    return False, ""


# ── Body manipulation ────────────────────────────────────────────────────────
def _body_has_images(orig: dict) -> bool:
    """True se il body contiene blocchi image. MiniMax allucina le immagini
    (bug 2026-07-08) → bypass diretto a Anthropic."""
    try:
        for msg in (orig.get("messages") or []):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        return True
    except Exception:
        pass
    return False

def strip_images_body(body: bytes) -> bytes:
    """Rimuove blocchi immagine dal body (context exceed workaround)."""
    try:
        d = json.loads(body)
    except Exception:
        return body
    for m in d.get("messages", []):
        if isinstance(m.get("content"), list):
            m["content"] = [b for b in m["content"]
                           if not (isinstance(b, dict) and b.get("type") == "image")]
    return json.dumps(d).encode()

# ── Context trim ──────────────────────────────────────────────────────────────
_TRIM_RE = re.compile(r'("role":\s*"(?:user|assistant)".*?)("role":\s*"(?:user|assistant)")')

def trim_old_messages(body: bytes, budget_tokens: int = 160_000) -> bytes:
    """Riduce i messaggi vecchi a ~budget_tokens token (stima byte//4)."""
    try:
        d = json.loads(body)
    except Exception:
        return body
    msgs = d.get("messages", [])
    if not msgs:
        return body
    # Byte budget = token_budget * 4
    budget_bytes = budget_tokens * 4
    total = sum(len(json.dumps(m).encode()) for m in msgs)
    if total <= budget_bytes:
        return body
    # Mantieni system + ultimi messaggi finché non entriamo nel budget
    system = [d["system"]] if "system" in d and d["system"] else []
    keep = []
    for m in reversed(msgs):
        test = keep + [m]
        if sum(len(json.dumps(x).encode()) for x in (system + test)) > budget_bytes:
            break
        keep = test
    d = {"system": d.get("system", ""), "messages": list(reversed(keep))}
    return json.dumps(d).encode()

# ── CallFull helper ──────────────────────────────────────────────────────────
async def call_full(fn, request, body, session, timeout: float = 120.0) -> tuple[int, dict | None]:
    """Chiama fn(request, body, session) con timeout. Ritorna (status, json_or_None)."""
    try:
        async with asyncio.timeout(timeout):
            result = await fn(request, body, session)
            if hasattr(result, "status"):
                status = result.status
                raw = await result.read()
                await result.release()
                try:
                    return status, json.loads(raw)
                except Exception:
                    return status, None
            return 0, None
    except asyncio.TimeoutError:
        return 0, None
    except Exception:
        return 0, None

# ── T2 classification helper ──────────────────────────────────────────────────
def classify_t2(body: bytes) -> bool:
    """True se la richiesta è 'critica' (T2) -> merita verifica Opus."""
    import os
    try:
        data = json.loads(body)
    except Exception:
        return False
    # Richieste agentiche (Claude Code/VSCode) contengono "tools" e si aspettano
    # blocchi tool_use in risposta. La pipeline collaborativa T2 appiattisce la
    # risposta a solo testo, distruggendo i tool_use -> l'agente non esegue nulla.
    # Mai farle entrare in T2: vanno in passthrough rel() che preserva i tool.
    if data.get("tools"):
        return False
    if os.environ.get("AIROUTER_FORCE_T2") == "1":
        return True
    text = extract_last_user_text(data)
    low = text.lower()
    if any(k in low for k in T2_KEYWORDS):
        return True
    if "?" in text and any(ch.isdigit() for ch in text):
        return True
    return False
