# ~250 lines
"""Sistema di debug centralizzato: cattura, persiste, deduplica errori.
Sostituisce debug_capture() in router_utils.py."""
import gzip
import json
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

from aiohttp import web

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR = _PROJECT_ROOT / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

DEBUG_ERRORS_JSONL = _LOGS_DIR / "debug-errors.jsonl"
DEBUG_EVENTS_JSONL = _LOGS_DIR / "debug-events.jsonl"
DEBUG_LAST_REQ = _LOGS_DIR / "debug-last-request.json"
DEBUG_LAST_SENT = _LOGS_DIR / "debug-last-sent.json"
DEBUG_REPAIR_TRACE = _LOGS_DIR / "debug-repair-trace.json"
_HEALTH_FILE = _LOGS_DIR / ".router_health.json"
_DEBUG_ERR_LOG = _LOGS_DIR / "debug-system-errors.log"

# Retrocompat: re-export nomi che router_utils usava
_DEBUG_LAST_SENT = DEBUG_LAST_SENT  # noqa: F401
_DEBUG_REPAIR_TRACE = DEBUG_REPAIR_TRACE  # noqa: F401
DEBUG_LAST_REQ = DEBUG_LAST_REQ  # noqa: F401

MAX_DEQUE = 500
MAX_JSONL_BYTES = 10 * 1024 * 1024


class DebugLogger:
    """Singleton: cattura strutturata, storage resiliente, health tracking."""

    _instance = None

    def __init__(self):
        self.errors: deque = deque(maxlen=MAX_DEQUE)
        self.events: deque = deque(maxlen=MAX_DEQUE)
        self._health: dict = {
            "ts": None, "total_errors": 0, "total_events": 0,
            "last_error_ts": None, "last_event_ts": None,
            "rotation_count": 0,
        }
        self._warm_errors()
        self._warm_events()
        self._load_health()

    @classmethod
    def get(cls) -> "DebugLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Timestamp ──────────────────────────────────────────────────────────────
    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")  # locale, no fake Z

    # ── Health ────────────────────────────────────────────────────────────────
    def _load_health(self) -> None:
        try:
            if _HEALTH_FILE.exists():
                self._health = json.loads(_HEALTH_FILE.read_text())
        except Exception:
            pass

    def _save_health(self) -> None:
        try:
            _HEALTH_FILE.write_text(json.dumps(self._health))
        except Exception:
            pass

    def _debug_err(self, msg: str) -> None:
        """Errori interni del sistema di debug → file dedicato, MAI swallowed."""
        try:
            with open(_DEBUG_ERR_LOG, "a") as f:
                f.write(f"[{self._ts()}] INTERNAL: {msg}\n")
        except Exception:
            pass

    # ── Warm-up ───────────────────────────────────────────────────────────────
    def _warm_errors(self) -> None:
        try:
            if not DEBUG_ERRORS_JSONL.exists():
                return
            lines = DEBUG_ERRORS_JSONL.read_text().strip().split("\n")
            for line in lines[-MAX_DEQUE:]:
                try:
                    self.errors.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass

    def _warm_events(self) -> None:
        try:
            if not DEBUG_EVENTS_JSONL.exists():
                return
            lines = DEBUG_EVENTS_JSONL.read_text().strip().split("\n")
            for line in lines[-MAX_DEQUE:]:
                try:
                    self.events.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass

    # ── Decompress ───────────────────────────────────────────────────────────
    @staticmethod
    def _decompress(raw: bytes, encoding: str = "") -> str:
        if not raw:
            return ""
        try:
            enc = (encoding or "").lower()
            if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
                raw = gzip.decompress(raw)
            elif "br" in enc or "brotli" in enc:
                try:
                    import brotli
                    raw = brotli.decompress(raw)
                except Exception:
                    pass
            elif "deflate" in enc:
                import zlib
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    raw = zlib.decompress(raw)
        except Exception:
            pass
        return raw.decode("utf-8", errors="replace")

    # ── Flags ────────────────────────────────────────────────────────────────
    @staticmethod
    def _orig_flags(orig: dict | None) -> dict:
        if not orig:
            return {}
        msgs = orig.get("messages", [])
        img_count = 0
        cache_control_count = 0
        for m in msgs:
            c = m.get("content", [])
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict):
                        if b.get("type") == "image":
                            img_count += 1
                        elif b.get("type") == "text":
                            tc = b.get("cache_control", {})
                            if isinstance(tc, dict) and tc:
                                cache_control_count += 1
        return {
            "msg_count": len(msgs),
            "has_tools": bool(orig.get("tools")),
            "has_images": img_count > 0,
            "has_thinking": bool(orig.get("thinking")),
            "cache_control_count": cache_control_count,  # fix: era img_count
            "system_is_list": isinstance(orig.get("system"), list),
        }

    # ── JSONL append ─────────────────────────────────────────────────────────
    def _append_jsonl(self, path: Path, record: dict) -> None:
        try:
            if path.exists() and path.stat().st_size > MAX_JSONL_BYTES:
                rot = path.with_suffix(".jsonl.1")
                try:
                    rot.unlink(missing_ok=True)
                except Exception:
                    pass
                try:
                    path.rename(rot)
                except Exception:
                    pass
                self._health["rotation_count"] = self._health.get("rotation_count", 0) + 1
            with open(path, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            self._debug_err(f"_append_jsonl({path.name}) failed: {e}")

    # ── Catalog ──────────────────────────────────────────────────────────────
    def _catalog_event(self, severity, category, kind, fp, code, snippet, detail) -> None:
        try:
            import debug_catalog
            debug_catalog.record_event(
                severity=severity, category=category, kind=kind, chat_fp=fp,
                code=code, snippet=snippet, detail=detail,
            )
        except Exception as e:
            self._debug_err(f"_catalog_event failed: {e}")

    # ── Snapshot ─────────────────────────────────────────────────────────────
    def _write_last_request(self, orig: dict) -> None:
        try:
            req_copy = dict(orig)
            for m in req_copy.get("messages", []):
                c = m.get("content", [])
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "image":
                            d = b.get("data", "")
                            if len(d) > 200:
                                b["data"] = d[:200] + f"... [TRUNCATED {len(d) - 200} chars]"
            DEBUG_LAST_REQ.write_text(json.dumps(req_copy, ensure_ascii=False))
        except Exception as e:
            self._debug_err(f"_write_last_request failed: {e}")

    # ── Main capture ─────────────────────────────────────────────────────────
    def capture(self, *, kind: str, request=None, fp: str = "",
                client_model: str = "", upstream_model: str = "",
                status: int | None = None, stage: str = "",
                upstream_status: int | None = None, upstream_raw: bytes = b"",
                upstream_encoding: str = "", sent_bytes: int = 0,
                orig: dict | None = None, sent_analysis: dict | None = None,
                note: str = "", mode: str = None, severity: str = "error") -> None:
        try:
            if mode is None:
                from router_mode import get_mode
                mode = get_mode(request, fp)

            err_text = self._decompress(upstream_raw, upstream_encoding)
            flags = self._orig_flags(orig)
            ts = self._ts()

            record = {
                "ts": ts, "kind": kind, "fp": fp, "mode": mode,
                "path": getattr(request, "path", "") if request else "",
                "client_model": client_model, "upstream_model": upstream_model,
                "status": status, "stage": stage,
                "upstream_status": upstream_status,
                "upstream_error": err_text[:2000],
                "sent_bytes": sent_bytes,
                "sent_analysis": sent_analysis, "flags": flags, "note": note,
            }

            self.errors.append(record)
            self.events.append(record)
            self._append_jsonl(DEBUG_ERRORS_JSONL, record)
            self._append_jsonl(DEBUG_EVENTS_JSONL, record)
            self._catalog_event(severity, mode, kind, fp, upstream_status or status,
                               err_text, {"client_model": client_model,
                                          "upstream_model": upstream_model,
                                          "stage": stage, "path": record["path"]})
            if orig:
                self._write_last_request(orig)

            self._health["total_errors"] += 1
            self._health["last_error_ts"] = ts
            self._health["ts"] = ts
            self._save_health()
        except Exception as e:
            self._debug_err(f"capture() failed: {e}")

    # ── HTTP endpoints ────────────────────────────────────────────────────────
    async def errors_endpoint(self, request) -> web.Response:
        n = int(request.query.get("n", "20"))
        return web.json_response(list(self.errors)[-n:])

    async def last_endpoint(self, request) -> web.Response:
        if not self.errors:
            return web.Response(text="No errors captured yet.", content_type="text/plain")
        ev = self.errors[-1]
        lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in ev.items()]
        return web.Response(text="\n".join(lines), content_type="text/plain")

    async def stats_endpoint(self, request) -> web.Response:
        from collections import Counter
        c_kind = Counter(e.get("kind") for e in self.errors)
        c_stage = Counter(e.get("stage") for e in self.errors)
        c_upstream = Counter(str(e.get("upstream_status")) for e in self.errors)
        return web.json_response({
            "total_errors": self._health.get("total_errors", 0),
            "total_events": self._health.get("total_events", 0),
            "last_error_ts": self._health.get("last_error_ts"),
            "by_kind": dict(c_kind), "by_stage": dict(c_stage),
            "by_upstream_status": dict(c_upstream),
        })

    async def trace_endpoint(self, request) -> web.Response:
        ev = self.errors[-1] if self.errors else None
        last_sent = None
        repair_trace = None
        try:
            if DEBUG_LAST_SENT.exists():
                last_sent = json.loads(DEBUG_LAST_SENT.read_text())
        except Exception:
            pass
        try:
            if DEBUG_REPAIR_TRACE.exists():
                repair_trace = json.loads(DEBUG_REPAIR_TRACE.read_text())
        except Exception:
            pass
        return web.json_response({
            "last_event": ev, "last_sent": last_sent, "repair_trace": repair_trace,
        })

    async def health_endpoint(self, request) -> web.Response:
        return web.json_response({
            "ok": True,
            "health": self._health,
            "errors_deque_len": len(self.errors),
            "events_deque_len": len(self.events),
        })

    async def catalog_endpoint(self, request) -> web.Response:
        import debug_catalog
        cat = request.query.get("mode") or request.query.get("category")
        sev = request.query.get("severity")
        items = debug_catalog.get_catalog(category=cat, severity=sev)
        return web.json_response({"total": len(items), "items": items})

    async def catalog_entry_endpoint(self, request) -> web.Response:
        import debug_catalog
        sig = request.match_info.get("signature", "")
        entry = debug_catalog.get_catalog_entry(sig)
        if entry is None:
            return web.json_response({"error": f"signature '{sig}' non trovata"}, status=404)
        return web.json_response(entry)


# ── Singleton ─────────────────────────────────────────────────────────────────
dl = DebugLogger.get()

# ── Compatibilità all'indietro ───────────────────────────────────────────────
DEBUG_EVENTS = dl.errors  # alias per chi legge la deque direttamente


def debug_capture(**kwargs) -> None:
    """Forward compat: tutti i call site esistenti continuano a funzionare."""
    dl.capture(**kwargs)
