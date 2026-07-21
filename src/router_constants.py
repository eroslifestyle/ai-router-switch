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
VERIFY_MODEL = os.environ.get("AIROUTER_VERIFY_MODEL", "claude-opus-4-8")
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
THINK_MODEL = os.environ.get("AIROUTER_THINK_MODEL", "claude-haiku-4-5-20251001")
THINK_TIMEOUT_SEC = float(os.environ.get("AIROUTER_THINK_TIMEOUT_SEC", "12"))
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
VALID_MODES = ("anthropic", "minimax", "mix-am", "mix-ag", "mix-gm", "glm")

# ── Port mode map ─────────────────────────────────────────────────────────────
PORT_MODE = {
    8771: "anthropic",
    8772: "minimax",
    8773: "mix-am",
    8775: "glm",
    8776: "mix-gm",
    8777: "mix-ag",
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
