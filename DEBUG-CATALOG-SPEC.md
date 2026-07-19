# Ultra Deep Debug System — DEBUG-CATALOG-SPEC

## Obiettivo
Catturare, persistere in modo durevole e documentare OGNI bug/blocco/errore in
TUTTE le modalita' del router (`anthropic`, `minimax`, `mix-am`, `mix-ag`,
`mix-gm`, `glm`) — non solo gli errori HTTP grezzi che l'infrastruttura
precedente (`debug_capture()`) copriva in modo parziale (5 "kind", quasi tutti
concentrati in mix-am).

## Gap risolti rispetto all'infrastruttura precedente
- **Copertura**: mix-ag/mix-gm/glm/minimax-puro/anthropic-puro avevano zero
  cattura strutturata, solo `log()` testuale non interrogabile.
- **Attribuzione mode**: `debug_capture()` usava `get_file_mode()` (il file
  globale) invece del mode realmente risolto per la richiesta — attribuzione
  fuorviante quando l'override per-chat differiva dal file. Ora usa
  `get_mode(request, fp)` (stessa risoluzione canonica forced→per-chat→file),
  o il mode esplicito passato dal chiamante quando disponibile (es.
  `StreamingRelay.mode`).
- **Retention**: prima solo RAM (100 eventi, persi al restart) + JSONL che
  ruota a 10MB su un solo `.1` (storia oltre 2 rotazioni persa), senza
  deduplicazione — lo stesso bug ricorrente produceva N righe identiche.
- **"Blocchi" invisibili**: tool_isolation strip, warning HHEM, backoff 429,
  escalation — nessuno produceva un record persistente/interrogabile.

## Schema

### Evento raw (`logs/debug-events.jsonl`, rotazione 10MB→`.1` come `debug-errors.jsonl`)
```json
{
  "ts": "2026-07-19T19:40:52Z",
  "sig": "9fb265787ba5870f",
  "severity": "bug | block | error",
  "category": "anthropic | minimax | mix-am | mix-ag | mix-gm | glm",
  "kind": "es. minimax_fallback_5xx, tool_isolation_strip, hhem_warning",
  "fp": "chat fingerprint (sid:... o hash content)",
  "code": "status HTTP o codice applicativo, puo' essere null",
  "snippet": "estratto troncato (max 300 char)",
  "detail": {"...": "metadata specifico del kind"}
}
```

### Voce catalogo (`logs/BUG-CATALOG.jsonl`, deduplicato, riscritto atomicamente)
```json
{
  "signature": "9fb265787ba5870f",
  "title": "[mix-am] minimax_fallback_5xx (502)",
  "severity": "error",
  "kind": "minimax_fallback_5xx",
  "code": 502,
  "categories": ["mix-am"],
  "first_seen": "2026-07-19T19:40:52Z",
  "last_seen": "2026-07-19T19:44:12Z",
  "count": 7,
  "example_snippet": "...",
  "example_fp": "sid:..."
}
```

## Algoritmo firma (dedup)
`signature = sha256(category|kind|code|snippet_normalizzato)[:16]`

`snippet_normalizzato` = primi 120 char dello snippet con le cifre rimosse
(id/timestamp variano tra occorrenze identiche dello stesso bug e
romperebbero la dedup se inclusi). Stesso bug ricorrente → stessa firma →
un'unica voce nel catalogo con `count` incrementale, invece di N righe
duplicate.

## Retention
| Storage | Capacita' | Rotazione |
|---|---|---|
| `logs/debug-events.jsonl` | illimitato | 10MB → `.1` (come `debug-errors.jsonl`) |
| `logs/BUG-CATALOG.jsonl` | deduplicato, resta piccolo | nessuna (riscritto atomico tmp+replace) |
| `DEBUG_EVENTS` (RAM) | 100 eventi | FIFO, invariato dall'infrastruttura precedente |

Entrambi i file sono in `logs/` e gitignorati (`logs/*.jsonl`), coerenti col
resto del logging del progetto.

## Interfaccia (`src/debug_catalog.py`)
```python
record_event(*, severity: str, category: str, kind: str, chat_fp: str = "",
             detail: dict = None, snippet: str = "", code=None) -> str  # ritorna la firma

get_catalog(category: str = None, severity: str = None) -> list  # ordinato per last_seen desc
get_catalog_entry(signature: str) -> dict | None
```

`router_utils.debug_capture()` chiama internamente `record_event()` — i call
site esistenti (5 kind storici) guadagnano il catalogo senza modifiche.

## Punti di chiamata per modalita'

| Modalita' | File | Eventi catturati |
|---|---|---|
| mix-am | `pipeline_anthropic.py` | BYPASS-THINK fallback, THINK EXC/ko/piano-vuoto, ACT executor fail (`minimax_fallback_5xx`/`minimax_context_exceed`), rescue chain (`mixed_rescue_502`), vision fallback |
| mix-am | `ai-router-proxy.py` | FAST-PATH MiniMax fallback status |
| mix-ag | `pipeline_glm.py` | THINK exception/failed, GLM ACT fail, VERIFY exception |
| mix-gm | `pipeline_glm.py` | THINK exception, MiniMax ACT fail, **HHEM warning** (`hhem_warning`), VERIFY exception/incoherent |
| glm (puro) | `glm_backend.py` `forward_glm()` | backoff 429, retry 5xx, timeout, client error, eccezione, esaurimento tentativi |
| minimax (puro) | `pipeline_minimax.py` | THINK exception/fail/piano-invalido, ACT exception/fail, VERIFY exception |
| anthropic (puro) | `ai-router-proxy.py` | forward exception, backoff burst-limiter 429 |
| trasversale (tutte) | `tool_isolation.py` `filter_tools_for_backend()` | ogni strip di tool brandizzati di provider diverso — choke-point comune a tutti i `forward_*` |
| trasversale (tutte) | `streaming_relay.py` | `relay_error_{status}` con `mode=self.mode` esplicito (il piu' affidabile) |

## Endpoint HTTP (`GET`, stessa famiglia di `/debug/errors` ecc., bypassano auth)
- `GET /debug/catalog[?mode=<categoria>][?severity=bug|block|error]` — catalogo
  filtrato, ordinato per `last_seen` desc.
- `GET /debug/catalog/{signature}` — dettaglio di una voce, 404 se non trovata.

## Documentazione generata
`scripts/generate_bug_report.py` legge `logs/BUG-CATALOG.jsonl` e scrive
`BUG-CATALOG.md` in root, raggruppato per modalita' → severita', ordinato per
occorrenze. Invocazione **manuale** (`python3 scripts/generate_bug_report.py`),
nessun overhead sul path critico. Rilanciarlo dopo eventi significativi o
periodicamente; non e' auto-triggerato a ogni evento.

## Non-obiettivi (scope deliberatamente escluso)
- Nessun nuovo storage engine (SQLite/DB) — solo JSONL, coerente al 100% con
  tutto il resto del logging del progetto.
- Nessuna cattura full-trace del happy-path (ogni richiesta riuscita) — solo
  bug/blocco/errore, per restare interrogabile e non duplicare
  `debug-last-request.json`/`debug-last-sent.json` gia' esistenti.
- Restart forzati del watchdog esterno (`ai-router-watchdog.sh`,
  `ai-router-freeze-watchdog.sh`) restano nei loro log dedicati
  (`~/.claude/logs/ai-router-watchdog.log`) — non integrati nel catalogo per
  non accoppiare lo script bash al processo Python del router.
