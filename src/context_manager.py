"""ContextManager — centralizzato context window + rate limit per chat e modo.

Soglie: 80% warn / 90% compact / 100% error (AQ-3).
Token counting: stima byte//4 (il campionamento AQ-8 non è mai stato completato, rimosso il 2026-08-04).
Storage: SQLite per-chat, isolato per chat_fp + modo.
"""
import sqlite3
import threading

from model_context_map import get_context_limit

# Le tre costanti dopo WARN_PCT erano scritte due volte di fila, con gli stessi
# valori: la seconda copia riassegnava la prima e non cambiava nulla. Rimossa il
# 2026-08-16 insieme al resto del residuo.
WARN_PCT    = 0.80
WARN2_PCT   = 0.88   # secondo alert, urgente: compressione (90%) imminente
COMPACT_PCT = 0.90
ERROR_PCT   = 1.00


class ContextManager:
    """Misura quanto contesto sta usando una chat e con quale soglia.

    `pre_check` stima i token del body e li confronta col limite del modello,
    ritornando ok/warn/warn2/compact/error. E' l'unico metodo pubblico: chi
    chiama decide cosa farne (oggi il proxy registra soltanto, perche' il router
    e' un tunnel trasparente e non decide da se' di compattare).
    Storage SQLite: una riga per (chat_fp, modo), in sola lettura da quando
    `_update_state` e' stato rimosso col codice che lo chiamava.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or "/tmp/ai-router-ctx.db"
        self._lock = threading.Lock()
        self._init_db()

    # ── Token counting ibrido (AQ-8) ─────────────────────────────────────────

    def _estimate_tokens(self, body_bytes: int) -> int:
        """Stima: byte / 4 (≈1 token per 4 char)."""
        return max(1, body_bytes // 4)

    # Rimosso il 2026-08-04: _count_tokens_real, stub del campionamento AQ-8
    # mai chiamato da nessuno e comunque disattivato da _TOKEN_STUB_AVAILABLE
    # = False, rimossa con lui. Nello stesso commit sono cadute anche
    # count_tokens e count_tokens_real in token_counter: il campionamento
    # non e mai stato completato. La stima in uso resta _estimate_tokens
    # qui sopra, e estimate_tokens_body in token_counter per il conteggio
    # per modello.

    # ── Pre-check (AQ-7) ──────────────────────────────────────────────────────

    def pre_check(self, chat_fp: str, modo: str, body_bytes: int, model: str | None = None, body: bytes | None = None) -> dict:
        """Controlla se il body sta nella soglia.

        Ritorna:
            {action: 'ok'|'warn'|'compact'|'error',
             est_tokens: int, limit: int, pct: float, model_used: str}
        """
        with self._lock:
            row = self._get_state(chat_fp, modo)
            # Risoluzione limite: priorità model > row > resolve_model
            # fix 2026-07-26: pre_check e rewrite usavano misure disallineate -> 400 sintetico
            if model:
                limit_model = model
                limit = get_context_limit(limit_model)
            elif row and row.get('tokens_limit'):
                limit_model = self._resolve_model(modo)
                limit = row['tokens_limit']
            else:
                limit_model = self._resolve_model(modo)
                limit = get_context_limit(limit_model)

            # Stima token
            if body is not None:
                try:
                    from token_counter import estimate_tokens_body
                    # Il divisore byte/token dipende dal tokenizer del modello:
                    # 2.5 per Anthropic >= Opus 4.7, 3.5 per i Claude precedenti,
                    # 3.8 MiniMax, 4.0 GLM (misurati 2026-07-27). Senza il modello
                    # la stima cadrebbe sul legacy 4.0, che sottostima del 36% i
                    # modelli con il tokenizer nuovo.
                    est = estimate_tokens_body(body, limit_model)
                except ImportError:
                    est = max(1, body_bytes // 4)
            else:
                est = self._estimate_tokens(body_bytes)

            pct = est / limit if limit else 0.0
            if pct >= ERROR_PCT:
                return {'action': 'error', 'est_tokens': est, 'limit': limit, 'pct': pct, 'model_used': limit_model}
            if pct >= COMPACT_PCT:
                return {'action': 'compact', 'est_tokens': est, 'limit': limit, 'pct': pct, 'model_used': limit_model}
            if pct >= WARN2_PCT:
                return {'action': 'warn2', 'est_tokens': est, 'limit': limit, 'pct': pct, 'model_used': limit_model}
            if pct >= WARN_PCT:
                return {'action': 'warn', 'est_tokens': est, 'limit': limit, 'pct': pct, 'model_used': limit_model}
            return {'action': 'ok', 'est_tokens': est, 'limit': limit, 'pct': pct, 'model_used': limit_model}

    # ── Rimosso il 2026-08-16: post_check / _compact_or_clear / reassign ──────
    #
    # Erano l'API pubblica di questo modulo e nessuno le ha mai chiamate: zero
    # riferimenti in src/, negli script e nei test. `post_check` doveva reagire al
    # 400 di contesto pieno decidendo un compact o un clear, `reassign` doveva
    # passare a un modello con finestra piu' grande. Entrambe funzionalita'
    # sensate, mai cablate — e non le si aggancia adesso, perche' farebbero
    # decidere al router quale modello risponde, che e' esattamente cio' che la
    # regola del tunnel trasparente vieta.
    #
    # Con loro se ne va `_update_state`, che scriveva la tabella `context_state`:
    # i suoi due unici chiamanti erano dentro `_compact_or_clear`. La tabella era
    # quindi gia' vuota nei fatti, e `_get_state` — che `pre_check` usa ancora —
    # continua a leggerne una vuota esattamente come prima. Nessun cambiamento di
    # comportamento a runtime: e' rimozione di residuo, non di funzionalita'.
    #
    # `_is_context_error` era un wrapper di una riga su
    # `router_constants.is_context_exceeded_body`, tenuto in vita solo dal proprio
    # test: il test ora chiama direttamente la funzione canonica, che e' l'unica
    # implementazione da quando le quattro copie divergenti sono state unificate
    # il 2026-08-06.

    # Rimosso il 2026-08-16 anche `acquire` (AQ-10): "rate limit unificato per
    # backend" che nessuno invocava. Ogni backend acquisisce dal proprio limiter
    # sul posto — forward_minimax, glm_backend, qwen_backend, forward_anthropic —
    # e il ramo minimax di questo metodo non faceva comunque nulla. Con lui esce
    # `_provider_for_model`, che restava usato solo da `_update_state`, rimosso
    # sopra.

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

    def _resolve_model(self, modo: str) -> str:
        defaults = {
            'anthropic': 'sonnet-4-7',
            'minimax':   'MiniMax-M2.7',
            'mix-am':    'MiniMax-M2.7',
            'mix-ag':    'claude-sonnet-4-7',
            'mix-gm':    'MiniMax-M2.7',
            'glm':       'glm-5.3',
        }
        return defaults.get(modo, 'MiniMax-M2.7')

    def _provider_for(self, modo: str) -> str:
        if modo in ('anthropic', 'mix-ag'):
            return 'anthropic'
        if modo in ('minimax', 'mix-am', 'mix-gm'):
            return 'minimax'
        return 'glm'

