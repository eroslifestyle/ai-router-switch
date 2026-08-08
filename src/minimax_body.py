"""MiniMax body transformation utilities — remap model, strip unsupported fields, sanitize server tools.

Dependency injection: il proxy passa log_fn, resolve_fp, log_model_fn, orig_model_store
per evitare closure sul global scope del proxy.
"""
import json
import os

MINIMAX_MODEL = os.environ.get("AIROUTER_MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_UNSUPPORTED_FIELDS = ("context_management", "mcp_servers", "thinking")
# Soglia minima imposta a max_tokens verso MiniMax. Se il client ne chiede meno,
# la richiesta viene innalzata a questo valore.
#
# Perche' esiste (documentato il 2026-08-08, audit A4 — prima non c'era alcun
# commento in un file per il resto densamente commentato): e' un rimedio alle
# risposte vuote da troncamento nel thinking. MiniMax spende i primi token in
# blocchi di ragionamento; con un tetto basso il generatore si ferma prima di
# emettere un solo blocco di testo e il client riceve una risposta senza
# contenuto, con status 200. E' la classe di bug piu' inseguita in questo
# progetto.
#
# Perche' NON si toglie: l'ipotesi che sprecasse quota e' stata SMENTITA dalla
# misura sul traffico reale (7 giorni, mix-am): solo 8 risposte su 17.468 hanno
# esattamente 1024 token di output, e il 71,9% sta sotto la soglia, perche' i
# client mandano max_tokens alto e l'innalzamento non li tocca mai. Il costo
# reale e' quindi trascurabile; il beneficio e' evitare una classe di
# fallimenti silenziosi.
#
# Resta una deviazione dal contratto API, e colpisce le richieste piccole: con
# max_tokens=8 si generano 1024 token, 128 volte il richiesto. Per questo ogni
# innalzamento viene ora LOGGATO: e' contabile, non piu' invisibile.
MINIMAX_MIN_MAX_TOKENS = int(os.environ.get("AIROUTER_MINIMAX_MIN_MAX_TOKENS", "1024"))


# Le due funzioni _system_to_text e _inject_system_as_message sono state rimosse.
# Motivo: _inject_system_as_message spostava il campo 'system' in messages come
# role='system', ma oggi e' dannoso. Con AIROUTER_TRANSITION_FILTERS=1 (produzione),
# _repair_message_sequence scarta tutti i messaggi role=='system', cancellando il
# system prompt e il contesto da context_rewrite. Risultato: risposte vuote o
# allucinate in mix-am. Misurato: con system 'rispondi SOLO BANANA', MiniMax diretto
# risponde BANANA, via router si presentava (come senza system). Non serve alcuna
# conversione: l'endpoint api.minimaxi.chat/anthropic onora il campo 'system'
# top-level, anche come lista di blocchi con cache_control.
_SERVER_TOOL_BLOCK_TYPES = (
    "server_tool_use",
    "web_search_tool_result",
    "web_fetch_tool_result",
    "code_execution_tool_result",
)

# Client tool che orchestrano subagent: NON vanno MAI rimossi da nessuna
# modalità, nemmeno se arrivassero privi di input_schema. Guardia esplicita
# (regola utente 2026-07-26: "dentro il router non devo avere questi blocchi"
# sul tool Agent). Match case-insensitive sul name della definizione tool.
_NEVER_STRIP_TOOL_NAMES = {"agent", "task", "subagent"}


def _is_protected_tool(tool: object) -> bool:
    """True se la definizione tool è un client-tool di orchestrazione subagent
    (Agent/Task/SubAgent) che va sempre conservato per MiniMax."""
    if not isinstance(tool, dict):
        return False
    name = str(tool.get("name") or "").strip().lower()
    return name in _NEVER_STRIP_TOOL_NAMES


def strip_server_tools_for_minimax(data: dict) -> None:
    """Bug 2026-07-04: MiniMax non conosce i server tool Anthropic (web_search_20250305...).
    Rifiuta sia le definizioni in `tools` (niente input_schema) sia i blocchi
    server_tool_use/web_search_tool_result rimasti nella history → 400 (2013).
    Strip delle definizioni + conversione dei blocchi in testo. Muta `data`.

    GUARDIA (2026-07-26): i client-tool Agent/Task/SubAgent NON vengono mai
    rimossi, in nessuna modalità, anche se privi di input_schema."""
    tools = data.get("tools")
    if isinstance(tools, list):
        kept = [
            t for t in tools
            if _is_protected_tool(t)
            or not (isinstance(t, dict) and "input_schema" not in t)
        ]
        if len(kept) != len(tools):
            if kept:
                data["tools"] = kept
            else:
                data.pop("tools", None)
                data.pop("tool_choice", None)
            # tool_choice puo' ancora nominare un tool appena rimosso -> 400
            # "unknown tool" su MiniMax. Va riallineato, non basta lo strip.
            from tool_isolation import sanitize_tool_choice
            sanitize_tool_choice(data)
    # Regola: o entrambi gli ingredienti del tool deferral, o nessuno. Lo strip
    # qui sopra porta via il tool di ricerca (privo di input_schema) e MiniMax
    # non lo supporta comunque, quindi va tolto anche il marcatore dagli altri.
    # La regola vale identica per glm e qwen: sta in tool_isolation e non piu'
    # a mano qui, perche' due copie della stessa logica divergono col tempo.
    from tool_isolation import sanitize_defer_loading
    sanitize_defer_loading(data)
    for m in data.get("messages", []):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for i, blk in enumerate(c):
            if isinstance(blk, dict) and blk.get("type") in _SERVER_TOOL_BLOCK_TYPES:
                payload = {k: v for k, v in blk.items() if k != "type"}
                c[i] = {"type": "text",
                        "text": f"[{blk['type']}] "
                                + json.dumps(payload, ensure_ascii=False, default=str)[:4000]}


def remap_body_for_minimax(raw: bytes, request=None, *,
                           orig_model_store=None, resolve_fp=None,
                           log_model_fn=None, log_fn=None, target_model: str | None = None) -> bytes:
    """Riscrive il model Claude -> MiniMax-M3 e rimuove i campi beta non supportati.

    Dependency injection (proxy passes its globals):
    - orig_model_store: dict shared with relay SSE (chat_fp -> orig_model)
    - resolve_fp: callable(request) -> chat fingerprint
    - log_model_fn: callable(orig_model, new_model, chat_id)
    - log_fn: callable(msg) for logging
    - target_model: str | None
        Modello MiniMax di destinazione per questa chiamata. Se None, usa MINIMAX_MODEL (default).
        Permette di selezionare il modello MiniMax per singola richiesta (es. "MiniMax-M2.7").
    """
    _log = log_fn or print
    _target = target_model or MINIMAX_MODEL
    try:
        data = json.loads(raw)
        orig = data.get("model", "")
        # Match case-INsensitive: con startswith("MiniMax") gli alias minuscoli
        # ("minimax-m2.7-hs", "minimax-m3", ...) non venivano riconosciuti come nomi
        # MiniMax e finivano riscritti al default MINIMAX_MODEL. Il confronto va fatto
        # sul nome normalizzato: non tocca a noi rinominare un modello del provider.
        # Nota (misurato 2026-08-02): l'upstream /anthropic accetta quegli alias e li
        # risolve DA SE' a MiniMax-M3 (`minimax-m2.7-hs` -> risposta model=MiniMax-M3),
        # quindi qui non cambia il modello servito — toglie solo una riscrittura nostra
        # che mascherava la scelta del client. Per ottenere davvero M2.7 va chiesto
        # "MiniMax-M2.7", che e' cio' che ~/.claude/m3/config.env imposta dal 2026-07-14.
        if orig and not orig.lower().startswith("minimax"):
            if resolve_fp and request:
                chat_id = resolve_fp(request)
                if log_model_fn:
                    log_model_fn(orig, _target, chat_id)
            else:
                chat_id = "?"
            # Ricorda per riscrittura SSE — consumato dal relay()
            if orig_model_store is not None:
                if len(orig_model_store) > 2000:
                    _keep = orig_model_store.get("__remap__")
                    orig_model_store.clear()
                    if _keep is not None:
                        orig_model_store["__remap__"] = _keep
                orig_model_store[chat_id] = orig
            data["model"] = _target
        for f in MINIMAX_UNSUPPORTED_FIELDS:
            data.pop(f, None)
        strip_server_tools_for_minimax(data)
        # Il campo `system` resta top-level: l'endpoint Anthropic-compatible di
        # MiniMax lo onora. Convertirlo in un messaggio role=system lo faceva
        # cancellare da _repair_message_sequence a valle (vedi commento in testa).
        try:
            _mt = int(data.get("max_tokens", 0) or 0)
            if 0 < _mt < MINIMAX_MIN_MAX_TOKENS:
                data["max_tokens"] = MINIMAX_MIN_MAX_TOKENS
                # Loggato dal 2026-08-08 (audit A4): l'innalzamento avveniva in
                # silenzio, quindi non era possibile sapere quanto spesso
                # accadesse ne' quanto costasse. Ora e' contabile: basta un
                # grep 'max_tokens innalzato' sul log del router.
                _log(f"max_tokens innalzato: {_mt} -> {MINIMAX_MIN_MAX_TOKENS} "
                     f"(minimo per evitare le risposte vuote da troncamento nel thinking)")
        except (TypeError, ValueError):
            pass
        return json.dumps(data).encode()
    except Exception:
        _log("remap_body: json parse fail, passthrough")
        return raw
