"""Estensioni debug per tracciamento body/risposta completa su provider local."""
import json
import time
from collections import deque
from pathlib import Path
from typing import Optional

from aiohttp import web

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR = _PROJECT_ROOT / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

DEBUG_TRACE_JSONL = _LOGS_DIR / "debug-trace.jsonl"
MAX_TRACE_DEQUE = 50


class TraceLogger:
    """Cattura trace completi di richiesta/risposta per provider local."""

    _instance = None

    def __init__(self):
        self.traces: deque = deque(maxlen=MAX_TRACE_DEQUE)
        self._warm_traces()

    @classmethod
    def get(cls) -> "TraceLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _warm_traces(self) -> None:
        """Carica traces salvati in memoria."""
        try:
            if not DEBUG_TRACE_JSONL.exists():
                return
            lines = DEBUG_TRACE_JSONL.read_text().strip().split("\n")
            for line in lines[-MAX_TRACE_DEQUE:]:
                try:
                    self.traces.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass

    def _append_jsonl(self, record: dict) -> None:
        """Append record al file trace con rotazione se necessario."""
        try:
            max_size = 5 * 1024 * 1024
            if DEBUG_TRACE_JSONL.exists() and DEBUG_TRACE_JSONL.stat().st_size > max_size:
                rot = DEBUG_TRACE_JSONL.with_suffix(".jsonl.1")
                try:
                    rot.unlink(missing_ok=True)
                except Exception:
                    pass
                try:
                    DEBUG_TRACE_JSONL.rename(rot)
                except Exception:
                    pass
            with open(DEBUG_TRACE_JSONL, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def capture(self, *, mode: str, model: str, request_body: bytes,
                response_status: int, response_body: bytes | None,
                response_headers: dict | None = None, elapsed_ms: float = 0,
                note: str = "", request_path: str = "") -> None:
        """Cattura un trace completo di richiesta/risposta."""
        try:
            request_body_str = request_body.decode("utf-8", errors="replace")
            response_body_str = ""
            if response_body:
                response_body_str = response_body.decode("utf-8", errors="replace")
                # Tronca se troppo grande (max 50KB)
                if len(response_body_str) > 50000:
                    response_body_str = response_body_str[:50000] + f"... [TRUNCATED {len(response_body_str) - 50000} chars]"

            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "mode": mode,
                "model": model,
                "request_path": request_path,
                "request_body": request_body_str[:50000] if len(request_body_str) > 50000 else request_body_str,
                "request_body_truncated": len(request_body_str) > 50000,
                "response_status": response_status,
                "response_body": response_body_str,
                "response_headers": dict(response_headers) if response_headers else {},
                "elapsed_ms": elapsed_ms,
                "note": note,
            }

            self.traces.append(record)
            self._append_jsonl(record)
        except Exception as e:
            pass  # Non fallire mai il trace

    async def trace_endpoint(self, request) -> web.Response:
        """Endpoint /debug/trace con filtro mode."""
        mode_filter = request.query.get("mode", "")
        n = int(request.query.get("n", "20"))

        traces = list(self.traces)
        if mode_filter:
            traces = [t for t in traces if t.get("mode") == mode_filter]

        return web.json_response(traces[-n:] if traces else [])


# Singleton
trace_logger = TraceLogger.get()
