# ~65 lines
"""!router command parsing extracted from ai-router-proxy.py (~lines 868-971)."""
import re as _re

from router_constants import VALID_MODES
from router_mode import get_chat_mode, set_chat_mode, clear_chat_mode, get_file_mode, conversation_fingerprint

_ALIAS_MAP = {
    "mixam": "mix-am",
    "mixgm": "mix-gm",
    "mixag": "mix-ag",
}
_INTERNAL_TO_DISPLAY = {
    "mix-am": "MixAM", "mix-gm": "MixGM", "mix-ag": "MixAG",
    "anthropic": "anthropic", "minimax": "minimax", "glm": "glm",
}
_NL_MODE = [
    (_re.compile(r"anthropic\s*[-+ ]?\s*glm|claude\s*[-+ ]?\s*glm|glm\s+esecutore|anthropic\s+con\s+glm", _re.I), "mix-ag"),
    (_re.compile(r"glm\s*[-+ ]?\s*minimax|glm\s+con\s+minimax|glm\s+orchestr\w+\s+minimax", _re.I), "mix-gm"),
    (_re.compile(r"solo\s+glm|usa\s+glm|glm\b", _re.I), "glm"),
    (_re.compile(r"solo\s+(claude|anthropic)|usa\s+(claude|anthropic)", _re.I), "anthropic"),
    (_re.compile(r"solo\s+minimax|usa\s+minimax", _re.I), "minimax"),
    (_re.compile(r"mod\w*\s+mist|mixed|mist[ao]\b", _re.I), "mix-am"),
]
_CMD_VERB = _re.compile(r"\b(usa|passa|metti|imposta|attiva|cambia|adesso\s+usa)\b", _re.I)
_EXPLICIT = _re.compile(r"(?:^|>|\n)\s*!router\s+([\w-]+)", _re.I)


def parse_router_command(text: str):
    """Ritorna {'action': ...} se il messaggio è un comando router, altrimenti None."""
    if not text:
        return None
    t = text.strip()
    m = _EXPLICIT.search(t)
    if m:
        trailing = _re.sub(r"</?\w[\w-]*>", "", t[m.end():]).strip()
        if len(trailing) > 5:
            return None
        arg = m.group(1).lower()
        resolved = _ALIAS_MAP.get(arg, arg)
        if resolved in VALID_MODES:
            return {"action": "set", "mode": resolved}
        if arg in ("status", "reset", "help"):
            return {"action": arg}
        return {"action": "help"}
    if len(t) <= 80 and _CMD_VERB.search(t):
        for rx, mode in _NL_MODE:
            if rx.search(t):
                return {"action": "set", "mode": mode}
    return None


def _router_reply_text(action: dict, fp: str, fallback_fp: str = None) -> str:
    if action["action"] == "set":
        set_chat_mode(fp, action["mode"])
        _disp = _INTERNAL_TO_DISPLAY.get(action["mode"], action["mode"])
        return f"✅ Questa chat ora usa: **{_disp}** (dal prossimo messaggio)."
    if action["action"] == "status":
        cm = get_chat_mode(fp) or (get_chat_mode(fallback_fp) if fallback_fp else None)
        if cm:
            return f"📍 Modalità chat: **{_INTERNAL_TO_DISPLAY.get(cm, cm)}**"
        _gm = get_file_mode()
        return f"📍 Modalità chat: **default ({_INTERNAL_TO_DISPLAY.get(_gm, _gm)})**"
    if action["action"] == "reset":
        clear_chat_mode(fp)
        if fallback_fp:
            clear_chat_mode(fallback_fp)
        _gm = get_file_mode()
        return f"↺ Chat riportata al default: **{_INTERNAL_TO_DISPLAY.get(_gm, _gm)}**"
    return ("🧭 Comandi: `!router <anthropic|minimax|mixam|glm|mixgm|mixag>` · "
            "`!router status` · `!router reset`. Anche a voce: «usa solo minimax».")


def _synthetic_message(text: str, model: str = "ai-router") -> dict:
    """Ritorna un message dict sintetico per la reply al client."""
    return {
        "id": "msg_router", "type": "message", "role": "assistant",
        "model": model or "ai-router", "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
