import os
import sys
import time
import threading

import paths

CONTEXT_ALERTS_LOG = str(paths.log_file("context-alerts.log"))
ALERT_MIN_INTERVAL_SEC = 120

_last_alert = {}
_lock = threading.Lock()


def notify_context_threshold(fp: str, mode: str, pct: float, est_tokens: int, limit: int, kind: str) -> None:
    """kind e' warn (80%) o warn2 (88%). Throttle+dedup per (fp, kind). Canale: log dedicato + bell su stderr."""
    try:
        now = time.monotonic()
        with _lock:
            if now - _last_alert.get((fp, kind), 0) < ALERT_MIN_INTERVAL_SEC:
                return
            _last_alert[(fp, kind)] = now

        pct_str = f"{pct:.0%}"

        # title, urg, body_msg servivano alla notifica desktop notify-send, rimossa
        # il 2026-07-20 col commit 75aa186 perche' il fingerprint risultava illeggibile;
        # restavano calcolati a vuoto, rimossi il 2026-08-04.
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

        # Il "Canale 2: banner pending" viveva qui. Rimosso il 2026-08-07: scriveva
        # un file che NESSUNO leggeva, perche' maybe_prepend_banner — l'unica
        # funzione che lo consegnava al client — non era chiamata da nessun punto
        # del proxy. Misura: 134 alert reali fino al 2026-08-02, altrettanti banner
        # mai visti dall'utente. Non e' stato ricablato perche' andrebbe contro la
        # decisione del 2026-07-26, quando il gate di contesto e' diventato un
        # osservatore proprio per non iniettare piu' contenuti sintetici nelle
        # risposte (riscrivere uno stream SSE a valle spezza la numerazione dei
        # content_block). Resta il Canale 1: log dedicato piu' bell su stderr.

    except Exception:
        pass
