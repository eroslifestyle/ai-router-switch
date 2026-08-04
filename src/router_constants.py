# ~130 lines
"""Router constants extracted from ai-router-proxy.py (~lines 386-476 + scattered scalars)."""
import os
from pathlib import Path

# ── Network ────────────────────────────────────────────────────────────────────
LISTEN_HOST = os.environ.get("AIROUTER_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("AIROUTER_PORT", "8787"))
ANTHROPIC_UPSTREAM = os.environ.get("AIROUTER_ANTHROPIC_UPSTREAM", "https://api.anthropic.com")
MINIMAX_UPSTREAM = os.environ.get("AIROUTER_MINIMAX_UPSTREAM", "https://api.minimaxi.chat/anthropic")
MINIMAX_MODEL = os.environ.get("AIROUTER_MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_ORCHESTRATOR_MODEL = os.environ.get("AIROUTER_MINIMAX_ORCHESTRATOR", "MiniMax-M3")
MINIMAX_EXECUTORS = set(
    m.strip() for m in os.environ.get(
        "AIROUTER_MINIMAX_EXECUTORS", "MiniMax-M2,MiniMax-M2.5,MiniMax-M2.7"
    ).split(",") if m.strip()
)
MIXED_EXECUTOR_MODEL = os.environ.get("AIROUTER_MIXED_EXECUTOR", "MiniMax-M2.7")
NEW_PIPELINE = os.environ.get("AIROUTER_NEW_PIPELINE", "1") == "1"
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
MODE_FILE = Path.home() / ".claude" / "ai-router-mode"
KEY_FILE = Path.home() / ".claude" / "secrets" / "secrets.sh"
LOG_FILE = Path.home() / ".claude" / "logs" / "ai-router.log"
SIDECAR = Path.home() / ".claude" / "logs" / "router-model-map.jsonl"
USAGE_SIDECAR = Path.home() / ".claude" / "logs" / "router-usage.jsonl"
CHAT_STORE = Path.home() / ".claude" / "ai-router-chats.json"
TRIM_STATE_DIR = Path(os.environ.get("AIROUTER_TRIM_DIR", "/tmp/ai-router-trim"))
TRIM_STATE_DIR.mkdir(exist_ok=True)

# ── Limits & constants ─────────────────────────────────────────────────────────
MINIMAX_CONTEXT_BYTE_LIMIT = int(os.environ.get("AIROUTER_MINIMAX_CONTEXT_LIMIT", "750000"))
ANTHROPIC_HAIKU_CONTEXT_BYTE_LIMIT = 200 * 1024
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
MINIMAX_ALERTS_LOG = os.path.expanduser("~/.claude/logs/minimax-alerts.log")
MINIMAX_RETRY_BUDGET_SHORT = float(os.environ.get("AIROUTER_MINIMAX_RETRY_SHORT_SEC", "8"))
# Fix 2026-07-21: allineato a pipelines/primitives.py (512) — 200 troncava i piani.
THINK_MAX_TOKENS = int(os.environ.get("AIROUTER_THINK_MAX_TOKENS", "512"))
# THINK_MODEL / THINK_MODEL_ANTHROPIC rimossi: il router usa SEMPRE orig_model
# (il modello scelto dalla config globale del client). La gerarchia THINK/exec/escalation
# vive SOLO nella config globale ~/.claude/CLAUDE.md, mai nel router (tubo trasparente).
THINK_TIMEOUT_SEC = float(os.environ.get("AIROUTER_THINK_TIMEOUT_SEC", "12"))
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
TRIM_TARGET_BYTES = MINIMAX_CONTEXT_BYTE_LIMIT // 2
TRIM_MIN_MESSAGES = 4
SUMMARY_BUDGET = MINIMAX_CONTEXT_BYTE_LIMIT * 3 // 4
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
VALID_MODES = ("anthropic", "minimax", "mix-am", "mix-ag", "mix-gm", "glm", "qwen", "mix-al")

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

# ── Generative paths ──────────────────────────────────────────────────────────
_GENERATIVE_PATHS = {
    "m3-image": "/v1/image_generation",
    "m3-video": "/v1/video_generation",
    "m3-music": "/v1/music_generation",
    "m3-tts":   "/v1/t2a_v2",
}

# ── Claude Code OAuth marker ───────────────────────────────────────────────────
CLAUDE_CODE_MARKER = "You are Claude Code, Anthropic's official CLI for Claude."
ANTHROPIC_UNSUPPORTED_FIELDS = ("context_management", "thinking", "output_config")

# ── Health-check paths ─────────────────────────────────────────────────────────
_HEALTH_CHECK_PATHS = frozenset({
    "/", "/readyz", "/livez", "/health", "/stats",
    "/metrics", "/status", "/debug/errors", "/debug/last",
    "/debug/stats", "/debug/trace",
})

# ── Per-model state ───────────────────────────────────────────────────────────
# ponytail: global lock dict — one Lock per fingerprint, created on demand
trim_locks: dict = {}
