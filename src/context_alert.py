import os
import sys
import time
import json
import threading
import subprocess

CONTEXT_ALERTS_LOG = os.path.expanduser("~/.claude/logs/context-alerts.log")
PENDING_DIR = "/tmp/ai-router-ctx-alert"
ALERT_MIN_INTERVAL_SEC = 120

_last_alert = {}
_lock = threading.Lock()


def notify_context_threshold(fp: str, mode: str, pct: float, est_tokens: int, limit: int, kind: str) -> None:
    """kind e' warn (80%) o warn2 (88%). Throttle+dedup per (fp, kind). Canali: log+bell, banner in-chat."""
    try:
        now = time.monotonic()
        with _lock:
            if now - _last_alert.get((fp, kind), 0) < ALERT_MIN_INTERVAL_SEC:
                return
            _last_alert[(fp, kind)] = now

        pct_str = f"{pct:.0%}"
        if kind == "warn2":
            title = f"⚠ Context {pct_str} — compressione IMMINENTE"
            urg = "critical"
        else:
            title = f"⚠ Context {pct_str} — crea checkpoint"
            urg = "normal"

        body_msg = (
            f"Context a {pct_str} ({est_tokens:,}/{limit:,} token), mode={mode}. "
            f"La compressione automatica (lossy) scattera' a breve. "
            f"Crea un checkpoint ORA per non perdere il lavoro."
        )

        # Canale 1: log dedicato + bell su stderr
        try:
            log_dir = os.path.dirname(CONTEXT_ALERTS_LOG)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(CONTEXT_ALERTS_LOG, "a") as f:
                f.write(f"[{ts}] {kind} fp={fp} mode={mode} pct={pct_str} {est_tokens}/{limit}\n")
        except Exception:
            pass

        try:
            sys.stderr.write("\a")
            sys.stderr.flush()
        except Exception:
            pass

        # Canale 3: banner pending
        banner_text = f"⚠ Context a {pct_str}: compressione automatica imminente. Crea un checkpoint ora (lavoro a rischio)."
        _write_pending_banner(fp, banner_text)

    except Exception:
        pass


def _write_pending_banner(fp: str, text: str) -> None:
    """Scrive il banner in PENDING_DIR/{fp}.txt. Salta se fp vuoto."""
    try:
        if not fp:
            return
        os.makedirs(PENDING_DIR, exist_ok=True)
        with open(os.path.join(PENDING_DIR, f"{fp}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def pop_pending_banner(fp: str) -> str | None:
    """Legge e cancella il banner pending. None se assente. Salta se fp vuoto."""
    try:
        if not fp:
            return None
        path = os.path.join(PENDING_DIR, f"{fp}.txt")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        os.remove(path)
        return text
    except Exception:
        return None


def maybe_prepend_banner(response_bytes: bytes, fp: str, is_stream: bool) -> bytes:
    """Antepone banner pending alla risposta. Stream: ri-scrive come pending. Fail-safe."""
    banner = pop_pending_banner(fp)
    if banner is None:
        return response_bytes

    if is_stream:
        # Non riscriviamo lo stream; ri-scrivi banner come pending per prossimo turno non-stream
        _write_pending_banner(fp, banner)
        return response_bytes

    try:
        resp = json.loads(response_bytes.decode("utf-8"))
        banner_with_newline = banner + "\n\n"

        if isinstance(resp, dict) and "content" in resp and isinstance(resp["content"], list):
            inserted = False
            for block in resp["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    block["text"] = banner_with_newline + block.get("text", "")
                    inserted = True
                    break
            if not inserted:
                resp["content"].insert(0, {"type": "text", "text": banner_with_newline})
            return json.dumps(resp, ensure_ascii=False).encode("utf-8")
        else:
            # Struttura inattesa: ri-scrivi banner come pending
            _write_pending_banner(fp, banner)
            return response_bytes

    except Exception:
        # Fail-safe: ri-scrivi banner come pending per non perderlo
        _write_pending_banner(fp, banner)
        return response_bytes
