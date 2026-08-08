"""Anthropic model capability detection and request sanitization."""

import json
import re
from typing import Any

CLAUDE_CODE_MARKER = "You are Claude Code, Anthropic's official CLI for Claude."
# Si matcha il PREFISSO, senza il punto finale: nei transcript il marker Claude
# Code compare sia chiuso da punto (59 volte) sia proseguito da una virgola in
# "...for Claude, running within the Claude Agent SDK." (14 volte). Entrambe le
# forme ottengono 200 su sonnet-5 e opus-5 (probe 2026-08-08).
CLAUDE_CODE_MARKER_PREFIX = CLAUDE_CODE_MARKER.rstrip('.')

AGENT_SDK_MARKER = "You are a Claude agent, built on Anthropic's Claude Agent SDK."
# Probe api.anthropic.com (sonnet-5, max_tokens=16, 2026-08-08):
# - "...SDK." e "...CLI for Claude."                                     -> 200
# - "...CLI for Claude, running within the Claude Agent SDK."             -> 200
# - "You are a helpful assistant."                                       -> 429
# Il gate guarda il system e accetta piu' di una forma first-party.
# La tupla e' una whitelist per forza incompleta: un falso negativo
# fa iniettare un marker duplicato in testa al system e azzera il
# prompt caching (rapporto cache_read/cache_creation: 82,4 -> 2,6),
# quindi l'iniezione resta disattivata sul traffico passante.
FIRST_PARTY_SYSTEM_SENTINELS = (CLAUDE_CODE_MARKER_PREFIX,
                                AGENT_SDK_MARKER.rstrip('.'))
BETA_CONTEXT_MANAGEMENT = "context-management-2025-06-27"
BETA_COMPACTION = "compact-2026-01-12"
BETA_TASK_BUDGETS = "task-budgets-2026-03-13"

_DATE_SUFFIX = re.compile(r"[-_]\d{8}$")

_MODEL_PATTERN = re.compile(
    r"(opus|sonnet|haiku|fable|mythos)[-_](\d+(?:[._-]\d+)?)",
    re.IGNORECASE,
)

_MIN_EFFORT_VERSION: dict[str, tuple[int, ...]] = {
    "opus": (4, 5),
    "sonnet": (4, 6),
}

_MIN_THINKING_VERSION: dict[str, tuple[int, ...]] = {
    "opus": (4, 6),
    "sonnet": (4, 6),
}

_ALWAYS_SUPPORTS: frozenset[str] = frozenset({"fable", "mythos", "opus-5", "sonnet-5"})


def _parse_model(name: str | None) -> tuple[str, tuple[int, ...]] | None:
    """Estrae famiglia e versione da un nome modello. None se non riconosciuto."""
    if not name:
        return None
    # Il suffisso data (es. '-20241022') non e' una versione: va tolto prima del
    # parsing, altrimenti 'claude-3-5-sonnet-20241022' verrebbe letto come
    # sonnet versione 20241022 e supererebbe qualunque soglia. Nei nomi legacy
    # la versione precede la famiglia ('claude-3-5-sonnet'), quindi dopo lo
    # strip non resta alcun numero dopo la famiglia e il modello resta ignoto.
    cleaned = _DATE_SUFFIX.sub("", name.strip())
    m = _MODEL_PATTERN.search(cleaned)
    if not m:
        return None
    family = m.group(1).lower()
    raw_ver = m.group(2).replace("_", "-").replace(".", "-")
    version = tuple(int(x) for x in raw_ver.split("-"))
    return family, version


def supports_effort(model: str | None) -> bool:
    """True se il modello supporta output_config.effort."""
    parsed = _parse_model(model)
    if not parsed:
        return False
    family, version = parsed
    if family in _ALWAYS_SUPPORTS:
        return True
    if family in _MIN_EFFORT_VERSION:
        return version >= _MIN_EFFORT_VERSION[family]
    return False


def supports_adaptive_thinking(model: str | None) -> bool:
    """True se il modello supporta il campo thinking."""
    parsed = _parse_model(model)
    if not parsed:
        return False
    family, version = parsed
    if family in _ALWAYS_SUPPORTS:
        return True
    if family in _MIN_THINKING_VERSION:
        return version >= _MIN_THINKING_VERSION[family]
    return False


def requires_claude_code_marker(model: str | None) -> bool:
    """True se il modello richiede il Claude Code marker nel system prompt."""
    if not model:
        return False
    parsed = _parse_model(model)
    if not parsed:
        return False
    family, _ = parsed
    return family in {"opus", "sonnet", "fable", "mythos"}


def unsupported_fields(
    model: str | None, allow_context_management: bool = False
) -> tuple[str, ...]:
    """Ritorna i campi da rimuovere dal body per il modello specificato."""
    fields = []
    if not supports_effort(model):
        fields.append("output_config")
    if not supports_adaptive_thinking(model):
        fields.append("thinking")
    if not allow_context_management:
        fields.append("context_management")
    return tuple(sorted(fields))


def _contains_sentinel(text: str) -> bool:
    """True se text contiene almeno una delle sentinelle first-party."""
    return any(sentinel in text for sentinel in FIRST_PARTY_SYSTEM_SENTINELS)


def has_marker(system_field: Any) -> bool:
    """True se un marker Claude Code e' presente nel system field.

    Il gate Anthropic accetta piu' di una forma di system first-party:
    1. 'You are a Claude agent, built on Anthropic's Claude Agent SDK.'
    2. 'You are Claude Code, Anthropic's official CLI for Claude.'
    3. 'You are Claude Code, ... for Claude, running within the Claude Agent SDK.'

    Il confronto con FIRST_PARTY_SYSTEM_SENTINELS (tupla di prefissi) copre
    tutte le varianti. Un falso negativo e' costoso: il proxy inietta il marker
    Claude Code, cambiando il prefisso del system e azzerando il prompt caching.
    """
    if not system_field:
        return False
    if isinstance(system_field, str):
        return _contains_sentinel(system_field)
    if isinstance(system_field, list):
        # Il formato canonico e' una lista di blocchi {type,text}, ma un proxy
        # vede body arbitrari: si accetta anche la lista di stringhe nuda,
        # altrimenti il marker verrebbe duplicato a ogni richiesta e la cache
        # invalidata a ogni turno.
        for item in system_field:
            if isinstance(item, str) and _contains_sentinel(item):
                return True
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and _contains_sentinel(text):
                    return True
        return False
    return False


def ensure_marker(
    body: dict, model: str | None = None
) -> tuple[dict, bool]:
    """Aggiunge il Claude Code marker se richiesto. Ritorna (body, added)."""
    if model is None:
        model = body.get("model")
    if not requires_claude_code_marker(model):
        return body, False
    system = body.get("system")
    if has_marker(system):
        return body, False
    new_body = dict(body)
    marker_block = {"type": "text", "text": CLAUDE_CODE_MARKER}
    if system is None:
        new_body["system"] = [marker_block]
    elif isinstance(system, str):
        new_body["system"] = [marker_block, {"type": "text", "text": system}]
    elif isinstance(system, list):
        new_body["system"] = [marker_block] + list(system)
    else:
        return body, False
    return new_body, True


def strip_for_model(
    body_bytes: bytes, model: str | None = None, allow_context_management: bool = False
) -> bytes:
    """Rimuove i campi non supportati dal body JSON."""
    try:
        body = json.loads(body_bytes)
        if not isinstance(body, dict):
            return body_bytes
    except (json.JSONDecodeError, TypeError):
        return body_bytes

    if model is None:
        model = body.get("model")
    fields = unsupported_fields(model, allow_context_management)
    if not fields:
        return body_bytes

    if any(k in body for k in fields):
        cleaned = {k: v for k, v in body.items() if k not in fields}
        return json.dumps(cleaned).encode()
    return body_bytes


def prepare_body(
    body_bytes: bytes,
    allow_context_management: bool = False,
    inject_marker: bool = True,
) -> tuple[bytes, dict]:
    """Sanifica un body per Anthropic in un solo parse: strip + marker.

    Fa due cose che finora erano separate o assenti:
    1. rimuove i campi che il modello di destinazione non supporta (whitelist
       per modello, non piu' strip cieco: i modelli recenti supportano
       output_config.effort e thinking, i vecchi rispondono 400);
    2. garantisce il marker Claude Code sui modelli che lo esigono, perche'
       senza di esso opus e sonnet rispondono 429 con un messaggio che parla
       (mentendo) di rate limit. Haiku non ne ha bisogno e non viene toccato.

    Il marker si inietta solo se assente: iniettarlo due volte gonfierebbe il
    system e invaliderebbe il prompt caching a ogni turno.

    Ritorna (body, info) dove info riporta cosa e' stato fatto, per il log.
    Su JSON non valido restituisce il body invariato: un proxy non deve mai
    rompere una richiesta che non riesce a interpretare.
    """
    info: dict = {"stripped": (), "marker_added": False, "model": None,
                  "parsed": False, "has_context_management": False}
    try:
        body = json.loads(body_bytes)
        if not isinstance(body, dict):
            return body_bytes, info
    except (json.JSONDecodeError, TypeError, ValueError):
        return body_bytes, info

    info["parsed"] = True
    model = body.get("model")
    info["model"] = model
    # Serve al chiamante per sapere se aggiungere il beta header: senza di quello
    # un context_management lasciato passare fa fallire la richiesta con 400.
    info["has_context_management"] = "context_management" in body

    fields = unsupported_fields(model, allow_context_management)
    present = tuple(k for k in fields if k in body)
    changed = False
    if present:
        body = {k: v for k, v in body.items() if k not in fields}
        info["stripped"] = present
        changed = True

    if inject_marker:
        body, added = ensure_marker(body, model)
        if added:
            info["marker_added"] = True
            changed = True

    if not changed:
        return body_bytes, info
    return json.dumps(body).encode(), info


if __name__ == "__main__":
    assert not supports_effort("claude-haiku-4-5")
    assert supports_effort("claude-sonnet-5")
    assert supports_effort("claude-opus-5")
    assert not supports_effort("claude-3-5-sonnet-20241022")
    assert not supports_effort("claude-sonnet-4-5")
    assert not supports_effort("claude-opus-4-1")
    assert supports_effort("claude-opus-4-5")
    assert supports_effort("claude-sonnet-4-6")
    assert supports_effort("claude-fable-5")
    assert supports_effort("claude-mythos-5")
    assert supports_effort(None) is False

    assert supports_adaptive_thinking("claude-opus-4-6")
    assert supports_adaptive_thinking("claude-sonnet-4-6")
    assert supports_adaptive_thinking("claude-fable-5")
    assert not supports_adaptive_thinking("claude-haiku-4-5")
    assert not supports_adaptive_thinking("claude-3-5-sonnet-20241022")
    assert supports_adaptive_thinking("claude-opus-5")
    assert supports_adaptive_thinking(None) is False

    assert requires_claude_code_marker("claude-opus-4-5")
    assert requires_claude_code_marker("claude-sonnet-4-5")
    assert requires_claude_code_marker("claude-fable-5")
    assert requires_claude_code_marker("claude-mythos-5")
    assert not requires_claude_code_marker("claude-haiku-4-5")
    assert not requires_claude_code_marker(None)

    haiku_unsupported = set(unsupported_fields("claude-haiku-4-5"))
    assert "output_config" in haiku_unsupported
    assert "context_management" in haiku_unsupported
    assert "thinking" in haiku_unsupported

    opus_unsupported = set(unsupported_fields("claude-opus-5"))
    assert "output_config" not in opus_unsupported
    assert "thinking" not in opus_unsupported
    assert "context_management" in opus_unsupported

    opus_with_cm = set(unsupported_fields("claude-opus-5", allow_context_management=True))
    assert "context_management" not in opus_with_cm

    assert has_marker(CLAUDE_CODE_MARKER)
    assert has_marker([{"type": "text", "text": CLAUDE_CODE_MARKER}])
    assert not has_marker([{"type": "text", "text": "other"}])
    assert not has_marker(CLAUDE_CODE_MARKER.replace("CLI", "XXX"))
    assert not has_marker(None)
    assert not has_marker(123)
    # La variante con la virgola vale come marker per l'API (200 su sonnet-5 e
    # opus-5, misurato 2026-08-08): non va duplicata, o si perde la cache.
    sdk_marker = "You are Claude Code, Anthropic's official CLI for Claude, running within the Claude Agent SDK."
    assert has_marker(sdk_marker)
    assert has_marker([{"type": "text", "text": sdk_marker}])
    assert has_marker([sdk_marker])
    opus_body = {"model": "claude-opus-4-5", "system": sdk_marker}
    opus_with_marker, added = ensure_marker(opus_body)
    assert not added
    assert opus_with_marker is opus_body

    # Forma catturata dal body reale del CLI 2.1.223 con cc_entrypoint=claude-vscode,
    # blocco system numero 1: da sola ottiene HTTP 200 su sonnet-5.
    assert has_marker(AGENT_SDK_MARKER)
    assert has_marker([{"type": "text", "text": AGENT_SDK_MARKER}])
    cli_system = [
        {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.223.ec2; cc_entrypoint=claude-vscode;"},
        {"type": "text", "text": AGENT_SDK_MARKER},
        {"type": "text", "text": "You are an interactive agent..."},
    ]
    assert has_marker(cli_system)
    opus_body = {"model": "claude-opus-4-5", "system": cli_system}
    opus_with_marker, added = ensure_marker(opus_body)
    assert not added
    assert opus_with_marker is opus_body

    opus_body = {"model": "claude-opus-4-5", "system": "hello"}
    opus_with_marker, added = ensure_marker(opus_body)
    assert added
    assert opus_with_marker is not opus_body
    assert opus_with_marker["system"][0]["text"] == CLAUDE_CODE_MARKER
    assert opus_with_marker["system"][1]["text"] == "hello"

    opus_body2 = {"model": "claude-opus-4-5", "system": [CLAUDE_CODE_MARKER]}
    opus_same, added2 = ensure_marker(opus_body2)
    assert not added2
    assert opus_same is opus_body2

    haiku_body = {"model": "claude-haiku-4-5", "system": "world"}
    haiku_same, added3 = ensure_marker(haiku_body)
    assert not added3
    assert haiku_same is haiku_body

    assert json.loads(strip_for_model(b'{"model":"claude-haiku-4-5","output_config":{"effort":"low"}}')) == {"model": "claude-haiku-4-5"}
    result = strip_for_model(b'not json')
    assert result == b'not json'
    result2 = strip_for_model(b'["list"]')
    assert result2 == b'["list"]'
    # opus-5 SUPPORTA thinking: il campo non va rimosso, il body resta invariato.
    result3 = strip_for_model(b'{"model":"claude-opus-5","thinking":{"type":"adaptive"}}')
    assert json.loads(result3) == {"model": "claude-opus-5", "thinking": {"type": "adaptive"}}
    # haiku NON lo supporta: li' il campo sparisce.
    result3b = strip_for_model(b'{"model":"claude-haiku-4-5","thinking":{"type":"adaptive"}}')
    assert json.loads(result3b) == {"model": "claude-haiku-4-5"}
    # context_management si strippa sempre, a meno che non sia esplicitamente permesso.
    cm = b'{"model":"claude-opus-5","context_management":{"edits":[]}}'
    assert json.loads(strip_for_model(cm)) == {"model": "claude-opus-5"}
    assert json.loads(strip_for_model(cm, allow_context_management=True)) == json.loads(cm)
    result4 = strip_for_model(b'{"model":"claude-haiku-4-5","other":"unchanged"}')
    assert result4 == b'{"model":"claude-haiku-4-5","other":"unchanged"}'

    print("OK")
