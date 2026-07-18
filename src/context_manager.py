"""ContextManager — centralizzato context window + rate limit per chat e modo.

Soglie: 80% warn / 90% compact / 100% error (AQ-3).
Token counting: ibrido byte//4 + campionamento count_tokens (AQ-8).
Storage: SQLite per-chat, isolato per chat_fp + modo.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

from model_context_map import get_context_limit, get_safe_input_limit

# ponytail: copy-on-write stubs — replace with real when /v1/messages/count_tokens is available
_TOKEN_STUB_AVAILABLE = False
def _count_tokens_stub(request, session, body: bytes) -> int | None:
    return None

WARN_PCT    = 0.80
COMPACT_PCT = 0.90
ERROR_PCT   = 1.00


class ContextManager:
    """Gestisce context window e rate limit per chat_fp + modo.

    Pre-check: stima byte//4 vs limit → action warn/compact/error.
    Post-check: 400 context error → _compact_or_clear (compact primo, clear se fallisce).
    Storage SQLite: una riga per (chat_fp, modo).
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or "/tmp/ai-router-ctx.db"
        self._lock = threading.Lock()
        self._init_db()

    # ── Token counting ibrido (AQ-8) ─────────────────────────────────────────

    def _estimate_tokens(self, body_bytes: int) -> int:
        """Stima: byte / 4 (≈1 token per 4 char)."""
        return max(1, body_bytes // 4)

    async def _count_tokens_real(self, request, session, body: bytes) -> int | None:
        """Chiama /v1/messages/count_tokens per campionamento (1 su 10 richieste)."""
        if not _TOKEN_STUB_AVAILABLE:
            return None
        import random
        if random.random() > 0.1:
            return None
        # TODO: chiama count_tokens endpoint quando disponibile
        return None

    # ── Pre-check (AQ-7) ──────────────────────────────────────────────────────

    def pre_check(self, chat_fp: str, modo: str, body_bytes: int) -> dict:
        """Controlla se il body sta nella soglia.

        Ritorna:
            {action: 'ok'|'warn'|'compact'|'error',
             est_tokens: int, limit: int, pct: float}
        """
        with self._lock:
            row = self._get_state(chat_fp, modo)
            if row and row.get('tokens_limit'):
                limit = row['tokens_limit']
            else:
                model = self._resolve_model(modo)
                limit = get_context_limit(model)
            est = self._estimate_tokens(body_bytes)
            pct = est / limit if limit else 0.0
            if pct >= ERROR_PCT:
                return {'action': 'error', 'est_tokens': est, 'limit': limit, 'pct': pct}
            if pct >= COMPACT_PCT:
                return {'action': 'compact', 'est_tokens': est, 'limit': limit, 'pct': pct}
            if pct >= WARN_PCT:
                return {'action': 'warn', 'est_tokens': est, 'limit': limit, 'pct': pct}
            return {'action': 'ok', 'est_tokens': est, 'limit': limit, 'pct': pct}

    # ── Post-check (AQ-7) ─────────────────────────────────────────────────────

    def post_check(self, chat_fp: str, modo: str,
                   response_status: int, response_body: bytes,
                   model: str) -> dict:
        """Gestisce la risposta upstream.

        400 context window → _compact_or_clear.
        Altrimenti action 'ok'.
        """
        if response_status == 400 and self._is_context_error(response_body):
            return self._compact_or_clear(chat_fp, modo, model)
        return {'action': 'ok'}

    # ── Azioni a soglia (AQ-3, AQ-4) ─────────────────────────────────────────

    def _compact_or_clear(self, chat_fp: str, modo: str, model: str) -> dict:
        """Compact primo, clear se già compactato."""
        with self._lock:
            row = self._get_state(chat_fp, modo)
            compact_count = (row['compact_count'] or 0) if row else 0
            if compact_count == 0:
                self._update_state(chat_fp, modo, model,
                                   status='compact', compact_count=1)
                return {'action': 'compact', 'reason': 'ctx_90pct'}
            # già compactato e fallito: clear
            self._update_state(chat_fp, modo, model,
                               status='clear', clear_count=1, tokens_used=0)
            return {'action': 'clear', 'reason': 'compact_failed'}

    def _is_context_error(self, body: bytes) -> bool:
        """Rileva errore context window nella risposta upstream."""
        low = body.lower() if isinstance(body, bytes) else body.lower()
        markers = (
            b"context window",
            b"reached its context",
            b"context_exceeded",
            b"context_window_exceeded",
            b"context limit",
            b"exceeds limit",
            b"2013",
            b"too long",
        )
        return any(m in low for m in markers)

    # ── Reassign (AQ-6) ───────────────────────────────────────────────────────

    def reassign(self, chat_fp: str, modo: str, user_model: str) -> str | None:
        """Riassegna a modello con contesto piú grande. Solo entro la selezione utente."""
        provider = self._provider_for(modo)
        chain = {
            'anthropic': ['haiku', 'sonnet-4-7', 'claude-opus-4-8'],
            'minimax':   ['MiniMax-M2', 'MiniMax-M2.5', 'MiniMax-M2.7', 'MiniMax-M3'],
            'glm':       ['glm-4.7', 'glm-5-turbo', 'glm-5.2'],
        }.get(provider, [])
        if user_model in chain:
            idx = chain.index(user_model)
            return chain[idx + 1] if idx + 1 < len(chain) else None
        return None

    # ── Rate limit acquire (AQ-10) ─────────────────────────────────────────────

    async def acquire(self, model: str, est_tokens: int,
                      budget_sec: float, session) -> bool:
        """Acquire rate limit slot per il modello. Unificato per backend."""
        provider = self._provider_for_model(model)
        if provider == 'minimax':
            try:
                from minimax_rate_limiter import MINIMAX_LIMITER
                await MINIMAX_LIMITER.acquire(model, est_tokens, budget_sec)
            except Exception:
                pass
        elif provider == 'glm':
            try:
                from glm_backend import GLM_LIMITER
                await GLM_LIMITER.acquire(model, est_tokens, budget_sec)
            except Exception:
                pass
        return True

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
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
        cols = [d[0] for d in sqlite3.connect(self._db_path)
                                    .execute("PRAGMA table_info(context_state)")
                                    .fetchall()]
        return dict(zip(cols, row))

    def _update_state(self, chat_fp: str, modo: str, model: str, **kwargs) -> None:
        provider = self._provider_for_model(model)
        update_cols = list(kwargs.keys())
        update_vals = list(kwargs.values())
        set_clause = ", ".join(f"{k}=?" for k in update_cols) + ", last_update=?"
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            f"INSERT INTO context_state (chat_fp,modo,provider,model,{','.join(update_cols)}) "
            f"VALUES (?,?,?,?,{','.join('?'*len(update_vals))}) "
            f"ON CONFLICT(chat_fp,modo) DO UPDATE SET {set_clause}",
            [chat_fp, modo, provider, model] + update_vals + update_vals + [time.time()]
        )
        conn.commit()
        conn.close()

    def _resolve_model(self, modo: str) -> str:
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
        if 'glm' in m:   return 'glm'
        if 'minimax' in m: return 'minimax'
        return 'anthropic'
