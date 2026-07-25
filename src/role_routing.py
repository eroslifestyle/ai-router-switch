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

# ── Model role families ────────────────────────────────────────────────────────
# These are the prefixes that identify a model's role.
_THINK_MODELS = ("claude-opus", "claude-sonnet", "claude-fable")
_ACT_MODELS = ("claude-haiku",)

# ── Provider model overrides ───────────────────────────────────────────────────
MINIMAX_THINK = "MiniMax-M3"
MINIMAX_ACT = "MiniMax-M2.7"
GLM_THINK = "glm-5.2"
GLM_ACT = "glm-4.7"

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
    ("mix-am", ROLE_THINK): ("anthropic", None),
    ("mix-am", ROLE_ACT): ("minimax", MINIMAX_ACT),
    ("mix-ag", ROLE_THINK): ("anthropic", None),
    ("mix-ag", ROLE_ACT): ("glm", GLM_ACT),
    ("mix-gm", ROLE_THINK): ("glm", GLM_THINK),
    ("mix-gm", ROLE_ACT): ("minimax", MINIMAX_ACT),
}

# ── Default provider per mode (used for unknown roles) ────────────────────────
# The default is the provider from the ACT column (the executor), with model=None.
_MODE_DEFAULT_PROVIDER = {
    "anthropic": "anthropic",
    "minimax": "minimax",
    "glm": "glm",
    "mix-am": "minimax",
    "mix-ag": "glm",
    "mix-gm": "minimax",
}

VALID_MODES = ("anthropic", "minimax", "glm", "mix-am", "mix-ag", "mix-gm")


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
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid mode: {mode!r}. Valid modes are: {', '.join(VALID_MODES)}"
        )

    role = model_role(model_name)

    # First try to lookup (mode, role) in the table
    if (mode, role) in ROUTING_TABLE:
        return ROUTING_TABLE[(mode, role)]

    # If role is unknown, use the mode's default provider with model=None
    if role == ROLE_UNKNOWN:
        default_provider = _MODE_DEFAULT_PROVIDER[mode]
        return (default_provider, None)

    # Should never reach here if ROUTING_TABLE and _MODE_DEFAULT_PROVIDER are complete
    raise RuntimeError(
        f"Incomplete routing table: mode={mode!r}, role={role!r}"
    )
