#!/usr/bin/env python3
"""
AI Router Proxy — switcher davanti a Claude Code.

Quattro modalità (file ~/.claude/ai-router-mode):
  - anthropic   : tutto verso headroom#1 (8787 -> api.anthropic.com)
  - minimax     : tutto verso headroom#2 (8790 -> api.minimax.io/anthropic)
  - mixed       : Anthropic primario; su 429/5xx/errore -> fallback MiniMax (bidir)
  - inverse     : MiniMax orchestra + esegue. Anthropic verifica (T2) i task critici.
                  Anthropic interviene direttamente come esecutore dopo 2 fallimenti
                  (resilienza). Task non-critici -> MiniMax diretto (streaming).


Claude Code punta qui: ANTHROPIC_BASE_URL=http://127.0.0.1:8789
Gestisce streaming SSE. Non modifica headroom né LiteLLM.
"""
import asyncio
import json
import os
import threading
import time
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout, TCPConnector

# ── Config ────────────────────────────────────────────────────────────────
LISTEN_HOST = "127.0.0.1"
# Il router E' il punto unico su :8787 (dove tutte le app gia' puntano).
# headroom#1 (Anthropic) si sposta su :8791 come backend interno.
LISTEN_PORT = int(os.environ.get("AIROUTER_PORT", "8787"))

ANTHROPIC_UPSTREAM = os.environ.get("AIROUTER_ANTHROPIC_UPSTREAM", "http://127.0.0.1:8791")
# MiniMax passa attraverso headroom #2 (compressione context attiva anche qui).
# headroom #2 (:8790) inoltra a https://api.minimax.io/anthropic via ANTHROPIC_TARGET_API_URL.
MINIMAX_UPSTREAM = os.environ.get("AIROUTER_MINIMAX_UPSTREAM", "http://127.0.0.1:8790")
MINIMAX_MODEL = os.environ.get("AIROUTER_MINIMAX_MODEL", "MiniMax-M3")
# Modello giudice per la verifica T2 in modalità interactive (Claude Opus).
VERIFY_MODEL = os.environ.get("AIROUTER_VERIFY_MODEL", "claude-opus-4-8")
VALID_MODES = ("anthropic", "minimax", "mixed", "inverse")

MODE_FILE = Path.home() / ".claude" / "ai-router-mode"
KEY_FILE = Path.home() / ".claude" / "secrets" / "secrets.sh"
LOG_FILE = Path.home() / ".claude" / "logs" / "ai-router.log"

# status code che in 'mixed' fanno scattare il fallback a MiniMax
# Fallback attivo su: 5xx/529 (server/overload) + 4xx eccetto 400/404 (client error puro).
# 401/403 (auth/billing) -> fallback a MiniMax cosi' l'utente non resta bloccato.
# 429 (rate limit) -> fallback per non aspettare.
FALLBACK_STATUSES = {401, 403, 408, 409, 413, 429, 500, 502, 503, 504, 529}

# Circuit breaker (D15): dopo N fail un backend va in cooldown e viene saltato.
BREAKER_FAIL_MAX = 3
BREAKER_COOLDOWN = 120  # secondi
_breaker = {"anthropic": {"fails": 0, "open_until": 0},
            "minimax": {"fails": 0, "open_until": 0}}

# Contatore per-chat dei fallimenti MiniMax (modalità inverse).
# Dopo N fail consecutivi: Anthropic esegue direttamente (bypass MiniMax).
INVERSE_FAIL_THRESHOLD = int(os.environ.get("AIROUTER_INVERSE_FAILS", "2"))
_inverse_fails = {}  # chat_fp -> int

# MIXED: MiniMax esegue tutto; dopo N fail consecutivi Anthropic prende il comando.
MIXED_FAIL_THRESHOLD = int(os.environ.get("AIROUTER_MIXED_FAILS", "2"))
_mixed_fails = {}  # chat_fp -> int

# FIX B1.1: lock condiviso per contatori globali (thread-safe + asyncio-safe via run_in_executor)
_counter_lock = threading.Lock()

# FIX #2: Cooldown e reset automatico per escalation
RESCUE_COOLDOWN_SEC = 30  # cooldown dopo escalation prima di ritentare
FAIL_RESET_SEC = 60  # reset counter se ultimo fallimento > 60s fa
_inverse_fail_ts = {}  # chat_fp -> timestamp ultimo fallimento
_mixed_fail_ts = {}  # chat_fp -> timestamp ultimo fallimento
_inverse_cooldown_until = {}  # chat_fp -> timestamp fine cooldown
_mixed_cooldown_until = {}  # chat_fp -> timestamp fine cooldown


def breaker_is_open(backend: str) -> bool:
    """True se il backend è in cooldown (da saltare)."""
    return time.time() < _breaker.get(backend, {}).get("open_until", 0)


def breaker_fail(backend: str):
    with _counter_lock:  # FIX B1.1
        b = _breaker.setdefault(backend, {"fails": 0, "open_until": 0})
        b["fails"] += 1
        if b["fails"] >= BREAKER_FAIL_MAX:
            b["open_until"] = time.time() + BREAKER_COOLDOWN
            b["fails"] = 0
            log(f"breaker OPEN {backend} per {BREAKER_COOLDOWN}s")


def breaker_ok(backend: str):
    with _counter_lock:  # FIX B1.1
        b = _breaker.setdefault(backend, {"fails": 0, "open_until": 0})
        b["fails"] = 0
        b["open_until"] = 0

def inverse_fail_inc(chat_fp: str) -> int:
    """Incrementa contatore fallimenti MiniMax per chat. Ritorna nuovo valore."""
    with _counter_lock:  # FIX B1.1
        now = time.time()
        # Reset automatico se ultimo fail > FAIL_RESET_SEC fa
        last_fail = _inverse_fail_ts.get(chat_fp, 0)
        if now - last_fail > FAIL_RESET_SEC:
            _inverse_fails[chat_fp] = 0
        n = _inverse_fails.get(chat_fp, 0) + 1
        _inverse_fails[chat_fp] = n
        _inverse_fail_ts[chat_fp] = now
    return n


def inverse_fail_reset(chat_fp: str) -> None:
    """Azzera contatore (chiamata MiniMax riuscita)."""
    _inverse_fails[chat_fp] = 0
    _inverse_fail_ts.pop(chat_fp, None)
    _inverse_cooldown_until.pop(chat_fp, None)


def inverse_should_escalate(chat_fp: str) -> bool:
    """True se Anthropic deve bypassare MiniMax ed eseguire direttamente."""
    # Cooldown attivo: resta in modalità escalation
    if time.time() < _inverse_cooldown_until.get(chat_fp, 0):
        return True
    fails = _inverse_fails.get(chat_fp, 0)
    if fails >= INVERSE_FAIL_THRESHOLD:
        # Attiva cooldown
        _inverse_cooldown_until[chat_fp] = time.time() + RESCUE_COOLDOWN_SEC
        return True
    return False


def mixed_fail_inc(chat_fp: str) -> int:
    with _counter_lock:  # FIX B1.1
        now = time.time()
        # Reset automatico se ultimo fail > FAIL_RESET_SEC fa
        last_fail = _mixed_fail_ts.get(chat_fp, 0)
        if now - last_fail > FAIL_RESET_SEC:
            _mixed_fails[chat_fp] = 0
        n = _mixed_fails.get(chat_fp, 0) + 1
        _mixed_fails[chat_fp] = n
        _mixed_fail_ts[chat_fp] = now
    return n


def mixed_fail_reset(chat_fp: str) -> None:
    _mixed_fails.pop(chat_fp, None)
    _mixed_fail_ts.pop(chat_fp, None)
    _mixed_cooldown_until.pop(chat_fp, None)


def mixed_anthropic_leads(chat_fp: str) -> bool:
    """True se MiniMax ha già fallito >= soglia: Anthropic prende il comando."""
    # Cooldown attivo: resta in modalità escalation
    if time.time() < _mixed_cooldown_until.get(chat_fp, 0):
        return True
    fails = _mixed_fails.get(chat_fp, 0)
    if fails >= MIXED_FAIL_THRESHOLD:
        # Attiva cooldown
        _mixed_cooldown_until[chat_fp] = time.time() + RESCUE_COOLDOWN_SEC
        return True
    return False


_minimax_key_cache = {"key": None, "ts": 0}


def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_exc(msg: str):  # FIX B5.2: log con traceback
    import traceback
    log(f"{msg}\n{traceback.format_exc()}")


# Porte con modalità FISSA (per usare modalità diverse su sessioni diverse).
# :8787 = dinamica (segue ai-mode / file). Le altre forzano la modalità.
# Porte fisse su range libero (8782/8783 erano occupate da audio LiteLLM).
PORT_MODE = {
    8771: "anthropic",
    8772: "minimax",
    8773: "mixed",
    8774: "inverse",
}
LISTEN_PORTS = [int(os.environ.get("AIROUTER_PORT", "8787"))] + list(PORT_MODE.keys())


def get_file_mode() -> str:
    try:
        m = MODE_FILE.read_text().strip().lower()
        if m in VALID_MODES:
            return m
    except Exception:
        pass
    return "anthropic"  # default sicuro


def _current_mode() -> str:
    """Helper per /health: modalità corrente (da file, fallback 'anthropic')."""
    return get_file_mode()


def _err_response(message: str, status: int = 502) -> web.Response:
    """FIX B4.10: err response con status propagato dall'upstream se disponibile.
    Il chiamante può passare status=up.status quando catches un errore dopo che
    l'upstream ha già risposto, evitando di rimappare 429->502."""
    return web.json_response(
        {"type": "error", "error": {"type": "router_error", "message": str(message)}},
        status=status,
    )


def get_mode(request=None) -> str:
    """Modalità deterministica: ogni porta ha la sua web.Application con
    app['forced_mode'] cablato. :8787 ha forced_mode=None -> dinamica da file.
    Niente più sockname (inaffidabile con runner condiviso)."""
    if request is not None:
        forced = request.app.get("forced_mode")
        if forced in VALID_MODES:
            return forced
    return get_file_mode()


# ── Fingerprint conversazione (chat indipendenti, D3=B/D4) ───────────────
# Identifica una chat senza session-id: hash(system + primo messaggio utente).
# Stabile per tutta la conversazione, distinto tra chat diverse.
import hashlib

CHAT_STORE = Path.home() / ".claude" / "ai-router-chats.json"
CHAT_TTL_DAYS = 7
_chat_cache = {"data": None, "ts": 0}


def conversation_fingerprint(data: dict) -> str:
    system = data.get("system", "")
    if isinstance(system, list):
        system = " ".join(b.get("text", "") for b in system if isinstance(b, dict))
    first_user = ""
    for m in data.get("messages", []):
        if m.get("role") == "user":
            c = m.get("content", "")
            first_user = c if isinstance(c, str) else " ".join(
                b.get("text", "") for b in c if isinstance(b, dict))
            break
    return hashlib.sha256((str(system) + "||" + str(first_user)).encode()).hexdigest()[:12]


def _load_chats() -> dict:
    now = time.time()
    if _chat_cache["data"] is not None and now - _chat_cache["ts"] < 5:
        return _chat_cache["data"]
    try:
        d = json.loads(CHAT_STORE.read_text())
    except Exception:
        d = {}
    # pulizia TTL (D26: 7 giorni)
    cutoff = now - CHAT_TTL_DAYS * 86400
    changed = False
    for fp in list(d.keys()):
        if d[fp].get("ts", 0) < cutoff:
            del d[fp]; changed = True
    if changed:
        _save_chats(d)
    _chat_cache["data"] = d
    _chat_cache["ts"] = now
    return d


def _save_chats(d: dict):
    try:
        CHAT_STORE.write_text(json.dumps(d))
        _chat_cache["data"] = d
        _chat_cache["ts"] = time.time()
    except Exception as e:
        log(f"ERR save chats: {e}")


def get_chat_mode(fp: str):
    """Modalità marcata per una chat (o None)."""
    return _load_chats().get(fp, {}).get("mode")


def set_chat_mode(fp: str, mode: str):
    d = _load_chats()
    d[fp] = {"mode": mode, "ts": time.time()}
    _save_chats(d)
    log(f"chat {fp} -> mode {mode}")


def clear_chat_mode(fp: str):
    d = _load_chats()
    if fp in d:
        del d[fp]; _save_chats(d)
        log(f"chat {fp} -> reset")


# ── Comandi in-chat (D5/D7/D9): !router + frasi naturali italiane ─────────
import re as _re

_NL_MODE = [
    (_re.compile(r"solo\s+(claude|anthropic)|usa\s+(claude|anthropic)", _re.I), "anthropic"),
    (_re.compile(r"solo\s+minimax|usa\s+minimax", _re.I), "minimax"),
    (_re.compile(r"mod\w*\s+mist|mixed|mist[ao]\b", _re.I), "mixed"),
    (_re.compile(r"inverse|inversa|inverti", _re.I), "inverse"),
]
_CMD_VERB = _re.compile(r"\b(usa|passa|metti|imposta|attiva|cambia|adesso\s+usa)\b", _re.I)
_EXPLICIT = _re.compile(r"^\s*!router\s+(\w+)", _re.I)


def parse_router_command(text: str):
    """Ritorna {'action': ...} se il messaggio è un comando router, altrimenti None.
    Prudente (D24): naturale solo se messaggio breve + verbo di comando."""
    if not text:
        return None
    t = text.strip()
    # esplicito: !router <x>
    m = _EXPLICIT.match(t)
    if m:
        arg = m.group(1).lower()
        if arg in VALID_MODES:
            return {"action": "set", "mode": arg}
        if arg in ("status", "reset", "help"):
            return {"action": arg}
        return {"action": "help"}
    # naturale: solo se breve (<80) e con verbo di comando (prudente)
    if len(t) <= 80 and _CMD_VERB.search(t):
        for rx, mode in _NL_MODE:
            if rx.search(t):
                return {"action": "set", "mode": mode}
    return None


def _router_reply_text(action: dict, fp: str) -> str:
    if action["action"] == "set":
        set_chat_mode(fp, action["mode"])
        return f"✅ Questa chat ora usa: **{action['mode']}** (dal prossimo messaggio)."
    if action["action"] == "status":
        cm = get_chat_mode(fp)
        return f"📍 Modalità chat: **{cm or 'default (' + get_file_mode() + ')'}**"
    if action["action"] == "reset":
        clear_chat_mode(fp)
        return f"↺ Chat riportata al default: **{get_file_mode()}**"
    return ("🧭 Comandi: `!router <anthropic|minimax|mixed|interactive>` · "
            "`!router status` · `!router reset`. Anche a voce: «usa solo minimax».")


def _synthetic_message(text: str) -> dict:
    return {
        "id": "msg_router", "type": "message", "role": "assistant",
        "model": "ai-router", "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


# ── Classificatore criticità T2 (euristiche locali, zero latenza) ──────────
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


def extract_last_user_text(data: dict) -> str:
    for msg in reversed(data.get("messages", [])):
        if msg.get("role") == "user":
            c = msg.get("content", "")
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            return str(c)
    return ""


def classify_t2(body: bytes) -> bool:
    """True se la richiesta è 'critica' (T2) -> merita verifica Opus."""
    if os.environ.get("AIROUTER_FORCE_T2") == "1":
        return True
    try:
        data = json.loads(body)
    except Exception:
        return False
    text = extract_last_user_text(data)
    low = text.lower()
    if any(k in low for k in T2_KEYWORDS):
        return True
    if "?" in text and any(ch.isdigit() for ch in text):
        return True
    return False


def get_minimax_key() -> str:
    # cache 60s
    now = time.time()
    if _minimax_key_cache["key"] and now - _minimax_key_cache["ts"] < 60:
        return _minimax_key_cache["key"]
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        try:
            import subprocess
            key = subprocess.check_output(
                ["bash", str(KEY_FILE), "get", "minimax.api_key"],
                text=True, timeout=5,
            ).strip()
        except Exception as e:
            log(f"ERR get key: {e}")
            key = ""
    _minimax_key_cache["key"] = key
    _minimax_key_cache["ts"] = now
    return key


# Campi beta/Anthropic-only che MiniMax (api.minimaxi.chat) rifiuta con 400.
MINIMAX_UNSUPPORTED_FIELDS = ("context_management", "mcp_servers", "thinking")

# Anthropic public API: 'context_management' è beta-only gated da
# 'anthropic-beta: context-management-2025-06-27'. Senza quel header,
# api.anthropic.com restituisce 400 "Extra inputs are not permitted".
# Lo strippiamo a monte per evitare il 400 (headroom/client possono inviarlo).
ANTHROPIC_UNSUPPORTED_FIELDS = ("context_management",)


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


def remap_body_for_minimax(raw: bytes) -> bytes:
    """Riscrive il model Claude -> MiniMax-M3 e rimuove i campi beta non supportati."""
    try:
        data = json.loads(raw)
        orig = data.get("model", "")
        if orig and not orig.startswith("MiniMax"):
            data["model"] = MINIMAX_MODEL
        # Strip campi che MiniMax non accetta (causano 400 "Extra inputs not permitted")
        for f in MINIMAX_UNSUPPORTED_FIELDS:
            data.pop(f, None)
        return json.dumps(data).encode()
    except Exception:
        return raw


def _load_oauth_token():
    """Carica il token OAuth Anthropic da ~/.claude/.credentials.json se non
    è già in env. Usato da forward_anthropic_direct (verify T2 in modalità inverse)."""
    if os.environ.get("ANTHROPIC_OAUTH_TOKEN"):
        return
    try:
        with open(Path.home() / ".claude" / ".credentials.json") as f:
            d = json.load(f)
        tok = d.get("claudeAiOauth", {}).get("accessToken", "")
        if tok:
            os.environ["ANTHROPIC_OAUTH_TOKEN"] = tok
    except Exception as e:
        log(f"ERR load oauth: {e}")

_load_oauth_token()  # eseguita dopo le def


# ── Forwarding ────────────────────────────────────────────────────────────
HOP_HEADERS = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade",
}


async def forward_anthropic(request, body, session):
    url = ANTHROPIC_UPSTREAM + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    # Se il client usa OAuth Bearer (sk-ant-oat*), aggiungo il beta header richiesto
    auth = headers.get("Authorization", "") or headers.get("authorization", "")
    if auth.startswith("Bearer sk-ant-oat"):
        headers["anthropic-beta"] = "oauth-2025-04-20"
    # Strip campi beta che api.anthropic.com rifiuta senza il beta header opportuno
    safe_body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS) \
        if request.path.endswith("/v1/messages") else body
    return await session.request(
        request.method, url, data=safe_body, headers=headers, allow_redirects=False
    )


async def forward_minimax(request, body, session):
    url = MINIMAX_UPSTREAM + request.path_qs
    key = get_minimax_key()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    # MiniMax vuole X-Api-Key; rimuovo auth Anthropic
    for h in list(headers):
        if h.lower() in ("authorization", "x-api-key"):
            headers.pop(h)
    headers["X-Api-Key"] = key
    new_body = remap_body_for_minimax(body)
    return await session.request(
        request.method, url, data=new_body, headers=headers, allow_redirects=False
    )

ANTHROPIC_DIRECT_URL = os.environ.get("AIROUTER_ANTHROPIC_DIRECT", "https://api.anthropic.com")
ANTHROPIC_OAUTH_TOKEN = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")  # Bearer da Claude Code OAuth


async def forward_anthropic_direct(request, body, session):
    """Bypass totale di headroom: chiama api.anthropic.com con OAuth Bearer.
    Usato dalle verify T2 in modalità inverse: il modello di verifica è
    Claude stesso (via login OAuth nativo di Claude Code)."""
    url = ANTHROPIC_DIRECT_URL + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    for h in list(headers):
        if h.lower() in ("authorization", "x-api-key"):
            headers.pop(h)
    if ANTHROPIC_OAUTH_TOKEN:
        headers["Authorization"] = f"Bearer {ANTHROPIC_OAUTH_TOKEN}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    headers.setdefault("anthropic-version", "2023-06-01")
    # Strip campi beta che api.anthropic.com rifiuta senza il beta header opportuno
    safe_body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS) \
        if request.path.endswith("/v1/messages") else body
    return await session.request(
        request.method, url, data=safe_body, headers=headers, allow_redirects=False
    )



# ── Helper per modalità interactive (T2 verify) ──────────────────────
def _force_no_stream(body: bytes):
    try:
        d = json.loads(body)
        d["stream"] = False
        return json.dumps(d).encode(), d
    except Exception:
        return body, {}


def _text_from_message(j: dict) -> str:
    out = []
    for b in (j or {}).get("content", []):
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(b.get("text", ""))
    return "".join(out)


async def _call_full(forward_fn, request, body, session):
    """Chiamata non-streaming: ritorna (status, json|None). Timeout 90s request + 90s read."""
    nb, _ = _force_no_stream(body)
    up = None
    try:
        up = await asyncio.wait_for(forward_fn(request, nb, session), timeout=90)
    except asyncio.TimeoutError:
        log(f"_call_full TIMEOUT 90s req {getattr(request, 'path', '?')}")
        return 0, None
    except Exception as e:
        log_exc(f"_call_full EXC req {getattr(request, 'path', '?')}: {e}")  # FIX B5.2
        if up is not None:
            try: up.release()
            except Exception: pass
        return 0, None
    status = up.status
    try:
        raw = await asyncio.wait_for(up.read(), timeout=90)
    except asyncio.TimeoutError:
        log(f"_call_full TIMEOUT 90s read {getattr(request, 'path', '?')}")
        try: up.release()
        except Exception: pass
        return status, None
    try:
        up.release()
    except Exception: pass
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


def _build_verify_body(orig: dict, question: str, draft: str) -> bytes:
    """Body per Opus che verifica/corregge la bozza MiniMax."""
    sys_msg = (
        "Sei un verificatore esperto. Ricevi una DOMANDA e una RISPOSTA BOZZA "
        "prodotta da un altro modello. Correggi errori fattuali, allucinazioni, "
        "incoerenze e imprecisioni. Mantieni ciò che è corretto. Rispondi in "
        "italiano con SOLO la risposta finale verificata, senza meta-commenti."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nRISPOSTA BOZZA:\n{draft}\n\n"
        "Restituisci la risposta finale verificata e corretta."
    )
    return json.dumps({
        "model": VERIFY_MODEL,
        "max_tokens": int(orig.get("max_tokens", 1024)),
        "system": sys_msg,
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


def _build_critique_body(orig: dict, question: str, draft: str) -> bytes:
    """Round 1: Anthropic riceve la bozza M3 e produce una critica costruttiva.
    NON genera la risposta finale: solo issue list, dubbi, fatti da verificare.
    Output strutturato per essere ri-usato da M3 nel round 2."""
    sys_msg = (
        "Sei un revisore critico esperto e SEVERO. Ricevi DOMANDA e BOZZA prodotta da M3. "
        "Il tuo compito: produrre SEMPRE una lista di almeno 2-4 criticita', anche se la bozza "
        "ti sembra accettabile. Cerca: imprecisioni, dettagli mancanti, formulazioni deboli, "
        "informazioni che potrebbero fuorviare, ordine non ottimale dei contenuti. "
        "Se davvero non trovi nulla, rispondi letteralmente 'NESSUNA CRITICA'. "
        "Altrimenti elenca punti concreti e azionabili, numerati, in italiano."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nBOZZA M3:\n{draft}\n\n"
        "Elenca le criticita' da correggere (o 'NESSUNA CRITICA')."
    )
    return json.dumps({
        "model": VERIFY_MODEL,
        "max_tokens": min(int(orig.get("max_tokens", 1024)) // 2, 512),
        "system": sys_msg,
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


def _build_revise_body(orig: dict, question: str, draft: str, critique: str) -> bytes:
    """Round 2: M3 riceve la propria bozza + la critica di Anthropic e produce la v2."""
    sys_msg = (
        "Sei M3, un modello che collabora. Hai prodotto una BOZZA iniziale. "
        "Un revisore (Anthropic) ha prodotto una CRITICA. Il tuo compito: "
        "rivedi la bozza tenendo conto della critica, mantenendo cio' che e' corretto "
        "e correggendo cio' che va corretto. Rispondi con la versione finale, in italiano."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nBOZZA v1:\n{draft}\n\n"
        f"CRITICA ANTHROPIC:\n{critique}\n\n"
        "Restituisci la risposta finale riveduta (v2)."
    )
    body = dict(orig)
    body["system"] = sys_msg
    body["messages"] = [{"role": "user", "content": user_msg}]
    body["stream"] = False
    return json.dumps(body).encode()


def _build_finalize_body(orig: dict, question: str, draft_v2: str) -> bytes:
    """Round 3: Anthropic finalizza — gateway di qualità, decide se la v2 e' pubblicabile."""
    sys_msg = (
        "Sei il finalizzatore. Ricevi DOMANDA e v2 prodotta da M3 dopo la tua critica. "
        "Se la v2 risponde correttamente, restituiscila identica. "
        "Se contiene ancora errori gravi, correggili SOLO dove necessario. "
        "Rispondi in italiano, SOLO la risposta finale, senza meta-commenti."
    )
    user_msg = (
        f"DOMANDA:\n{question}\n\nRISPOSTA v2 (M3):\n{draft_v2}\n\n"
        "Restituisci la risposta finale."
    )
    return json.dumps({
        "model": VERIFY_MODEL,
        "max_tokens": int(orig.get("max_tokens", 1024)),
        "system": sys_msg,
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


def _sse_events_from_message(j: dict, verified: str) -> list:
    """Ritorna lista di eventi SSE Anthropic-compat (per invio progressivo con flush)."""
    text = _text_from_message(j)
    mid = j.get("id", "msg_router")
    model = j.get("model", "unknown")
    usage = j.get("usage", {})
    msg_start = {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": usage}}
    return [
        f"event: message_start\ndata: {json.dumps(msg_start)}\n\n",
        "event: content_block_start\ndata: " + json.dumps({
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}) + "\n\n",
        "event: content_block_delta\ndata: " + json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text}}) + "\n\n",
        "event: content_block_stop\ndata: " + json.dumps({
            "type": "content_block_stop", "index": 0}) + "\n\n",
        "event: message_delta\ndata: " + json.dumps({
            "type": "message_delta",
            "delta": {"stop_reason": j.get("stop_reason", "end_turn"),
                      "stop_sequence": None},
            "usage": {"output_tokens": usage.get("output_tokens", 0)}}) + "\n\n",
        "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n\n",
    ]


def _sse_from_message(j: dict, verified: str) -> bytes:
    """Costruisce uno stream SSE Anthropic-compat (compat legacy)."""
    return "".join(_sse_events_from_message(j, verified)).encode()


async def _prepare_sse_response(request, status: int = 200, extra_headers=None):
    """Prepara una StreamResponse SSE con header anti-buffering.

    Fix per ECONNRESET in VSCode: flush immediato + no buffering downstream.
    """
    resp = web.StreamResponse(status=status)
    resp.headers["content-type"] = "text/event-stream; charset=utf-8"
    resp.headers["cache-control"] = "no-cache, no-transform"
    resp.headers["connection"] = "keep-alive"
    resp.headers["x-accel-buffering"] = "no"
    if extra_headers:
        for k, v in extra_headers.items():
            resp.headers[k] = str(v)
    resp.enable_chunked_encoding()
    await resp.prepare(request)
    return resp


async def _send_sse_message(request, final_json: dict, verified_flag: str, status: int = 200):
    """Invia un message SSE Anthropic-compat evento-per-evento con flush immediato.

    Garantisce che il PRIMO evento (message_start) raggiunga il client SUBITO,
    evitando ECONNRESET 'before first event' in VSCode.
    """
    resp = await _prepare_sse_response(request, status=status,
                                       extra_headers={"x-ai-verified": verified_flag})
    for ev in _sse_events_from_message(final_json, verified_flag):
        await resp.write(ev.encode())
        try:
            await resp.drain()
        except Exception:
            pass
    await resp.write_eof()
    return resp


def _relay_collaborative(final_text, verified_flag, model_name, request):
    """Helper: costruisce la message-response finale e la restituisce."""
    final_json = {
        "id": f"msg_collab_{int(time.time()*1000)}",
        "type": "message", "role": "assistant",
        "model": model_name,
        "content": [{"type": "text", "text": final_text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)}  # FIX B4.2: ~4 char/token, meglio di split(),
    }
    return web.json_response(final_json, headers={"x-ai-verified": verified_flag})


async def handle(request):
    mode = get_mode(request)
    # FIX B3.8: rifiuta esplicitamente multipart (non supportato dal routing).
    ct = (request.headers.get("Content-Type") or "").lower()
    if "multipart/form-data" in ct:
        return _err_response("multipart not supported", status=415)
    body = await request.read()
    forced = request.app.get("forced_mode")

    # health locale
    if request.path == "/__router_health":
        return web.json_response({
            "service": "ai-router-proxy", "mode": mode,
            "port_role": forced or "dynamic",
            "anthropic_upstream": ANTHROPIC_UPSTREAM,
            "minimax_upstream": MINIMAX_UPSTREAM,
            "minimax_key_present": bool(get_minimax_key()),
        })

    # FIX #1: Health-check e probe watchdog interni: risposta locale 200.
    # Evita che /, /readyz, /livez, /health, /stats, /status vadano all'upstream
    # e causino cascate 404/405 infinite che bloccano l'utente.
    _HC = {"/", "/readyz", "/livez", "/health", "/stats", "/metrics", "/status"}
    if request.path in _HC:
        return web.Response(status=200, text="ok")

    # Comandi in-chat + marca-chat (solo porta dinamica :8787, solo /v1/messages).
    if forced is None and request.path.endswith("/v1/messages"):
        try:
            _data = json.loads(body)
            _fp = conversation_fingerprint(_data)
            # D39: guarda solo l'ULTIMO messaggio vero dell'utente
            _last = ""
            for _m in reversed(_data.get("messages", [])):
                if _m.get("role") == "user":
                    _c = _m.get("content", "")
                    _last = _c if isinstance(_c, str) else " ".join(
                        b.get("text", "") for b in _c if isinstance(b, dict))
                    break
            _cmd = parse_router_command(_last)
            if _cmd:
                # gestione locale, risposta sintetica (D40: costo zero, no inoltro)
                _txt = _router_reply_text(_cmd, _fp)
                _msg = _synthetic_message(_txt)
                log(f"in-chat command {_cmd} fp={_fp}")
                if bool(_data.get("stream")):
                    # FIX SSE: usa helper con header anti-buffering + flush per evento
                    return await _send_sse_message(request, _msg, "router")
                return web.json_response(_msg)
            # nessun comando: applica eventuale marca-chat (D5)
            _cm = get_chat_mode(_fp)
            if _cm in VALID_MODES:
                mode = _cm
        except Exception:
            pass

    session = request.app["session"]

    async def relay(upstream):
        # FIX SSE: rileva text/event-stream per applicare flush immediato + no-buffering
        is_sse = "text/event-stream" in (upstream.headers.get("content-type") or "").lower()
        resp = web.StreamResponse(status=upstream.status)
        for k, v in upstream.headers.items():
            lk = k.lower()
            if lk in HOP_HEADERS:
                continue
            # FIX #8: Forward Content-Encoding (br/gzip) so client can decode.
            # We use auto_decompress=False in ClientSession to pass through as-is.
            # Evita Content-Length su SSE: rompe chunked streaming
            if is_sse and lk == "content-length":
                continue
            resp.headers[k] = v
        if is_sse:
            # Header SSE-corretti: nessun buffering downstream, keep-alive
            resp.headers.setdefault("content-type", "text/event-stream")
            resp.headers["cache-control"] = "no-cache, no-transform"
            resp.headers["connection"] = "keep-alive"
            resp.headers["x-accel-buffering"] = "no"
        # FIX #6: NON usare enable_chunked_encoding() - aiohttp lo fa automaticamente
        # quando Transfer-Encoding non è in headers (già skippato da HOP_HEADERS).
        # Evita doppia codifica/conflitto chunked.
        await resp.prepare(request)
        # FIX #2: usa iter_any() anche per SSE - iter_chunked(N) può bloccare aspettando N bytes
        # mentre SSE invia eventi piccoli (<200 byte). iter_any() yielda appena disponibile.
        iterator = upstream.content.iter_any()
        chunk_count = 0
        total_bytes = 0
        try:
            async for chunk in iterator:
                if not chunk:
                    continue
                chunk_count += 1
                total_bytes += len(chunk)
                # FIX #4: log primo chunk per debug
                if chunk_count == 1:
                    log(f"relay first chunk {len(chunk)}B (SSE={is_sse})")
                await resp.write(chunk)
                if is_sse:
                    # FIX #5: drain senza try/except - se fallisce vogliamo saperlo
                    await resp.drain()
        except Exception as e:
            # FIX #3: log esplicito eccezioni nel loop streaming
            log(f"relay loop ERROR after {chunk_count} chunks ({total_bytes}B): {e}")
            raise
        finally:
            # FIX B2.3: garantisce chiusura upstream su client disconnect/cancel/exception
            if not upstream.closed:
                upstream.release()
        # FIX #4: log bytes totali inoltrati
        if is_sse or total_bytes > 0:
            log(f"relay done: {chunk_count} chunks, {total_bytes} bytes (SSE={is_sse})")
        # FIX #5: drain finale prima di write_eof per garantire flush completo
        await resp.drain()
        await resp.write_eof()
        return resp

    forwarders = {"anthropic": forward_anthropic, "minimax": forward_minimax, "anthropic_direct": forward_anthropic_direct}

    # ── MODALITÀ PURE: nessuno switch, mai. Si ritorna ciò che dà l'upstream ──
    if mode in ("anthropic", "minimax"):
        # Health-check interni headroom (/readyz /livez /health /stats /metrics):
        # chiamate del watchdog, non traffico utente. Non loggare (rumore).
        if not request.path.endswith("/v1/messages"):
            up = await forwarders[mode](request, body, session)
            return await relay(up)
        try:
            up = await forwarders[mode](request, body, session)
            log(f"{mode} (pure) -> {up.status} {request.path}")
            return await relay(up)
        except Exception as e:
            log(f"ERR {mode} (pure) {request.path}: {e}")
            return web.json_response(
                {"type": "error", "error": {"type": "router_error", "message": str(e)}},
                status=502,
            )


    # ── MODALITÀ INVERSE: MiniMax orchestra + esegue ─────────────────────
    # Anthropic verifica i T2; dopo N fail consecutivi (default 2) Anthropic
    # esegue direttamente, bypassando MiniMax. Il contatore è per-chat.
    if mode == "inverse":
        chat_fp = request.remote or "default"
        is_messages = request.path.endswith("/v1/messages")
        is_t2 = is_messages and classify_t2(body)

        # ESCALATION: dopo N fail consecutivi su questa chat, Anthropic esegue
        if inverse_should_escalate(chat_fp):
            log(f"inverse: {chat_fp} ha {_inverse_fails.get(chat_fp, 0)} fail -> Anthropic esegue direttamente")
            try:
                up = await forward_anthropic(request, body, session)
                log(f"inverse (escalated) anthropic -> {up.status} {request.path}")
                return await relay(up)
            except Exception as e:
                log(f"inverse escalated EXC: {e}")
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)  # legacy, vedi _err_response

        # solo /v1/messages è soggetto a verifica; altri path -> minimax passthrough
        if not is_t2:
            # task non critico (o non-messages): MiniMax diretto, streaming preservato
            try:
                up = await forward_minimax(request, body, session)
                inverse_fail_reset(chat_fp)
                log(f"inverse T0/T1 -> minimax {up.status} {request.path}")
                return await relay(up)
            except Exception as e:
                n = inverse_fail_inc(chat_fp)
                log(f"inverse T1 EXC ({n}/{INVERSE_FAIL_THRESHOLD}): {e}")
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)  # legacy, vedi _err_response

        # ---- T2 critico: pipeline collaborativa M3 <-> Anthropic ----
        # R1: M3 genera bozza
        # R2: Anthropic produce critica costruttiva
        # R3: M3 rivede la bozza (v2) usando la critica
        # R4: Anthropic finalizza (gate qualità)
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        question = extract_last_user_text(orig)
        wants_stream = bool(orig.get("stream"))

        # R1: bozza M3
        try:
            gen_status, gen_json = await _call_full(forward_minimax, request, body, session)
        except Exception as e:
            gen_status, gen_json = 0, None
            log(f"inverse T2 R1 EXC: {e}")
        if not gen_json or gen_status in FALLBACK_STATUSES:  # FIX B4.1: solo retryable
            n = inverse_fail_inc(chat_fp)
            log(f"inverse T2: M3 R1 fallita ({gen_status}) [{n}/{INVERSE_FAIL_THRESHOLD}]")
            if inverse_should_escalate(chat_fp):
                log(f"inverse T2: ESCALATION -> Anthropic esegue ({n} fail)")
                try:
                    up = await forward_anthropic(request, body, session)
                    return await relay(up)
                except Exception as e:
                    return web.json_response(
                        {"type": "error", "error": {"type": "router_error",
                         "message": f"escalation ko: {e}"}}, status=502)
            try:
                up = await forward_anthropic(request, body, session)
                return await relay(up)
            except Exception as e:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": f"gen+fallback ko: {e}"}}, status=502)
        inverse_fail_reset(chat_fp)
        draft_v1 = _text_from_message(gen_json)
        log(f"inverse T2 R1: M3 bozza v1 ({len(draft_v1)} chars)")

        # R2: Anthropic critica
        cbody = _build_critique_body(orig, question, draft_v1)
        critique = ""
        try:
            c_status, c_json = await _call_full(forward_anthropic_direct, request, cbody, session)
            if c_json and c_status < 400:
                critique = _text_from_message(c_json).strip()
        except Exception as e:
            log(f"inverse T2 R2 EXC: {e}")
        log(f"inverse T2 R2: Anthropic critica ({len(critique)} chars)")

        no_critique = (not critique) or ("NESSUNA CRITICA" in critique.upper())
        if no_critique:
            final_text = draft_v1
            verified_flag = "m3_only"
            log(f"inverse T2: nessuna critica -> M3 v1 finale")
        else:
            # R3: M3 rivede -> v2
            rbody = _build_revise_body(orig, question, draft_v1, critique)
            draft_v2 = draft_v1  # fallback
            try:
                r_status, r_json = await _call_full(forward_minimax, request, rbody, session)
                if r_json and r_status < 400:
                    draft_v2 = _text_from_message(r_json) or draft_v1
            except Exception as e:
                log(f"inverse T2 R3 EXC: {e}")
            log(f"inverse T2 R3: M3 v2 ({len(draft_v2)} chars)")

            # R4: Anthropic finalizza
            fbody = _build_finalize_body(orig, question, draft_v2)
            final_text = draft_v2  # fallback
            try:
                f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
                if f_json and f_status < 400 and _text_from_message(f_json):
                    final_text = _text_from_message(f_json)
                    verified_flag = "collaborative"
                else:
                    verified_flag = "m3_v2_unfinalized"
            except Exception as e:
                verified_flag = "m3_v2_unfinalized"
                log(f"inverse T2 R4 EXC: {e}")
            log(f"inverse T2 R4: flag={verified_flag} ({len(final_text)} chars)")

        # Costruisci final_json (SSE o JSON) da final_text
        final_json = {
            "id": f"msg_collab_{int(time.time()*1000)}",
            "type": "message", "role": "assistant",
            "model": "minimax-m3+claude" if verified_flag == "collaborative" else "minimax-m3",
            "content": [{"type": "text", "text": final_text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)}  # FIX B4.2: ~4 char/token, meglio di split(),
        }

        # 3) risposta al client (SSE sintetico se chiedeva stream)
        if wants_stream:
            # FIX SSE: invio evento-per-evento con flush + header anti-buffering
            return await _send_sse_message(request, final_json, verified_flag)
        return web.json_response(
            final_json, headers={"x-ai-verified": verified_flag})

    # ── MODALITÀ MIXED: Anthropic orchestra SEMPRE + MiniMax esegue SEMPRE ──
    # Pipeline gerarchica:
    #   - T2-classifier (locale, zero-token) marca le richieste 'complesse'
    #   - T0/T1 (semplici): MiniMax esegue diretto
    #   - T2 (complesse): pipeline verify M3→Anthropic→M3→Anthropic
    #   - Se MiniMax fallisce 2 volte sulla stessa chat: Anthropic prende il
    #     comando ed esegue con la sua orchestrazione gerarchica (= pipeline verify).
    chat_fp = request.remote or "default"
    anthropic_leads = mixed_anthropic_leads(chat_fp)
    is_messages = request.path.endswith("/v1/messages")
    is_t2 = is_messages and classify_t2(body)

    # ESCALATION: M3 ha fallito N volte -> Anthropic esegue con pipeline gerarchica
    if anthropic_leads and is_messages:
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        question = extract_last_user_text(orig)
        wants_stream = bool(orig.get("stream"))
        # pipeline verify (T2-style): M3 draft -> Anthropic critique -> M3 revise
        # -> Anthropic finalize. Se T2 è False, salta al primo step Anthropic.
        try:
            gen_status, gen_json = await _call_full(forward_minimax, request, body, session)
        except Exception as e:
            gen_status, gen_json = 0, None
        if not gen_json or gen_status in FALLBACK_STATUSES:  # FIX B4.1 residuo
            # M3 ancora giù: Anthropic esegue DIRETTO senza pipeline
            log(f"mixed escalation: M3 ko {gen_status}, Anthropic esegue diretto")
            try:
                up = await forward_anthropic(request, body, session)
                return await relay(up)
            except Exception as e:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)  # legacy, vedi _err_response
        draft_v1 = _text_from_message(gen_json)
        log(f"mixed escalation R1 M3 draft ({len(draft_v1)} chars)")
        # Anthropic finalizza direttamente (salta critique+revise per latenza)
        fbody = _build_finalize_body(orig, question, draft_v1)
        try:
            f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
            if f_json and f_status < 400 and _text_from_message(f_json):
                final_text = _text_from_message(f_json)
                verified_flag = "escalation"
            else:
                final_text = draft_v1
                verified_flag = "m3_only_escalation"
        except Exception as e:
            final_text = draft_v1
            verified_flag = "m3_only_escalation"
            log(f"mixed escalation R2 EXC: {e}")
        final_json = {
            "id": f"msg_mixed_{int(time.time()*1000)}",
            "type": "message", "role": "assistant",
            "model": "minimax-m3+claude" if verified_flag == "escalation" else "minimax-m3",
            "content": [{"type": "text", "text": final_text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)}  # FIX B4.2: ~4 char/token, meglio di split(),
        }
        if wants_stream:
            # FIX SSE: invio evento-per-evento con flush + header anti-buffering
            return await _send_sse_message(request, final_json, verified_flag)
        return web.json_response(final_json, headers={"x-ai-verified": verified_flag})

    # Path normale: T0/T1 -> M3 diretto; T2 -> pipeline verify gerarchica
    if not is_t2:
        # T0/T1: M3 diretto, streaming preservato
        try:
            up = await forwarders["minimax"](request, body, session)
        except Exception as e:
            n = mixed_fail_inc(chat_fp)
            log(f"mixed T0/T1 M3 EXC ({n}/{MIXED_FAIL_THRESHOLD}) {request.path}: {e}")
            try:
                up = await forwarders["anthropic"](request, body, session)
                log(f"mixed T0/T1 fallback anthropic {up.status} {request.path}")
                return await relay(up)
            except Exception as e2:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": f"both down: {e2}"}}, status=502)
        if up.status in FALLBACK_STATUSES:  # FIX B4.1: solo retryable, NON 400/404
            n = mixed_fail_inc(chat_fp)
            await up.release()
            log(f"mixed T0/T1 M3 {up.status} ({n}/{MIXED_FAIL_THRESHOLD}) {request.path}")
            if n >= MIXED_FAIL_THRESHOLD:
                log(f"mixed escalation: M3 ha fallito {n}x -> Anthropic prende comando")
            try:
                up2 = await forwarders["anthropic"](request, body, session)
                log(f"mixed T0/T1 rescue anthropic {up2.status} {request.path}")
                return await relay(up2)
            except Exception as e2:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": f"rescue ko: {e2}"}}, status=502)
        if up.status < 400:
            mixed_fail_reset(chat_fp)
        log(f"mixed T0/T1 executor M3 {up.status} {request.path}")
        return await relay(up)

    # T2: pipeline verify gerarchica (Anthropic orchestra M3)
    try:
        orig = json.loads(body)
    except Exception:
        orig = {}
    question = extract_last_user_text(orig)
    wants_stream = bool(orig.get("stream"))
    try:
        gen_status, gen_json = await _call_full(forward_minimax, request, body, session)
    except Exception as e:
        gen_status, gen_json = 0, None
    if not gen_json or gen_status in FALLBACK_STATUSES:  # FIX B4.1: solo retryable
        n = mixed_fail_inc(chat_fp)
        log(f"mixed T2 M3 R1 ko {gen_status} ({n}/{MIXED_FAIL_THRESHOLD}) {request.path}")
        try:
            up = await forward_anthropic(request, body, session)
            log(f"mixed T2 fallback anthropic {up.status} {request.path}")
            return await relay(up)
        except Exception as e:
            return web.json_response(
                {"type": "error", "error": {"type": "router_error",
                 "message": str(e)}}, status=502)
    mixed_fail_reset(chat_fp)
    draft_v1 = _text_from_message(gen_json)
    log(f"mixed T2 R1 M3 draft ({len(draft_v1)} chars)")
    # Anthropic finalize (gerarchia: decide se la bozza è ok o la riscrive)
    fbody = _build_finalize_body(orig, question, draft_v1)
    final_text = draft_v1
    verified_flag = "m3_only"
    try:
        f_status, f_json = await _call_full(forward_anthropic_direct, request, fbody, session)
        if f_json and f_status < 400 and _text_from_message(f_json):
            final_text = _text_from_message(f_json)
            verified_flag = "collaborative"
    except Exception as e:
        log(f"mixed T2 R2 anthropic EXC: {e}")
    final_json = {
        "id": f"msg_mixed_{int(time.time()*1000)}",
        "type": "message", "role": "assistant",
        "model": "minimax-m3+claude" if verified_flag == "collaborative" else "minimax-m3",
        "content": [{"type": "text", "text": final_text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": max(1, len(final_text) // 4)}  # FIX B4.2: ~4 char/token, meglio di split(),
    }
    if wants_stream:
        # FIX SSE: invio evento-per-evento con flush + header anti-buffering
        return await _send_sse_message(request, final_json, verified_flag)
    return web.json_response(final_json, headers={"x-ai-verified": verified_flag})


ALLOWED_PATHS = ("/v1/messages", "/v1/messages/*", "/v1/models", "/v1/models/*")  # FIX B3.1: whitelist


def _make_app(session, forced_mode):
    """Crea una web.Application con la modalità cablata (deterministica).
    forced_mode=None -> porta dinamica (:8787) che segue il file ai-router-mode."""
    app = web.Application(client_max_size=1024 * 1024 * 100)
    app["session"] = session
    app["forced_mode"] = forced_mode

    async def healthz(request):
        return web.json_response({"ok": True, "mode": forced_mode or _current_mode()})

    app.router.add_get("/health", healthz)
    for p in ALLOWED_PATHS:
        app.router.add_route("*", p, handle)
    return app


async def _run_multiport():
    # FIX B1.3: connector con limit espliciti per evitare starvation pool sotto carico
    conn_anthropic = TCPConnector(limit=50, limit_per_host=20, ttl_dns_cache=300)
    conn_minimax = TCPConnector(limit=50, limit_per_host=20, ttl_dns_cache=300)
    # Una sola ClientSession condivisa da tutte le porte.
    session = ClientSession(
        # FIX: timeout granulari per streaming SSE - sock_read alto per first-token LLM
        timeout=ClientTimeout(total=600, connect=30, sock_read=120, sock_connect=15),
        connector=conn_anthropic,  # default per anthropic; minimax userà conn dedicato
        # FIX #7 CRITICAL: auto_decompress=False per passthrough brotli/gzip.
        auto_decompress=False,
    )
    session._minimax_connector = conn_minimax  # FIX B1.3: pool separato per fallback
    runners = []
    for port in LISTEN_PORTS:
        forced = PORT_MODE.get(port)  # None per :8787 (dinamica)
        app = _make_app(session, forced)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, LISTEN_HOST, port)
        try:
            await site.start()
            log(f"LISTEN {LISTEN_HOST}:{port} mode={forced or 'dynamic'}")
            runners.append(runner)
        except Exception as e:
            log(f"ERR listen {port}: {e}")
    if not runners:
        log("no ports bound (already in use?) — exiting to avoid orphan instance")
        await session.close()
        return
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for r in runners:
            await r.cleanup()
        await session.close()


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MODE_FILE.exists():
        MODE_FILE.write_text("anthropic\n")
    log(f"START ai-router-proxy multi-port {LISTEN_PORTS}")
    asyncio.run(_run_multiport())


if __name__ == "__main__":
    main()
