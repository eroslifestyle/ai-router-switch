#!/usr/bin/env python3
"""
AI Router Proxy — switcher davanti a Claude Code.

Quattro modalità (file ~/.claude/ai-router-mode):
  - anthropic   : tutto verso headroom#1 (8787 -> api.anthropic.com)
  - minimax     : tutto verso headroom#2 (8790 -> api.minimax.io/anthropic)
  - mixed       : Anthropic primario; su 429/5xx/errore -> fallback MiniMax (bidir)
  - interactive : MiniMax genera (veloce/economico). Classificatore T2 automatico:
                  i task CRITICI vengono verificati/corretti da Opus (Claude) via
                  headroom#1. Se Opus ko -> bozza MiniMax marcata 'non verificata'
                  (resilienza). Task non-critici -> MiniMax diretto (streaming).

Claude Code punta qui: ANTHROPIC_BASE_URL=http://127.0.0.1:8789
Gestisce streaming SSE. Non modifica headroom né LiteLLM.
"""
import asyncio
import json
import os
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
VALID_MODES = ("anthropic", "minimax", "mixed", "interactive")

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


def breaker_is_open(backend: str) -> bool:
    """True se il backend è in cooldown (da saltare)."""
    return time.time() < _breaker.get(backend, {}).get("open_until", 0)


def breaker_fail(backend: str):
    b = _breaker.setdefault(backend, {"fails": 0, "open_until": 0})
    b["fails"] += 1
    if b["fails"] >= BREAKER_FAIL_MAX:
        b["open_until"] = time.time() + BREAKER_COOLDOWN
        b["fails"] = 0
        log(f"breaker OPEN {backend} per {BREAKER_COOLDOWN}s")


def breaker_ok(backend: str):
    b = _breaker.setdefault(backend, {"fails": 0, "open_until": 0})
    b["fails"] = 0
    b["open_until"] = 0

_minimax_key_cache = {"key": None, "ts": 0}


def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# Porte con modalità FISSA (per usare modalità diverse su sessioni diverse).
# :8787 = dinamica (segue ai-mode / file). Le altre forzano la modalità.
# Porte fisse su range libero (8782/8783 erano occupate da audio LiteLLM).
PORT_MODE = {
    8771: "anthropic",
    8772: "minimax",
    8773: "mixed",
    8774: "interactive",
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
    (_re.compile(r"interattiv|interactive", _re.I), "interactive"),
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


def remap_body_for_minimax(raw: bytes) -> bytes:
    """Riscrive il model Claude -> MiniMax-M3."""
    try:
        data = json.loads(raw)
        orig = data.get("model", "")
        if orig and not orig.startswith("MiniMax"):
            data["model"] = MINIMAX_MODEL
        return json.dumps(data).encode()
    except Exception:
        return raw


# ── Forwarding ────────────────────────────────────────────────────────────
HOP_HEADERS = {
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade",
}


async def forward_anthropic(request, body, session):
    url = ANTHROPIC_UPSTREAM + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    return await session.request(
        request.method, url, data=body, headers=headers, allow_redirects=False
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
    """Chiamata non-streaming: ritorna (status, json|None)."""
    nb, _ = _force_no_stream(body)
    up = await forward_fn(request, nb, session)
    status = up.status
    raw = await up.read()
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


def _sse_from_message(j: dict, verified: str) -> bytes:
    """Costruisce uno stream SSE Anthropic-compat da una risposta completa."""
    text = _text_from_message(j)
    mid = j.get("id", "msg_router")
    model = j.get("model", "unknown")
    usage = j.get("usage", {})
    msg_start = {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": usage}}
    parts = [
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
    return "".join(parts).encode()


async def handle(request):
    mode = get_mode(request)
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
                    _resp = web.StreamResponse(status=200)
                    _resp.headers["content-type"] = "text/event-stream"
                    await _resp.prepare(request)
                    await _resp.write(_sse_from_message(_msg, "router"))
                    await _resp.write_eof()
                    return _resp
                return web.json_response(_msg)
            # nessun comando: applica eventuale marca-chat (D5)
            _cm = get_chat_mode(_fp)
            if _cm in VALID_MODES:
                mode = _cm
        except Exception:
            pass

    session = request.app["session"]

    async def relay(upstream):
        resp = web.StreamResponse(status=upstream.status)
        for k, v in upstream.headers.items():
            if k.lower() in HOP_HEADERS or k.lower() == "content-encoding":
                continue
            resp.headers[k] = v
        await resp.prepare(request)
        async for chunk in upstream.content.iter_any():
            await resp.write(chunk)
        await resp.write_eof()
        return resp

    forwarders = {"anthropic": forward_anthropic, "minimax": forward_minimax}

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

    # ── MODALITÀ INTERACTIVE: MiniMax genera, Opus verifica i task T2 critici ──
    if mode == "interactive":
        # solo /v1/messages è soggetto a verifica; altri path -> minimax passthrough
        is_messages = request.path.endswith("/v1/messages")
        is_t2 = is_messages and classify_t2(body)

        if not is_t2:
            # task non critico (o non-messages): MiniMax diretto, streaming preservato
            try:
                up = await forward_minimax(request, body, session)
                log(f"interactive T0/T1 -> minimax {up.status} {request.path}")
                return await relay(up)
            except Exception as e:
                log(f"ERR interactive T1 {request.path}: {e}")
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": str(e)}}, status=502)

        # ---- T2 critico: genera (MiniMax) -> verifica (Opus) ----
        try:
            orig = json.loads(body)
        except Exception:
            orig = {}
        question = extract_last_user_text(orig)
        wants_stream = bool(orig.get("stream"))

        # 1) bozza MiniMax (non-stream)
        try:
            gen_status, gen_json = await _call_full(forward_minimax, request, body, session)
        except Exception as e:
            gen_status, gen_json = 0, None
            log(f"interactive T2 gen EXC: {e}")
        if not gen_json or gen_status >= 400:
            log(f"interactive T2: gen MiniMax fallita ({gen_status}) -> fallback anthropic")
            try:
                up = await forward_anthropic(request, body, session)
                return await relay(up)
            except Exception as e:
                return web.json_response(
                    {"type": "error", "error": {"type": "router_error",
                     "message": f"gen+fallback ko: {e}"}}, status=502)
        draft = _text_from_message(gen_json)

        # 2) verifica Opus (non-stream) via headroom#1
        verified_json = None
        vbody = _build_verify_body(orig, question, draft)
        try:
            v_status, v_json = await _call_full(forward_anthropic, request, vbody, session)
            if v_json and v_status < 400 and _text_from_message(v_json):
                verified_json = v_json
                log(f"interactive T2: VERIFICATO da Opus ({v_status})")
            else:
                log(f"interactive T2: Opus non disponibile ({v_status}) -> bozza MiniMax")
        except Exception as e:
            log(f"interactive T2: Opus EXC ({e}) -> bozza MiniMax")

        final_json = verified_json or gen_json
        verified_flag = "opus" if verified_json else "unavailable"

        # 3) risposta al client (SSE sintetico se chiedeva stream)
        if wants_stream:
            payload = _sse_from_message(final_json, verified_flag)
            resp = web.StreamResponse(status=200)
            resp.headers["content-type"] = "text/event-stream"
            resp.headers["x-ai-verified"] = verified_flag
            await resp.prepare(request)
            await resp.write(payload)
            await resp.write_eof()
            return resp
        return web.json_response(
            final_json, headers={"x-ai-verified": verified_flag})

    # ── MODALITÀ MIXED: fallback BIDIREZIONALE (primario -> secondario) ──
    # Primario configurabile (default anthropic); il secondario è l'altro.
    primary = os.environ.get("AIROUTER_MIXED_PRIMARY", "anthropic").lower()
    if primary not in forwarders:
        primary = "anthropic"
    secondary = "minimax" if primary == "anthropic" else "anthropic"

    # Circuit breaker (D15): se il primario è in cooldown, prova prima il secondario.
    order = [primary, secondary]
    if breaker_is_open(primary) and not breaker_is_open(secondary):
        order = [secondary, primary]
        log(f"mixed: {primary} in cooldown -> provo prima {secondary}")

    last_err = None
    for stage, name in enumerate(order):
        tag = "primary" if stage == 0 else "FALLBACK"
        if breaker_is_open(name) and stage == 0:
            log(f"mixed: {name} OPEN, salto al fallback")
            continue
        try:
            up = await forwarders[name](request, body, session)
        except Exception as e:
            last_err = str(e)
            breaker_fail(name)
            log(f"mixed {tag} {name} EXC {request.path}: {e}")
            continue  # eccezione -> prova l'altro
        if up.status in FALLBACK_STATUSES and stage == 0:
            breaker_fail(name)
            log(f"mixed {tag} {name} {up.status} -> switch a {order[1]} {request.path}")
            await up.release()
            continue  # errore retryable sul primario -> switch al secondario
        if up.status < 400:
            breaker_ok(name)
        log(f"mixed {tag} {name} {up.status} {request.path}")
        return await relay(up)

    # entrambi i backend giù
    log(f"mixed: ENTRAMBI giù {request.path} ({last_err})")
    return web.json_response(
        {"type": "error", "error": {"type": "router_error",
         "message": f"both backends unavailable: {last_err}"}},
        status=502,
    )


def _make_app(session, forced_mode):
    """Crea una web.Application con la modalità cablata (deterministica).
    forced_mode=None -> porta dinamica (:8787) che segue il file ai-router-mode."""
    app = web.Application(client_max_size=1024 * 1024 * 100)
    app["session"] = session
    app["forced_mode"] = forced_mode
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


async def _run_multiport():
    # Una sola ClientSession condivisa da tutte le porte.
    session = ClientSession(
        timeout=ClientTimeout(total=None, connect=30),
        connector=TCPConnector(limit=0, ttl_dns_cache=300),
    )
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
