# AQ-REF3 — ContextManager centralizzato

## Obiettivo
Classe `ContextManager` in `context_manager.py` che gestisce context window e rate limit in modo centralizzato per ogni chat e modo.

## Decisioni AQ (2026-07-18)

| # | Decisione |
|---|---|
| AQ-1 | **Tutto unificato**: context + rate limit in una classe |
| AQ-2 | **SQLite per-chat**: persistenza tra restart |
| AQ-3 | **80% warn / 90% compact / 100% error** |
| AQ-4 | **Compact primo, clear se fallisce** |
| AQ-5 | **Per chat + per modo**: stato isolato per `chat_fp + modo` |
| AQ-6 | **Reassign solo a modello utente** (non scavalcare senza permesso) |
| AQ-7 | **Entrambi (pre + post)**: pre-check + post-check safety net |
| AQ-8 | **Ibrido**: stima byte/4 + campionamento count_tokens per calibrazione |
| AQ-9 | **Classe standalone** `context_manager.py` |
| AQ-10 | **Unificato con regole per backend** |

---

## Mappa modelli (da AQ-2)

### Anthropic
| Model | Context | 80% token | 90% token | Byte@80% |
|---|---|---|---|---|
| `claude-opus-4-8` / `opus` | 1M | 800k | 900k | ~3.2M |
| `claude-sonnet-4-6/4-7/4-8` | 1M | 800k | 900k | ~3.2M |
| `claude-sonnet-4-5` | 200k | 160k | 180k | ~640k |
| `claude-haiku-4-5-20251001` / `haiku` | 200k | 160k | 180k | ~640k |

### MiniMax
| Model | Context | 80% token | 90% token | Byte@80% |
|---|---|---|---|---|
| `MiniMax-M3` | 200k | 160k | 180k | ~640k |
| `MiniMax-M2.7` / `-hs` | 200k | 160k | 180k | ~640k |
| `MiniMax-M2.5` / `-hs` | 200k | 160k | 180k | ~640k |
| `MiniMax-M2` | 200k | 160k | 180k | ~640k |

### GLM / Z.ai
| Model | Context | 80% token | 90% token | Byte@80% |
|---|---|---|---|---|
| `glm-5.2` | 1M | 800k | 900k | ~3.2M |
| `glm-5-turbo` | 200k | 160k | 180k | ~640k |
| `glm-4.7` | 128k | 102k | 115k | ~400k |
| `glm-4.6V` (vision) | 131k | 105k | 118k | ~420k |
| `glm-5V-Turbo` (video) | 200k | 160k | 180k | ~640k |

---

## Schema SQLite

```sql
-- Una riga per chat_fp + modo
CREATE TABLE context_state (
    chat_fp TEXT NOT NULL,        -- fingerprint chat
    modo TEXT NOT NULL,           -- anthropic/minimax/mix-am/mix-ag/mix-gm/glm
    provider TEXT NOT NULL,       -- anthropic/minimax/glm
    model TEXT NOT NULL,          -- modello attivo (es. sonnet-4-7)
    tokens_used INTEGER DEFAULT 0,
    tokens_80pct INTEGER,        -- soglia 80%
    tokens_90pct INTEGER,        -- soglia 90%
    tokens_limit INTEGER,         -- context window
    status TEXT DEFAULT 'ok',    -- ok / warn / compact / error
    compact_count INTEGER DEFAULT 0,
    clear_count INTEGER DEFAULT 0,
    last_update REAL,            -- unix timestamp
    PRIMARY KEY (chat_fp, modo)
);

-- Rate limit tracking per modello
CREATE TABLE rate_state (
    provider TEXT NOT NULL,       -- anthropic/minimax/glm
    model TEXT NOT NULL,
    window_start REAL,           -- unix timestamp inizio finestra 60s
    rpm_count INTEGER DEFAULT 0,
    tpm_count INTEGER DEFAULT 0,
    PRIMARY KEY (model)
);
```

---

## Interfaccia classe

```python
# context_manager.py

import sqlite3, time, threading, asyncio, json
from pathlib import Path

class ContextManager:
    """Centralizzato: context window + rate limit per chat e modo.

    Storage: SQLite per-chat, isolato per chat_fp + modo (AQ-5).
    Soglie: 80% warn / 90% compact / 100% error (AQ-3).
    Azione: compact primo, clear se fallisce (AQ-4).
    Reassign: solo a modello utente (AQ-6).
    Token counting: ibrido byte/4 + campionamento count_tokens (AQ-8).
    """

    WARN_PCT  = 0.80   # AQ-3
    COMPACT_PCT = 0.90  # AQ-3
    ERROR_PCT  = 1.00  # AQ-3

    def __init__(self, db_path=None):
        self._db_path = db_path or Path("/tmp/ai-router-ctx.db")
        self._lock = threading.Lock()
        self._init_db()

    # ── Token counting ibrido (AQ-8) ───────────────────────────────────────
    def _estimate_tokens(self, body_bytes: int) -> int:
        """Stima: byte / 4 (≈1 token per 4 char)."""
        return max(1, body_bytes // 4)

    async def _count_tokens_real(self, request, session, body: bytes) -> int | None:
        """Chiama /v1/messages/count_tokens per campionamento."""
        # Campiona 1 su 10 richieste per calibrazione
        import random
        if random.random() > 0.1:
            return None
        # ... chiama count_tokens e ritorna token reali
        return None

    # ── Pre-check (AQ-7) ───────────────────────────────────────────────────
    def pre_check(self, chat_fp: str, modo: str, body_bytes: int) -> dict:
        """Controlla se il body sta nella soglia warn (80%).
        Ritorna: {action: 'ok'|'warn'|'compact', est_tokens, limit}."""
        with self._lock:
            row = self._get_state(chat_fp, modo)
            if not row:
                model = self._resolve_model(modo)
                limit = self._get_limit(model)
            else:
                limit = row['tokens_limit'] or self._get_limit(
                    row['model'] or self._resolve_model(modo))
            est = self._estimate_tokens(body_bytes)
            pct = est / limit if limit else 0
            if pct >= self.ERROR_PCT:
                return {'action': 'error', 'est_tokens': est, 'limit': limit, 'pct': pct}
            if pct >= self.COMPACT_PCT:
                return {'action': 'compact', 'est_tokens': est, 'limit': limit, 'pct': pct}
            if pct >= self.WARN_PCT:
                return {'action': 'warn', 'est_tokens': est, 'limit': limit, 'pct': pct}
            return {'action': 'ok', 'est_tokens': est, 'limit': limit, 'pct': pct}

    # ── Post-check (AQ-7) ─────────────────────────────────────────────────
    def post_check(self, chat_fp: str, modo: str, response_status: int,
                   response_body: bytes, model: str) -> dict:
        """Gestisce la risposta upstream. Ritorna azione da compiere."""
        if response_status == 400:
            is_ctx = self._is_context_error(response_body)
            if is_ctx:
                return self._compact_or_clear(chat_fp, modo, model)
        return {'action': 'ok'}

    # ── Azioni a soglia (AQ-3, AQ-4) ──────────────────────────────────────
    def _compact_or_clear(self, chat_fp: str, modo: str, model: str) -> dict:
        """Compact primo, clear se fallisce."""
        with self._lock:
            row = self._get_state(chat_fp, modo)
            compact_count = (row['compact_count'] or 0) if row else 0
            if compact_count == 0:
                self._update_state(chat_fp, modo, model,
                                   status='compact', compact_count=1)
                return {'action': 'compact', 'reason': 'ctx_90pct'}
            # giá compactato e fallito: clear
            self._update_state(chat_fp, modo, model,
                               status='clear', clear_count=1, tokens_used=0)
            return {'action': 'clear', 'reason': 'compact_failed'}

    def _is_context_error(self, body: bytes) -> bool:
        """Rileva errore context window nella risposta."""
        low = body.lower() if isinstance(body, bytes) else body.lower()
        markers = [b"context window", b"reached its context", b"context_exceeded",
                   b"context limit", b"exceeds limit", b"2013", b"too long"]
        return any(m in low for m in markers)

    # ── Reassign (AQ-6) ────────────────────────────────────────────────────
    def reassign(self, chat_fp: str, modo: str, user_model: str) -> str | None:
        """Riassegna a modello con context piú grande. Solo entro la selezione utente."""
        # Mappa fallback per provider
        fallback = {
            'anthropic': ['haiku', 'sonnet', 'opus'],
            'minimax':   ['MiniMax-M2', 'MiniMax-M2.5', 'MiniMax-M2.7', 'MiniMax-M3'],
            'glm':       ['glm-4.7', 'glm-5-turbo', 'glm-5.2'],
        }
        provider = self._provider_for(modo)
        chain = fallback.get(provider, [])
        # Trova il prossimo modello nella chain dopo user_model
        if user_model in chain:
            idx = chain.index(user_model)
            return chain[idx + 1] if idx + 1 < len(chain) else None
        return None

    # ── Rate limit ibrido (AQ-1, AQ-10) ───────────────────────────────────
    async def acquire(self, model: str, est_tokens: int,
                      budget_sec: float, session) -> bool:
        """Acquisisce rate limit slot per il modello. Unificato per backend."""
        provider = self._provider_for_model(model)
        if provider == 'minimax':
            from minimax_rate_limiter import MINIMAX_LIMITER
            await MINIMAX_LIMITER.acquire(model, est_tokens, budget_sec)
        elif provider == 'glm':
            from glm_backend import GLM_LIMITER
            await GLM_LIMITER.acquire(model, est_tokens, budget_sec)
        # Anthropic: delegato a upstream (x-should-retry)
        return True

    # ── DB helpers ──────────────────────────────────────────────────────────
    def _init_db(self):
        """Crea tabelle se non esistono."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_state (
                    chat_fp TEXT NOT NULL, modo TEXT NOT NULL,
                    provider TEXT, model TEXT,
                    tokens_used INTEGER DEFAULT 0,
                    tokens_80pct INTEGER, tokens_90pct INTEGER, tokens_limit INTEGER,
                    status TEXT DEFAULT 'ok', compact_count INTEGER DEFAULT 0,
                    clear_count INTEGER DEFAULT 0, last_update REAL,
                    PRIMARY KEY (chat_fp, modo)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_state (
                    model TEXT NOT NULL PRIMARY KEY,
                    window_start REAL, rpm_count INTEGER DEFAULT 0, tpm_count INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()

    def _get_state(self, chat_fp: str, modo: str) -> dict | None:
        conn = sqlite3.connect(self._db_path)
        row = conn.execute(
            "SELECT * FROM context_state WHERE chat_fp=? AND modo=?",
            (chat_fp, modo)).fetchone()
        conn.close()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("PRAGMA table_info(context_state)").fetchall()]
        return dict(zip(cols, row))

    def _update_state(self, chat_fp: str, modo: str, model: str, **kwargs):
        conn = sqlite3.connect(self._db_path)
        cols = list(kwargs.keys()) + ['chat_fp', 'modo', 'last_update']
        vals = list(kwargs.values()) + [chat_fp, modo, time.time()]
        sets = ', '.join(f"{k}=?" for k in kwargs) + ", last_update=?"
        conn.execute(
            f"INSERT INTO context_state (chat_fp,modo,{','.join(kwargs)}) "
            f"VALUES (?{',?'*len(kwargs)},?) "
            f"ON CONFLICT(chat_fp,modo) DO UPDATE SET {sets}",
            [chat_fp, modo] + list(kwargs.values()) + [chat_fp, modo, time.time()]
        )
        conn.commit()
        conn.close()

    def _get_limit(self, model: str) -> int:
        """Lookup context window da MODEL_CONTEXT_MAP."""
        from model_context_map import get_context_limit
        return get_context_limit(model)

    def _resolve_model(self, modo: str) -> str:
        """Model di default per modo."""
        defaults = {
            'anthropic': 'sonnet-4-7',
            'minimax':   'MiniMax-M2.7',
            'mix-am':    'MiniMax-M2.7',
            'mix-ag':    'claude-sonnet-4-7',
            'mix-gm':    'MiniMax-M2.7',
            'glm':       'glm-5.2',
        }
        return defaults.get(modo, 'MiniMax-M2.7')

    def _provider_for(self, modo: str) -> str:
        if modo in ('anthropic', 'mix-ag'):
            return 'anthropic'
        if modo in ('minimax', 'mix-am', 'mix-gm'):
            return 'minimax'
        return 'glm'

    def _provider_for_model(self, model: str) -> str:
        m = model.lower()
        if 'glm' in m: return 'glm'
        if 'minimax' in m: return 'minimax'
        return 'anthropic'
```

---

## Integrazione in ai-router-proxy.py

### 1. Import
```python
from context_manager import ContextManager

CTX = ContextManager()  # globale
```

### 2. In handle() — pre-check
```python
# Dopo _resolve_chat_fingerprint, prima del routing
pre = CTX.pre_check(fp, mode, len(body))
if pre['action'] == 'error':
    return web.json_response({"type":"error","error":{
        "type":"context_exceeded",
        "message":f"body {len(body)}b ({pre['pct']:.0%} del context {pre['limit']})"
    }}, status=400)
if pre['action'] == 'compact':
    # richiama compact via CTX o esegue trim inline
    pass
```

### 3. In forward_anthropic / forward_minimax — post-check
```python
# Dopo upstream response
if up.status == 400:
    raw = await up.read()
    action = CTX.post_check(fp, mode, 400, raw, model)
    if action['action'] == 'compact':
        stripped = _strip_images_body(body)
        up = await session.request(...)
    elif action['action'] == 'clear':
        return _clear_context_response()
```

### 4. Fallback chain — reassign
```python
user_model = orig.get('model', '')
new_model = CTX.reassign(fp, mode, user_model)
if new_model:
    # ritenta con new_model
    pass
```

---

## Test minimo
1. `python3 -c "from context_manager import ContextManager; c=ContextManager(); print(c.pre_check('test','mix-am',500_000))"` — nessun ImportError
2. Pre-check body > 90% → action='compact'
3. Post-check 400 context → action='compact' o 'clear'
4. Reassign da haiku → sonnet
5. SQLite creato e writable
