# Debug System Refactor — SPEC

## Contesto
Sistema attuale: `router_utils.py` (debug_capture) + `debug_catalog.py` (record_event) + `streaming_relay.py` (relay_error_*). Storage: 3 JSONL (errors, events, catalog) + 2 snapshot (last-request, last-sent) + RAM deque.

**Gap critici trovati:**
1. `forward_minimax.py` e `forward_anthropic.py` usano SOLO `log()` testuale per errori — `debug_capture()` mai chiamato.
2. `glm_backend.py` non cattura errori HTTP grezzi (eccezioni solo).
3. Relay error 4xx/5xx catturati SOLO in `StreamingRelay` (streaming path), errori nei path non-streaming (`_handle_glm_mode`, `_handle_minimax_mode`) invisibili.
4. `_orig_flags()` ha `cache_control_count = img_count` — copy-paste bug (copia da `img_count`, non `cache_control`).
5. `debug_capture()` silenzia TUTTE le eccezioni — un `except Exception: pass` in cima cancella ogni errore diagnostico.
6. `_rotated_jsonl_path()` + `_rotated_events_path()` duplicati tra `router_utils.py` e `debug_catalog.py`.
7. `DEBUG_EVENTS` deque ha `maxlen=100` — dimensione troppo piccola per retain diagnostico tra eventi rari (es. 1 evento/giorno).
8. Nessun heartbeat/liveness nel sistema di debug — non si distingue "mai loggato nulla" da "file corrotto/troncato".

**Bug reale nel catalogo:** `categories: ["?"]` nell'output Python è un artefatto di `json.dumps()` che serializza il dict `_catalog_cache` — il file è corretto. Verificato: `wc -l logs/BUG-CATALOG.jsonl` = 25 entries, tutte con `categories` non-nulle nel JSONL. Nessun fix necessario per questo.

## Architettura target

### Storage (nessuna modifica — JSONL kept, nessun DB)
```
logs/
  debug-errors.jsonl      # raw error events (rotazione 10MB→.1)
  debug-events.jsonl       # catalog events raw (rotazione 10MB→.1)
  BUG-CATALOG.jsonl       # deduplicato, atomico tmp+replace (INVARIATO)
  debug-last-request.json # snapshot ultima richiesta (INVARIATO)
  debug-last-sent.json    # snapshot ultimo sent (INVARIATO)
  debug-repair-trace.json # trace ripristino contesto (INVARIATO)
  .router_health.json     # NUOVO: heartbeat/liveness del sistema di debug
```

### Moduli
```
src/
  router_debug.py         # NUOVO: tutto il debug in un posto solo
    - DebugLogger (classe)
    - Rotazione JSONL (una funzione)
    - _orig_flags() (fixato)
    - Health tracker
    - Forward verso gli altri moduli come API compatibile
  debug_catalog.py        # MODIFICATO: usa DebugLogger internamente
  router_utils.py         # MODIFICATO: rimuove debug_capture duplicato, importa da router_debug
  streaming_relay.py      # MODIFICATO: usa DebugLogger
  forward_minimax.py      # MODIFICATO: chiama DebugLogger
  forward_anthropic.py    # MODIFICATO: chiama DebugLogger
  glm_backend.py          # MODIFICATO: chiama DebugLogger
  pipeline_*.py           # MODIFICATO: usa DebugLogger invece di debug_capture
  ai-router-proxy.py      # MODIFICATO: importa DebugLogger, rimuove vecchie funzioni
```

### Nuovo modulo: `src/router_debug.py`

```python
from router_debug import DebugLogger, dl  # dl = singleton globale

class DebugLogger:
    MAX_EVENTS_DEQUE = 500   # era 100 — più spazio per eventi rari
    MAX_ERRORS_BYTES = 10 * 1024 * 1024
    MAX_EVENTS_BYTES = 10 * 1024 * 1024
    HEALTH_FILE = _LOGS_DIR / ".router_health.json"

    def __init__(self, logs_dir: Path):
        self.events: deque = deque(maxlen=self.MAX_EVENTS_DEQUE)
        self.errors: deque = deque(maxlen=self.MAX_EVENTS_DEQUE)
        self._health: dict = {"ts": None, "total_errors": 0, "total_events": 0,
                               "last_error_ts": None, "last_event_ts": None,
                               "rotation_count": 0}
        self._last_req: Path = logs_dir / "debug-last-request.json"
        self._last_sent: Path = logs_dir / "debug-last-sent.json"
        self._repair_trace: Path = logs_dir / "debug-repair-trace.json"
        # Carica health da file
        self._load_health()
        # Carica ultimi N errori da JSONL per popolare la deque
        self._warm_errors()
        self._warm_events()

    # ── Core capture ──────────────────────────────────────────────────────────
    def capture(self, *, kind: str, request=None, fp: str = "",
                client_model: str = "", upstream_model: str = "",
                status: int | None = None, stage: str = "",
                upstream_status: int | None = None, upstream_raw: bytes = b"",
                upstream_encoding: str = "", sent_bytes: int = 0,
                orig: dict | None = None, sent_analysis: dict | None = None,
                note: str = "", mode: str = None, severity: str = "error") -> None:
        """Unified capture: RAM + JSONL + catalog. Tutti i catch: log locale."""
        try:
            # Risolvi mode
            if mode is None:
                from router_mode import get_mode
                mode = mode or get_mode(request, fp)

            err_text = self._decompress(upstream_raw, upstream_encoding)
            flags = self._orig_flags(orig)
            ts = self._ts_local()  # locale, NO suffisso Z fasullo
            record = {
                "ts": ts,
                "kind": kind, "fp": fp, "mode": mode,
                "path": getattr(request, "path", "") if request else "",
                "client_model": client_model, "upstream_model": upstream_model,
                "status": status, "stage": stage,
                "upstream_status": upstream_status,
                "upstream_error": err_text[:2000],
                "sent_bytes": sent_bytes,
                "sent_analysis": sent_analysis, "flags": flags, "note": note,
            }
            # RAM
            self.errors.append(record)
            self.events.append(record)
            # JSONL errors
            self._append_jsonl("debug-errors.jsonl", record)
            # JSONL events
            self._append_jsonl("debug-events.jsonl", record)
            # Catalog
            self._catalog_event(severity, mode, kind, fp, upstream_status or status, err_text, {
                "client_model": client_model, "upstream_model": upstream_model,
                "stage": stage, "path": record["path"]
            })
            # Snapshot last-request
            if orig:
                self._write_last_request(orig)
            # Health
            self._health["total_errors"] += 1
            self._health["last_error_ts"] = ts
            self._save_health()
        except Exception as e:
            # Questo è l'unico posto dove gli errori di debug vengono persi silenziosamente
            # → log su file separato, NON swallowed
            self._debug_error(f"DebugLogger.capture() failed: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _ts_local(self) -> str:
        """Timestamp ISO locale senza Z fasullo."""
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def _decompress(self, raw: bytes, encoding: str) -> str:
        """Decomprime gzip/brotli/deflate."""
        # ... (codice esistente da router_utils.py)

    def _orig_flags(self, orig: dict | None) -> dict:
        """Estrae flags diagnostici — FIX: cache_control_count corretto."""
        # ... (codice esistente con fix: cache_control_count = count di block con type=text e cache_control)

    def _append_jsonl(self, filename: str, record: dict) -> None:
        """Append atomico con rotazione. Gestisce errori I/O."""
        p = self._logs_dir / filename
        try:
            # Rotazione
            if p.exists() and p.stat().st_size > self.MAX_ERRORS_BYTES:
                rot = p.with_suffix(".jsonl.1")
                try: rot.unlink(missing_ok=True)
                except Exception: pass
                p.rename(rot)
                self._health["rotation_count"] = self._health.get("rotation_count", 0) + 1
            # Append
            with open(p, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            self._debug_error(f"_append_jsonl({filename}) failed: {e}")

    def _catalog_event(self, severity, category, kind, fp, code, snippet, detail) -> None:
        """Wrapper che chiama debug_catalog.record_event() con gestione errori."""
        try:
            import debug_catalog
            debug_catalog.record_event(
                severity=severity, category=category, kind=kind, chat_fp=fp,
                code=code, snippet=snippet, detail=detail,
            )
        except Exception as e:
            self._debug_error(f"_catalog_event failed: {e}")

    def _load_health(self) -> None:
        try:
            if self.HEALTH_FILE.exists():
                self._health = json.loads(self.HEALTH_FILE.read_text())
        except Exception: pass

    def _save_health(self) -> None:
        try:
            self.HEALTH_FILE.write_text(json.dumps(self._health))
        except Exception: pass

    def _warm_errors(self) -> None:
        """Popola la deque errors con le ultime N righe del JSONL."""
        # Legge le ultime maxlen righe dal JSONL all'avvio
        # così la deque non parte vuota dopo un restart
        p = self._logs_dir / "debug-errors.jsonl"
        try:
            if p.exists():
                lines = p.read_text().strip().split("\n")
                for line in lines[-self.MAX_EVENTS_DEQUE:]:
                    try:
                        self.errors.append(json.loads(line))
                    except Exception: pass
        except Exception: pass

    def _warm_events(self) -> None:
        """Come _warm_errors ma per events."""
        p = self._logs_dir / "debug-events.jsonl"
        try:
            if p.exists():
                lines = p.read_text().strip().split("\n")
                for line in lines[-self.MAX_EVENTS_DEQUE:]:
                    try:
                        self.events.append(json.loads(line))
                    except Exception: pass
        except Exception: pass

    def _debug_error(self, msg: str) -> None:
        """Log errori interni del sistema di debug — mai silenced."""
        try:
            err_file = self._logs_dir / "debug-system-errors.log"
            with open(err_file, "a") as f:
                f.write(f"[{self._ts_local()}] INTERNAL: {msg}\n")
        except Exception: pass

    def _write_last_request(self, orig: dict) -> None:
        """Scrivi snapshot request con immagini troncate."""
        # ... (codice esistente da debug_capture)

    def _orig_flags(self, orig: dict | None) -> dict:
        """Estrae flags diagnostici dal body richiesta originale."""
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
                        if b.get("type") == "text":
                            tc = b.get("cache_control", {})
                            if isinstance(tc, dict) and tc:
                                cache_control_count += 1
        return {
            "msg_count": len(msgs),
            "has_tools": bool(orig.get("tools")),
            "has_images": img_count > 0,
            "has_thinking": bool(orig.get("thinking")),
            "cache_control_count": cache_control_count,  # era: img_count (BUG!)
            "system_is_list": isinstance(orig.get("system"), list),
        }

    # ── HTTP endpoints (aiohttp) ──────────────────────────────────────────────
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
            if self._last_sent.exists():
                last_sent = json.loads(self._last_sent.read_text())
        except Exception: pass
        try:
            if self._repair_trace.exists():
                repair_trace = json.loads(self._repair_trace.read_text())
        except Exception: pass
        return web.json_response({
            "last_event": ev, "last_sent": last_sent,
            "repair_trace": repair_trace,
        })

    async def health_endpoint(self, request) -> web.Response:
        """NUOVO endpoint: stato di salute del sistema di debug."""
        return web.json_response({
            "ok": True,
            "health": self._health,
            "errors_deque_len": len(self.errors),
            "events_deque_len": len(self.events),
        })

    def catalog_endpoint(self, request) -> web.Response:
        import debug_catalog
        category = request.query.get("mode") or request.query.get("category")
        severity = request.query.get("severity")
        items = debug_catalog.get_catalog(category=category, severity=severity)
        return web.json_response({"total": len(items), "items": items})

    def catalog_entry_endpoint(self, request) -> web.Response:
        import debug_catalog
        sig = request.match_info.get("signature", "")
        entry = debug_catalog.get_catalog_entry(sig)
        if entry is None:
            return web.json_response({"error": f"signature '{sig}' non trovata"}, status=404)
        return web.json_response(entry)


# ── Singleton ─────────────────────────────────────────────────────────────────
dl = DebugLogger(_LOGS_DIR)

# ── Compatibilità all'indietro ───────────────────────────────────────────────
# Vecchie funzioni che i call site esistenti già usano:
def debug_capture(**kwargs) -> None:
    dl.capture(**kwargs)

# Vecchia deque (per endpoint che la leggono direttamente)
DEBUG_EVENTS = dl.errors  # alias per retrocompatibilità
```

### Modifiche per file

**`src/debug_catalog.py`**
- `_rotated_events_path()` → usa `router_debug.dl._append_jsonl("debug-events.jsonl", ...)`
- Tutti i `except Exception: pass` → `except Exception as e: dl._debug_error(...)`

**`src/router_utils.py`**
- Rimuovi: `_rotated_jsonl_path()`, `debug_capture()`, `debug_errors()`, `debug_last()`, `debug_stats()`, `debug_trace()`, `debug_catalog_endpoint()`, `debug_catalog_entry()`, `_decompress_upstream()`, `_orig_flags()`
- Rimuovi: `DEBUG_EVENTS: deque`, `SENT_ANALYSIS: deque`
- Importa: `from router_debug import dl, debug_capture, DEBUG_EVENTS`
- Keep: `log()`, `log_exc()`, `MinimaxRateLimiter`, `RateLimitExhausted`, `_repair_message_sequence`, `_request_orig_model`

**`src/forward_minimax.py`**
- Importa: `from router_debug import dl`
- Sostituisce `log(...)` con `dl.capture(kind="minimax_...")` per:
  - 429 token plan esaurito
  - 429 RPM/TPM con backoff
  - Contest exceed
  - Rate limit pacing
  - Errori HTTP grezzi (status != 200/429)

**`src/forward_anthropic.py`**
- Importa: `from router_debug import dl`
- Cattura: errori HTTP 4xx/5xx non-429, contest exceed, retry exception

**`src/glm_backend.py`**
- Importa: `from router_debug import dl`
- Cattura: tutti i `except Exception` che loggano solo stringa — passa anche `upstream_raw` e `upstream_encoding` quando disponibili

**`streaming_relay.py`**
- Importa: `from router_debug import dl`
- Sostituisce `self.debug_capture_fn(...)` con `dl.capture(...)`

**`ai-router-proxy.py`**
- Rimuove import di `debug_capture, debug_errors, debug_last, debug_stats, debug_trace, debug_catalog_endpoint, debug_catalog_entry, DEBUG_EVENTS` da `router_utils`
- Aggiunge: `from router_debug import dl`
- Route `/debug/health` → `dl.health_endpoint`
- Tutti i `relay(...)` call: `mode=self.mode` già passato

**Pipeline files** (`pipeline_anthropic.py`, `pipeline_minimax.py`, `pipeline_glm.py`)
- `from router_debug import dl` invece di importare `debug_capture` separatamente

## Non-obiettivi
- Nessuna modifica allo schema JSONL (retrocompatibilità con tool esistenti)
- Nessun DB — solo JSONL, coerente al resto del logging
- Nessuna modifica a `generate_bug_report.py` (legge `BUG-CATALOG.jsonl` invariato)
- Endpoint `/debug/catalog` e `/debug/catalog/{sig}` invariati come interfaccia

## Test
1. Restart router → health file creato con `ts`, `total_errors=0`
2. Una richiesta che genera un errore (es. mock status=500) → health aggiornato
3. Restart router → deque ripopolata da JSONL (`_warm_errors`)
4. `curl http://localhost:8787/debug/health` → JSON con `ok: true`, counters, deque len
5. `curl http://localhost:8787/debug/errors` → funziona come prima
6. `curl http://localhost:8787/debug/stats` → nuovo campo `total_errors`

## Sequenza implementazione (ordine obbligatorio)
1. `src/router_debug.py` (il nuovo modulo base — dipende da nessun altro)
2. `debug_catalog.py` (usa router_debug internamente)
3. `router_utils.py` (rimuove codice spostato)
4. `streaming_relay.py` (usa dl.capture)
5. `forward_minimax.py` (aggiunge debug_capture mancanti)
6. `forward_anthropic.py` (aggiunge debug_capture mancanti)
7. `glm_backend.py` (aggiunge debug_capture mancanti)
8. Pipeline files (pipeline_*.py — usa dl.capture)
9. `ai-router-proxy.py` (route /debug/health, cleanup import)
