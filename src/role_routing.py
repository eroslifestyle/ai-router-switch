"""Pure role routing for LLM proxy.

This module determines the routing of LLM requests to providers (Anthropic, MiniMax, GLM)
based on the active mode and the model role (THINK, ACT, or unknown).

DESIGN PHILOSOPHY:
The router is a transparent tunnel — it does not orchestrate phases or hold state.
The gerarchia THINK/ACT/VERIFY/escalation is configured globally on the client
(~/.claude/CLAUDE.md), not in this module. This function is a simple LOOKUP:
given the mode and the model name, return (provider, model_override).

A model_override of None means: do not rewrite the "model" field — forward the
original model name to the provider as-is (used for Anthropic, which handles
version negotiation server-side).
"""
import os

# ── Model role families ────────────────────────────────────────────────────────
# These are the prefixes that identify a model's role.
_THINK_MODELS = ("claude-opus", "claude-sonnet", "claude-fable")
_ACT_MODELS = ("claude-haiku",)

# GPT_MODE_THINK: l'UNICO modello locale della modalità `gpt` — serve sia il
# THINK sia l'ACT (decisione utente 2026-08-18). Deve essere un modello GIA'
# RESIDENTE in memoria: la modalità nasce per non pagare mai un carico/scarico.
# Default `code-max` = llama-qcnext.service su :8083 (persistente), l'unico
# residente stabile. `coder-abliterated` (:8085) e' stato escluso come THINK
# separato: 40 GB di GTT in piu' con la macchina al 99% di RAM.
# Per puntarla altrove senza toccare il codice:
#   AIROUTER_GPT_MODEL=<modello>
# Verifica cosa è servito davvero:
#   curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:4000/v1/models
GPT_MODE_THINK = os.environ.get("AIROUTER_GPT_MODEL", "code-max")

# ── Provider model overrides ───────────────────────────────────────────────────
MINIMAX_THINK = "MiniMax-M3"
MINIMAX_ACT = "MiniMax-M2.7"
GLM_THINK = "glm-5.3"
GLM_ACT = "glm-4.7"
# qwen3.8-max: e' il piu' recente della linea max e il probe live del
# 2026-08-03 lo trova DISPONIBILE sull'account (HTTP 200), insieme a
# 3.7-max. Si usa il piu' nuovo.
QWEN_THINK = "qwen3.8-max"
QWEN_ACT = "qwen3-coder-plus"
# code-max: esecutore coding locale (Qwen3-Coder-Next 80B MXFP4 servito da
# llama.cpp dietro LiteLLM). Il provider 'local' NON ha un modello THINK:
# in mix-al il THINK resta su Anthropic.
LOCAL_ACT = "code-max"
# LOCAL_ACT_FAST era code-fast (Laguna XS 2.1, 33B-A3B via Ollama), esecutore ACT
# di local e mix-al dal 2026-08-17. Rimosso il 2026-08-19 per decisione utente: in
# locale si usa solo code-max. Due modelli locali insieme si contendono la stessa
# memoria — su questo APU la GPU pesca dalla RAM di sistema — ed e' la coabitazione
# che ha causato i lockup da saturazione GTT. L'alias resta puntato a code-max
# perche' e' citato altrove; non introdurre un secondo modello locale senza motivo.
LOCAL_ACT_FAST = LOCAL_ACT

# ── Role constants ─────────────────────────────────────────────────────────────
ROLE_THINK = "think"
ROLE_ACT = "act"
ROLE_UNKNOWN = "unknown"

# Le fasi della gerarchia sono TRE (THINK, ACT, VERIFY) ma i ruoli di routing
# sono DUE. Non è una dimenticanza: il VERIFY lo esegue sempre lo stesso modello
# che ha fatto il THINK, quindi la richiesta di verifica arriva qui con lo stesso
# nome di modello (es. claude-opus-5) e ricade su ROLE_THINK — cioè esattamente
# sul provider giusto. Una terza riga in ROUTING_TABLE sarebbe un duplicato di
# quella del THINK, con il rischio che le due divergano nel tempo.
# ROLE_VERIFY esiste per rendere leggibile questa scelta nel codice chiamante.
ROLE_VERIFY = ROLE_THINK

# ── Routing table (data-driven, not if/elif) ──────────────────────────────────
# Keyed by (mode, role) → (provider, model_override)
ROUTING_TABLE = {
    ("anthropic", ROLE_THINK): ("anthropic", None),
    ("anthropic", ROLE_ACT): ("anthropic", None),
    ("minimax", ROLE_THINK): ("minimax", MINIMAX_THINK),
    ("minimax", ROLE_ACT): ("minimax", MINIMAX_ACT),
    ("glm", ROLE_THINK): ("glm", GLM_THINK),
    ("glm", ROLE_ACT): ("glm", GLM_ACT),
    ("qwen", ROLE_THINK): ("qwen", QWEN_THINK),
    ("qwen", ROLE_ACT): ("qwen", QWEN_ACT),
    ("mix-am", ROLE_THINK): ("anthropic", None),
    ("mix-am", ROLE_ACT): ("minimax", MINIMAX_ACT),
    # mix-am-2: routing IDENTICO a mix-am (THINK->Anthropic, ACT->MiniMax). La
    # differenza e' tutta in enforce_hierarchy (deny mode-specific, vedi ~/.claude/hooks/):
    # chiude Edit/Write/Bash-redirect/micro-edit per forzare la delega a m3-code.
    ("mix-am-2", ROLE_THINK): ("anthropic", None),
    ("mix-am-2", ROLE_ACT): ("minimax", MINIMAX_ACT),
    ("mix-ag", ROLE_THINK): ("anthropic", None),
    ("mix-ag", ROLE_ACT): ("glm", GLM_ACT),
    # mix-ag-2: routing identico a mix-ag. La differenza e' enforce_hierarchy
    # (deny mode-specific). NB: in mix-ag-2 NON c'e' CLI di coding (glm-code non
    # esiste) -> _CODE_EXECUTOR e' stringa vuota: la delega passa solo dai subagent.
    ("mix-ag-2", ROLE_THINK): ("anthropic", None),
    ("mix-ag-2", ROLE_ACT): ("glm", GLM_ACT),
    ("mix-gm", ROLE_THINK): ("glm", GLM_THINK),
    ("mix-gm", ROLE_ACT): ("minimax", MINIMAX_ACT),
    # mix-gm-2: routing identico a mix-gm (THINK->GLM, ACT->MiniMax). Differenza
    # in enforce_hierarchy (deny mode-specific, delega a m3-code).
    ("mix-gm-2", ROLE_THINK): ("glm", GLM_THINK),
    ("mix-gm-2", ROLE_ACT): ("minimax", MINIMAX_ACT),
    ("mix-al", ROLE_THINK): ("anthropic", None),
    ("mix-al", ROLE_ACT): ("local", LOCAL_ACT_FAST),
    # local è una modalità pura: THINK/VERIFY e ACT vanno entrambi a code-max
    # (2026-08-19: code-fast rimosso, un solo modello locale).
    ("local", ROLE_THINK): ("local", LOCAL_ACT),
    ("local", ROLE_ACT): ("local", LOCAL_ACT_FAST),
    # gpt: MODELLO UNICO, quello gia' residente in memoria (decisione utente
    # 2026-08-18). Il THINK separato coder-abliterated e' stato escluso: teneva
    # 40 GB di GTT con la macchina al 99% di RAM, e col contesto pieno di Claude
    # Code il suo prefill (~250 tok/s su ~104k token di schemi MCP) sforava il
    # LOCAL_TIMEOUT_SEC di 240 s. Un solo modello = zero carico/scarico, zero
    # secondo residente. GPT_MODE_THINK resta configurabile via AIROUTER_GPT_MODEL.
    ("gpt", ROLE_THINK): ("local", GPT_MODE_THINK),
    ("gpt", ROLE_ACT): ("local", GPT_MODE_THINK),
}

# ── Default provider per mode (used for unknown roles) ────────────────────────
# The default is the provider from the ACT column (the executor), with model=None.
_MODE_DEFAULT_PROVIDER = {
    "anthropic": "anthropic",
    "minimax": "minimax",
    "glm": "glm",
    "qwen": "qwen",
    "mix-am": "minimax",
    "mix-am-2": "minimax",
    "mix-ag": "glm",
    "mix-gm": "minimax",
    "mix-gm-2": "minimax",
    "mix-ag-2": "glm",
    "mix-al": "local",
    "local": "local",
    "gpt": "local",
}

VALID_MODES = ("anthropic", "minimax", "glm", "qwen", "mix-am", "mix-am-2", "mix-ag", "mix-ag-2", "mix-gm", "mix-gm-2", "mix-al", "local", "gpt")


def modes_with_act_provider(provider: str) -> frozenset:
    """Modalita' il cui ESECUTORE (ACT) e' ``provider``.

    Esiste perche' l'elenco a mano si dimentica le modalita' nuove. Il proxy
    teneva ``{"minimax", "mix-am", "mix-gm"}`` scritto a mano per decidere se
    applicare lo shrink proattivo verso il collo di bottiglia MiniMax: quando
    sono arrivate ``mix-am-2`` e ``mix-gm-2`` — stesso routing delle basi —
    nessuno ha aggiornato il set, e mix-am-2 (il 47% del traffico, misurato il
    2026-08-16) e' rimasta senza shrink. Derivandolo da qui, una modalita' nuova
    e' coperta il giorno stesso in cui entra in ``_MODE_DEFAULT_PROVIDER``.
    """
    return frozenset(m for m, p in _MODE_DEFAULT_PROVIDER.items() if p == provider)


# ── Native executor per provider ─────────────────────────────────────────────
# It's the NATIVE EXECUTOR of the provider, never the THINK (the THINK is
# chosen manually by the user, it should never be decided by code).
_NATIVE_EXECUTOR = {
    # ponytail: local ACT = code-max, unico modello locale dal 2026-08-19

    "anthropic": "claude-haiku-4-5-20251001",
    "minimax": MINIMAX_ACT,
    "glm": GLM_ACT,
    "qwen": QWEN_ACT,
    "local": LOCAL_ACT_FAST,
}


def model_role(model_name: str | None) -> str:
    """Determine the role of a model based on its name.

    Args:
        model_name: The model name from the client request (e.g., "claude-opus-5").
                   Can be None or empty.

    Returns:
        ROLE_THINK, ROLE_ACT, or ROLE_UNKNOWN.

    Model families are matched case-insensitively on the prefix, allowing for
    version suffixes (e.g., "claude-opus-5", "claude-haiku-4-5-20251001").
    """
    if not model_name:
        return ROLE_UNKNOWN

    model_lower = model_name.lower()

    if any(model_lower.startswith(prefix) for prefix in _THINK_MODELS):
        return ROLE_THINK

    if any(model_lower.startswith(prefix) for prefix in _ACT_MODELS):
        return ROLE_ACT

    return ROLE_UNKNOWN



def model_provider(model_name: str | None) -> str | None:
    """Distinguish a FOREIGN model from an unclassified name.

    A foreign model is one that clearly belongs to a different provider
    (e.g., "MiniMax-M3" belongs to minimax, "claude-opus-5" belongs to anthropic).

    An unclassified name should NEVER be rewritten: this is the case of
    legacy Claude names like "claude-3-5-sonnet-20241022", which Anthropic
    also knows even if they don't match the role prefixes.

    Args:
        model_name: The model name to classify.

    Returns:
        "anthropic", "minimax", "glm", "qwen" or None if unclassified.
    """
    if not model_name:
        return None

    model_lower = model_name.lower()

    if model_lower.startswith("claude"):
        return "anthropic"
    if "minimax" in model_lower:
        return "minimax"
    if model_lower.startswith("glm"):
        return "glm"

    # qwen* = LLM, qwq/qvq = legacy reasoning/visione, wan* = immagini,
    # happyhorse* = video (modelli dei servizi nativi Model Studio).
    if model_lower.startswith(("qwen", "qwq", "qvq", "wan", "happyhorse")):
        return "qwen"

    # Modelli locali (LiteLLM/llama.cpp su questa macchina): confronto ESATTO o
    # startswith, mai `in`, per non catturare per sbaglio altri nomi. Serve
    # all'isolamento fra modalita': un modello locale non va mai a un provider remoto.
    if model_lower in ("code-max", "code-max-ollama") or model_lower.startswith("qcnext"):
        return "local"

    return None


def _nativize(provider: str, override: str | None, model_name: str | None) -> str | None:
    """Apply nativization: replace foreign model with provider's native executor if needed."""
    if override is not None:
        return override

    foreign = model_provider(model_name)
    if foreign is not None and foreign != provider:
        return _NATIVE_EXECUTOR[provider]

    return override


def resolve_route(mode: str, model_name: str | None) -> tuple[str, str | None]:
    """Resolve the routing for a given mode and model.

    Args:
        mode: The active mode (must be one of VALID_MODES).
        model_name: The model name from the client request (e.g., "claude-opus-5").

    Returns:
        A tuple (provider, model_override):
        - provider: one of "anthropic", "minimax", "glm"
        - model_override: the model name to use (or None to keep the original)

    Raises:
        ValueError: if mode is not in VALID_MODES.

    Note:
        A pure mode should never call a model from another provider (user rule 2026-08-01).
        If the resolved model is foreign to the provider, it will be replaced with the
        provider's native executor model.
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid mode: {mode!r}. Valid modes are: {', '.join(VALID_MODES)}"
        )

    role = model_role(model_name)

    # First try to lookup (mode, role) in the table
    if (mode, role) in ROUTING_TABLE:
        provider, override = ROUTING_TABLE[(mode, role)]
        return (provider, _nativize(provider, override, model_name))

    # If role is unknown, use the mode's default provider with model=None
    if role == ROLE_UNKNOWN:
        provider = _MODE_DEFAULT_PROVIDER[mode]
        return (provider, _nativize(provider, None, model_name))

    # Should never reach here if ROUTING_TABLE and _MODE_DEFAULT_PROVIDER are complete
    raise RuntimeError(
        f"Incomplete routing table: mode={mode!r}, role={role!r}"
    )
