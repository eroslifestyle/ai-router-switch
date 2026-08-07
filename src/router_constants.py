# ~130 lines
"""Router constants extracted from ai-router-proxy.py (~lines 386-476 + scattered scalars)."""
import os

import paths

# ── Network ────────────────────────────────────────────────────────────────────
LISTEN_HOST = os.environ.get("AIROUTER_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("AIROUTER_PORT", "8787"))
ANTHROPIC_UPSTREAM = os.environ.get("AIROUTER_ANTHROPIC_UPSTREAM", "https://api.anthropic.com")
MINIMAX_UPSTREAM = os.environ.get("AIROUTER_MINIMAX_UPSTREAM", "https://api.minimaxi.chat/anthropic")
MINIMAX_MODEL = os.environ.get("AIROUTER_MINIMAX_MODEL", "MiniMax-M3")
# MINIMAX_ORCHESTRATOR_MODEL e MINIMAX_EXECUTORS rimossi il 2026-08-07: erano
# residui della pipeline THINK/ACT tolta dal proxy il 2026-07-25, mai letti da
# nessuno da allora. Il ruolo lo decide role_routing.resolve_route.
# NEW_PIPELINE rimosso il 2026-08-06: era il flag che sceglieva fra la pipeline
# THINK/ACT/VERIFY e il path legacy. La pipeline non esiste piu' dal 2026-07-25,
# quindi il flag non aveva piu' un lettore: solo la propria definizione.
# VERIFY_MODEL rimosso: il router non sceglie il modello Anthropic (gerarchia = config globale).
ANTHROPIC_DIRECT_URL = os.environ.get("AIROUTER_ANTHROPIC_DIRECT", "https://api.anthropic.com")
MINIMAX_GENERATIVE_HOST = os.environ.get(
    "AIROUTER_MINIMAX_GENERATIVE_HOST", "https://api.minimaxi.chat"
)

# ── GLM backend (deferred import in proxy) ────────────────────────────────────
try:
    import glm_backend as _glm
    import peak_scheduler as _peak
    GLM_AVAILABLE = True
except Exception:
    _glm = None
    _peak = None
    GLM_AVAILABLE = False

# -- Qwen backend (Alibaba Model Studio, deferred import come GLM) --
try:
    import qwen_backend as _qwen
    QWEN_AVAILABLE = True
except Exception:
    _qwen = None
    QWEN_AVAILABLE = False

# ── Paths ─────────────────────────────────────────────────────────────────────
# Risolti da paths.py: su questa macchina ~/.claude esiste e vince, quindi i
# valori sono gli stessi di prima; altrove seguono XDG / APPDATA / Library.
MODE_FILE = paths.mode_file()
# KEY_FILE rimossa il 2026-08-07: le chiavi passano da secrets_provider dal
# 2026-08-06, e nessuno leggeva piu' questa costante.
LOG_FILE = paths.log_file("ai-router.log")
# SIDECAR (router-model-map.jsonl) rimossa il 2026-08-07: l'unico scrittore era
# _log_original_model, senza chiamanti dal refactor a tunnel del 2026-07-25, e
# l'unico lettore era il relay, che ricostruiva da quel file fermo una mappa
# orig->final poi sostituita da resolve_route. Il dato vive in router-usage.jsonl.
USAGE_SIDECAR = paths.log_file("router-usage.jsonl")
CHAT_STORE = paths.chat_store()

TRIM_STATE_DIR = paths.ensure_dir(paths.trim_state_dir())

# ── Limits & constants ─────────────────────────────────────────────────────────
MINIMAX_CONTEXT_BYTE_LIMIT = int(os.environ.get("AIROUTER_MINIMAX_CONTEXT_LIMIT", "750000"))
MINIMAX_RATE_LIMITS = {
    "MiniMax-M3": (200, 10_000_000),
    "MiniMax-M2.7": (500, 20_000_000),
    "MiniMax-M2.7-highspeed": (500, 20_000_000),
    "MiniMax-M2.5": (500, 20_000_000),
    "MiniMax-M2.5-highspeed": (500, 20_000_000),
    "MiniMax-M2": (500, 20_000_000),
}
MINIMAX_RATE_LIMITS_DEFAULT = (200, 10_000_000)
MINIMAX_SAFETY = float(os.environ.get("AIROUTER_MINIMAX_SAFETY", "0.8"))
MINIMAX_RETRY_CAP_SEC = float(os.environ.get("AIROUTER_MINIMAX_RETRY_CAP_SEC", "90"))
MINIMAX_CONCURRENCY = int(os.environ.get("AIROUTER_MINIMAX_SEMAPHORE", "8"))
MINIMAX_BACKOFF_STEPS = (5, 10, 20, 40, 60)
MINIMAX_ALERTS_LOG = str(paths.log_file("minimax-alerts.log"))
# MINIMAX_RETRY_BUDGET_SHORT rimossa il 2026-08-07 con _fwd_minimax_short, il
# suo unico lettore: il wrapper serviva alle chiamate non-streaming della
# pipeline, che non esiste piu' dal 2026-07-25.
# THINK_MODEL / THINK_MODEL_ANTHROPIC rimossi: il router usa SEMPRE orig_model
# (il modello scelto dalla config globale del client). La gerarchia THINK/exec/escalation
# vive SOLO nella config globale ~/.claude/CLAUDE.md, mai nel router (tubo trasparente).
# Timeout di lettura per le richieste NON-streaming (2026-07-28).
# La sessione condivisa usa sock_read=120: su una risposta non-streaming l'upstream
# non manda un solo byte finche' la generazione non e' conclusa, quindi quei 120s
# smettono di proteggere dagli stall e diventano un tetto sulla DURATA della
# generazione -> 502 "Timeout on reading data from socket" (59 casi nei log fra il
# 26 e il 28/07). Misura di riferimento: una generazione MiniMax da 32.000 max_tokens
# impiega 144s, quindi 600s lascia ~4x di margine. Lo streaming NON usa questo valore:
# li' i chunk arrivano di continuo e sock_read=120 fa il suo mestiere.
NON_STREAM_SOCK_READ_SEC = float(
    os.environ.get("AIROUTER_NON_STREAM_SOCK_READ_SEC", "600"))
# TRIM_TARGET_BYTES, TRIM_MIN_MESSAGES e SUMMARY_BUDGET rimossi il 2026-08-07:
# politiche superate. Lo shrink punta a MINIMAX_CONTEXT_BYTE_LIMIT pieno
# (pipeline_minimax.py:71) e il budget del riassunto lo calcola ogni call site.
CHAT_TTL_DAYS = 7
CHAT_MAX_ENTRIES = 10000

# ── Hop-by-hop headers ─────────────────────────────────────────────────────────
HOP_HEADERS = frozenset({
    "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade",
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    "x-forwarded-port", "x-real-ip", "via", "forwarded",
})

# ── Valid modes ────────────────────────────────────────────────────────────────
VALID_MODES = ("anthropic", "minimax", "mix-am", "mix-ag", "mix-gm", "glm", "qwen", "mix-al", "local")

# ── Port mode map ─────────────────────────────────────────────────────────────
PORT_MODE = {
    8771: "anthropic",
    8772: "minimax",
    8773: "mix-am",
    8775: "glm",
    8776: "mix-gm",
    8777: "mix-ag",
    8778: "qwen",
    8774: "mix-al",   # sandbox: prova mix-al senza toccare :8787
    8779: "local",   # sandbox: prova local (pura, tutto sul modello locale) senza toccare :8787
}
_pm_override = os.environ.get("AIROUTER_PORT_MODE_JSON", "").strip()
if _pm_override:
    try:
        import json as _json
        PORT_MODE = {int(k): v for k, v in _json.loads(_pm_override).items() if v in VALID_MODES}
    except Exception:
        pass
LISTEN_PORTS = [LISTEN_PORT] + list(PORT_MODE.keys())

# ── Fallback statuses ─────────────────────────────────────────────────────────
FALLBACK_STATUSES = {401, 403, 404, 408, 409, 413, 429, 500, 502, 503, 504, 529}
# 429 è ESCLUSO di proposito per MiniMax: un rate limit non è un guasto del
# provider, è pacing. Su 429 si aspetta il retry-after e si ritenta lo stesso
# upstream — dirottare altrove moltiplicherebbe le richieste proprio mentre il
# provider ci sta chiedendo di rallentare. (Il commento che diceva il contrario
# era il finding audit 2026-07-17, MEDIA.)
MINIMAX_FALLBACK_STATUSES = FALLBACK_STATUSES - {429}

# ── Marker context-exceeded ───────────────────────────────────────────────────
# Questi marker riconoscono nel corpo di un errore 400 il caso in cui il prompt
# supera la finestra di contesto. Esistevano in quattro copie divergenti, due
# vive (dentro forward_anthropic e in ContextManager._is_context_error) e due
# usate solo dai test (providers.base e pipeline_anthropic). La lista di
# forward_anthropic non conteneva "too long", quindi non riconosceva l'errore
# più frequente di Anthropic, che nei log di produzione si presenta come "prompt
# is too long: 232125 tokens > 200000 maximum": dieci occorrenze su dodici. Il
# retry che rimuove le immagini e ritenta non scattava mai per quel caso. Questa
# è la lista unica, unione di tutte e quattro, in byte minuscoli perché il
# confronto avviene sul corpo grezzo già portato a minuscolo.
CONTEXT_EXCEED_MARKERS = (
    b"context window",
    b"context_window_exceeded",
    b"reached its context",
    b"context_exceeded",
    b"context limit",
    b"context_length",
    b"exceeds limit",
    b"maximum context",
    b"too long",
    b"2013",
)

def is_context_exceeded_body(body: bytes) -> bool:
    """True se il corpo dell'errore contiene uno dei marker di contesto esaurito."""
    if not body:
        return False
    low = body.lower() if isinstance(body, bytes) else str(body).lower().encode()
    return any(m in low for m in CONTEXT_EXCEED_MARKERS)

# ── Generative paths ──────────────────────────────────────────────────────────
_GENERATIVE_PATHS = {
    "m3-image": "/v1/image_generation",
    "m3-video": "/v1/video_generation",
    "m3-music": "/v1/music_generation",
    "m3-tts":   "/v1/t2a_v2",
}

# CLAUDE_CODE_MARKER rimossa il 2026-08-07: nessun lettore. L'autenticazione
# legge il token OAuth da file o Keychain (router_auth.py), non dal system prompt.
# Lo strip cieco dei tre campi e' stato sostituito da una whitelist PER MODELLO
# in anthropic_capabilities.py: i modelli recenti supportano output_config.effort
# e thinking (misurato: -40% di token in uscita a effort=low), i vecchi rispondono
# 400. Il marker Claude Code, contrariamente al commento precedente, UN LETTORE CE
# L'HA: senza di esso opus e sonnet rispondono 429 (verificato 2026-08-07); haiku no.

# ── Health-check paths ─────────────────────────────────────────────────────────
_HEALTH_CHECK_PATHS = frozenset({
    "/", "/readyz", "/livez", "/health", "/stats",
    "/metrics", "/status", "/debug/errors", "/debug/last",
    "/debug/stats", "/debug/trace",
})

# ── Per-model state ───────────────────────────────────────────────────────────
# ponytail: global lock dict — one Lock per fingerprint, created on demand
trim_locks: dict = {}
