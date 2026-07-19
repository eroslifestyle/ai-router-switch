# ~150 lines
"""Catalogo unificato bug/blocco/errore per ogni modalita' del router.
Vedi DEBUG-CATALOG-SPEC.md per schema, algoritmo firma e punti di chiamata."""
import hashlib
import json
import time
from pathlib import Path

_CATALOG_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_LOGS_DIR = _CATALOG_PROJECT_ROOT / "logs"
_CATALOG_LOGS_DIR.mkdir(exist_ok=True)

EVENTS_JSONL = _CATALOG_LOGS_DIR / "debug-events.jsonl"
CATALOG_JSONL = _CATALOG_LOGS_DIR / "BUG-CATALOG.jsonl"

MAX_EVENTS_BYTES = 10 * 1024 * 1024
SNIPPET_MAX_CHARS = 300
VALID_SEVERITIES = ("bug", "block", "error")

_catalog_cache = {"data": None, "ts": 0}
_CACHE_TTL_SEC = 5


def _rotated_events_path() -> Path:
    p = EVENTS_JSONL
    try:
        if p.exists() and p.stat().st_size > MAX_EVENTS_BYTES:
            rot = p.with_suffix(".jsonl.1")
            try:
                rot.unlink()
            except Exception:
                pass
            p.rename(rot)
    except Exception:
        pass
    return p


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

    severity: 'bug' | 'block' | 'error'
    category: modalita' del router che ha generato l'evento (anthropic/minimax/
              mix-am/mix-ag/mix-gm/glm) — usare il mode REALMENTE risolto per la
              richiesta (get_mode/self.mode), mai il file globale.
    kind: tipo specifico (es. 'relay_error_404', 'tool_isolation_strip', 'hhem_reject')
    """
    if severity not in VALID_SEVERITIES:
        severity = "error"
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    snippet = (snippet or "")[:SNIPPET_MAX_CHARS]
    sig = _signature(category, kind, "", code, snippet)

    raw_event = {
        "ts": ts, "sig": sig, "severity": severity, "category": category,
        "kind": kind, "fp": chat_fp, "code": code,
        "snippet": snippet, "detail": detail or {},
    }
    try:
        p = _rotated_events_path()
        with open(p, "a") as f:
            f.write(json.dumps(raw_event, ensure_ascii=False) + "\n")
    except Exception:
        pass

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
            }
        else:
            entry["last_seen"] = ts
            entry["count"] = entry.get("count", 0) + 1
            if category not in entry.get("categories", []):
                entry.setdefault("categories", []).append(category)
            if snippet:
                entry["example_snippet"] = snippet
            if chat_fp:
                entry["example_fp"] = chat_fp
        catalog[sig] = entry
        _save_catalog(catalog)
    except Exception:
        pass

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
