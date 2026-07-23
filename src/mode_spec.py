"""
ModeSpec data-driven descriptions for THINK→ACT→VERIFY router modes.

Replaces the 3 copied functions (think_act_verify_*_am, think_act_verify_*_ag,
think_act_verify_*_gm) with data-driven ModeSpec definitions.

Each ModeSpec describes WHAT a mode does (not HOW - that is agent_loop.py).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

# Try to import from router_constants, fallback to literal values if missing
try:
    from router_constants import (
        MINIMAX_MODEL,
        MIXED_EXECUTOR_MODEL,
        THINK_MODEL,
        THINK_MODEL_ANTHROPIC,
        MINIMAX_FALLBACK_STATUSES,
        FALLBACK_STATUSES,
    )
except ImportError:
    logging.warning(
        "router_constants not found, using fallback literal values for mode_spec.py"
    )
    MINIMAX_MODEL = "MiniMax-M3"
    MIXED_EXECUTOR_MODEL = "MiniMax-M2.7"
    THINK_MODEL = "claude-sonnet-4-6"
    THINK_MODEL_ANTHROPIC = "claude-haiku-4-5-20251001"
    MINIMAX_FALLBACK_STATUSES = frozenset()
    FALLBACK_STATUSES = frozenset()


@dataclass(frozen=True)
class ModeSpec:
    """
    Data-driven specification for a THINK→ACT→VERIFY router mode.

    Attributes:
        name: Mode identifier (e.g., "mix-am", "mix-ag", "mix-gm")
        think_backend: Backend for THINK phase ("anthropic" | "glm" | None)
        act_backend: Backend for ACT phase ("minimax" | "glm" | "anthropic")
        verify_backend: Backend for VERIFY phase ("anthropic" | "glm" | None)
        act_chain: Ordered tuple of ACT models (escalation order)
        rescue_backend: Backend for rescue phase ("anthropic" | None)
        rescue_model: Model for rescue phase (str | None)
        use_hhem: Whether HHEM gating applies to output
        verify_sampled: Whether VERIFY runs on sampled basis vs always
        max_iterations: Full THINK→ACT→VERIFY passes. Default 1: i retry
            interni sono gestiti da act_chain/act_fn; >1 ri-eseguirebbe anche
            il THINK a ogni giro (costo upstream inutile su ACT gia' fallito).
    """

    name: str
    think_backend: str | None
    act_backend: str
    verify_backend: str | None
    act_chain: tuple[str, ...] = field(default_factory=tuple)
    rescue_backend: str | None = None
    rescue_model: str | None = None
    use_hhem: bool = False
    verify_sampled: bool = True
    max_iterations: int = 1


# Mode specifications
MODE_SPECS: dict[str, ModeSpec] = {
    # mix-am: Anthropic THINK → MiniMax ACT → Anthropic VERIFY
    "mix-am": ModeSpec(
        name="mix-am",
        think_backend="anthropic",
        act_backend="minimax",
        verify_backend="anthropic",
        act_chain=(MIXED_EXECUTOR_MODEL, MINIMAX_MODEL),  # m2.7 → M3 escalation
        rescue_backend="anthropic",
        rescue_model=THINK_MODEL_ANTHROPIC,  # Haiku rescue fixes bug (was THINK_MODEL=Sonnet)
        use_hhem=False,
        verify_sampled=True,
    ),
    # mix-ag: Anthropic THINK → GLM ACT → Anthropic VERIFY
    "mix-ag": ModeSpec(
        name="mix-ag",
        think_backend="anthropic",
        act_backend="glm",
        verify_backend="anthropic",
        act_chain=(),  # GLM tiering handled by glm_backend
        rescue_backend="anthropic",
        rescue_model=THINK_MODEL_ANTHROPIC,
        use_hhem=False,
        verify_sampled=True,
    ),
    # mix-gm: GLM THINK → MiniMax ACT → GLM VERIFY
    "mix-gm": ModeSpec(
        name="mix-gm",
        think_backend="glm",
        act_backend="minimax",
        verify_backend="glm",
        act_chain=(MINIMAX_MODEL,),  # single executor + retry
        # regola inviolabile: mix-gm non fa MAI fallback ad Anthropic
        rescue_backend=None,
        rescue_model=None,
        use_hhem=True,
        verify_sampled=True,
    ),
}


def get_mode_spec(name: str) -> ModeSpec | None:
    """
    Retrieve ModeSpec by name.

    Args:
        name: Mode identifier

    Returns:
        ModeSpec instance or None if not found
    """
    return MODE_SPECS.get(name)
