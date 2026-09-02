# ~65 lines
"""!router command parsing extracted from ai-router-proxy.py (~lines 868-971)."""
import re as _re

from router_constants import VALID_MODES
from router_mode import get_chat_mode, set_chat_mode, clear_chat_mode, get_file_mode

_ALIAS_MAP = {
    "mixam": "mix-am",
    "mixam2": "mix-am-2",
    "mixgm": "mix-gm",
    "mixgm2": "mix-gm-2",
    "mixag": "mix-ag",
    "mixag2": "mix-ag-2",
}
_INTERNAL_TO_DISPLAY = {
    "mix-am": "MixAM", "mix-am-2": "MixAM-2", "mix-gm": "MixGM", "mix-gm-2": "MixGM-2", "mix-ag": "MixAG", "mix-ag-2": "MixAG-2",
    "mix-al": "MixAL",  # aggiunta 2026-08-04: l'8a modalita' mancava dalla mappa
    "local": "Local",   # aggiunta 2026-08-04: 9a modalita', pura locale (tutto su code-max)
    "gpt": "GPT",       # aggiunta 2026-08-18: 13a modalita', pura locale su GPT_MODE_THINK
    "ultra": "Ultra",   # aggiunta 2026-08-22: 14a modalita', Anthropic THINK + GLM ACT + MiniMax codice via CLI
    "opr": "OpenRouter",  # aggiunta 2026-08-30: 15a modalita', OpenRouter/ox-alpha
    "anthropic": "anthropic", "minimax": "minimax", "glm": "glm",
    "qwen": "qwen",   # aggiunta 2026-08-04: la 7a modalita' mancava dalla mappa
}
# RIMOSSO 2026-07-26 (decisione utente): riconoscimento in linguaggio naturale
# (_NL_MODE + _CMD_VERB). Cambiava la modalita' SENZA autorizzazione esplicita:
# bastava un messaggio <=80 char con un verbo comune ("usa|cambia|metti|attiva|
# imposta|passa") piu' una parola-modalita' ovunque nel testo. Il pattern "glm\b"
# matchava la sola parola "glm", quindi frasi di lavoro normali come "cambia il
# commento che cita glm" o "usa il file glm.py" commutavano la chat su glm; e per
# ordine di valutazione "usa solo claude, non glm" finiva su glm, l'OPPOSTO del
# richiesto. Evidenza: 31 override glm su 86 accumulati in ai-router-chats.json.
# L'UNICO switch da chat e' ora il comando esplicito !router.
_EXPLICIT = _re.compile(r"(?:^|>|\n)\s*!router\s+([\w-]+)", _re.I)

# FIX 2026-07-26: il CLI appende al messaggio utente blocchi che l'utente non ha
# scritto (<system-reminder> con memoria/CLAUDE.md, <ide_selection>, ...). Il vecchio
# strip a riga 36 toglieva solo i TAG e lasciava il CONTENUTO: la "coda" dopo !router
# risultava lunga centinaia di char, il guardrail `len(trailing) > 5` scartava il
# comando, e "!router glm" partiva verso il provider come messaggio normale -> API
# error in chat (riprodotto: 429 rate_limit_error). Ora i blocchi iniettati spariscono
# per intero PRIMA del parse. Il guardrail resta: protegge dal cambio-modalita'
# accidentale quando !router e' citato dentro un discorso vero.
_INJECTED_BLOCK = _re.compile(
    r"<(system-reminder|ide_selection|ide_opened_file|ide_diagnostics|"
    r"local-command-stdout|command-name|command-message|command-args)\b[^>]*>"
    r".*?</\1\s*>",
    _re.I | _re.S,
)


def _strip_injected_blocks(text: str) -> str:
    """Rimuove i blocchi iniettati dall'harness (tag + contenuto)."""
    prev, out = None, text
    while prev != out:  # blocchi consecutivi/annidati
        prev = out
        out = _INJECTED_BLOCK.sub(" ", out)
    return out


def parse_router_command(text: str):
    """Ritorna {'action': ...} se il messaggio è un comando router, altrimenti None."""
    if not text:
        return None
    t = _strip_injected_blocks(text).strip()
    m = _EXPLICIT.search(t)
    if m:
        trailing = _re.sub(r"</?\w[\w-]*>", "", t[m.end():]).strip()
        if len(trailing) > 5:
            return None
        # gli alias brevi (mixam/mixgm/mixag) stanno in _ALIAS_MAP qui, i nomi storici in _LEGACY_MODE_MAP di router_mode,
        # che è la stessa mappa usata in lettura da get_mode -- riusarla evita che i due ingressi divergano.
        from router_mode import _LEGACY_MODE_MAP
        arg = m.group(1).lower()
        _short = _ALIAS_MAP.get(arg, arg)
        resolved = _LEGACY_MODE_MAP.get(_short, _short)
        if resolved in VALID_MODES:
            return {"action": "set", "mode": resolved}
        if arg in ("status", "reset", "help"):
            return {"action": arg}
        return {"action": "help"}
    return None


def _router_reply_text(action: dict, fp: str, fallback_fp: str = None) -> str:
    # fp e' costruito dal proxy come "sid:<session-id>" quando l'header
    # X-Claude-Code-Session-Id e' presente, altrimenti conversation_fingerprint()
    # (content-hash 12 char). Entrambi i casi sono coperti da get_chat_mode(fp),
    # che usa la stessa chiave con cui set_chat_mode ha scritto l'override.
    # Verificato 2026-08-09: !router status risponde correttamente minimax anche
    # quando il file globale ai-router-mode dice mix-am-2 (divergenza intenzionale:
    # il file e' il DEFAULT per le chat nuove, non la fonte di verita per-chat).
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
    # L'elenco si deriva da VALID_MODES: scritto a mano si disallinea (il 2026-08-04
    # mancava 'qwen', poi 'mix-al' e 'local'), e l'utente non trova il comando che esiste.
    _modes = '|'.join(VALID_MODES)
    return (f"🧭 Comandi: `!router <{_modes}>` · "
            "`!router status` · `!router reset`.\n"
            "Alias accettati: `mixam`/`mixag`/`mixgm` in forma breve, e i nomi storici "
            "`mixed`, `glm-minimax`, `anthropic-glm`, normalizzati al canonico.")


def _synthetic_message(text: str, model: str = "ai-router") -> dict:
    """Ritorna un message dict sintetico per la reply al client."""
    return {
        "id": "msg_router", "type": "message", "role": "assistant",
        "model": model or "ai-router", "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
