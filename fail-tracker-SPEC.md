# AQ-REF2 — FailTracker centralizzato

## Obiettivo
Estrarre lo stato e le funzioni fail-tracking (`_mixed_fails`, `_mixed_fail_ts`, `_mixed_cooldown_until`, lock, GC, e le 4 funzioni) → classe `FailTracker` stand-alone in `fail_tracker.py`.

## Stato corrente (da ai-router-proxy.py r 616–685)

### Variabili globali
```python
MIXED_FAIL_THRESHOLD = int(os.environ.get("AIROUTER_MIXED_FAILS", "2"))
_mixed_fails: dict[str, int]          # chat_fp -> fail count
_mixed_fail_ts: dict[str, float]        # chat_fp -> epoch sec ultimo fail
_mixed_cooldown_until: dict[str, float] # chat_fp -> epoch sec fine cooldown
_counter_lock = threading.Lock()
_gc_mix_counter = 0                   # throttling GC
```
### Costanti inline
```python
RESCUE_COOLDOWN_SEC = 30
FAIL_RESET_SEC = 60
_FAILS_GC_MAX = 5000
_FAILS_GC_INTERVAL = 1000
```

### Funzioni da estrarre
1. `_gc_fail_dicts(fails, ts, cooldown, now)` — GC fuori lock
2. `mixed_fail_inc(chat_fp)` → int — incrementa e ritorna count
3. `mixed_fail_reset(chat_fp)` — reset completo (3 dicts)
4. `mixed_anthropic_leads(chat_fp)` → bool — check escalation

## Interfaccia classe

```python
# fail_tracker.py

class FailTracker:
    """Thread-safe fail tracker per-chat. Gestisce counter, cooldown e GC."""

    def __init__(
        self,
        fail_threshold: int | None = None,    # default: AIROUTER_MIXED_FAILS env
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
        Attiva cooldown Rescue_COOLDOWN_SEC."""
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

    # Proprietà utili per debug/admin
    @property
    def threshold(self) -> int: return self._threshold
    @property
    def active_count(self) -> int: return len(self._fails)
```

## Modifiche a ai-router-proxy.py

### 1. Aggiungere import
```python
from fail_tracker import FailTracker
```

### 2. Istanziare tracker globale
```python
fail_tracker = FailTracker()
```

### 3. Sostituire le 4 funzioni con forwarding
```python
def mixed_fail_inc(chat_fp: str) -> int:
    return fail_tracker.inc(chat_fp)

def mixed_fail_reset(chat_fp: str) -> None:
    fail_tracker.reset(chat_fp)

def mixed_anthropic_leads(chat_fp: str) -> bool:
    return fail_tracker.anthropic_leads(chat_fp)
```

### 4. Rimuovere (o commentare) stato inline vecchio
Le variabili `_mixed_fails`, `_mixed_fail_ts`, `_mixed_cooldown_until`, `_counter_lock`, `_gc_mix_counter`, `RESCUE_COOLDOWN_SEC`, `FAIL_RESET_SEC`, `_FAILS_GC_MAX`, `_FAILS_GC_INTERVAL`, `_gc_fail_dicts` — commentare o rimuovere.

### 5. Call sites invariati
`mixed_fail_inc()`, `mixed_fail_reset()`, `mixed_anthropic_leads()` — identici, solo il binding cambia.

## Test minimo
1. `python3 -c "from fail_tracker import FailTracker; ft = FailTracker(); print(ft.inc('test')); print(ft.anthropic_leads('test'))"` — nessun ImportError
2. Fail 2+ volte → `anthropic_leads` True
3. `reset` → counter azzerato, `anthropic_leads` False
4. Concurrent access (2 thread inc same chat_fp) → risultato consistente
