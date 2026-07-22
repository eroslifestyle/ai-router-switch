"""Run loop unico tipizzato per le modalità mix del router — stile OpenAI Agents SDK.

Sostituisce le 3 copie quasi-identiche di THINK→ACT→VERIFY (mix-am/mix-ag/mix-gm)
con una singola funzione `run_agent_loop` parametrizzata da una ModeSpec (mode_spec.py).

Ispirato a `next_step` dell'OpenAI Agents SDK: gli esiti di ogni fase sono stati
espliciti tipizzati (StepType) invece di condizioni su status HTTP sparse.
La logica di trasporto (forward_*, shrink, escalation) NON è reimplementata qui:
viene iniettata come callback in LoopContext (dependency injection), così questo
modulo resta puro e additivo finché non viene cablato nel dispatch (fase successiva).

Regola inviolabile preservata: se spec.rescue_backend is None (mix-gm), rescue_fn
non viene MAI chiamato — mix-gm non fa fallback ad Anthropic.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class StepType(Enum):
    FINAL_OUTPUT = "final_output"
    RUN_AGAIN = "run_again"
    ESCALATE = "escalate"
    INTERRUPTION = "interruption"


@dataclass
class StepResult:
    step_type: StepType
    payload: Any = None
    reason: str = ""
    next_model: str | None = None


@dataclass
class LoopContext:
    spec: Any
    request: Any
    body: bytes
    session: Any
    orig: dict
    chat_fp: str
    relay: Callable[[Any], Any] | None = None
    think_fn: Callable[..., tuple[int, str]] | None = None
    act_fn: Callable[..., Any] | None = None
    verify_fn: Callable[..., bool] | None = None
    rescue_fn: Callable[..., Any] | None = None
    should_verify_fn: Callable[..., bool] | None = None


DEFAULT_MAX_TURNS = 6
FALLBACK_STATUSES = {401, 403, 404, 408, 409, 413, 429, 500, 502, 503, 504, 529}


def _is_ok_status(status: int, fallback_statuses: set[int] | None = None) -> bool:
    return status not in (fallback_statuses or FALLBACK_STATUSES)


async def run_agent_loop(ctx: LoopContext) -> StepResult:
    max_iterations = getattr(ctx.spec, "max_iterations", DEFAULT_MAX_TURNS)
    for i in range(max_iterations):
        plan_text = ""
        if ctx.spec.think_backend and ctx.think_fn:
            try:
                status, plan_text = await ctx.think_fn(ctx)
                logger.info(f"[agent_loop] {ctx.spec.name} iteration={i+1} step=THINK status={status}")
            except Exception as e:
                logger.warning(f"[agent_loop] {ctx.spec.name} step=THINK_ERROR reason={e}")
        elif ctx.spec.think_backend:
            logger.warning(f"[agent_loop] {ctx.spec.name} step=THINK_SKIP reason=think_fn_not_injected")
        act_chain = getattr(ctx.spec, "act_chain", None) or (None,)
        for model in act_chain:
            if ctx.act_fn is None:
                logger.warning(f"[agent_loop] {ctx.spec.name} step=ACT_SKIP reason=act_fn_not_injected")
                break
            try:
                act_output = await ctx.act_fn(ctx, model, plan_text)
                status = getattr(act_output, "status", 200)
                logger.info(f"[agent_loop] {ctx.spec.name} step=ACT model={model} status={status}")
                if _is_ok_status(status):
                    verify_needed = ctx.spec.verify_backend
                    if getattr(ctx.spec, "verify_sampled", False):
                        if ctx.should_verify_fn is None:
                            logger.warning(f"[agent_loop] {ctx.spec.name} step=VERIFY_SKIP reason=sampled_but_no_should_verify_fn")
                            verify_needed = False
                        else:
                            verify_needed = ctx.should_verify_fn(ctx.chat_fp, act_output)
                    if verify_needed and ctx.verify_fn:
                        try:
                            coherent = await ctx.verify_fn(ctx, act_output)
                            logger.info(f"[agent_loop] {ctx.spec.name} step=VERIFY coherent={coherent}")
                            if not coherent:
                                if model != act_chain[-1]:
                                    continue
                                return StepResult(step_type=StepType.FINAL_OUTPUT, payload=act_output, reason="verify_fail_no_more_models")
                        except Exception as e:
                            logger.error(f"[agent_loop] {ctx.spec.name} step=VERIFY_ERROR reason={e}")
                    return StepResult(step_type=StepType.FINAL_OUTPUT, payload=act_output, reason="act_success")
            except Exception as e:
                logger.error(f"[agent_loop] {ctx.spec.name} step=ACT_ERROR model={model} reason={e}")
        if ctx.spec.rescue_backend:
            if ctx.rescue_fn is None:
                logger.warning(f"[agent_loop] {ctx.spec.name} step=RESCUE_SKIP reason=rescue_backend_set_but_fn_missing")
            else:
                try:
                    rescue_output = await ctx.rescue_fn(ctx)
                    logger.info(f"[agent_loop] {ctx.spec.name} step=RESCUE_SUCCESS")
                    return StepResult(step_type=StepType.FINAL_OUTPUT, payload=rescue_output, reason="rescue_success")
                except Exception as e:
                    logger.error(f"[agent_loop] {ctx.spec.name} step=RESCUE_ERROR reason={e}")
        else:
            logger.info(f"[agent_loop] {ctx.spec.name} step=NO_RESCUE reason=rescue_backend_not_set")
    return StepResult(step_type=StepType.FINAL_OUTPUT, payload=None, reason="max_iterations_reached")
