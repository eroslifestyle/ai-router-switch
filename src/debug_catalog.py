# ~150 lines
"""Catalogo unificato bug/blocco/errore per ogni modalita' del router.
Vedi DEBUG-CATALOG-SPEC.md per schema, algoritmo firma e punti di chiamata."""
import hashlib
import json
import os
import time
from pathlib import Path

# AIROUTER_CATALOG_PATH sposta il catalogo altrove. Senza questo override il
# percorso è ancorato alla root del repo, quindi OGNI test che importa il modulo
# scrive nel catalogo di PRODUZIONE. Misurato il 2026-08-06: la suite aggiungeva
# 7 eventi per esecuzione e soprattutto SOVRASCRIVEVA l'example_snippet delle
# entry reali con esempi sintetici — tool_isolation_strip era passato da
# "kept=72/73" di produzione a "kept=0/1" di test — cioè proprio il campo che
# rende il catalogo diagnostico. La conftest lo punta a una tmpdir.
_CATALOG_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_LOGS_DIR = _CATALOG_PROJECT_ROOT / "logs"

_catalog_override = os.environ.get("AIROUTER_CATALOG_PATH", "").strip()
if _catalog_override:
    CATALOG_JSONL = Path(_catalog_override)
    _CATALOG_LOGS_DIR = CATALOG_JSONL.parent
else:
    CATALOG_JSONL = _CATALOG_LOGS_DIR / "BUG-CATALOG.jsonl"

# mkdir non deve mai impedire l'import: se la directory non è creabile fallirà la
# scrittura, che è già gestita dove avviene. parents=True perché un override può
# puntare a una directory annidata non ancora esistente.
try:
    _CATALOG_LOGS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

SNIPPET_MAX_CHARS = 300
# "info" (2026-07-26): eventi osservativi, non anomalie — es. l'heartbeat del
# gate di contesto. Senza questo valore record_event li degradava a "error",
# inquinando proprio la metrica degli errori che servono a osservare.
VALID_SEVERITIES = ("bug", "block", "error", "info")

_catalog_cache = {"data": None, "ts": 0}
_CACHE_TTL_SEC = 5


def _dl():
    """Lazy import di router_debug per evitare circular import."""
    from router_debug import dl
    return dl


def _sanitize_snippet(s: str) -> str:
    """Toglie i byte non testuali: i chiamanti passano anche risposte grezze
    decodificate con errors='replace' e, se lo stream era gzip, finivano NUL nel
    catalogo (155 in BUG-CATALOG.md, che git classificava binario — 2026-08-02)."""
    if not s:
        return ""
    unreadable = sum(1 for c in s if (ord(c) < 32 and c not in "\t\n") or ord(c) == 0xFFFD)
    if unreadable > len(s) * 0.3:
        return f"[binario non testuale, {len(s)} caratteri]"
    return "".join(c for c in s if (ord(c) >= 32 or c in "\t\n") and ord(c) != 0xFFFD)


def _signature(category: str, kind: str, mode: str, code, snippet: str) -> str:
    """Firma stabile per deduplicare occorrenze dello stesso tipo di bug/blocco/errore.
    Esclude cifre dallo snippet (id/timestamp variano tra occorrenze identiche)."""
    norm_snippet = "".join(ch for ch in (snippet or "")[:120] if not ch.isdigit())
    raw = "|".join((category, kind, str(code), norm_snippet))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _title_for(category: str, kind: str, code) -> str:
    code_part = f" ({code})" if code not in (None, "") else ""
    return f"[{category}] {kind}{code_part}"


def _load_catalog() -> dict:
    now = time.time()
    if _catalog_cache["data"] is not None and now - _catalog_cache["ts"] < _CACHE_TTL_SEC:
        return _catalog_cache["data"]
    d = {}
    try:
        with open(CATALOG_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    d[entry["signature"]] = entry
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _catalog_cache["data"] = d
    _catalog_cache["ts"] = now
    return d


def _save_catalog(d: dict) -> None:
    try:
        tmp = CATALOG_JSONL.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for entry in d.values():
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        tmp.replace(CATALOG_JSONL)
        _catalog_cache["data"] = d
        _catalog_cache["ts"] = time.time()
    except Exception:
        pass


def record_event(*, severity: str, category: str, kind: str, chat_fp: str = "",
                  detail: dict = None, snippet: str = "", code=None) -> str:
    """Registra un evento bug/block/error nel catalogo unificato. Ritorna la firma.

    severity: 'bug' | 'block' | 'error' | 'info' (osservativo, non anomalia)
    category: modalita' del router che ha generato l'evento (anthropic/minimax/
              mix-am/mix-ag/mix-gm/glm) — usare il mode REALMENTE risolto per la
              richiesta (get_mode/self.mode), mai il file globale.
    kind: tipo specifico (es. 'relay_error_404', 'tool_isolation_strip', 'hhem_reject')
    detail: dizionario diagnostico; viene serializzato in JSON e conservato (troncato) come example_detail dell'ultima occorrenza.
    """
    if severity not in VALID_SEVERITIES:
        severity = "error"
    # Offset esplicito, non "Z": il formato precedente stampava l'ora LOCALE con
    # il suffisso che significa UTC (2 h di scarto in CEST), e chi leggeva il
    # catalogo confrontava orari falsi. Si usa %z invece di convertire in UTC per
    # non creare un salto all'indietro rispetto alle entry già scritte.
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    snippet = _sanitize_snippet((snippet or "")[:SNIPPET_MAX_CHARS])
    # Limite caratteri per il detail serializzato (1000)
    _detail_max = 1000
    _detail_safe = ""
    if detail:
        # json è già importato a livello modulo: un import locale lo renderebbe
        # locale all'intera funzione (rischio UnboundLocalError).
        try:
            _detail_safe = json.dumps(detail, ensure_ascii=False, default=str)[:_detail_max]
        except Exception:
            _detail_safe = str(detail)[:_detail_max]
    sig = _signature(category, kind, "", code, snippet)

    # Note: DEBUG_EVENTS_JSONL è gia' scritto da router_debug.capture()
    # che chiama qui dentro _catalog_event(). Scriviamo SOLO il BUG-CATALOG dedup.
    try:
        catalog = _load_catalog()
        entry = catalog.get(sig)
        if entry is None:
            entry = {
                "signature": sig, "title": _title_for(category, kind, code),
                "severity": severity, "kind": kind, "code": code,
                "categories": [category],
                "first_seen": ts, "last_seen": ts, "count": 1,
                "example_snippet": snippet, "example_fp": chat_fp,
                "example_detail": _detail_safe,
            }
        else:
            entry["last_seen"] = ts
            entry["count"] = entry.get("count", 0) + 1
            # Promuove la severità: un'entry nata da un evento osservativo non deve
            # restare "info" quando la stessa firma torna come errore reale.
            _rank = {"info": 0, "block": 1, "error": 2, "bug": 3}
            if _rank.get(severity, 0) > _rank.get(entry.get("severity", "info"), 0):
                entry["severity"] = severity
            if category not in entry.get("categories", []):
                entry.setdefault("categories", []).append(category)
            if snippet:
                entry["example_snippet"] = snippet
            if chat_fp:
                entry["example_fp"] = chat_fp
            if _detail_safe:
                entry["example_detail"] = _detail_safe
        catalog[sig] = entry
        _save_catalog(catalog)
    except Exception as e:
        _dl()._debug_err(f"record_event failed: {e}")

    return sig


def get_catalog(category: str = None, severity: str = None) -> list:
    catalog = _load_catalog()
    items = list(catalog.values())
    if category:
        items = [i for i in items if category in i.get("categories", [])]
    if severity:
        items = [i for i in items if i.get("severity") == severity]
    items.sort(key=lambda i: i.get("last_seen", ""), reverse=True)
    return items


def get_catalog_entry(signature: str):
    return _load_catalog().get(signature)
