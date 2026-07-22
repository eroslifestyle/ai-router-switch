"""
Guardrail unificato stile OpenAI Agents SDK per il router HTTP.

Unifica 3 gate di verifica:
1. should_verify (campionamento) - da pipeline_common
2. hhem_score (factual) - da hhem_gate
3. llm_verify (coerenza semantica) - callback iniettato

Best-effort: 1 retry poi passa, agent_loop decide.
"""
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

try:
    from hhem_gate import HHEM_THRESHOLD
except ImportError:
    HHEM_THRESHOLD = 0.5


@dataclass
class VerifyResult:
    """Risultato guardrail SDK-style."""
    tripwire_triggered: bool
    reason: str
    score: float | None = None
    checked: bool = False


async def run_verify_guardrail(
    chat_fp: str,
    act_raw: bytes,
    *,
    use_hhem: bool = False,
    hhem_source: str = "",
    hhem_claim: str = "",
    llm_verify_fn: Callable[[bytes], Awaitable[bool]] | None = None,
    verify_sampled: bool = True,
) -> VerifyResult:
    """
    Guardrail unificato. Ordine:
    1. should_verify -> se False: not_sampled (skip)
    2. hhem_score -> se score < 0.5: tripwire
    3. llm_verify_fn -> se False: tripwire
    4. Altrimenti: ok

    Fail-open ovunque: eccezioni non bloccano, logga e continua.
    """
    if verify_sampled:
        try:
            from pipeline_common import should_verify
            should_check, _ = should_verify(chat_fp, act_raw)
            if not should_check:
                logger.info(
                    f"[verify_guardrail] fp={chat_fp} tripwire=False reason=not_sampled checked=False"
                )
                return VerifyResult(tripwire_triggered=False, reason="not_sampled", checked=False)
        except Exception as e:
            logger.warning(f"[verify_guardrail] should_verify failed: {e}")

    checked = False
    score = None

    if use_hhem and hhem_claim:
        try:
            from hhem_gate import hhem_score
            score = await hhem_score(hhem_source, hhem_claim, timeout_sec=10)
            checked = True
            if score is not None and score < HHEM_THRESHOLD:
                logger.info(
                    f"[verify_guardrail] fp={chat_fp} tripwire=True reason=hhem score={score}"
                )
                return VerifyResult(tripwire_triggered=True, reason="hhem", score=score, checked=True)
        except Exception as e:
            logger.warning(f"[verify_guardrail] hhem_score failed: {e}")

    if llm_verify_fn:
        try:
            is_coherent = await llm_verify_fn(act_raw)
            checked = True
            if not is_coherent:
                logger.info(
                    f"[verify_guardrail] fp={chat_fp} tripwire=True reason=llm_incoherent"
                )
                return VerifyResult(tripwire_triggered=True, reason="llm_incoherent", checked=True)
        except Exception as e:
            logger.warning(f"[verify_guardrail] llm_verify failed: {e}")

    logger.info(f"[verify_guardrail] fp={chat_fp} tripwire=False reason=ok score={score}")
    return VerifyResult(tripwire_triggered=False, reason="ok", score=score, checked=checked)
