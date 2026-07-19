# ~190 lines
"""Anthropic forwarding extracted from ai-router-proxy.py (~lines 1034-1703)."""
import json
import os
import time
from pathlib import Path

import tool_isolation

from router_constants import (
    ANTHROPIC_UPSTREAM, ANTHROPIC_DIRECT_URL, HOP_HEADERS,
    ANTHROPIC_UNSUPPORTED_FIELDS, CLAUDE_CODE_MARKER,
)
from router_utils import (
    _analyze_body_structure, SENT_ANALYSIS, _DEBUG_LAST_SENT, log,
)
from router_auth import _reload_oauth_token


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


def _force_no_stream(body: bytes):
    try:
        d = json.loads(body)
        d["stream"] = False
        return json.dumps(d).encode(), d
    except Exception:
        return body, {}


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

    url = ANTHROPIC_UPSTREAM + request.path_qs
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

    # Deep debug
    _fn = "forward_anthropic"
    try:
        analysis = _analyze_body_structure(safe_body)
        SENT_ANALYSIS.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fn": _fn, "path": request.path,
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
            log(f"[DEEP-DEBUG-WARN] {_fn}: orphans={len(analysis['orphan_tool_results'])} "
                f"role_system_msgs={analysis['role_system_in_messages']}")
    except Exception:
        pass

    # Context window retry: 400 context -> strip images and retry
    try:
        up = await session.request(
            request.method, url, data=safe_body, headers=headers, allow_redirects=False
        )
        if up.status == 400:
            try:
                raw_err = await up.read()
            except Exception:
                raw_err = b""
            await up.release()
            low = raw_err.lower()
            is_ctx = (b"context window" in low or b"reached its context" in low
                      or b"context_exceeded" in low or b"context limit" in low
                      or b"exceeds limit" in low)
            if is_ctx:
                from providers.base import strip_images_body
                stripped = strip_images_body(safe_body)
                if stripped != safe_body:
                    log(f"[forward_anthropic] ctx-exceed 400 -> retry with images stripped")
                    up = await session.request(
                        request.method, url, data=stripped, headers=headers,
                        allow_redirects=False
                    )
                    if up.status < 400:
                        return up
                    try:
                        await up.read()
                    except Exception:
                        pass
                    await up.release()
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

    url = ANTHROPIC_DIRECT_URL + request.path_qs
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

    _fn = "forward_anthropic_direct"
    try:
        analysis = _analyze_body_structure(safe_body)
        SENT_ANALYSIS.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fn": _fn, "path": request.path,
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
            log(f"[DEEP-DEBUG-WARN] {_fn}: orphans={len(analysis['orphan_tool_results'])} "
                f"role_system_msgs={analysis['role_system_in_messages']}")
    except Exception:
        pass

    return await session.request(
        request.method, url, data=safe_body, headers=headers, allow_redirects=False
    )
