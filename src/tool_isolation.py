"""Isolamento tool nativi per provider (anthropic/minimax/glm).

Soluzione centralizzata (2026-07-19): sostituisce le funzioni ad-hoc duplicate
per singola modalità (_strip_foreign_branded_tools, strip_foreign_branded_tools_for_glm)
che coprivano solo le 3 modalità pure e lasciavano scoperte le modalità miste
(mix-am/mix-ag/mix-gm) — bug reale: MiniMax MCP restava visibile a GLM in
mix-ag e ad Anthropic server-tool restava visibile a MiniMax/GLM in mix-gm.

Design a choke-point: filter_tools_for_backend() va chiamata dentro le
funzioni forward_anthropic/forward_anthropic_direct/forward_minimax/forward_glm
(un solo punto per backend, non ai call-site delle pipeline). Ogni richiesta
verso un upstream passa SEMPRE da una di queste 4 funzioni, quindi ogni
pipeline — presente o futura — eredita l'isolamento senza doverlo richiamare
esplicitamente a ogni nuovo mix.
"""
import json
import os
import re

import debug_catalog


# Tool client Claude Code con input_schema ma eseguiti via infrastruttura
# Anthropic (WebSearch/WebFetch): brandizzati Anthropic per nome esatto,
# altrimenti restano visibili a MiniMax/GLM che li scelgono al posto dei
# propri tool nativi (leak isolamento 2026-07-22, es. GLM usava WebSearch).
_ANTHROPIC_CLIENT_TOOL_NAMES = {"websearch", "webfetch", "web_search", "web_fetch"}


# Prefissi MCP dei tool nativi Alibaba Model Studio/Bailian
_QWEN_TOOL_PREFIXES = ("mcp__qwen__", "mcp__dashscope__", "mcp__websearch__")


def is_anthropic_server_tool(t: dict) -> bool:
    """Server-tool Anthropic (web_search_20250305, computer_use, bash,
    code_execution, ...): eseguiti server-side su api.anthropic.com,
    riconoscibili perché privi di input_schema (i tool client-eseguiti/MCP
    ce l'hanno sempre). Nessun altro backend sa eseguirli — se ricevuti,
    MiniMax/GLM rispondono 400 (bug 2013, 2026-07-04). In più i tool client
    Anthropic-branded (WebSearch/WebFetch) sono riconosciuti per nome esatto.

    Fix 2026-07-22: i tool MCP branded di altri provider (mcp__zai__/mcp__MiniMax__/
    webSearchPrime) possono essere privi di input_schema; senza questa esclusione
    venivano classificati come server-tool Anthropic e strippati anche in mode
    glm/minimax (bug: tool nativo GLM rimosso in modalità GLM). Fix 2026-08-03:
    stessa esclusione estesa ai tool nativi Qwen/Bailian, altrimenti in modalità
    qwen il tool nativo veniva strippato."""
    if not isinstance(t, dict):
        return False
    name = (t.get("name") or "").lower()
    if ("minimax" in name or "websearchprime" in name
            or name.startswith("mcp__zai__")
            or name.startswith(_QWEN_TOOL_PREFIXES) or "dashscope" in name):
        return False
    if "input_schema" not in t:
        return True
    return name in _ANTHROPIC_CLIENT_TOOL_NAMES


def is_minimax_branded_tool(t: dict) -> bool:
    """Tool nativo MiniMax (MCP mcp__MiniMax__web_search/understand_image)."""
    return isinstance(t, dict) and "minimax" in (t.get("name") or "").lower()


def is_glm_branded_tool(t: dict) -> bool:
    """Tool nativo z.ai/GLM (es. MCP webSearchPrime), riconosciuto per nome
    indipendentemente dall'alias del server MCP scelto dall'utente."""
    if not isinstance(t, dict):
        return False
    name = (t.get("name") or "").lower()
    return "websearchprime" in name or name.startswith("mcp__zai__")


def is_qwen_branded_tool(t: dict) -> bool:
    """Tool nativo Qwen/Alibaba Model Studio: MCP WebSearch di Bailian e affini,
    riconosciuto per nome indipendentemente dall'alias del server MCP scelto
    dall'utente."""
    if not isinstance(t, dict):
        return False
    name = (t.get("name") or "").lower()
    return name.startswith(_QWEN_TOOL_PREFIXES) or "dashscope" in name


# Prefissi/nomi dei connettori claude.ai orientati a produttivita' personale
# (Gmail/Calendar/Drive/Canva): pesano da soli decine di migliaia di token di
# schema per richiesta (misurato 2026-08-22: 40K/req di soli tool MCP su GLM,
# 48% del contesto processato) e non servono al ruolo ACT/coding che GLM copre
# nel router. A differenza dei tool brandizzati di un provider AI, questi non
# sono MAI necessari a GLM per eseguire codice; il filtro e' quindi opt-out
# (env AIROUTER_GLM_MCP_FILTER=0) e non tocca gli altri backend.
_HEAVY_PRODUCTIVITY_MCP_PREFIXES = (
    "mcp__claude_ai_gmail__",
    "mcp__claude_ai_google_calendar__",
    "mcp__claude_ai_google_drive__",
    "mcp__claude_ai_canva__",
)


def is_heavy_productivity_mcp_tool(t: dict) -> bool:
    """Tool MCP dei connettori Gmail/Calendar/Drive/Canva: pesanti e non
    pertinenti al ruolo ACT/coding di GLM (vedi _HEAVY_PRODUCTIVITY_MCP_PREFIXES)."""
    if not isinstance(t, dict):
        return False
    name = (t.get("name") or "").lower()
    return name.startswith(_HEAVY_PRODUCTIVITY_MCP_PREFIXES)

def is_local_branded_tool(t: dict) -> bool:
    """Nessun tool e' brandizzato del backend locale: il modello sulla macchina
    non ha tool nativi propri. Serve solo a dare a "local" una voce in
    _BRAND_CHECK, senza la quale filter_tools_for_backend(body, "local") era un
    no-op silenzioso e il modello locale riceveva i server-tool di Anthropic e i
    tool brandizzati degli altri provider, che non puo' eseguire."""
    return False


_BRAND_CHECK = {
    "anthropic": is_anthropic_server_tool,
    "minimax": is_minimax_branded_tool,
    "glm": is_glm_branded_tool,
    "qwen": is_qwen_branded_tool,
    "local": is_local_branded_tool,
}


def sanitize_tool_choice(data: dict) -> None:
    """Riallinea `tool_choice` all'array `tools` dopo uno strip. Muta `data`.

    Un tool_choice che punta a un tool appena rimosso fa rispondere 400
    all'upstream ("unknown tool" per type=tool; contraddizione per type=any/none).
    Lo strip da solo non basta: va sanato anche il riferimento.

    Regole:
    - Senza alcun tool rimasto, `tool_choice` sparisce del tutto, indipendentemente
      dal `type` (auto/any/tool/none o futuri). I tipi validi sono quelli che
      il protocollo Anthropic riconosce; il criterio applicato è «restano tool
      dopo lo strip oppure no», NON un enum di tipi: così un tipo nuovo introdotto
      in futuro è gestito correttamente senza modificare il codice.
    - Con almeno un tool rimasto:
      - Se type=tool e il tool citato per nome esiste ancora -> nessuna modifica
      - Se type=tool e il tool citato NON esiste più -> degrada a {"type":"auto"}
      - Se type=auto/any/none -> nessuna modifica, sono legittimi con tool presenti
    - Se `tool_choice` è assente o non è un dict -> nessuna modifica."""
    tc = data.get("tool_choice")
    if not isinstance(tc, dict):
        return

    tools = data.get("tools")
    names = {t.get("name") for t in tools if isinstance(t, dict)} if isinstance(tools, list) else set()

    # Nessun tool: rimuovi sempre tool_choice, indipendentemente dal type
    if not names:
        data.pop("tool_choice", None)
        return

    # Con tool presenti: solo type=tool ha regole speciali
    if tc.get("type") != "tool":
        return

    # type=tool con tool presente: controlla se il tool citato esiste ancora
    if tc.get("name") in names:
        return

    # type=tool ma il tool citato non esiste più: degrada a auto
    data["tool_choice"] = {"type": "auto"}


def sanitize_defer_loading(data: dict) -> bool:
    """Rimuove i marcatori `defer_loading` rimasti orfani. Muta `data`.

    Regola: o entrambi gli ingredienti, o nessuno. Il tool deferral di Claude
    Code ne richiede DUE: i tool marcati `{"defer_loading": true}` e un tool di
    ricerca che li espande (il suo `type` contiene "tool_search"). Quest'ultimo
    non ha `input_schema`, quindi lo strip lo classifica come server-tool
    Anthropic e lo rimuove verso minimax/glm/qwen — lasciando pero' i marcatori
    sugli altri. Il body che ne esce dichiara tool "da caricare dopo" e non
    offre nulla che possa caricarli: quei tool restano irraggiungibili.
    """
    tools = data.get("tools")
    if not isinstance(tools, list):
        return False

    ha_ricerca = any(
        isinstance(t, dict) and "tool_search" in str(t.get("type", ""))
        for t in tools
    )
    if ha_ricerca:
        return False

    rimossi = False
    for t in tools:
        if isinstance(t, dict) and "defer_loading" in t:
            t.pop("defer_loading")
            rimossi = True
    return rimossi


def preserva_cache_control(originali: list, kept: list) -> None:
    """Riporta su `kept` il breakpoint `cache_control` dei tool rimossi. Muta `kept`.

    Claude Code chiude il blocco `tools` con un `cache_control` sull'ultimo elemento:
    se lo strip porta via proprio quel tool, il breakpoint sparisce, il prompt caching
    non si estende piu' e ogni turno riprocessa l'intera conversazione — lo stream
    rallenta finche' il client chiude con "Response stalled mid-stream" (2026-08-01,
    3.263 occorrenze).

    Esisteva in tre copie: due divergenti (`filter_tools_for_backend` risaliva
    all'ultimo dict di `kept`, `qwen_tool_trim` guardava solo `kept[-1]` e con un
    elemento spurio in coda perdeva comunque il breakpoint) e una mancante del tutto
    (`strip_server_tools_for_minimax`). Unificata qui il 2026-08-16.
    """
    if not kept:
        return
    if any(isinstance(t, dict) and t.get("cache_control") for t in kept):
        return
    cc = next((t.get("cache_control") for t in reversed(originali)
               if isinstance(t, dict) and t not in kept and t.get("cache_control")), None)
    if cc is None:
        return
    # L'ultimo elemento di `kept` che sia un dict: un client puo' mandare un `tools`
    # con un elemento spurio in coda, e lo spread su una stringa solleverebbe
    # TypeError che nessun chiamante intercetta (500 al client).
    i = next((k for k in reversed(range(len(kept))) if isinstance(kept[k], dict)), None)
    if i is not None:
        kept[i] = {**kept[i], "cache_control": cc}


def filter_tools_for_backend(body: bytes, backend: str) -> bytes:
    """Rimuove dall'array `tools` i tool brandizzati di provider DIVERSI da
    `backend` ('anthropic'|'minimax'|'glm'). I tool locali di Claude Code
    (Bash/Read/Write/Edit/...) e qualunque MCP non riconosciuto (context7,
    github, ...) non sono mai toccati: non sono brandizzati di un provider
    AI specifico. `backend` sconosciuto o body non-JSON → no-op sicuro."""
    if backend not in _BRAND_CHECK:
        return body
    try:
        data = json.loads(body)
    except Exception:
        return body
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        return body
    foreign = [check for name, check in _BRAND_CHECK.items() if name != backend]
    kept = [t for t in tools if not any(check(t) for check in foreign)]
    if len(kept) == len(tools):
        return body
    stripped_names = [t.get("name", "?") for t in tools if t not in kept]
    debug_catalog.record_event(
        # "info" e non "block": è l'esito NORMALE dell'isolamento in modalità pura,
        # non un'anomalia. Con "block" faceva 16.174 occorrenze, da solo metà del
        # catalogo, e seppelliva i tre o quattro errori veri sotto il rumore (2026-08-06).
        severity="info", category=backend, kind="tool_isolation_strip",
        snippet=f"stripped={stripped_names[:10]} kept={len(kept)}/{len(tools)}",
    )
    if kept:
        preserva_cache_control(tools, kept)
        data["tools"] = kept
    else:
        data.pop("tools", None)
        data.pop("tool_choice", None)
    sanitize_tool_choice(data)
    # Lo strip appena eseguito puo' aver rimosso il tool di ricerca (privo di
    # input_schema, quindi classificato come server-tool Anthropic) lasciando
    # orfani i marcatori defer_loading sugli altri tool. Qui e non prima del
    # return anticipato: se non rimuoviamo nulla, il body non va toccato.
    sanitize_defer_loading(data)
    return json.dumps(data).encode()



def strip_heavy_mcp_for_glm(body: bytes) -> bytes:
    """Rimuove dai `tools` i connettori pesanti di produttivita' personale
    quando il backend e' GLM. Choke-point separato da filter_tools_for_backend
    (che isola solo per BRAND di provider AI, non per peso/pertinenza): questi
    tool non sono brandizzati di nessun provider AI, quindi filter_tools_for_backend
    non li tocca mai. Override: AIROUTER_GLM_MCP_FILTER=0 disattiva il filtro."""
    if os.environ.get("AIROUTER_GLM_MCP_FILTER", "1") == "0":
        return body
    try:
        data = json.loads(body)
    except Exception:
        return body
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        return body
    kept = [t for t in tools if not is_heavy_productivity_mcp_tool(t)]
    if len(kept) == len(tools):
        return body
    stripped_names = [t.get("name", "?") for t in tools if t not in kept]
    debug_catalog.record_event(
        severity="info", category="glm", kind="heavy_mcp_strip",
        snippet=f"stripped={stripped_names[:10]} kept={len(kept)}/{len(tools)}",
    )
    if kept:
        preserva_cache_control(tools, kept)
        data["tools"] = kept
    else:
        data.pop("tools", None)
        data.pop("tool_choice", None)
    sanitize_tool_choice(data)
    sanitize_defer_loading(data)
    return json.dumps(data).encode()

_TOOL_USE_NAME_AFTER = re.compile(r'"type"\s*:\s*"tool_use"\s*,\s*(?:[^{}]*?,\s*)??"name"\s*:\s*"([^"]+)"')
_TOOL_USE_NAME_BEFORE = re.compile(r'"name"\s*:\s*"([^"]+)"\s*,\s*(?:[^{}]*?,\s*)??"type"\s*:\s*"tool_use"')

def brand_of_tool_name(name: str) -> str | None:
    """
    Classifica un nome nudo di tool (senza input_schema) nel provider che lo esegue.
    Restituisce 'minimax', 'glm', 'qwen' o 'anthropic' a seconda del brand,
    oppure None se il tool non è brandizzato (es. Bash, Read, tool MCP di terze parti).
    In nessun caso si deve bloccare un tool con brand None: sono sempre leciti.

    Perché non si riutilizza ``is_anthropic_server_tool``?
    Quel criterio classifica per *assenza* di ``input_schema`` e funziona solo sulle
    *definizioni* dei tool. Su un nome nudo direbbe "anthropic" per qualsiasi tool,
    compresi Bash e Read, generando falsi positivi su ogni turno.
    """
    if not name or not isinstance(name, str):
        return None
    name_lower = name.lower()
    if "minimax" in name_lower:
        return "minimax"
    if "websearchprime" in name_lower or name_lower.startswith("mcp__zai__"):
        return "glm"
    if name_lower.startswith(_QWEN_TOOL_PREFIXES) or "dashscope" in name_lower:
        return "qwen"
    if name_lower in _ANTHROPIC_CLIENT_TOOL_NAMES:
        return "anthropic"
    return None

def backend_from_final(final: str) -> str | None:
    """
    Deduce il backend esecutore dalla stringa ``final`` della telemetria del relay.
    Restituisce 'minimax', 'glm', 'qwen' o 'anthropic' a seconda del contenuto,
    oppure None se non è classificabile; in tal caso la guardia si astiene.
    """
    if not final or not isinstance(final, str):
        return None
    final_lower = final.lower()
    if "minimax" in final_lower:
        return "minimax"
    if final_lower.startswith("glm") or "glm-mode" in final_lower or "glm:" in final_lower:
        return "glm"
    if final_lower.startswith("qwen") or "qwen-mode" in final_lower or "qwen:" in final_lower:
        return "qwen"
    if "claude" in final_lower or "anthropic" in final_lower:
        return "anthropic"
    return None

def detect_foreign_tool_use(text: str, backend: str) -> list[str]:
    """
    Rileva, nella risposta in formato testo, i nomi dei ``tool_use`` che appartengono
    a un provider diverso da ``backend``. La rilevazione è **osservativa** e non
    modifica la risposta; serve a cogliere il caso in cui il modello, usando la
    cronologia, imiti il nome di un tool di un altro provider, cosa che il filtro
    request‑side sulle *definizioni* dei tool non può intercettare.

    """
    if backend not in ("anthropic", "minimax", "glm", "qwen") or not text:
        return []
    # Estrai tutti i match mantenendo l'ordine di apparizione
    matches = []
    # Un solo passaggio: fino al 2026-08-07 questo ciclo era scritto due volte,
    # identico. Il dedup su `seen` più sotto mascherava l'effetto — stesso
    # risultato — ma ogni risposta veniva scandita due volte per nulla.
    for regex in (_TOOL_USE_NAME_AFTER, _TOOL_USE_NAME_BEFORE):
        for m in regex.finditer(text):
            matches.append((m.start(), m.group(1)))
    # Ordina per posizione nel testo
    matches.sort(key=lambda x: x[0])
    seen = set()
    result = []
    for _, name in matches:
        if name not in seen:
            seen.add(name)
            result.append(name)
    # Filtra solo i tool con brand diverso da backend
    foreign = [name for name in result if brand_of_tool_name(name) and brand_of_tool_name(name) != backend]
    return foreign
