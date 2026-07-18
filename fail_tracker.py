"""AQ-REF2 — FailTracker centralizzato."""

import os
import threading
import time


class FailTracker:
    """Thread-safe fail tracker per-chat. Gestisce counter, cooldown e GC."""

    def __init__(
        self,
        fail_threshold: int | None = None,
        rescue_cooldown_sec: float = 30.0,
        fail_reset_sec: float = 60.0,
        gc_max: int = 5000,
        gc_interval: int = 1000,
    ):
        self._fails: dict[str, int] = {}
        self._ts: dict[str, float] = {}
        self._cooldown: dict[str, float] = {}
        self._lock = threading.Lock()
        self._gc_counter = 0
        self._threshold = fail_threshold or int(os.environ.get("AIROUTER_MIXED_FAILS", "2"))
        self._cooldown_sec = rescue_cooldown_sec
        self._reset_sec = fail_reset_sec
        self._gc_max = gc_max
        self._gc_interval = gc_interval

    def inc(self, chat_fp: str) -> int:
        """Incrementa fail count per chat_fp. Ritorna il nuovo count.
        Reset automatico se ultimo fail > reset_sec fa.
        GC a ogni gc_interval incrementi."""
        with self._lock:
            now = time.time()
            if now - self._ts.get(chat_fp, 0) > self._reset_sec:
                self._fails[chat_fp] = 0
            n = self._fails.get(chat_fp, 0) + 1
            self._fails[chat_fp] = n
            self._ts[chat_fp] = now
            self._gc_counter += 1
            counter = self._gc_counter
        if counter % self._gc_interval == 0:
            self._gc(time.time())
        return n

    def reset(self, chat_fp: str) -> None:
        """Reset completo: rimuove tutte le entry per chat_fp."""
        with self._lock:
            self._fails.pop(chat_fp, None)
            self._ts.pop(chat_fp, None)
            self._cooldown.pop(chat_fp, None)

    def anthropic_leads(self, chat_fp: str) -> bool:
        """True se MiniMax ha fallito >= threshold: Anthropic prende il comando.
        Attiva cooldown rescue_cooldown_sec."""
        with self._lock:
            now = time.time()
            if now < self._cooldown.get(chat_fp, 0):
                return True
            if self._fails.get(chat_fp, 0) >= self._threshold:
                self._cooldown[chat_fp] = now + self._cooldown_sec
                return True
            return False

    def _gc(self, now: float) -> None:
        """GC entry stale (nessun fail da > reset_sec) quando size > gc_max."""
        if len(self._fails) <= self._gc_max:
            return
        stale = [fp for fp, t in self._ts.items() if now - t > self._reset_sec]
        for fp in stale:
            self._fails.pop(fp, None)
            self._ts.pop(fp, None)
            self._cooldown.pop(fp, None)

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def active_count(self) -> int:
        return len(self._fails)


# --- forwarding globali (back-compat proxy) ---

_tracker = FailTracker()

# Alias per retrocompatibilità con import legacy `from fail_tracker import fail_tracker`
fail_tracker = _tracker


def mixed_fail_inc(chat_fp: str) -> int:
    return _tracker.inc(chat_fp)


def mixed_fail_reset(chat_fp: str) -> None:
    _tracker.reset(chat_fp)


def mixed_anthropic_leads(chat_fp: str) -> bool:
    return _tracker.anthropic_leads(chat_fp)


if __name__ == "__main__":
    ft = FailTracker(fail_threshold=2)
    print("inc('t'):", ft.inc("t"))
    print("inc('t'):", ft.inc("t"))
    print("anthropic_leads('t'):", ft.anthropic_leads("t"))  # True (threshold=2)
    ft.reset("t")
    print("after reset:", ft.anthropic_leads("t"))  # False
    print("OK")
