"""Richiama i messaggi vecchi PERTINENTI, invece di tenere solo i piu' recenti.

Il problema. Con una finestra locale da 65k o 131k, una sessione di lavoro vera non
ci sta. La finestra scorrevole (`context_rewrite._fit_keep`) tiene quanti piu'
messaggi recenti entrano, ma "recente" e "utile" non sono la stessa cosa: la
decisione presa duecento messaggi fa e' quella che serve adesso, e cade fuori.

Cosa NON serve. Archiviare per non perdere: il client rimanda la storia completa a
ogni turno, quindi nel corpo c'e' gia' tutto. La selezione si fa in memoria, sul
corpo stesso, senza storage e senza rete.

Cosa serve davvero l'archivio su disco: sopravvivere a `/compact`. Dopo una
compattazione il client butta la storia e non la rimanda piu'; da li' in poi
l'unica copia dei messaggi vecchi e' quella che abbiamo messo da parte noi.

Come si sceglie: BM25 sui messaggi scartati, con query il testo dell'ultimo
messaggio utente. Nessun embedding, nessuna chiamata di rete, nessun token speso —
la latenza deve restare quella di un proxy.

Spegnibili separatamente: AIROUTER_CTX_RECALL=0 (selezione), AIROUTER_CTX_ARCHIVE=0
(scrittura su disco).
"""

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

RECALL_ENABLED = os.environ.get("AIROUTER_CTX_RECALL", "1") not in ("0", "false", "no")
ARCHIVE_ENABLED = os.environ.get("AIROUTER_CTX_ARCHIVE", "1") not in ("0", "false", "no")
RECALL_TOP_K = int(os.environ.get("AIROUTER_CTX_RECALL_K", "4"))
# Quota del budget del riassunto che va agli estratti richiamati.
RECALL_BUDGET_SHARE = float(os.environ.get("AIROUTER_CTX_RECALL_SHARE", "0.5"))
# Sotto questa lunghezza un messaggio non porta informazione utile al richiamo.
MIN_DOC_CHARS = 120
MAX_SNIPPET_CHARS = 1_200
# Tetto per file di archivio: oltre, si tengono le voci piu' recenti.
MAX_ARCHIVE_ENTRIES = 2_000

ARCHIVE_DIR = Path(os.environ.get(
    "AIROUTER_CTX_ARCHIVE_DIR", str(Path.home() / ".claude" / "state" / "ctx-offload")))

_WORD_RE = re.compile(r"[a-z0-9_]{3,}")
# Parole troppo comuni per discriminare: in italiano, inglese e nel gergo di questi
# corpi (tool, assistant...). Restano fuori dall'indice.
_STOPWORDS = frozenset("""
the and for that with this from you your are was not but have has had can will
per che non con una uno del della delle dei degli come piu' cioe' quindi anche
sono stato essere fare fatto quando dove perche' quale quali tutto tutti
assistant user tool result content type text file path json true false null
""".split())

BLOCK_HEADER = (
    "[Estratti richiamati dalla conversazione precedente — NON sono messaggi recenti, "
    "sono passaggi piu' vecchi selezionati perche' pertinenti alla richiesta attuale.]"
)


# ── testo dei messaggi ──────────────────────────────────────────────────────

def message_text(msg: dict) -> str:
    """Testo leggibile di un messaggio, qualunque sia la forma del content."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_use":
            parts.append(f"{block.get('name', '')} {json.dumps(block.get('input'), ensure_ascii=False)}")
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                parts.append(inner)
            elif isinstance(inner, list):
                parts.extend(s.get("text", "") for s in inner
                             if isinstance(s, dict) and s.get("type") == "text")
    return "\n".join(p for p in parts if p)


def _tokens(text: str) -> list:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


def query_text(msgs: list) -> str:
    """La richiesta corrente: l'ultimo messaggio utente che non sia solo tool_result."""
    for msg in reversed(msgs):
        if msg.get("role") != "user":
            continue
        text = message_text(msg)
        if text.strip():
            return text
    return ""


# ── BM25 ────────────────────────────────────────────────────────────────────

def _bm25_rank(query: str, docs: list, top_k: int) -> list:
    """Indici dei `docs` piu' pertinenti alla query, dal migliore. BM25 classico.

    k1=1.5 e b=0.75 sono i valori canonici: qui non c'e' niente da tarare, i
    documenti sono messaggi di chat di lunghezza molto varia ed e' esattamente il
    caso per cui la normalizzazione sulla lunghezza di BM25 esiste.
    """
    q_terms = set(_tokens(query))
    if not q_terms or not docs:
        return []

    tokenized = [_tokens(d) for d in docs]
    lengths = [len(t) for t in tokenized]
    avg_len = sum(lengths) / len(lengths) if lengths else 0.0
    if not avg_len:
        return []

    df = Counter()
    for terms in tokenized:
        for term in set(terms) & q_terms:
            df[term] += 1

    k1, b = 1.5, 0.75
    n_docs = len(docs)
    scored = []
    for i, terms in enumerate(tokenized):
        if not terms:
            continue
        freq = Counter(terms)
        score = 0.0
        for term in q_terms:
            f = freq.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            norm = f * (k1 + 1) / (f + k1 * (1 - b + b * lengths[i] / avg_len))
            score += idf * norm
        if score > 0:
            scored.append((score, i))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scored[:top_k]]


# ── archivio su disco (serve solo a sopravvivere a /compact) ────────────────

def _archive_path(fp: str) -> Path:
    safe = hashlib.sha1(fp.encode("utf-8", "replace")).hexdigest()[:16]
    return ARCHIVE_DIR / f"{safe}.jsonl"


def archive(fp: str, dropped: list) -> None:
    """Mette da parte i messaggi usciti dalla finestra. Silenzioso su errore."""
    if not (ARCHIVE_ENABLED and fp and dropped):
        return
    try:
        path = _archive_path(fp)
        path.parent.mkdir(parents=True, exist_ok=True)

        visti = set()
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                visti = {json.loads(r).get("h") for r in fh if r.strip()}

        nuove = []
        for msg in dropped:
            text = message_text(msg)
            if len(text) < MIN_DOC_CHARS:
                continue
            h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
            if h in visti:
                continue
            visti.add(h)
            nuove.append(json.dumps({"h": h, "role": msg.get("role", ""), "t": text},
                                    ensure_ascii=False))

        if not nuove:
            return
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(nuove) + "\n")
        _trim_archive(path)
    except Exception as e:
        log.warning("ctx-archive fallito fp=%s: %s", fp, e)


def _trim_archive(path: Path) -> None:
    """Tiene solo le ultime MAX_ARCHIVE_ENTRIES voci: e' una cache, non un log."""
    try:
        righe = path.read_text(encoding="utf-8").splitlines()
        if len(righe) <= MAX_ARCHIVE_ENTRIES:
            return
        path.write_text("\n".join(righe[-MAX_ARCHIVE_ENTRIES:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _archived_docs(fp: str) -> list:
    if not (ARCHIVE_ENABLED and fp):
        return []
    try:
        path = _archive_path(fp)
        if not path.exists():
            return []
        docs = []
        with path.open(encoding="utf-8") as fh:
            for riga in fh:
                if not riga.strip():
                    continue
                try:
                    docs.append(json.loads(riga))
                except Exception:
                    continue
        return docs
    except Exception:
        return []


# ── blocco da iniettare ─────────────────────────────────────────────────────

def build_recall_block(fp: str, msgs: list, dropped: list, budget_chars: int) -> str:
    """Estratti pertinenti dai messaggi scartati, pronti da appendere al system.

    Cerca sia fra i messaggi appena scartati (che sono ancora nel corpo) sia fra
    quelli archiviati in passato — dopo un /compact restano solo i secondi.
    Stringa vuota se non c'e' nulla di pertinente: in quel caso il comportamento
    e' identico a prima.
    """
    if not RECALL_ENABLED or budget_chars <= 0:
        return ""

    query = query_text(msgs)
    if not query.strip():
        return ""

    candidati = []
    visti = set()
    for msg in dropped:
        text = message_text(msg)
        if len(text) < MIN_DOC_CHARS:
            continue
        h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
        if h in visti:
            continue
        visti.add(h)
        candidati.append((msg.get("role", ""), text))

    for voce in _archived_docs(fp):
        h, text = voce.get("h"), voce.get("t", "")
        if not text or h in visti:
            continue
        visti.add(h)
        candidati.append((voce.get("role", ""), text))

    if not candidati:
        return ""

    migliori = _bm25_rank(query, [t for _, t in candidati], RECALL_TOP_K)
    if not migliori:
        return ""

    pezzi, usati = [], len(BLOCK_HEADER)
    for i in migliori:
        role, text = candidati[i]
        snippet = text[:MAX_SNIPPET_CHARS]
        if len(text) > MAX_SNIPPET_CHARS:
            snippet += "\n…[estratto troncato]"
        voce = f"\n\n— da un messaggio {role or 'precedente'}:\n{snippet}"
        if usati + len(voce) > budget_chars:
            break
        pezzi.append(voce)
        usati += len(voce)

    if not pezzi:
        return ""
    return BLOCK_HEADER + "".join(pezzi)
