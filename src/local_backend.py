"""Backend proxy per provider LLM locale via LiteLLM."""
import asyncio
import re
import json
import os
import tempfile
from typing import Optional, Callable

import aiohttp
from aiohttp import web
from multidict import CIMultiDict

import debug_catalog
import paths
import tool_isolation
import secrets_provider
from synthetic_response import synthetic_error
from context_rewrite import rewrite_for_context, _STICKY_DROP_COUNT, _STICKY_SUMMARY
from token_counter import estimate_tokens_body

LOCAL_MODEL_CODE = 'code-max'
# Era 'code-fast', tolto il 2026-08-19 (decisione utente: un solo modello locale).
# Puntato a code-max e non rimosso perche' e' nell'allow-list di
# resolve_local_model: chi chiedesse ancora il vecchio nome finisce su code-max
# invece di essere scartato in silenzio.
LOCAL_MODEL_FAST = LOCAL_MODEL_CODE
# THINK locale della modalità gpt: coder-abliterated su llama.cpp :8085 (via LiteLLM).
# Senza questa voce nell'allow-list di resolve_local_model, il THINK di gpt veniva
# scartato su code-max (:8083) — resolve_route lo instrada bene, ma qui ripiegava.
LOCAL_MODEL_THINK = 'coder-abliterated'
# Nessun fallback: il modello locale ha una sola via, llama.cpp :8083 dietro LiteLLM.
# L'alias Ollama code-max-ollama e' stato rimosso il 2026-08-04 (duplicazione da 48GB).
LOCAL_MODEL_FALLBACK = LOCAL_MODEL_CODE  # default quando non specificato
# Il client Claude Code taglia a API_TIMEOUT_MS (300s di default): il router DEVE
# scadere PRIMA, altrimenti dopo l'abbandono del client la richiesta continua a
# occupare uno slot GPU di llama.cpp e la saturazione si auto-alimenta. Questo
# tetto resta INVARIATO (vincolo di stabilita' del sistema, non negoziabile qui):
# vedi LOCAL_IDLE_READ_SEC sotto per il fix del 2026-09-02.
LOCAL_TIMEOUT_SEC = int(os.environ.get('AIROUTER_LOCAL_TIMEOUT_SEC', 240))
# Idle-read timeout (2026-09-02, audit velocita' reale — sviluppo/audit/2026-09-02-
# audit-velocita-reale/REPORT.md §4.1): 146 richieste (0,06% del totale, 63 su
# code-max) restavano appese fino al tetto LOCAL_TIMEOUT_SEC con status 200 e un
# outcome "empty" (nessun byte utile arrivato in tutto quel tempo) — 240s pieni
# sprecati per scoprire un fallimento che con connessione davvero silenziosa era
# gia' evidente molto prima. sock_read aborta appena il SILENZIO fra due letture
# supera questa soglia, mentre LOCAL_TIMEOUT_SEC resta il tetto assoluto invariato
# (una generazione che manda byte regolarmente, anche lenta, non viene toccata da
# questa soglia: puo' correre fino al tetto totale come oggi). Deliberatamente
# SOTTO il caso di prefill-lungo documentato in role_routing.py (104K token di
# schemi-tool, ~250 tok/s, puo' gia' superare 240s TOTALI): quel caso e' gia'
# oltre LOCAL_TIMEOUT_SEC stesso e viene gestito lasciando correre finche' arriva
# ALMENO un byte (keep-alive/primo chunk) entro questa soglia — non e' il target
# di questo fix, che riguarda le connessioni COMPLETAMENTE silenziose.
LOCAL_IDLE_READ_SEC = int(os.environ.get('AIROUTER_LOCAL_IDLE_READ_SEC', 90))
# Retry solo su errori di connessione (rapidi). Su timeout NON si ritenta: vedi forward_local.
LOCAL_MAX_RETRY = 2

def _local_env_file():
    """File .env dedicato al backend locale: non e quello della config dir."""
    return paths.secrets_dir() / "local-llm.env"


async def get_local_key() -> str:
    """Chiave del provider locale: env LOCAL_LLM_API_KEY, poi secrets/local-llm.env."""
    return await secrets_provider.get_secret_async(
        "local_llm.api_key", extra_env=("LOCAL_LLM_API_KEY",), env_files=(_local_env_file(),),
    )


def get_local_base() -> str:
    """URL base del provider locale; default http://127.0.0.1:4000 se non configurato."""
    base = secrets_provider.get_secret(
        "local_llm.api_base", extra_env=("LOCAL_LLM_API_BASE",), env_files=(_local_env_file(),),
    )
    return (base or "http://127.0.0.1:4000").rstrip("/")


def set_body_model(body: bytes, model: str) -> bytes:
    """Imposta il campo 'model' nel body JSON e ritorna il body modificato."""
    try:
        data = json.loads(body)
        data['model'] = model
        return json.dumps(data).encode()
    except (json.JSONDecodeError, TypeError):
        return body


def resolve_local_model(requested: Optional[str]) -> str:
    """Restituisce il modello richiesto se consentito, altrimenti LOCAL_MODEL_CODE."""
    if requested in (LOCAL_MODEL_CODE, LOCAL_MODEL_FALLBACK, LOCAL_MODEL_FAST, LOCAL_MODEL_THINK):
        return requested
    return LOCAL_MODEL_CODE


# --- 400 exceed_context_size_error (2026-10-01): llama.cpp rifiuta il prompt
# oltre la finestra e LiteLLM avvolge l'errore in ContextWindowExceededError;
# il JSON con n_prompt_tokens/n_ctx e' annidato dentro una stringa, quindi si
# estraggono con regex. Un solo retry con body accorciato sul limite reale.
_CTX_EXCEEDED_RE = re.compile(
    r"exceed_context_size_error|ContextWindowExceededError", re.IGNORECASE)
_PROMPT_TOKENS_RE = re.compile(r"n_prompt_tokens['\"\\]*\s*[:=]\s*(\d+)")
_N_CTX_RE = re.compile(r"n_ctx['\"\\]*\s*[:=]\s*(\d+)")


def parse_ctx_exceeded(text: str) -> Optional[tuple]:
    """Estrae (n_prompt_tokens, n_ctx) dal corpo di un 400 ctx-exceeded, o None."""
    if not _CTX_EXCEEDED_RE.search(text):
        return None
    try:
        prompt_tokens = int(_PROMPT_TOKENS_RE.search(text).group(1))
        n_ctx = int(_N_CTX_RE.search(text).group(1))
        return (prompt_tokens, n_ctx)
    except (AttributeError, ValueError):
        return None


# --- Correzione difensiva stop_reason (LiteLLM 1.95.0 non mappa finish_reason
# 'length' su stop_reason 'max_tokens': il client crede completa una risposta
# troncata). La trasformazione avviene chunk per chunk: lo streaming resta intatto.
# ELEMENTO 1
def requested_max_tokens(body: bytes) -> int | None:
    try:
        obj = json.loads(body)
        return obj.get("max_tokens")
    except Exception:
        return None


# --- Iniezione istruzione visione (SOLO modalità local): il modello locale
# è di solo testo e va istruito a delegare le immagini ai tool vision_local/ocr_image.
# Costante per il sistema
LOCAL_SYSTEM_HINT = (
    "Sei un modello locale di solo testo: non puoi vedere le immagini direttamente. "
    "Quando l'utente allega o menziona un'immagine o uno screenshot: per capire il CONTENUTO visivo "
    "(forme, colori, oggetti, interfacce, grafici, scene) usa il tool vision_local passando image_path; "
    "per estrarre solo il TESTO da un'immagine usa il tool ocr_image. "
    "Non tentare mai di leggere un'immagine senza questi tool, perche' fallirebbe con un errore."
)


def inject_system_hint(body: bytes) -> bytes:
    """
    Inietta l'hint per la visione nel campo system della richiesta.
    Idempotente: non duplica l'hint se gia' presente.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        # Body non e' JSON valido, inoltra invariato
        return body

    system = data.get("system")

    # Caso assente o None: imposta l'hint
    if system is None:
        data["system"] = LOCAL_SYSTEM_HINT

    # Caso stringa: appendi hint se non gia' presente
    elif isinstance(system, str):
        if "vision_local" not in system:
            data["system"] = system + "\n\n" + LOCAL_SYSTEM_HINT

    # Caso lista di blocchi: aggiungi hint in coda se non gia' presente
    elif isinstance(system, list):
        hint_gia_presente = any(
            isinstance(b, dict) and "vision_local" in b.get("text", "")
            for b in system
        )
        if not hint_gia_presente:
            data["system"] = system + [{"type": "text", "text": LOCAL_SYSTEM_HINT}]

    # Altri tipi (dict, int, etc.): non toccare
    else:
        return body

    try:
        return json.dumps(data).encode()
    except (TypeError, ValueError):
        # Ri-serializzazione fallita: non rompere il forward
        return body


# --- Immagini in modalità local: il modello è solo-testo e llama.cpp le rifiuta
# con 500. Le SALVIAMO su disco e le sostituiamo con una nota che contiene il
# PERCORSO ESATTO, così il modello fa una sola chiamata a vision_local/ocr_image
# invece di brancolare (Read, ricerca file, percorsi indovinati).
SAVED_IMAGE_DIR = os.path.join(tempfile.gettempdir(), "claude-local-images")

_IMG_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/webp": ".webp", "image/gif": ".gif",
}


def _save_image_block(source) -> Optional[str]:
    """Salva un blocco immagine base64 su disco (nome = hash del contenuto).

    Ritorna il path assoluto, o None se il blocco non è salvabile.
    Idempotente: la stessa immagine finisce sempre nello stesso file.
    """
    import base64
    import hashlib

    if not isinstance(source, dict):
        return None
    data = source.get("data")
    if not data:
        return None
    ext = _IMG_EXT.get(source.get("media_type", "image/png"), ".png")
    try:
        raw = base64.b64decode(data)
    except Exception:
        return None
    name = hashlib.sha1(raw).hexdigest()[:16] + ext
    path = os.path.join(SAVED_IMAGE_DIR, name)
    try:
        os.makedirs(SAVED_IMAGE_DIR, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(raw)
    except Exception:
        return None
    return os.path.abspath(path)


def _build_image_note(paths: list) -> dict:
    """Costruisce il blocco-nota testuale con i percorsi salvati (istruzione direttiva)."""
    if len(paths) == 1:
        text = (
            f"[Immagine allegata salvata in: {paths[0]}. Sei un modello di solo testo: "
            f"per analizzarla usa SUBITO il tool vision_local con image_path={paths[0]} "
            f"(contenuto visivo) oppure ocr_image (solo testo). NON usare Read, "
            f"NON cercare il file: il percorso è questo.]"
        )
    else:
        joined = ", ".join(paths)
        text = (
            f"[Immagini allegate salvate in: {joined}. Sei un modello di solo testo: "
            f"per analizzarle usa SUBITO il tool vision_local con image_path=<percorso> "
            f"(contenuto visivo) oppure ocr_image (solo testo) per ciascuna. "
            f"NON usare Read, NON cercare i file: i percorsi sono questi.]"
        )
    return {"type": "text", "text": text}


def _process_content_list(content_list: list) -> list:
    """Rimuove i blocchi image (salvandoli su disco) e aggiunge UNA nota col percorso."""
    saved = []
    result = []
    for b in content_list:
        if not isinstance(b, dict):
            result.append(b)
            continue
        if b.get("type") == "image":
            path = _save_image_block(b.get("source", {}))
            if path:
                saved.append(path)
            continue  # il blocco image non va all'output
        if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
            b = dict(b)
            b["content"] = _process_content_list(b["content"])
        result.append(b)

    if saved:
        result.append(_build_image_note(saved))
    return result


def strip_images_with_note(body: bytes) -> bytes:
    """Salva le immagini del body su disco e le sostituisce con una nota col percorso."""
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or "messages" not in data:
            return body
        for message in data["messages"]:
            if isinstance(message, dict) and isinstance(message.get("content"), list):
                message["content"] = _process_content_list(message["content"])
        return json.dumps(data).encode()
    except Exception:
        return body


# ELEMENTO 2
class _FixedContent:
    def __init__(self, original_content, max_tokens: int, is_sse: bool, log_fn):
        self._orig = original_content
        self._max = max_tokens
        self._is_sse = is_sse
        self._log = log_fn

    def at_eof(self):
        return self._orig.at_eof()

    def __getattr__(self, name):
        return getattr(self._orig, name)

    async def iter_any(self):
        if self._is_sse:
            buffer = b""
            async for chunk in self._orig.iter_any():
                buffer += chunk
                parts = buffer.split(b"\n")
                buffer = parts.pop()
                for line in parts:
                    yield self._fix_line(line) + b"\n"
            if buffer:
                yield buffer
        else:
            chunks = []
            async for chunk in self._orig.iter_any():
                chunks.append(chunk)
            raw = b"".join(chunks)
            yield self._fix_json(raw)

    def _fix_line(self, line: bytes):
        prefix = b"data: "
        if not line.startswith(prefix):
            return line
        try:
            obj = json.loads(line[len(prefix):])
        except Exception:
            return line
        if (isinstance(obj, dict)
                and obj.get("type") == "message_delta"
                and obj.get("delta", {}).get("stop_reason") == "end_turn"
                and obj.get("usage", {}).get("output_tokens", 0) >= self._max):
            obj["delta"]["stop_reason"] = "max_tokens"
            self._log("stop_reason corretto: end_turn -> max_tokens")
            debug_catalog.record_event(
                severity="error", category="local", kind="truncated_response_local",
                snippet=f"end_turn->max_tokens output_tokens={obj.get('usage', {}).get('output_tokens', 0)} max={self._max}")
            return prefix + json.dumps(obj).encode()
        return line

    def _fix_json(self, raw: bytes):
        try:
            obj = json.loads(raw)
        except Exception:
            return raw
        if (obj.get("stop_reason") == "end_turn"
                and obj.get("usage", {}).get("output_tokens", 0) >= self._max):
            obj["stop_reason"] = "max_tokens"
            self._log("stop_reason corretto: end_turn -> max_tokens")
            debug_catalog.record_event(
                severity="error", category="local", kind="truncated_response_local",
                snippet=f"end_turn->max_tokens output_tokens={obj.get('usage', {}).get('output_tokens', 0)} max={self._max}")
            return json.dumps(obj).encode()
        return raw


# ELEMENTO 3
class _StopReasonFixed:
    def __init__(self, original_response, max_tokens: int, log_fn):
        ct = original_response.headers.get("content-type", "")
        is_sse = "text/event-stream" in ct.lower()
        object.__setattr__(self, "_orig", original_response)
        object.__setattr__(self, "_content", _FixedContent(
            original_response.content, max_tokens, is_sse, log_fn))

    @property
    def content(self):
        return self._content

    @property
    def headers(self):
        # CIMultiDict e NON dict: aiohttp conserva il case con cui l'header e'
        # arrivato, e il lookup case-insensitive lo fa la struttura, non la chiave.
        # Con una copia in dict normale, `headers.get("content-type")` del relay
        # restituisce None se l'upstream ha mandato "Content-Type" — quindi
        # `is_sse` diventa False, uno stream SSE viene trattato come JSON, il
        # parser dell'usage non trova nulla e ripiega sulle stime `total_bytes//4`.
        # E' il motivo per cui in mode `local` il sidecar registrava sempre
        # `stop_reason=""`, `text_blocks=0`, `outcome=unknown` e conteggi inventati
        # (misurato il 2026-08-16: 36 righe su 37, e una sonda con max_tokens=16
        # che risultava con 698 token di output).
        return CIMultiDict(
            (k, v) for k, v in self._orig.headers.items()
            if k.lower() != "content-length"
        )

    def __getattr__(self, name):
        return getattr(self._orig, name)


async def forward_local(
    request,
    body: bytes,
    session: aiohttp.ClientSession,
    model: str,
    log_fn: Callable[..., None] = print,
    passthrough: bool = False,
    upstream_model: str = ''
) -> Optional[aiohttp.ClientResponse]:
    """Inoltra la richiesta al backend locale con retry su errori transienti."""
    key = await get_local_key()
    if not key:
        log_fn("forward_local: API key mancante")
        debug_catalog.record_event(
            severity="block", category="local", kind="local_key_missing",
            code=502, snippet="LOCAL_LLM_API_KEY not configured")
        if passthrough:
            return synthetic_error(502, 'local_unavailable', 'Local LLM key not configured')
        return web.Response(
            text='{"type":"error","error":{"type":"local_unavailable","message":"Local LLM key not configured"}}',
            status=502,
            content_type='application/json'
        )

    # Il modello REALE servito da LiteLLM. `model` e' quello richiesto dal client
    # (claude-opus-5 in local/gpt/mix-al): finora `upstream_model` era dichiarato e
    # mai letto, quindi ogni riga di log e ogni evento del catalogo di categoria
    # "local" nominava un modello Anthropic. Chi poi cercava quale modello locale
    # dava body vuoti non trovava il dato. Stesso difetto chiuso su GLM il 2026-08-19.
    mod_reale = upstream_model or model

    # ISOLAMENTO TOOL: stesso choke-point di glm/qwen/minimax/anthropic, che qui
    # mancava. Il modello locale non puo' eseguire i server-tool di Anthropic ne'
    # i tool brandizzati degli altri provider: lasciarglieli nel body significa
    # offrirgli strumenti che non esistono da questa parte della catena.
    body = tool_isolation.filter_tools_for_backend(body, "local")

    base = get_local_base()
    url = base + request.path_qs
    anth_version = request.headers.get('anthropic-version', '2023-06-01')

    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'anthropic-version': anth_version,
    }

    # Loop retry: stesso numero di tentativi per tutti i provider locali. Il
    # tentativo EXTRA serve solo al ctx-retry, che non dipende da
    # LOCAL_MAX_RETRY (anche a 0 deve poter accorciare e ritentare una volta);
    # i retry 5xx/connessione restano gated da attempt < LOCAL_MAX_RETRY.
    max_attempts = LOCAL_MAX_RETRY + 2
    ctx_retry_done = False   # un solo retry per ctx-exceeded

    for attempt in range(max_attempts):
        try:
            start = asyncio.get_event_loop().time()
            resp = await session.post(
                url,
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=LOCAL_TIMEOUT_SEC,
                                              sock_read=LOCAL_IDLE_READ_SEC)
            )
            status = resp.status
            elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000
            log_fn(f"forward_local attempt {attempt+1}/{max_attempts}: model={mod_reale} status={status} elapsed={elapsed_ms:.0f}ms")
            if status in (502, 503, 504):
                if attempt < LOCAL_MAX_RETRY:
                    log_fn(f"forward_local retry {attempt+1}: status {status}")
                    debug_catalog.record_event(
                        severity="error", category="local", kind="local_5xx_retry",
                        code=status, snippet=f"attempt {attempt+1} model={mod_reale}")
                    await resp.release()
                    await asyncio.sleep(2)
                    continue
            if status == 429:
                # 429 provider locali
                debug_catalog.record_event(
                    severity="block", category="local", kind="quota_429_local",
                    code=429, snippet=f"model={mod_reale}")
            if status == 400:
                # 400 exceed_context_size_error: accorciamo sul limite reale e
                # ritentiamo UNA volta. Il retry rientra nel timeout complessivo
                # (LOCAL_TIMEOUT_SEC invariato, < 300s del client). Il ctx-retry
                # non dipende da LOCAL_MAX_RETRY: si fa sempre la prima volta.
                _err_text = (await resp.read()).decode("utf-8", errors="replace")
                await resp.release()
                _content_type = resp.headers.get('content-type', 'application/json')
                _ctx = parse_ctx_exceeded(_err_text)
                if _ctx and not ctx_retry_done:
                    ctx_retry_done = True
                    n_prompt, n_ctx_limit = _ctx
                    # Il limite è in TOKEN per lo stimatore del router:
                    # scala la stima del body col rapporto fra finestra
                    # reale e token reali contati da llama.cpp.
                    stima = estimate_tokens_body(body, mod_reale)
                    nuovo_limite = max(1, int(n_ctx_limit * 0.9 * stima / n_prompt))
                    # fp usa-e-getta: non deve finire nello stato sticky condiviso
                    _fp = f"ctx-retry-{id(body)}-{attempt}"
                    nuovo_body, _taglio = rewrite_for_context(
                        body, mod_reale, _fp, safe_limit_override=nuovo_limite)
                    _STICKY_DROP_COUNT.pop(_fp, None)
                    _STICKY_SUMMARY.pop(_fp, None)
                    log_fn(f"local ctx-exceeded: n_prompt={n_prompt} n_ctx={n_ctx_limit} "
                           f"→ retry accorciato (taglio={_taglio})")
                    debug_catalog.record_event(
                        severity="error", category="local", kind="local_ctx_exceeded_retry",
                        code=400,
                        snippet=f"n_prompt={n_prompt} n_ctx={n_ctx_limit} trimmed={_taglio}")
                    body = nuovo_body
                    continue
                # Non ctx-exceeded, o ctx-retry già usato: inoltra il 400
                # (risposta già letta: si restituisce una Response nuova).
                # Via header e non content_type=: aiohttp rifiuta i valori
                # con "; charset=..." nel parametro content_type.
                return web.Response(body=_err_text.encode(), status=status,
                                    headers={"Content-Type": _content_type})
            if passthrough:
                _max_tok = requested_max_tokens(body)
                if status == 200 and _max_tok:
                    return _StopReasonFixed(resp, _max_tok, log_fn)
                return resp
            body_bytes = await resp.read()
            await resp.release()
            if status == 200 and not body_bytes.strip():
                debug_catalog.record_event(
                    severity="error", category="local", kind="empty_response_local",
                    code=200, snippet=f"model={mod_reale} empty body")
            return web.Response(
                body=body_bytes,
                status=status,
                content_type='application/json'
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000
            # Su TIMEOUT non si ritenta: la richiesta scaduta ha gia' consumato il
            # budget del client e un nuovo tentativo occuperebbe un altro slot GPU
            # mentre il client sta per abbandonare. Si ritenta solo la connessione.
            _is_timeout = isinstance(e, asyncio.TimeoutError)
            if attempt < LOCAL_MAX_RETRY and not _is_timeout:
                log_fn(f"forward_local retry {attempt+1}: {type(e).__name__} elapsed={elapsed_ms:.0f}ms")
                await asyncio.sleep(2)
                continue
            if _is_timeout:
                _causa = "idle-read" if elapsed_ms < LOCAL_IDLE_READ_SEC * 1000 + 5000 else "totale"
                log_fn(f"forward_local TIMEOUT dopo {elapsed_ms:.0f}ms (causa presunta: {_causa}, "
                       f"soglie idle={LOCAL_IDLE_READ_SEC}s/totale={LOCAL_TIMEOUT_SEC}s): nessun retry")
            log_fn(f"forward_local error: {type(e).__name__} elapsed={elapsed_ms:.0f}ms")
            debug_catalog.record_event(
                severity="error", category="local",
                kind="upstream_timeout" if _is_timeout else "upstream_conn_error",
                code=502, snippet=f"{type(e).__name__} elapsed={elapsed_ms:.0f}ms model={mod_reale}")
            err_msg = f'{{"type":"error","error":{{"type":"local_unavailable","message":"Local LLM backend unreachable: {e}"}}}}'
            if passthrough:
                return synthetic_error(502, 'local_unavailable', err_msg)
            return web.Response(
                text=err_msg,
                status=502,
                content_type='application/json'
            )

    log_fn(f"forward_local: exhausted retries (attempted {max_attempts} times)")
    debug_catalog.record_event(
        severity="error", category="local", kind="upstream_error",
        code=502, snippet=f"exhausted {max_attempts} attempts model={mod_reale}")
    err_msg = f'{{"type":"error","error":{{"type":"local_unavailable","message":"Local LLM backend failed after {max_attempts} retries"}}}}'
    if passthrough:
        return synthetic_error(502, 'local_unavailable', err_msg)
    return web.Response(text=err_msg, status=502, content_type='application/json')


# Slim del body per il provider local (2026-09-29): i modelli locali hanno la
# finestra piu' stretta (code-max 131k), ma il body di Claude Code arriva a
# ~44k token di cui gran parte in schemi di tool inutilizzati e liste
# "Available agent types"/"skills available" dentro i system-reminder dei
# messages. Allowlist di tool (default: i core di Claude Code), override
# AIROUTER_LOCAL_TOOL_ALLOWLIST. Disattivabile con AIROUTER_LOCAL_SLIM=0.
LOCAL_TOOL_ALLOWLIST_DEFAULT = "Bash,Read,Edit,Write,Grep,Glob,Agent,MultiEdit,ToolSearch"

_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder[^>]*>.*?</system-reminder>", re.DOTALL)
_SLIM_MARKERS = ("Available agent types", "The following skills are available")


def _slim_system_reminders(text: str) -> tuple[str, int]:
    """Toglie i system-reminder con liste agent/skills, conserva il resto."""
    removed = 0

    def _sost(m: "re.Match") -> str:
        nonlocal removed
        if any(marker in m.group(0) for marker in _SLIM_MARKERS):
            removed += 1
            return ""
        return m.group(0)

    return _SYSTEM_REMINDER_RE.sub(_sost, text), removed


def _demote_removed_tool_refs(data: dict, allow: set) -> None:
    """Invariante: dopo il taglio dei tool, nessun riferimento ai nomi rimossi
    puo' sopravvivere nei messages (tool_use, tool_result abbinato, tool_reference
    annidato in tool_search_tool_result — forma verificata contro l'API 2026-09-14).
    Primo livello: declassa a text; annidato: rimuove. Stesso schema di
    tool_isolation.demote_foreign_tool_use, ma per nome invece che per brand."""
    messages = data.get("messages")
    if not isinstance(messages, list):
        return
    demoted_ids: set = set()

    def scansiona(content: list, annidato: bool) -> None:
        da_rimuovere: list = []
        for i, blk in enumerate(content):
            if not isinstance(blk, dict):
                continue
            if blk.get("type") in ("tool_use", "server_tool_use", "mcp_tool_use", "tool_reference") \
                    and (blk.get("name") or blk.get("tool_name")) not in allow:
                tid = blk.get("id")
                if isinstance(tid, str):
                    demoted_ids.add(tid)
                if annidato:
                    da_rimuovere.append(i)
                else:
                    content[i] = {"type": "text",
                                  "text": f"[tool_use {blk.get('name')}] "
                                          + json.dumps(blk.get("input"), ensure_ascii=False, default=str)[:2000]}
                continue
            figli = blk.get("content")
            if isinstance(figli, dict):
                figli = figli.get("tool_references")
            if isinstance(figli, list):
                scansiona(figli, True)
        for i in reversed(da_rimuovere):
            del content[i]

    for msg in messages:
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            scansiona(msg["content"], False)
    if demoted_ids:
        for msg in messages:
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for i, blk in enumerate(content):
                if isinstance(blk, dict) and blk.get("type") == "tool_result" \
                        and blk.get("tool_use_id") in demoted_ids:
                    content[i] = {"type": "text",
                                  "text": f"[tool_result {blk.get('tool_use_id')}] "
                                          + json.dumps(blk.get("content"), ensure_ascii=False, default=str)[:2000]}


def slim_local_body(body: bytes, mode: str = "local") -> bytes:
    """Slim del body per il provider local: allowlist tool + pulizia dei
    riferimenti orfani + rimozione delle liste agent/skills nei system-reminder.
    NON tocca system top-level, CLAUDE.md, max_tokens, thinking, metadata.
    Logga `local_slim` SOLO se la riduzione supera 1000 char."""
    if os.environ.get("AIROUTER_LOCAL_SLIM", "1") == "0":
        return body
    try:
        data = json.loads(body)
    except Exception:
        return body
    before = len(body)
    # 1) allowlist tool + invariante riferimenti (demote con brand-check
    # aggirato: qui i nomi da declassare sono quelli FUORI allowlist, non
    # quelli brandizzati, quindi riusiamo demote_foreign_tool_use con un
    # backend fittizio dopo aver marchiato i rimossi come brand "local_orphan").
    tools = data.get("tools")
    removed_names: list[str] = []
    if isinstance(tools, list) and tools:
        allow = set(filter(None, os.environ.get(
            "AIROUTER_LOCAL_TOOL_ALLOWLIST", LOCAL_TOOL_ALLOWLIST_DEFAULT).split(",")))
        removed_names = [t.get("name", "?") for t in tools
                         if not (isinstance(t, dict) and t.get("name") in allow)]
        if removed_names:
            kept = [t for t in tools if isinstance(t, dict) and t.get("name") in allow]
            if kept:
                data["tools"] = kept
            else:
                data.pop("tools", None)
                data.pop("tool_choice", None)
            tool_isolation.sanitize_tool_choice(data)
            tool_isolation.sanitize_defer_loading(data)
        # Invariante: nessun riferimento a un nome fuori allowlist puo'
        # sopravvivere nei messages (anche se tools[] non lo dichiarava).
        _demote_removed_tool_refs(data, allow)
    # 2) liste agent/skills nei system-reminder dei messages
    reminders_removed = 0
    for msg in data.get("messages", []):
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for i, blk in enumerate(content):
            if isinstance(blk, dict) and blk.get("type") == "text" \
                    and isinstance(blk.get("text"), str):
                nuovo, n = _slim_system_reminders(blk["text"])
                if n:
                    blk["text"] = nuovo
                    reminders_removed += n
    after = len(json.dumps(data).encode())
    if before - after > 1000:
        debug_catalog.record_event(
            severity="info", category="local", kind="local_slim",
            snippet=(f"chars={before}->{after} tools_kept={len(kept) if isinstance(tools, list) and tools else 0} "
                     f"tools_removed={removed_names[:10]} reminders_removed={reminders_removed}"),
        )
    return json.dumps(data).encode()
