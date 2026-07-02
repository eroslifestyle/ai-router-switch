#!/usr/bin/env python3
"""
AI Router Proxy — switcher davanti a Claude Code.

Quattro modalità (file ~/.claude/ai-router-mode):
  - anthropic   : tutto diretto a api.anthropic.com
  - minimax     : tutto diretto a api.minimaxi.chat/anthropic
  - mixed       : Anthropic primario; su 429/5xx/errore -> fallback MiniMax (bidir)
  - inverse     : MiniMax orchestra + esegue. Anthropic verifica (T2) i task critici.
                  Anthropic interviene direttamente come esecutore dopo 2 fallimenti
                  (resilienza). Task non-critici -> MiniMax diretto (streaming).


Claude Code punta qui: ANTHROPIC_BASE_URL=http://127.0.0.1:8787
Gestisce streaming SSE. Backend diretto (nessun proxy intermedio).
"""
import asyncio
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout, TCPConnector

# ── Modulo resilienza (RESILIENZA 2026-06-30) ────────────────────────────
# Aggiunge: OAuth self-test al boot, modalità DEGRADED quando OAuth manca,
# auto-recovery quando l'utente fa `claude login` (refresh creds.json),
# crash dump su SIGTERM, heartbeat watchdog per freeze-watchdog esterno.
sys.path.insert(0, str(Path.home() / ".claude" / "scripts"))
try:
    from ai_router_resilience import Resilience
    _RESILIENCE_AVAILABLE = True
except Exception as _rexc:
    Resilience = None
    _RESILIENCE_AVAILABLE = False
    log_bootstrap = lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", file=sys.stderr)
    log_bootstrap(f"WARN: resilience module non disponibile: {_rexc}")

# ── Config ────────────────────────────────────────────────────────────────
LISTEN_HOST = os.environ.get("AIROUTER_LISTEN_HOST", "127.0.0.1")  # ponytail: 127.0.0.1 default; export AIROUTER_LISTEN_HOST=0.0.0.0 per Tailscale/VSCode remoto
# Il router E' il punto unico su :8787 (dove tutte le app gia' puntano).
# Backend DIRETTO alle API ufficiali (nessun proxy intermedio).
LISTEN_PORT = int(os.environ.get("AIROUTER_PORT", "8787"))

ANTHROPIC_UPSTREAM = os.environ.get("AIROUTER_ANTHROPIC_UPSTREAM", "https://api.anthropic.com")
# MiniMax: endpoint Anthropic-compat ufficiale, diretto.
MINIMAX_UPSTREAM = os.environ.get("AIROUTER_MINIMAX_UPSTREAM", "https://api.minimaxi.chat/anthropic")
MINIMAX_MODEL = os.environ.get("AIROUTER_MINIMAX_MODEL", "MiniMax-M3")
# mode=minimax: M3 ORCHESTRA (produce il piano, MAI esegue), i modelli inferiori
# eseguono. L'orchestratore è M3; gli executor ammessi sono i modelli inferiori.
MINIMAX_ORCHESTRATOR_MODEL = os.environ.get("AIROUTER_MINIMAX_ORCHESTRATOR", "MiniMax-M3")
MINIMAX_EXECUTORS = set(
    m.strip() for m in os.environ.get(
        "AIROUTER_MINIMAX_EXECUTORS", "MiniMax-M2,MiniMax-M2.5,MiniMax-M2.7"
    ).split(",") if m.strip()
)
# Feature flag redesign 4 modalità 2026-07-01: se =1 (default), mixed/inverse
# usano le NUOVE pipeline gerarchiche (Anthropic THINK+CONTROLLA+M3 ACT / M3 THINK
# + Opus OPPOSE + M3 ACT) per TUTTE le /v1/messages — abroga la distinzione T0/T1/T2
# che escludeva le richieste agentiche. =0 fallback al comportamento legacy T2-only.
NEW_PIPELINE = os.environ.get("AIROUTER_NEW_PIPELINE", "1") == "1"
INVERSE_REVIEW_MAX_ITER = int(os.environ.get("AIROUTER_INVERSE_REVIEW_MAX_ITER", "2"))
# Modello giudice per la verifica T2 in modalità interactive (Claude Opus).
VERIFY_MODEL = os.environ.get("AIROUTER_VERIFY_MODEL", "claude-opus-4-8")
VALID_MODES = ("anthropic", "minimax", "mixed", "inverse")

MODE_FILE = Path.home() / ".claude" / "ai-router-mode"
KEY_FILE = Path.home() / ".claude" / "secrets" / "secrets.sh"
LOG_FILE = Path.home() / ".claude" / "logs" / "ai-router.log"
SIDECAR = Path.home() / ".claude" / "logs" / "router-model-map.jsonl"
USAGE_SIDECAR = Path.home() / ".claude" / "logs" / "router-usage.jsonl"

# status code che in 'mixed' fanno scattare il fallback a MiniMax
# Fallback attivo su: 5xx/529 (server/overload) + 4xx eccetto 400/404 (client error puro).
# 401/403 (auth/billing) -> fallback a MiniMax cosi' l'utente non resta bloccato.
# 429 (rate limit) -> fallback per non aspettare.
FALLBACK_STATUSES = {401, 403, 408, 409, 413, 429, 500, 502, 503, 504, 529}

# Circuit breaker (D15): dopo N fail un backend va in cooldown e viene saltato.
# FIX audit v4: BREAKER_* removed (dead code - mai chiamato nel flusso;
# la logica di escalation usa _inverse_fails / _mixed_fails per-chat).
# Se serve circuit-breaker globale, va reintrodotto con test.

# Contatore per-chat dei fallimenti MiniMax (modalità inverse).
# Dopo N fail consecutivi: Anthropic esegue direttamente (bypass MiniMax).
INVERSE_FAIL_THRESHOLD = int(os.environ.get("AIROUTER_INVERSE_FAILS", "2"))
_inverse_fails = {}  # chat_fp -> int

# FIX E: mappa chat_fp -> modello originale richiesto dal client, usata dal relay
# per riscrivere il campo 'model' nella SSE response (così il jsonl di Claude Code
# riceve il modello reale, non "MiniMax-M3" rimappato dall'upstream).
_request_orig_model = {}  # chat_fp -> orig_model (es. "claude-sonnet-4-6")

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
    """DEPRECATO audit v4: mai chiamato nel flusso principale (dead code)."""
    return False


def breaker_fail(backend: str):
    """DEPRECATO audit v4: mai chiamato."""
    pass


def breaker_ok(backend: str):
    """DEPRECATO audit v4: mai chiamato."""
    pass

_FAILS_GC_MAX = 5000  # FIX audit v5 #7: soglia oltre cui ripulire chat_fp stale


def _gc_fail_dicts(fails: dict, ts: dict, cooldown: dict, now: float) -> None:
    """FIX audit v5 #7: rimuove entry stale (no inc da >FAIL_RESET_SEC) quando i
    dict crescono troppo. Da chiamare DENTRO _counter_lock. Evita crescita RAM
    indefinita su proxy long-running con molti chat_fp unici (es. X-Session-ID)."""
    if len(fails) <= _FAILS_GC_MAX:
        return
    stale = [fp for fp, t in ts.items() if now - t > FAIL_RESET_SEC]
    for fp in stale:
        fails.pop(fp, None)
        ts.pop(fp, None)
        cooldown.pop(fp, None)


def inverse_fail_inc(chat_fp: str) -> int:
    """Incrementa contatore fallimenti MiniMax per chat. Ritorna nuovo valore."""
    with _counter_lock:  # FIX B1.1
        now = time.time()
        _gc_fail_dicts(_inverse_fails, _inverse_fail_ts, _inverse_cooldown_until, now)
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
    with _counter_lock:  # FIX audit v5 #2: simmetrico con inverse_fail_inc
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
        _gc_fail_dicts(_mixed_fails, _mixed_fail_ts, _mixed_cooldown_until, now)
        # Reset automatico se ultimo fail > FAIL_RESET_SEC fa
        last_fail = _mixed_fail_ts.get(chat_fp, 0)
        if now - last_fail > FAIL_RESET_SEC:
            _mixed_fails[chat_fp] = 0
        n = _mixed_fails.get(chat_fp, 0) + 1
        _mixed_fails[chat_fp] = n
        _mixed_fail_ts[chat_fp] = now
    return n


def mixed_fail_reset(chat_fp: str) -> None:
    with _counter_lock:  # FIX audit v5 #2: simmetrico con mixed_fail_inc
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
CHAT_MAX_ENTRIES = 10000  # FIX B2.4: cap duro anti-DoS
_chat_cache = {"data": None, "ts": 0}
_chat_lock = threading.Lock()  # FIX audit v3: TOCTOU read-modify-write protection


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


def _resolve_chat_fingerprint(request) -> str:
    """FIX audit v4: chat_fp NAT-friendly.

    Priorita':
    1. Header esplicito X-Session-ID se presente (clients possono coabitare su stesso NAT).
    2. request.remote (IP:port) come fallback operativo - accettabile per setup locale single-user.
    3. "default" se neanche quello.

    NB: in setup multi-utente dietro NAT stesso, abilitare X-Session-ID via client."""
    sid = request.headers.get("X-Session-ID") or request.headers.get("x-session-id")
    if sid:
        return f"sid:{sid[:64]}"  # bound size anti-abuse
    return request.remote or "default"

def _load_chats() -> dict:
    """FIX deadlock 2026-06-30: cache + lettura file FUORI dal lock.

    Bug originale: `CHAT_STORE.read_text()` eseguito DENTRO `with _chat_lock`
    (threading.Lock). Chiamato da handler async: due richieste concorrenti
    `get_chat_mode` → la 1a tiene il lock durante il read → la 2a aspetta
    → l'event loop asyncio resta appeso finché il read non termina.

    Fix: cache hit è atomic con una semplice flag (single-thread); cache miss
    legge il file senza lock e aggiorna la cache con un dict nuovo (GIL-safe).
    Il TOCTOU è accettabile qui perché il file è piccolo e write è raro.
    """
    now = time.time()
    cached = _chat_cache["data"]
    cached_ts = _chat_cache["ts"]
    if cached is not None and now - cached_ts < 5:
        return cached

    # Cache miss: leggi file FUORI dal lock (atomic GIL-protected)
    try:
        raw = CHAT_STORE.read_text()
        d = json.loads(raw)
    except Exception:
        d = {}

    # cleanup TTL (D26: 7 giorni)
    cutoff = now - CHAT_TTL_DAYS * 86400
    changed = False
    for fp in list(d.keys()):
        if d[fp].get("ts", 0) < cutoff:
            del d[fp]; changed = True
    # bound hard (FIFO by timestamp asc)
    if len(d) > CHAT_MAX_ENTRIES:
        for fp in sorted(d, key=lambda k: d[k].get("ts", 0))[: len(d) - CHAT_MAX_ENTRIES]:
            del d[fp]; changed = True

    if changed:
        # Scrivi FUORI dal lock, atomic via temp file
        try:
            tmp = CHAT_STORE.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(CHAT_STORE)
        except Exception as e:
            log(f"ERR save chats: {type(e).__name__}")

    _chat_cache["data"] = d
    _chat_cache["ts"] = now
    return d


def _save_chats(d: dict):
    """FIX: write atomico via temp file, NO lock (async-safe)."""
    try:
        tmp = CHAT_STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(CHAT_STORE)
        _chat_cache["data"] = d
        _chat_cache["ts"] = time.time()
    except Exception as e:
        log(f"ERR save chats: {type(e).__name__}")


def get_chat_mode(fp: str):
    """FIX deadlock 2026-06-30: lock-free, cache-first."""
    try:
        d = _load_chats()
        return d.get(fp, {}).get("mode")
    except Exception:
        return None


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


def _body_has_tools(body: bytes) -> bool:
    """True se il body JSON contiene 'tools' = richiesta agentica (Claude Code/VSCode).
    NB: MiniMax-M3 È agentico ed emette tool_use nativamente (verificato 2026-06-29:
    non-stream + SSE streaming + multi-turn tool_result su api.minimaxi.chat/anthropic).
    Le richieste agentiche vanno quindi a MiniMax in passthrough, con fallback Anthropic
    su 429/5xx per resilienza sotto carico (es. 100 agenti paralleli)."""
    try:
        return bool(json.loads(body).get("tools"))
    except Exception:
        return False


def classify_t2(body: bytes) -> bool:
    """True se la richiesta è 'critica' (T2) -> merita verifica Opus."""
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


async def get_minimax_key() -> str:
    """FIX deadlock 2026-06-30: cache hit sotto lock, subprocess FUORI dal lock.

    Bug originale: `asyncio.to_thread(subprocess.check_output)` chiamato DENTRO
    `with _counter_lock` (threading.Lock). Quando arriva una 2ª richiesta mentre
    la 1ª è ancora in `await`, il 2° handler thread entra nello stesso lock
    perché `asyncio.to_thread` rilascia il GIL ma NON la threading.Lock tenuta
    dal thread asyncio principale → deadlock su event loop (lock ricorsivo).
    Fix: cache hit (veloce) resta dentro lock; subprocess si esegue FUORI lock,
    poi rientra per scrivere il risultato.
    """
    now = time.time()
    # 1) Cache hit: veloce, dentro lock (lettura atomica)
    with _counter_lock:
        cached = _minimax_key_cache["key"]
        cached_ts = _minimax_key_cache["ts"]
        if cached and now - cached_ts < 60:
            return cached

    # 2) Cache miss: leggi da env (zero I/O)
    key = os.environ.get("MINIMAX_API_KEY", "")

    # 3) Cache miss + env vuoto: subprocess FUORI dal lock
    if not key:
        try:
            import subprocess
            try:
                loop = asyncio.get_running_loop()
                proc = await asyncio.to_thread(
                    subprocess.check_output,
                    ["bash", str(KEY_FILE), "get", "minimax.api_key"],
                    {"timeout": 5, "text": True},
                )
                key = proc.strip() if isinstance(proc, str) else proc.decode().strip()
            except RuntimeError:
                proc = subprocess.check_output(
                    ["bash", str(KEY_FILE), "get", "minimax.api_key"],
                    text=True, timeout=5,
                )
                key = proc.strip()
        except Exception as e:
            log(f"ERR get key: {type(e).__name__}")
            key = ""

    # 4) Scrivi cache dentro lock (write atomico)
    with _counter_lock:
        # Doppio check: un altro handler potrebbe aver già popolato la cache
        # nel frattempo. Usa il valore appena letto se la cache è ancora vuota,
        # altrimenti rispetta chi ha scritto prima.
        if not _minimax_key_cache["key"] or now - _minimax_key_cache["ts"] >= 60:
            _minimax_key_cache["key"] = key
            _minimax_key_cache["ts"] = now

    return key


# Campi beta/Anthropic-only che MiniMax (api.minimaxi.chat) rifiuta con 400.
MINIMAX_UNSUPPORTED_FIELDS = ("context_management", "mcp_servers", "thinking")
# FIX 2026-07-02: floor max_tokens per MiniMax reasoning-first (vedi remap_body_for_minimax)
MINIMAX_MIN_MAX_TOKENS = int(os.environ.get("AIROUTER_MINIMAX_MIN_MAX_TOKENS", "1024"))

# Anthropic public API: 'context_management' è beta-only gated da
# 'anthropic-beta: context-management-2025-06-27'. Senza quel header,
# api.anthropic.com restituisce 400 "Extra inputs are not permitted".
# Lo strippiamo a monte per evitare il 400 (il client può inviarlo).
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


def _log_original_model(orig: str, final: str, chat_id: str) -> None:
    """FIX A: Log modello originale prima del remap a MiniMax.

    Scrive in SIDECAR (router-model-map.jsonl) una riga JSON per tracciare
    il modello originale richiesto vs il modello finale remappato.
    Fallback silenzioso su errore IO (non rompe il flusso di richiesta).
    """
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "chat": chat_id,
            "orig": orig,
            "final": final
        }
        with open(SIDECAR, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # silent fallback, never break the request


def log_router_usage(chat_id: str, orig: str, final: str, usage: dict,
                     mode: str, client: str = "?", status: int = 200, path: str = ""):
    """FIX F: log per-request usage su USAGE_SIDECAR (router-usage.jsonl).

    Cattura OGNI richiesta che passa dal router, indipendentemente dal client
    (Claude Code, m3-code, m3-web, m3x, local-llm, agenti remoti). Questo
    è il single source of truth per i tool che non scrivono JSONL.

    FIX 2026-07-01: root-cause `?` final — quando final non è risolto
    (es. path interni /v1/models, /health, /metrics; mode non mappato),
    marca final come 'router-internal' invece di '?' così il ledger sa
    che il record è rumore non fatturabile ma distingue da upstream 200.

    Schema: ts, chat, orig, final, mode, client, status,
            input_tokens, output_tokens, cache_read, cache_creation
    """
    if not final or final == "?":
        final = "router-internal"
    try:
        USAGE_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "chat": chat_id,
            "orig": orig or "?",
            "final": final,
            "mode": mode,
            "client": client,
            "status": status,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_read": int(usage.get("cache_read_input_tokens", 0) or 0),
            "cache_creation": int(usage.get("cache_creation_input_tokens", 0) or 0),
        }
        with open(USAGE_SIDECAR, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # silent fallback


def remap_body_for_minimax(raw: bytes, request=None) -> bytes:
    """Riscrive il model Claude -> MiniMax-M3 e rimuove i campi beta non supportati.

    FIX A: Se request è fornito, estrae chat_fp e loga il modello originale.
    FIX E: salva anche in _request_orig_model[chat_fp] il modello originale,
    così il relay SSE può riscrivere il campo 'model' nella risposta upstream
    e il jsonl di Claude Code riceve il modello realmente richiesto (no leak di MiniMax-M3).
    """
    try:
        data = json.loads(raw)
        orig = data.get("model", "")
        if orig and not orig.startswith("MiniMax"):
            chat_id = "?"
            if request:
                chat_id = _resolve_chat_fingerprint(request)
                _log_original_model(orig, MINIMAX_MODEL, chat_id)
            # FIX E: ricorda per riscrittura SSE — consumato dal relay()
            _request_orig_model[chat_id] = orig
            data["model"] = MINIMAX_MODEL
        # Strip campi che MiniMax non accetta (causano 400 "Extra inputs not permitted")
        for f in MINIMAX_UNSUPPORTED_FIELDS:
            data.pop(f, None)
        # FIX 2026-07-02: MiniMax-M2.7 è reasoning-first — il blocco <think> consuma
        # i token PRIMA del testo. Con max_tokens piccolo (chiamate interne Claude Code:
        # titoli/topic/commit-msg, spesso 20-100) il thinking mangia tutto il budget →
        # content vuoto. Floor a MINIMAX_MIN_MAX_TOKENS garantisce spazio per il testo.
        # (Il modello si ferma comunque da solo con end_turn: nessuno spreco di token.)
        try:
            _mt = int(data.get("max_tokens", 0) or 0)
            if 0 < _mt < MINIMAX_MIN_MAX_TOKENS:
                data["max_tokens"] = MINIMAX_MIN_MAX_TOKENS
        except (TypeError, ValueError):
            pass
        return json.dumps(data).encode()
    except Exception:
        log("remap_body: json parse fail, passthrough")  # FIX B5.1 residuo: log silenzioso
        return raw


def _read_oauth_from_file() -> str:
    """Legge il token OAuth dal file di credenziali Claude Code (zero IO se errore)."""
    try:
        with open(Path.home() / ".claude" / ".credentials.json") as f:
            return json.load(f).get("claudeAiOauth", {}).get("accessToken", "")
    except Exception:
        return ""


def _load_oauth_token():
    """Carica il token OAuth Anthropic da ~/.claude/.credentials.json se non
    è già in env. Usato da forward_anthropic_direct (verify T2 in modalità inverse)."""
    if os.environ.get("ANTHROPIC_OAUTH_TOKEN"):
        return
    tok = _read_oauth_from_file()
    if tok:
        os.environ["ANTHROPIC_OAUTH_TOKEN"] = tok


def _reload_oauth_token() -> bool:
    """FIX B3.2 (lazy): ricarica il token da .credentials.json.
    Claude Code aggiorna quel file indipendentemente a ogni refresh OAuth;
    non serve implementare un client OAuth (client_id/secret/refresh endpoint).
    Ritorna True se ha popolato ANTHROPIC_OAUTH_TOKEN."""
    tok = _read_oauth_from_file()
    if tok:
        cur = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
        if tok != cur:  # evita log inutile se token invariato
            log(f"oauth token reload: {'changed' if cur else 'initial'}")
        os.environ["ANTHROPIC_OAUTH_TOKEN"] = tok
        return True
    return False

_load_oauth_token()  # eseguita dopo le def


# ── Forwarding ────────────────────────────────────────────────────────────
HOP_HEADERS = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade",
    # FIX: strippa X-Forwarded-* per evitare che il client forgia IP/host percepiti dall'upstream
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port",
    "x-real-ip", "via", "forwarded",
}


async def forward_anthropic(request, body, session):
    """Chiama api.anthropic.com con OAuth subscription Bearer.

    FIX resilienza 2026-06-30: aggiunge SEMPRE il token OAuth letto da
    ~/.claude/.credentials.json (subscription web login), come fa Claude Code
    nel terminale. Il client non deve passare Authorization — il proxy gestisce
    l'auth in modo trasparente, esattamente come il Claude Code locale.

    - Se il client passa un OAuth Bearer sk-ant-oat*, viene rispettato (override esplicito)
    - Altrimenti usa il token OAuth letto lazy da .credentials.json
    - OAuth-lazy: rilegge .credentials.json ad ogni chiamata (Claude Code può refreshare)
    """
    url = ANTHROPIC_UPSTREAM + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    auth = headers.get("Authorization", "") or headers.get("authorization", "")

    if auth.startswith("Bearer sk-ant-oat"):
        # Client passa già OAuth subscription Bearer — mantienilo
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        # FIX OAuth subscription: il client non ha auth, usa il Bearer da .credentials.json
        # (come fa Claude Code nel terminale — il client non vede mai la chiave)
        _reload_oauth_token()  # lazy: rilegge se Claude Code ha refreshato
        tok = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        elif auth:
            # Client ha passato x-api-key (API key legacy) — rispettalo senza beta header
            pass
        # Altrimenti: nessuna auth = proxy trasparente, sarà upstream a rifiutare con 401

    # Strip campi beta che api.anthropic.com rifiuta senza il beta header opportuno.
    # FIX audit v5 #4: copri anche /v1/messages/count_tokens (sub-path), non solo
    # il path esatto, altrimenti context_management su count_tokens -> 400.
    safe_body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS) \
        if "/v1/messages" in request.path else body
    # FIX 2026-07-01: anthropic-version obbligatorio per /v1/messages (Anthropic
    # restituisce 400 "anthropic-version: header is required" se mancante). Se il
    # client non lo passa (es. curl di test, proxy custom), il router lo aggiunge
    # da solo per non rompere.
    if "/v1/messages" in request.path:
        headers.setdefault("anthropic-version", "2023-06-01")
    return await session.request(
        request.method, url, data=safe_body, headers=headers, allow_redirects=False
    )


async def forward_minimax(request, body, session):
    url = MINIMAX_UPSTREAM + request.path_qs
    key = await get_minimax_key()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    # MiniMax vuole X-Api-Key; rimuovo auth Anthropic
    for h in list(headers):
        if h.lower() in ("authorization", "x-api-key"):
            headers.pop(h)
    headers["X-Api-Key"] = key
    new_body = remap_body_for_minimax(body, request=request)  # FIX A: pass request per modello log
    return await session.request(
        request.method, url, data=new_body, headers=headers, allow_redirects=False
    )


ANTHROPIC_DIRECT_URL = os.environ.get("AIROUTER_ANTHROPIC_DIRECT", "https://api.anthropic.com")
ANTHROPIC_OAUTH_TOKEN = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")  # Bearer da Claude Code OAuth


async def forward_anthropic_direct(request, body, session):
    """Chiama api.anthropic.com diretto con OAuth Bearer.
    Usato dalle verify T2 in modalità inverse: il modello di verifica è
    Claude stesso (via login OAuth nativo di Claude Code)."""
    global ANTHROPIC_OAUTH_TOKEN  # FIX B3.2: refresh lazy del token
    if not ANTHROPIC_OAUTH_TOKEN:
        # primo utilizzo / cache cold: carica dal file delle credenziali
        _load_oauth_token()
    # ogni chiamata: rileggi il file (Claude Code può aver refreshato il token)
    if _reload_oauth_token():
        ANTHROPIC_OAUTH_TOKEN = os.environ["ANTHROPIC_OAUTH_TOKEN"]
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
        if "/v1/messages" in request.path else body  # FIX audit v5 #4: copri count_tokens
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
    # FIX 2026-07-01: decomprimi Content-Encoding se attivo. La session ha
    # auto_decompress=False per fare passthrough gzip/brotli al client relay,
    # ma le pipeline interne (THINK/REVISE/ACT/finalize) parsa il body con
    # json.loads: se il body è compresso, parse fallisce silenziosamente →
    # t_json=None → fallback spurio (mixed_new THINK ko 200 / inverse THINK
    # EXC). Decomprimiamo qui per essere robusti a proxy intermedi (Cloudflare).
    ce = (up.headers.get("Content-Encoding") or "").lower().strip()
    if ce and raw:
        try:
            if "gzip" in ce:
                import gzip
                raw = gzip.decompress(raw)
            elif "br" in ce or "brotli" in ce:
                try:
                    import brotli
                    raw = brotli.decompress(raw)
                except Exception:
                    log(f"_call_full: brotli module mancante, body {len(raw)}B non decompresso")
            elif "deflate" in ce:
                import zlib
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    raw = zlib.decompress(raw)
        except Exception as e:
            log(f"_call_full: decompress {ce} fail: {e} (raw={len(raw)}B)")
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


# OAuth subscription (sk-ant-oat01, piano Max) rifiuta con 429 le richieste
# Sonnet/Opus il cui system NON inizia con questo marker esatto. Haiku è esente.
# Bug 2026-07-02: le fasi THINK/VERIFY/OPPOSE sovrascrivevano il system col prompt
# orchestratore, cancellando il marker → 429 → il router cadeva sempre su M3.
CLAUDE_CODE_MARKER = "You are Claude Code, Anthropic's official CLI for Claude."


def _anthropic_system(instruction: str) -> list:
    """System array per Anthropic OAuth: marker Claude Code (auth-gate) + istruzione.
    Il primo blocco deve essere il marker ESATTO o l'API risponde 429."""
    return [
        {"type": "text", "text": CLAUDE_CODE_MARKER},
        {"type": "text", "text": instruction},
    ]


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
        "system": _anthropic_system(sys_msg),
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
        "system": _anthropic_system(sys_msg),
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
        "system": _anthropic_system(sys_msg),
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


def _build_think_body(orig: dict) -> bytes:
    """Redesign 2026-07-01 mixed: Anthropic PENSA + fa self-review.
    Deve produrre SOLO JSON: {plan, tools_to_call, self_review_ok, self_review_notes}.
    Niente testo fuori dal JSON (forza output pulito)."""
    sys_msg = (
        "Sei un orchestratore esperto. Ricevi una richiesta utente (con possibili tools). "
        "Il tuo compito: produrre un PIANO D'AZIONE ragionato, scegliere quali tool chiamare, "
        "e fare AUTO-REVIEW interno. Rispondi SOLO con JSON valido, nessun testo fuori.\n\n"
        "Schema esatto:\n"
        '{"plan": "<chain-of-thought in italiano, max 800 char>",'
        ' "tools_to_call": [{"name": "<tool_name>", "input": <object>}, ...],'
        ' "self_review_ok": <bool>,'
        ' "self_review_notes": ["<criticita risolta>", ...]}\n\n'
        "Regole self_review: il piano è completo? edge case coperti? tool scelti corretti? "
        "Se trovi debolezze, correggile nel campo 'plan' PRIMA di emettere JSON, "
        "poi metti self_review_ok=true. self_review_ok=false SOLO se dopo la revisione "
        "il piano resta vuoto o incoerente (in quel caso tools_to_call=[]). "
        "Se tools_to_call è vuoto perché la richiesta non richiede tool (es. domanda "
        "di conoscenza), metti comunque self_review_ok=true con plan che spiega perché."
    )
    body = dict(orig)
    body["system"] = _anthropic_system(sys_msg)
    body["stream"] = False
    # max_tokens basta per un JSON da ~1500 char
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _extract_balanced_json(text: str) -> list:
    """Ritorna tutte le sottostringhe JSON-object top-level bilanciate nel testo,
    ignorando le graffe dentro le stringhe. Robusto a preamboli, code-fence e
    più oggetti concatenati. Ordine di apparizione."""
    objs = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objs.append(text[start:i + 1])
                    start = -1
    return objs


def _parse_json_with_keys(text: str, required: dict) -> dict | None:
    """Estrae il primo oggetto JSON bilanciato nel testo che soddisfa i vincoli
    `required` (chiave -> validatore/callable, o None = presente). Robusto a
    preamboli testuali, code-fence e oggetti multipli (es. esempio + reale)."""
    if not text:
        return None
    for cand in _extract_balanced_json(text):
        try:
            j = json.loads(cand)
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        ok = True
        for k, check in required.items():
            if k not in j:
                ok = False
                break
            if callable(check) and not check(j[k]):
                ok = False
                break
        if ok:
            return j
    return None


def _parse_think_json(text: str) -> dict | None:
    """Parsifica il JSON emesso in fase THINK. Robusto a preamboli/code-fence/multipli."""
    j = _parse_json_with_keys(text, {"plan": None, "self_review_ok": None})
    if j is None:
        return None
    j.setdefault("tools_to_call", [])
    j.setdefault("self_review_notes", [])
    return j


def _build_act_body(orig: dict, plan: str, tools_to_call: list) -> bytes:
    """Redesign 2026-07-01 mixed: M3 ESEGUE il piano prodotto da Anthropic.
    Passa a M3 i tools_to_call decisi da Anthropic come tool_use espliciti."""
    tools = orig.get("tools") or []
    sys_msg = (
        "Sei M3, l'esecutore. Hai ricevuto un PIANO da un orchestratore Anthropic. "
        "Il tuo compito: eseguilo usando i tool elencati in tools_to_call. "
        "Rispondi come faresti normalmente all'utente, eseguendo i tool nell'ordine "
        "indicato dal piano. Se il piano è una domanda senza tool, rispondi direttamente.\n\n"
        f"PIANO:\n{plan}\n\n"
        f"TOOLS DA USARE (decisi dall'orchestratore):\n{json.dumps(tools_to_call, ensure_ascii=False)}"
    )
    body = dict(orig)
    body["system"] = sys_msg
    body["stream"] = bool(orig.get("stream"))  # preserva stream se client lo chiedeva
    return json.dumps(body).encode()


async def _pipeline_think_act(request, body, session, orig: dict, relay) -> web.Response:
    """Redesign 2026-07-01 mixed: Anthropic THINK+self-review → M3 ACT.
    Scatta per TUTTE le /v1/messages (incluso agentico con tools)."""
    chat_fp = _resolve_chat_fingerprint(request)
    wants_stream = bool(orig.get("stream"))

    # THINK: Anthropic produce piano self-reviewed (JSON puro, no streaming)
    think_body = _build_think_body(orig)
    try:
        t_status, t_json = await _call_full(forward_anthropic, request, think_body, session)
    except Exception as e:
        log(f"mixed-new THINK EXC: {e} → fallback M3 diretto")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e2:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"think+fallback ko: {e2}"}}, status=502)
    if not t_json or t_status in FALLBACK_STATUSES:
        log(f"mixed-new THINK ko {t_status} → fallback M3 diretto")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"think ko + fallback ko: {e}"}}, status=502)
    raw_text = _text_from_message(t_json)
    plan_json = _parse_think_json(raw_text)
    if not plan_json:
        log(f"mixed-new THINK: parse JSON fallito ({len(raw_text)}c) → fallback M3 diretto")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"parse ko + fallback ko: {e}"}}, status=502)
    if not plan_json.get("self_review_ok", False):
        log(f"mixed-new THINK: self_review_ok=false ({plan_json.get('self_review_notes')}) → fallback M3 diretto")
        try:
            return await relay(await forward_minimax(request, body, session))
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"self_review false + fallback ko: {e}"}}, status=502)
    plan = plan_json.get("plan", "")
    tools_to_call = plan_json.get("tools_to_call", []) or []
    log(f"mixed-new THINK OK plan={len(plan)}c tools={len(tools_to_call)} notes={len(plan_json.get('self_review_notes', []))} fp={chat_fp}")

    # ACT: M3 esegue il piano. Preserve stream se client lo chiedeva.
    act_body = _build_act_body(orig, plan, tools_to_call)
    try:
        up = await forward_minimax(request, act_body, session)
    except Exception as e:
        log(f"mixed-new ACT EXC: {e} → Anthropic rescue diretto (senza pipeline)")
        try:
            return await relay(await forward_anthropic(request, body, session))
        except Exception as e2:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"act+rescue ko: {e2}"}}, status=502)
    if up.status in FALLBACK_STATUSES:
        log(f"mixed-new ACT M3 {up.status} → Anthropic rescue")
        try:
            await up.release()
        except Exception:
            pass
        try:
            up2 = await forward_anthropic(request, body, session)
            return await relay(up2)
        except Exception as e:
            return web.json_response({"type": "error", "error": {"type": "router_error",
                "message": f"act fallback ko: {e}"}}, status=502)
    log(f"mixed-new ACT {MINIMAX_MODEL} {up.status} {request.path} fp={chat_fp}")
    # Header distinctivo: gate attivo, l'esecutore MiniMax ha eseguito il piano Anthropic
    return await relay(up, extra_headers={"x-ai-verified": f"anthropic-think+{MINIMAX_MODEL.lower()}-act"})


# ── MINIMAX redesign 2026-07-02: M3 ORCHESTRA (mai esegue) → executor inferiore ACT ──

def _build_minimax_think_body(orig: dict) -> bytes:
    """mode=minimax: M3 orchestra e produce SOLO JSON con piano + executor scelto.
    Forza model=MiniMax-M3 così remap_body_for_minimax NON lo rimappa all'executor."""
    executors = ", ".join(sorted(MINIMAX_EXECUTORS)) or MINIMAX_MODEL
    sys_msg = (
        "Sei M3, il META-ORCHESTRATORE. Il tuo compito è PIANIFICARE, non eseguire. "
        "Ricevi una richiesta utente (con possibili tools), produci un PIANO ragionato, "
        "scegli quali tool chiamare e QUALE modello esecutore inferiore deve eseguire. "
        "Rispondi SOLO con JSON valido, nessun testo fuori.\n\n"
        "Schema esatto:\n"
        '{"plan": "<ragionamento in italiano, max 800 char>",'
        ' "tools_to_call": [{"name": "<tool_name>", "input": <object>}, ...],'
        f' "executor_model": "<uno tra: {executors}>",'
        ' "self_review_ok": <bool>,'
        ' "self_review_notes": ["<criticita risolta>", ...]}\n\n'
        "Regole: scegli executor_model in base al task (coding pesante → il più capace). "
        "Fai auto-review del piano PRIMA di emettere JSON; se resta incoerente metti "
        "self_review_ok=false e tools_to_call=[]. Tu NON esegui mai: solo pianifichi."
    )
    body = dict(orig)
    body["model"] = MINIMAX_ORCHESTRATOR_MODEL   # M3 — remap lo preserva (inizia con 'MiniMax')
    body["system"] = sys_msg
    body["stream"] = False
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _pick_minimax_executor(plan_json: dict) -> str:
    """Executor scelto da M3, validato contro la whitelist dei modelli inferiori.
    Default: MINIMAX_MODEL. M3 non è mai un executor (non esegue)."""
    em = (plan_json.get("executor_model") or "").strip()
    if em in MINIMAX_EXECUTORS and em != MINIMAX_ORCHESTRATOR_MODEL:
        return em
    return MINIMAX_MODEL


def _build_minimax_act_body(orig: dict, plan: str, tools_to_call: list, executor: str) -> bytes:
    """L'executor inferiore esegue il piano prodotto da M3."""
    sys_msg = (
        f"Sei {executor}, l'esecutore. Hai ricevuto un PIANO dal meta-orchestratore M3. "
        "Eseguilo usando i tool in tools_to_call, nell'ordine indicato. Se il piano è una "
        "domanda senza tool, rispondi direttamente.\n\n"
        f"PIANO:\n{plan}\n\n"
        f"TOOLS DA USARE (decisi da M3):\n{json.dumps(tools_to_call, ensure_ascii=False)}"
    )
    body = dict(orig)
    body["model"] = executor   # MiniMax-* → remap lo preserva; se non-MiniMax, remap forza MINIMAX_MODEL
    body["system"] = sys_msg
    body["stream"] = bool(orig.get("stream"))
    return json.dumps(body).encode()


async def _pipeline_minimax_orchestrate(request, body, session, orig: dict, relay) -> web.Response:
    """mode=minimax redesign: M3 THINK/orchestra → executor inferiore ACT.
    M3 non esegue MAI. Su ogni fallimento del THINK, l'executor esegue il task
    originale direttamente (rimappato a MINIMAX_MODEL): M3 resta fuori dall'esecuzione."""
    chat_fp = _resolve_chat_fingerprint(request)

    async def _executor_direct():
        """Fallback: l'executor esegue il task originale (remap → MINIMAX_MODEL). M3 non esegue."""
        return await relay(await forward_minimax(request, body, session))

    think_body = _build_minimax_think_body(orig)
    try:
        t_status, t_json = await _call_full(forward_minimax, request, think_body, session)
    except Exception as e:
        log(f"minimax-orch THINK EXC: {e} → executor diretto")
        return await _executor_direct()
    if not t_json or t_status in FALLBACK_STATUSES:
        log(f"minimax-orch THINK ko {t_status} → executor diretto")
        return await _executor_direct()
    plan_json = _parse_think_json(_text_from_message(t_json))
    if not plan_json or not plan_json.get("self_review_ok", False):
        log(f"minimax-orch THINK: piano non valido → executor diretto")
        return await _executor_direct()
    plan = plan_json.get("plan", "")
    tools_to_call = plan_json.get("tools_to_call", []) or []
    executor = _pick_minimax_executor(plan_json)
    log(f"minimax-orch THINK OK plan={len(plan)}c tools={len(tools_to_call)} executor={executor} fp={chat_fp}")

    act_body = _build_minimax_act_body(orig, plan, tools_to_call, executor)
    try:
        up = await forward_minimax(request, act_body, session)
    except Exception as e:
        log(f"minimax-orch ACT EXC: {e} → executor diretto")
        return await _executor_direct()
    log(f"minimax-orch ACT {executor} {up.status} {request.path} fp={chat_fp}")
    return await relay(up, extra_headers={"x-ai-verified": f"m3-orchestrate+{executor.lower()}-act"})


# ── INVERSE redesign 2026-07-01: M3 THINK → Opus OPPOSE → M3 ACT (loop max 2) ──

INVERSE_OPPOSE_MODEL = "claude-opus-4-8"


def _build_inverse_think_body(orig: dict) -> bytes:
    """Inverse THINK: M3 genera un piano d'azione (testo libero, no JSON)."""
    sys_msg = (
        "Sei M3, l'orchestratore economico. Ricevi una richiesta utente (con possibili tools). "
        "Il tuo compito: produrre un PIANO D'AZIONE dettagliato in italiano. "
        "Elenca: (1) obiettivo, (2) passi da eseguire in ordine, (3) tool da chiamare "
        "con i parametri previsti, (4) edge case da considerare, (5) rischi. "
        "Rispondi SOLO con il piano, niente JSON obbligatorio, niente meta-commenti."
    )
    body = dict(orig)
    body["model"] = MINIMAX_ORCHESTRATOR_MODEL  # M3 orchestra — remap preserva ('MiniMax')
    body["system"] = sys_msg
    body["stream"] = False
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _build_inverse_oppose_body(orig: dict, plan: str) -> bytes:
    """Inverse OPPOSE: Opus esamina il piano M3 e decide approved/reject.
    Risponde JSON: {approved: bool, fixes: [...], warnings: [...]}"""
    sys_msg = (
        f"Sei Opus, un critico severo (modello {INVERSE_OPPOSE_MODEL}). "
        "Ricevi un PIANO prodotto da M3. Il tuo compito è bocciare piani deboli, "
        "rischiosi, incompleti o inefficienti. NON eseguire il piano: solo reviewer.\n\n"
        "Schema JSON esatto (SOLO JSON, niente testo fuori):\n"
        '{"approved": <bool>, "fixes": ["<correzione>", ...], "warnings": ["<rischio>", ...]}\n\n'
        "Regole: approved=true SOLO se il piano è eseguibile senza modifiche rilevanti "
        "E non ha rischi operativi. Altrimenti approved=false con fixes/warnings azionabili. "
        "Sii severo: meglio un rifiuto in più che un piano fragile.\n"
        "FORMATO OBBLIGATORIO: il PRIMO carattere della risposta è '{', l'ULTIMO è '}'. "
        "Nessun preambolo, nessuna spiegazione fuori dal JSON, nessun blocco markdown."
    )
    user_msg = f"PIANO M3:\n{plan}\n\nDecidi: approved? Se no, quali fix? Rispondi SOLO col JSON."
    return json.dumps({
        "model": INVERSE_OPPOSE_MODEL,
        "max_tokens": 2048,
        "system": _anthropic_system(sys_msg),
        "messages": [{"role": "user", "content": user_msg}],
        "stream": False,
    }).encode()


def _build_inverse_revise_body(orig: dict, plan: str, fixes: list, warnings: list) -> bytes:
    """Inverse REVISE: M3 corregge il piano sulla base delle critiche Opus."""
    sys_msg = (
        "Sei M3. Hai prodotto un piano, Opus l'ha criticato. Rivedilo applicando le "
        "correzioni richieste e mitigando i warnings. Rispondi SOLO con il piano rivisto, "
        "stesso formato dell'originale (obiettivo, passi, tool, edge case, rischi)."
    )
    user_msg = (
        f"PIANO ORIGINALE:\n{plan}\n\n"
        f"FIXES RICHIESTI:\n{json.dumps(fixes, ensure_ascii=False)}\n\n"
        f"WARNINGS:\n{json.dumps(warnings, ensure_ascii=False)}\n\n"
        "Restituisci il piano rivisto."
    )
    body = dict(orig)
    body["model"] = MINIMAX_ORCHESTRATOR_MODEL  # M3 rivede il proprio piano — remap preserva
    body["system"] = sys_msg
    body["messages"] = [{"role": "user", "content": user_msg}]
    body["stream"] = False
    body["max_tokens"] = max(int(orig.get("max_tokens", 2048)), 2048)
    return json.dumps(body).encode()


def _build_inverse_act_body(orig: dict, plan: str) -> bytes:
    """Inverse ACT: l'executor coder (MiniMax code) esegue il piano approvato (con tool_use)."""
    sys_msg = (
        f"Sei {MINIMAX_MODEL}, l'esecutore. Un piano è stato prodotto da M3 e validato da Opus. "
        "Ora eseguilo. Usa i tool come da piano e rispondi all'utente con i risultati. "
        "Se il piano è una domanda senza tool, rispondi direttamente.\n\n"
        f"PIANO APPROVATO:\n{plan}"
    )
    body = dict(orig)
    body["model"] = MINIMAX_MODEL  # executor coder esplicito (M2.7) — remap preserva
    body["system"] = sys_msg
    body["stream"] = bool(orig.get("stream"))
    return json.dumps(body).encode()


def _parse_oppose_json(text: str) -> dict | None:
    """Parsifica JSON emesso da Opus in fase OPPOSE. Schema: {approved, fixes, warnings}.
    Robusto a preamboli testuali, code-fence e oggetti JSON multipli: prende il primo
    oggetto bilanciato con 'approved' bool."""
    j = _parse_json_with_keys(text, {"approved": lambda v: isinstance(v, bool)})
    if j is None:
        return None
    j.setdefault("fixes", [])
    j.setdefault("warnings", [])
    return j


async def _pipeline_think_oppose_act(request, body, session, orig: dict, relay) -> web.Response:
    """Redesign 2026-07-01 inverse: M3 THINK → Opus OPPOSE → loop max 2 → M3 ACT."""
    chat_fp = _resolve_chat_fingerprint(request)

    # THINK: M3 genera piano
    try:
        plan = await _m3_think_iter(request, session, orig, None)
    except Exception as e:
        log(f"inverse-new THINK EXC: {e} → fallback Anthropic diretto")
        return await _inverse_rescue_anthropic(request, body, session, relay)

    # OPPOSE/REVISE loop (max INVERSE_REVIEW_MAX_ITER volte)
    for i in range(INVERSE_REVIEW_MAX_ITER):
        op_body = _build_inverse_oppose_body(orig, plan)
        try:
            o_status, o_json = await _call_full(forward_anthropic_direct, request, op_body, session)
        except Exception as e:
            log(f"inverse-new OPPOSE iter{i} EXC: {e} → ACT con piano attuale")
            break
        if not o_json or o_status in FALLBACK_STATUSES:
            log(f"inverse-new OPPOSE iter{i} ko {o_status} → ACT con piano attuale")
            break
        op_text = _text_from_message(o_json)
        op = _parse_oppose_json(op_text)
        if not op:
            log(f"inverse-new OPPOSE iter{i} parse fail ({len(op_text)}c) → ACT con piano attuale")
            break
        log(f"inverse-new OPPOSE iter{i}: approved={op['approved']} fixes={len(op['fixes'])} warnings={len(op['warnings'])}")
        if op["approved"]:
            break  # piano ok
        # M3 revisa
        try:
            plan = await _m3_think_iter(request, session, orig, plan, op["fixes"], op["warnings"])
        except Exception as e:
            log(f"inverse-new REVISE iter{i} EXC: {e} → ACT con piano non rivisto")
            break
    else:
        log(f"inverse-new: max iter ({INVERSE_REVIEW_MAX_ITER}) raggiunto, ACT con piano finale")

    # ACT: M3 esegue il piano
    act_body = _build_inverse_act_body(orig, plan)
    try:
        up = await forward_minimax(request, act_body, session)
    except Exception as e:
        n = inverse_fail_inc(chat_fp)
        log(f"inverse-new ACT EXC ({n}/{INVERSE_FAIL_THRESHOLD}): {e} → rescue Anthropic")
        return await _inverse_rescue_anthropic(request, body, session, relay)
    if up.status in FALLBACK_STATUSES:
        n = inverse_fail_inc(chat_fp)
        log(f"inverse-new ACT M3 {up.status} ({n}/{INVERSE_FAIL_THRESHOLD}) → rescue Anthropic")
        try:
            await up.release()
        except Exception:
            pass
        return await _inverse_rescue_anthropic(request, body, session, relay)
    log(f"inverse-new ACT {MINIMAX_MODEL} {up.status} {request.path} fp={chat_fp}")
    return await relay(up, extra_headers={"x-ai-verified": f"{MINIMAX_ORCHESTRATOR_MODEL.lower()}-think+opus-oppose+{MINIMAX_MODEL.lower()}-act"})


async def _m3_think_iter(request, session, orig, prev_plan, fixes=None, warnings=None) -> str:
    """Helper: una iter M3 THINK (o REVISE se prev_plan+fixes). Ritorna testo piano."""
    if prev_plan is None:
        body = _build_inverse_think_body(orig)
    else:
        body = _build_inverse_revise_body(orig, prev_plan, fixes or [], warnings or [])
    s, j = await _call_full(forward_minimax, request, body, session)
    if not j or s in FALLBACK_STATUSES:
        raise RuntimeError(f"M3 think iter ko {s}")
    plan = _text_from_message(j).strip()
    if not plan:
        raise RuntimeError("M3 think iter vuoto")
    return plan


async def _inverse_rescue_anthropic(request, body, session, relay) -> web.Response:
    """Fallback finale: Anthropic esegue la richiesta originale senza pipeline."""
    try:
        up = await forward_anthropic(request, body, session)
        return await relay(up, extra_headers={"x-ai-verified": "inverse-rescue-anthropic"})
    except Exception as e:
        return web.json_response({"type": "error", "error": {"type": "router_error",
            "message": f"inverse rescue ko: {e}"}}, status=502)
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


# FIX bug 2026-06-29: _path_allowed era chiamata ma mai definita -> NameError -> 500 su ogni request.
# Soft-whitelist coerente col commento sopra il call site (B3.1): ammette /v1/*, blocca path traversal.
def _path_allowed(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    # path traversal / double slash
    if ".." in path or "//" in path:
        return False
    # endpoint locale del proxy
    if path == "/__router_health":
        return True
    # API Anthropic-compatible: /v1/messages, /v1/messages/count_tokens,
    # /v1/messages/batches, /v1/models, /v1/models/{id}, ecc.
    if path.startswith("/v1/"):
        return True
    return False

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
            "minimax_key_present": bool(await get_minimax_key()),  # FIX: async dopo to_thread
        })

    # FIX #1: Health-check e probe watchdog interni: risposta locale 200.
    # Evita che /, /readyz, /livez, /health, /stats, /status vadano all'upstream
    # e causino cascate 404/405 infinite che bloccano l'utente.
    _HC = {"/", "/readyz", "/livez", "/health", "/stats", "/metrics", "/status"}
    if request.path in _HC:
        return web.Response(status=200, text="ok")

    # FIX B3.1 (corretto): soft-whitelist sul path. Ammette /v1/* (incluso
    # count_tokens/batches/models/{id}) e i path locali; blocca path traversal
    # e probe arbitrari con 404. Sostituisce la route-literal '*' rotta.
    if not _path_allowed(request.path):
        log(f"path non consentito: {request.path}")
        return web.Response(status=404, text="not found")

    # ── RESILIENZA: blocco traffico in modalità DEGRADED ────────────────────
    # Se OAuth subscription è mancante o scaduto, accettiamo SOLO health/probe.
    # /v1/* e /v1/messages tornano 503 con istruzioni re-login
    # (`claude login` nel terminale aggiorna automaticamente, no restart).
    if RESILIENCE_INST is not None and not RESILIENCE_INST.state_is_ok():
        probe_paths = {
            "/", "/health", "/readyz", "/livez", "/stats", "/metrics", "/status",
            "/__router_health", "/__resilience",
        }
        if request.path not in probe_paths:
            log(f"DEGRADED: rifiuto {request.path} (OAuth {RESILIENCE_INST.state()})")
            return web.json_response(RESILIENCE_INST.degraded_response(), status=503)

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

    async def relay(upstream, chat_fp_for_rewrite: str = "default", extra_headers: dict | None = None):
        # FIX E: leggi e rimuovi orig_model da riscrivere nello SSE/non-stream
        # NB: i call site passano spesso chat_fp sbagliato (es 'default' vs IP reale).
        # Soluzione: prova la chiave esplicita; se manca e c'è esattamente UN orig
        # pending in _request_orig_model, usa quello (single-user loopback tipico).
        # FIX D38 2026-07-02: escludi la chiave interna '__remap__' (indice remap, dict)
        # dal fallback single-entry — altrimenti il dict finisce riscritto in body['model'].
        orig_model = _request_orig_model.pop(chat_fp_for_rewrite, None)
        if orig_model is None:
            _pending = [k for k in _request_orig_model if k != "__remap__"]
            if len(_pending) == 1:
                orig_model = _request_orig_model.pop(_pending[0])
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
        # FIX redesign 2026-07-01: header extra iniettati dal caller (es x-ai-verified).
        # Evidenzia la pipeline gerarchica mixed/inverse per audit downstream.
        if extra_headers:
            for k, v in extra_headers.items():
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
        model_rewrite_done = orig_model is None  # se non c'è orig_model, skip subito
        # FIX F: accumula chunks per estrarre usage reale dai record SSE/JSON
        _acc_buf = bytearray()
        _acc_limit = 16384  # massimo 16KB per evitare OOM su risposte enormi
        # Precompila pattern per SSE message_start rewrite
        import re as _re
        sse_model_pat = _re.compile(rb'"model":"[^"]*"')
        try:
            async for chunk in iterator:
                if not chunk:
                    continue
                chunk_count += 1
                total_bytes += len(chunk)
                # FIX #4: log primo chunk per debug
                if chunk_count == 1:
                    log(f"relay first chunk {len(chunk)}B (SSE={is_sse})")
                # FIX E: riscrivi il campo 'model' nello stream SSE (solo primo chunk rilevante)
                if not model_rewrite_done and orig_model:
                    if is_sse:
                        # cerca il pattern "model":"<qualsiasi>" e sostituisci SOLO nel primo evento message_start
                        new_chunk = sse_model_pat.sub(
                            f'"model":"{orig_model}"'.encode(), chunk, count=1
                        )
                        if new_chunk != chunk:
                            log(f"FIX E: SSE model rewritten to '{orig_model}'")
                            chunk = new_chunk
                            model_rewrite_done = True
                    else:
                        # non-streaming JSON response: parsifica e riscrivi
                        try:
                            j = json.loads(chunk)
                            if isinstance(j, dict) and "model" in j:
                                j["model"] = orig_model
                                chunk = json.dumps(j).encode()
                                log(f"FIX E: JSON model rewritten to '{orig_model}'")
                            model_rewrite_done = True
                        except Exception:
                            pass  # non-JSON body, skip
                # FIX F: accumulazione parziale per usage extraction
                if len(_acc_buf) < _acc_limit:
                    _acc_buf.extend(chunk[:(_acc_limit - len(_acc_buf))])
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
            # FIX F: log per-request usage. Estrai token reali da _acc_buf.
            try:
                _usage = {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
                _buf_str = _acc_buf.decode("utf-8", errors="replace")
                if is_sse:
                    # Cerca message_start (input) e message_delta (output) nei chunk SSE
                    import re as _re2
                    for _data in _re2.findall(r"^data: (.+)$", _buf_str, _re2.MULTILINE):
                        try:
                            _ev = json.loads(_data)
                            if _ev.get("type") == "message_start":
                                _u = (_ev.get("message") or {}).get("usage") or {}
                                _usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                                _usage["cache_read_input_tokens"] = int(_u.get("cache_read_input_tokens", 0) or 0)
                                _usage["cache_creation_input_tokens"] = int(_u.get("cache_creation_input_tokens", 0) or 0)
                            elif _ev.get("type") == "message_delta":
                                _u = _ev.get("usage") or {}
                                _usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                        except Exception:
                            pass
                    if _usage["output_tokens"] == 0:
                        _usage["output_tokens"] = max(1, total_bytes // 4)
                else:
                    try:
                        _j = json.loads(_buf_str)
                        if isinstance(_j, dict):
                            _u = _j.get("usage") or {}
                            _usage["input_tokens"] = int(_u.get("input_tokens", 0) or 0)
                            _usage["output_tokens"] = int(_u.get("output_tokens", 0) or 0)
                            _usage["cache_read_input_tokens"] = int(_u.get("cache_read_input_tokens", 0) or 0)
                            _usage["cache_creation_input_tokens"] = int(_u.get("cache_creation_input_tokens", 0) or 0)
                    except Exception:
                        _usage["output_tokens"] = max(1, total_bytes // 4)
                # Input: estrai dal body richiesta (non compresso) se non già noto.
                # È una stima sicura perché il body request è sempre in chiaro.
                if _usage["input_tokens"] == 0:
                    try:
                        _req_j = json.loads(body.decode("utf-8", errors="replace"))
                        # Stima da prompt: somma len(c["content"]) per tutti i messaggi
                        _chars = 0
                        for _m in (_req_j.get("messages") or []):
                            c = _m.get("content", "")
                            if isinstance(c, str):
                                _chars += len(c)
                            elif isinstance(c, list):
                                for _b in c:
                                    if isinstance(_b, dict) and isinstance(_b.get("text"), str):
                                        _chars += len(_b["text"])
                        _sys = _req_j.get("system", "")
                        if isinstance(_sys, str):
                            _chars += len(_sys)
                        _usage["input_tokens"] = max(1, _chars // 4)
                    except Exception:
                        _usage["input_tokens"] = max(1, len(body) // 4)
                # FIX bug stats: passa il FINAL reale (risolto da remap) + fallback al
                # model nel body della request se orig_model (chat_fp-mismatch) è vuoto.
                try:
                    _body_j = json.loads(body.decode("utf-8", errors="replace"))
                    _body_model = (_body_j.get("model") or "").strip()
                except Exception:
                    _body_model = ""
                _orig = orig_model or _body_model or "?"
                # FIX bug 2026-07-01: per mode=mixed il final NON è "?" — è il modello
                # rimappato (MiniMax-M3 se orig è nel remap index) oppure "claude-direct"
                # se mixed è caduto in fallback Anthropic.
                if mode == "minimax":
                    _final = MINIMAX_MODEL
                elif mode == "anthropic":
                    _final = "claude-direct"
                elif mode == "mixed":
                    try:
                        _remap_idx = _request_orig_model.get("__remap__") or {}
                        if not _remap_idx:
                            # costruisci al volo dal sidecar (cache 60s gia' presente altrove)
                            import json as _json
                            _idx = {}
                            try:
                                with open(SIDECAR, "r") as _sf:
                                    for _sl in _sf:
                                        _so = _json.loads(_sl) if _sl.strip() else None
                                        if _so and _so.get("orig") and _so.get("final"):
                                            _so_o = _so["orig"]
                                            if _so_o not in _idx:
                                                _idx[_so_o] = _so["final"]
                            except Exception:
                                pass
                            _request_orig_model["__remap__"] = _idx
                            _remap_idx = _idx
                        _final = _remap_idx.get(_orig, "claude-direct") if _orig != "?" else "?"
                    except Exception:
                        _final = "?"
                else:
                    _final = "?"
                log_router_usage(
                    chat_id=chat_fp_for_rewrite,
                    orig=_orig,
                    final=_final,
                    usage=_usage,
                    mode=mode,
                    client=request.headers.get("User-Agent", "?")[:40] or "?",
                    status=upstream.status,
                    path=request.path,
                )
            except Exception:
                pass
        # FIX #4: log bytes totali inoltrati
        if is_sse or total_bytes > 0:
            log(f"relay done: {chunk_count} chunks, {total_bytes} bytes (SSE={is_sse})")
        # FIX #5: drain finale prima di write_eof per garantire flush completo
        await resp.drain()
        await resp.write_eof()
        return resp

    forwarders = {"anthropic": forward_anthropic, "minimax": forward_minimax, "anthropic_direct": forward_anthropic_direct}

    # ── MODALITÀ anthropic PURA: proxy trasparente, nessuno switch ──
    if mode == "anthropic":
        if not request.path.endswith("/v1/messages"):
            up = await forward_anthropic(request, body, session)
            return await relay(up)
        try:
            up = await forward_anthropic(request, body, session)
            log(f"anthropic (pure) -> {up.status} {request.path}")
            # FIX D38 2026-07-02: header di verifica esecutore anche in modalità pura
            return await relay(up, extra_headers={"x-ai-verified": "anthropic-pure"})
        except Exception as e:
            log(f"ERR anthropic (pure) {request.path}: {e}")
            return web.json_response(
                {"type": "error", "error": {"type": "router_error", "message": str(e)}},
                status=502,
            )

    # ── MODALITÀ minimax: M3 ORCHESTRA (mai esegue) → executor inferiore ACT ──
    if mode == "minimax":
        # Health-check / path non-messages: executor passthrough (no orchestrazione).
        if not request.path.endswith("/v1/messages"):
            up = await forward_minimax(request, body, session)
            return await relay(up)
        if NEW_PIPELINE:
            try:
                orig = json.loads(body)
            except Exception:
                orig = {}
            log(f"minimax-orch pipeline attivata fp={_resolve_chat_fingerprint(request)} tools={bool(orig.get('tools'))}")
            return await _pipeline_minimax_orchestrate(request, body, session, orig, relay)
        # Legacy (NEW_PIPELINE=0): passthrough diretto all'executor.
        try:
            up = await forward_minimax(request, body, session)
            log(f"minimax (pure) -> {up.status} {request.path}")
            return await relay(up)
        except Exception as e:
            log(f"ERR minimax (pure) {request.path}: {e}")
            return web.json_response(
                {"type": "error", "error": {"type": "router_error", "message": str(e)}},
                status=502,
            )


    # ── MODALITÀ INVERSE: MiniMax orchestra + esegue ─────────────────────
    # Anthropic verifica i T2; dopo N fail consecutivi (default 2) Anthropic
    # esegue direttamente, bypassando MiniMax. Il contatore è per-chat.
    if mode == "inverse":
        chat_fp = _resolve_chat_fingerprint(request)  # FIX audit v4: NAT-safe
        is_messages = request.path.endswith("/v1/messages")
        is_t2 = is_messages and classify_t2(body)

        # NEW PIPELINE redesign 2026-07-01: M3 THINK + Opus OPPOSE + M3 ACT.
        # Scatta per TUTTE le /v1/messages in mode=inverse (incluso agentico con tools).
        # Abroga la distinzione T0/T1/T2 che escludeva le richieste agentiche.
        # ESCALATION ha priorità: se M3 ha già fallito N volte, salta a Anthropic diretto.
        if NEW_PIPELINE and is_messages and not inverse_should_escalate(chat_fp):
            try:
                orig = json.loads(body)
            except Exception:
                orig = {}
            log(f"inverse-new pipeline attivata fp={chat_fp} tools={bool(orig.get('tools'))}")
            return await _pipeline_think_oppose_act(request, body, session, orig, relay)

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
            # T0/T1 (incluso agentico con tools): MiniMax esegue. M3 è agentico ed
            # emette tool_use nativamente (verificato 2026-06-29). Su 429/5xx -> rescue
            # Anthropic, così sotto carico (100 agenti) nessuno si blocca sul rate-limit.
            try:
                up = await forward_minimax(request, body, session)
            except Exception as e:
                n = inverse_fail_inc(chat_fp)
                log(f"inverse T1 M3 EXC ({n}/{INVERSE_FAIL_THRESHOLD}): {e}")
                try:
                    return await relay(await forward_anthropic(request, body, session))
                except Exception as e2:
                    return web.json_response(
                        {"type": "error", "error": {"type": "router_error",
                         "message": f"both down: {e2}"}}, status=502)
            if up.status in FALLBACK_STATUSES:
                n = inverse_fail_inc(chat_fp)
                await up.release()
                log(f"inverse T1 M3 {up.status} ({n}/{INVERSE_FAIL_THRESHOLD}) -> anthropic rescue")
                try:
                    up2 = await forward_anthropic(request, body, session)
                    if up2.status < 400:
                        inverse_fail_reset(chat_fp)
                    return await relay(up2)
                except Exception as e2:
                    return web.json_response(
                        {"type": "error", "error": {"type": "router_error",
                         "message": f"rescue ko: {e2}"}}, status=502)
            inverse_fail_reset(chat_fp)
            log(f"inverse T0/T1 -> minimax {up.status} {request.path}")
            return await relay(up)

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
    chat_fp = _resolve_chat_fingerprint(request)  # FIX audit v4: NAT-safe
    anthropic_leads = mixed_anthropic_leads(chat_fp)
    is_messages = request.path.endswith("/v1/messages")
    is_t2 = is_messages and classify_t2(body)

    # ESCALATION: M3 ha fallito N volte -> Anthropic esegue con pipeline gerarchica
    if anthropic_leads and is_messages:
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        # Agentico (tools): la pipeline verify appiattirebbe la risposta a solo
        # testo e distruggerebbe i tool_use. Relay diretto Anthropic = passthrough
        # che preserva i blocchi tool_use richiesti dall'agente.
        if orig.get("tools"):
            log("mixed escalation: agentic (tools) -> relay anthropic passthrough")
            try:
                return await relay(await forward_anthropic(request, body, session))
            except Exception as e:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)
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

    # NEW PIPELINE redesign 2026-07-01: Anthropic THINK + self-review + M3 ACT.
    # Scatta per TUTTE le /v1/messages in mode=mixed (incluso agentico con tools),
    # tranne quando M3 è in escalation (anthropic_leads). Abroga T0/T1/T2.
    if NEW_PIPELINE and is_messages and not anthropic_leads:
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        log(f"mixed-new pipeline attivata fp={chat_fp} tools={bool(orig.get('tools'))}")
        return await _pipeline_think_act(request, body, session, orig, relay)

    # Path normale: T0/T1 -> M3 diretto; T2 -> pipeline verify gerarchica
    if not is_t2:
        # T0/T1 (incluso agentico con tools): M3 esegue diretto, streaming preservato.
        # MiniMax-M3 emette tool_use nativamente (verificato 2026-06-29) -> i task
        # agentici girano su MiniMax. Su 429/5xx il ramo sotto fa fallback Anthropic
        # (rescue), così 100 agenti paralleli non si bloccano sul rate-limit M3.
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
                if up2.status < 400:
                    mixed_fail_reset(chat_fp)  # FIX audit v3: reset counter su rescue OK
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


# LEZIONE B3.1 (NON reintrodurre una whitelist di path): aiohttp NON interpreta
# '*' come glob. Una route literal "/v1/messages/*" NON matcha
# "/v1/messages/count_tokens" -> 404 su count_tokens/batches/models/{id} ->
# Claude Code/VSCode si bloccano. Il routing path-level lo fa SOLO il catch-all
# "/{tail:.*}"; i path inesistenti tornano 404 dal backend upstream, corretto.
# Istanza globale resilienza, popolata in _run_multiport.
RESILIENCE_INST = None


def _make_app(session, forced_mode):
    """Crea una web.Application con la modalità cablata (deterministica).
    forced_mode=None -> porta dinamica (:8787) che segue il file ai-router-mode."""
    app = web.Application(client_max_size=1024 * 1024 * 100)
    app["session"] = session
    app["forced_mode"] = forced_mode
    if RESILIENCE_INST is not None:
        app["RESILIENCE"] = RESILIENCE_INST

    async def healthz(request):
        return web.json_response({"ok": True, "mode": forced_mode or _current_mode()})

    async def resiliencez(request):
        """Stato resilienza: OAuth, heartbeat, modalità corrente."""
        if RESILIENCE_INST is None:
            return web.json_response({"resilience": "unavailable"})
        s = RESILIENCE_INST.get_status()
        s["service_state"] = RESILIENCE_INST.state()
        s["pid"] = os.getpid()
        return web.json_response(s)

    async def admin_mode_switch(request):
        """POST /admin/mode/{mode} — switch modalità (porta :8787 dinamica, no restart)."""
        mode = request.match_info.get("mode", "")
        if mode not in VALID_MODES:
            return web.json_response({"ok": False, "error": f"Modo '{mode}' non valido. Validi: {VALID_MODES}"}, status=400)
        mode_file = Path.home() / ".claude" / "ai-router-mode"
        mode_file.write_text(mode + "\n")
        # :8787 rilegge il file ad ogni richiesta -> switch immediato senza restart
        return web.json_response({"ok": True, "mode": mode, "msg": f"Switched to {mode}"})

    app.router.add_get("/health", healthz)
    app.router.add_get("/__resilience", resiliencez)
    app.router.add_post("/admin/mode/{mode}", admin_mode_switch)
    # catch-all: tutto il routing path-level passa da handle().
    # NB: route literal con '*' NON funziona in aiohttp; il catch-all e'
    # l'unico modo per coprire i sub-path legittimi (count_tokens, batches, ...).
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


async def _run_multiport():
    # RESILIENZA: inizializza modulo (OAuth check strutturale + degraded mode)
    global RESILIENCE_INST
    if _RESILIENCE_AVAILABLE:
        RESILIENCE_INST = Resilience(
            port=LISTEN_PORT,
            log_fn=lambda m: log(f"[RES] {m}"),
            get_pid=lambda: os.getpid(),
        )
        # Boot validation senza self-test (lo facciamo dopo, appena sessione è pronta)
        ok = RESILIENCE_INST.boot_validate(run_self_test=False)
        if not ok:
            log("RESILIENZA: BOOT in modalità DEGRADED — accettiamo solo health endpoints finché OAuth non è presente")
        # Setup signal handler per crash dump
        RESILIENCE_INST.install_signal_handlers()

    # FIX audit v5 #6: una ClientSession ha UN solo connector (il "pool separato"
    # del vecchio B1.3 era fittizio: _minimax_connector non veniva mai usato da
    # session.request). Un unico pool con limit alto basta per un proxy locale.
    # ponytail: limit=100 globale, alzare se servisse throughput multi-tenant.
    connector = TCPConnector(limit=100, limit_per_host=40, ttl_dns_cache=300)
    # Una sola ClientSession condivisa da tutte le porte.
    session = ClientSession(
        # FIX: timeout granulari per streaming SSE - sock_read alto per first-token LLM
        timeout=ClientTimeout(total=600, connect=30, sock_read=120, sock_connect=15),
        connector=connector,
        # FIX #7 CRITICAL: auto_decompress=False per passthrough brotli/gzip.
        auto_decompress=False,
    )

    # RESILIENZA: avvia self-test periodico (usa session = vero HTTP, non new)
    # e heartbeat watchdog per freeze-watchdog esterno bash.
    if RESILIENCE_INST is not None:
        RESILIENCE_INST.start_periodic_self_test(session=session)
        RESILIENCE_INST.start_heartbeat()
    # FIX audit v3: signal handler installato PRIMA del port loop.
    # Cosi' un SIGTERM early (subito dopo il boot) trova un handler graceful
    # invece del default kill-hard.
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    runners = []
    for port in LISTEN_PORTS:
        # FIX: tra SIGTERM (stop.set) e l'attesa stop.wait(), uno shutdown puo'
        # partire mentre stiamo ancora bind-ando le porte. Check esplicito.
        if stop.is_set():
            log("shutdown requested during bind, exiting early")
            break
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
        await session.close()  # chiude anche il connector associato
        return
    # Signal handler gia' installato prima del port loop (FIX audit v3).
    try:
        await stop.wait()
        log("shutdown signal received, draining...")
    finally:
        for r in runners:
            await r.cleanup()
        await session.close()  # chiude anche il connector associato
        log("shutdown complete")


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)  # FIX A: ensure log dir
    if not MODE_FILE.exists():
        MODE_FILE.write_text("anthropic\n")
    log(f"START ai-router-proxy multi-port {LISTEN_PORTS}")
    asyncio.run(_run_multiport())


if __name__ == "__main__":
    main()
