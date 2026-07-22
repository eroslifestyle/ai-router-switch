# Ultra Deep Debug System — piano

## Obiettivo
Un sistema unificato che cattura, persiste in modo durevole (oltre l'attuale RAM-100/JSONL-10MB-poi-perso) e documenta OGNI bug/blocco/errore in TUTTE le modalità del router (anthropic, minimax, mix-am, mix-ag, mix-gm, glm) — non solo gli errori HTTP grezzi come oggi.

## Gap trovati nell'infrastruttura attuale (ricerca fatta)
- `debug_capture()` (router_utils.py) copre solo 5 "kind" HTTP-error-shaped, concentrati quasi tutti in mix-am (`relay_error_*`, `minimax_fallback_5xx`, `minimax_context_exceed`, `minimax_vision_fallback`, `mixed_rescue_502`). mix-ag/mix-gm/glm/minimax puro/anthropic puro fanno solo `log()` testuale, niente di strutturato/persistente.
- **Bug scoperto oggi**: il campo `"mode"` di ogni evento catturato usa `get_file_mode()` (il file globale), NON il mode realmente risolto per quella richiesta — ha reso fuorviante mezza indagine di questa sessione. Va corretto come parte di questo lavoro.
- Zero eventi per i "blocchi": strip di tool_isolation, risposte web_search-bloccato, warning/reject HHEM, backoff su 429, trigger di escalation (`mixed_anthropic_leads`), restart forzati dal watchdog — tutto invisibile fuori dai log testuali.
- Retention: RAM ring buffer 100 eventi (persi al restart), JSONL ruota a 10MB su UN solo `.1` (storia oltrei 2 rotation persa). Nessun catalogo deduplicato di "quali bug distinti sono mai comparsi".
- Nessuna deduplicazione: lo stesso bug ricorrente (es. il 404 MiniMax di oggi) produce N righe identiche senza raggruppamento/conteggio/first-seen/last-seen.

## Design

### 1. Nuovo modulo `src/debug_catalog.py`
- `record_event(*, category, kind, mode, chat_fp, request=None, detail: dict, severity)` — punto d'ingresso unico, superset dell'attuale `debug_capture()`.
- Firma stabile per evento distinto: hash(category + kind + mode + status/code + estratto normalizzato) → usata per dedup.
- Append del raw event su `logs/debug-events.jsonl` (nuovo stream, più ampio dell'attuale `debug-errors.jsonl` che resta invariato per compatibilità con watchdog/consumer esistenti).
- Catalogo deduplicato `logs/BUG-CATALOG.jsonl`: una riga per firma distinta, upsert atomico (tmp+replace, stesso pattern di `CHAT_STORE`) con signature, category, kind, mode(s), first_seen, last_seen, count, ultimo esempio troncato, titolo human-readable auto-generato.
- `debug_capture()` esistente viene esteso per richiamare internamente `record_event()` → i 5 call site attuali guadagnano il catalogo gratis, zero modifiche ai chiamanti.

### 2. Fix attribuzione mode
Ogni nuovo call site passa il `mode` realmente risolto (variabile già calcolata in `handle()`), non più `get_file_mode()` globale.

### 3. Istrumentazione per modalità (nuovi call site)
- **mix-am** (`pipeline_anthropic.py`): trigger BYPASS-THINK verso fallback, fallimenti THINK, step della rescue chain, escalation `mixed_anthropic_leads`.
- **mix-ag / mix-gm** (`pipeline_glm.py`): fallimenti THINK/ACT/VERIFY, warning HHEM (già calcolati, mai persistiti), esito loop retry-con-correzione.
- **glm puro** (`glm_backend.py`): backoff 429, retry 5xx, esaurimento tentativi, capping tier (peak scheduler).
- **minimax puro** (`pipeline_minimax.py`): fail/retry della sua pipeline.
- **anthropic puro**: retry su burst-limiter 429, fallimenti reload OAuth.
- **Trasversali**: `tool_isolation.py` (ogni strip), risposta web_search-bloccato, reject HHEM (`hhem_gate.py`).

### 4. Nuovi endpoint HTTP (famiglia `/debug/*` esistente)
- `GET /debug/catalog` — catalogo deduplicato, ordinato per last_seen, filtrabile `?mode=` `?category=`.
- `GET /debug/catalog/{signature}` — dettaglio + esempi raw recenti.

### 5. Generatore documentazione
- `scripts/generate_bug_report.py` — legge `BUG-CATALOG.jsonl`, scrive `BUG-CATALOG.md` in root (stesso stile delle `*-SPEC.md` esistenti), raggruppato per mode → severity, con conteggi/first-seen/last-seen/esempio. Invocazione manuale (no overhead su ogni evento).

### 6. Documentazione design
- `DEBUG-CATALOG-SPEC.md` in root, stesso formato di `fail-tracker-SPEC.md`/`streaming-relay-SPEC.md`: obiettivo, schema, algoritmo firma, retention, punti di chiamata per pipeline.

## Scelte tecniche (coerenti con la codebase esistente)
- Solo JSONL, nessuna nuova dipendenza (niente SQLite) — coerente al 100% con tutto il logging già presente.
- Retention raw: stessa rotazione 10MB→`.1` già in uso.
- Catalogo: deduplicato, resta piccolo, riscritto atomicamente.

## Ordine di implementazione (commit incrementali, come nel resto della sessione)
1. `src/debug_catalog.py` (core: record_event + firma + upsert catalogo + append raw) + estensione `debug_capture()` esistente.
2. Fix attribuzione mode nei 5 call site esistenti.
3. Nuova istrumentazione nelle 6 modalità (elenco sopra).
4. Endpoint HTTP `/debug/catalog[/id]`.
5. `scripts/generate_bug_report.py` + primo `BUG-CATALOG.md` generato.
6. `DEBUG-CATALOG-SPEC.md`.
7. Restart + smoke test live (trigger deliberato di un path di errore, verifica comparsa nel catalogo + nel report).

Ogni step = commit separato, push, verifica (compile-check + restart + log check) come fatto finora in sessione.
