"""
Generalizes tool_isolation to all body/history transformations.
Single registry point that orchestrates existing transformation functions
in deterministic order per backend target.

This module is additive: it composes existing functions without reimplementing them.
The forward_* modules remain responsible for model field and SSE store handling.
"""

import json
import logging

from router_constants import (
    ANTHROPIC_UNSUPPORTED_FIELDS,
    CLAUDE_CODE_MARKER,
)
from minimax_body import MINIMAX_MIN_MAX_TOKENS

logger = logging.getLogger(__name__)


def build_body_for(backend: str, body: bytes, request=None) -> bytes:
    """
    Applies in pipeline the filters for the target backend.

    backend in {"anthropic", "minimax", "glm"}.
    Unknown backend or non-JSON body -> returns body unchanged (safe no-op).

    Note: this function works only on body/history/tools/system/max_tokens.
    The `model` field and SSE store are handled by forward_* modules.
    """
    if backend not in {"anthropic", "minimax", "glm"}:
        return body

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body  # Non-JSON: no-op

    try:
        if backend == "anthropic":
            return _build_anthropic_body(body, data)
        elif backend == "minimax":
            return _build_minimax_body(data)
        elif backend == "glm":
            return _build_glm_body(data)
    except Exception:
        logger.exception("transition_filters: backend=%s unexpected error", backend)
        return body


def _safe_json_dumps(data: dict) -> bytes:
    """Serialize dict to JSON bytes, safe."""
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _inject_claude_code_marker(data: dict) -> None:
    """
    Injects CLAUDE_CODE_MARKER into system content if not already present.
    Handles both string and block-list system formats.
    Idempotent: safe to call multiple times without duplicating the marker.
    """
    system = data.get("system")
    if system is None:
        return

    marker = CLAUDE_CODE_MARKER

    if isinstance(system, str):
        if marker not in system:
            data["system"] = system + "\n" + marker
    elif isinstance(system, list):
        # Check if marker already present in any text block
        already_present = False
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                if marker in block.get("text", ""):
                    already_present = True
                    break
        if not already_present:
            data["system"] = [{"type": "text", "text": marker}] + system


def _build_anthropic_body(body: bytes, data: dict) -> bytes:
    """Pipeline for anthropic backend."""
    # 1. strip_unsupported_fields
    try:
        from forward_anthropic import strip_unsupported_fields
        body = strip_unsupported_fields(body, ANTHROPIC_UNSUPPORTED_FIELDS)
        data = json.loads(body)  # re-parse after mutation
    except ImportError:
        logger.warning("transition_filters: forward_anthropic not available")
    except Exception:
        logger.exception("transition_filters: strip_unsupported_fields failed")
        return body

    # 2. filter_tools_for_backend
    try:
        from tool_isolation import filter_tools_for_backend
        body = filter_tools_for_backend(body, "anthropic")
        data = json.loads(body)  # re-parse after mutation
    except ImportError:
        logger.warning("transition_filters: filter_tools_for_backend not available")
    except Exception:
        logger.exception("transition_filters: filter_tools_for_backend failed")

    # 3. _repair_message_sequence on messages
    if "messages" in data and isinstance(data["messages"], list):
        try:
            from router_utils import _repair_message_sequence
            data["messages"] = _repair_message_sequence(data["messages"])
        except ImportError:
            logger.warning("transition_filters: _repair_message_sequence not available")
        except Exception:
            logger.exception("transition_filters: _repair_message_sequence failed")

    # 4. inject_claude_code_marker
    _inject_claude_code_marker(data)

    return _safe_json_dumps(data)


def _build_minimax_body(data: dict) -> bytes:
    """Pipeline for minimax backend (no model/SSE touch)."""
    if "messages" not in data:
        return _safe_json_dumps(data)

    # 1. strip_server_tools_for_minimax (generic server_tool_use blocks)
    try:
        from minimax_body import strip_server_tools_for_minimax
        strip_server_tools_for_minimax(data)
    except ImportError:
        logger.warning("transition_filters: strip_server_tools_for_minimax not available")
    except Exception:
        logger.exception("transition_filters: strip_server_tools_for_minimax failed")

    # 2. _strip_images_from_messages
    if "messages" in data and isinstance(data["messages"], list):
        try:
            from pipeline_common import _strip_images_from_messages
            data["messages"] = _strip_images_from_messages(data["messages"])
        except ImportError:
            logger.warning("transition_filters: _strip_images_from_messages not available")
        except Exception:
            logger.exception("transition_filters: _strip_images_from_messages failed")

        # 3. _repair_message_sequence (rimuove i role=system: DEVE precedere l'inject)
        try:
            from router_utils import _repair_message_sequence
            data["messages"] = _repair_message_sequence(data["messages"])
        except ImportError:
            logger.warning("transition_filters: _repair_message_sequence not available")
        except Exception:
            logger.exception("transition_filters: _repair_message_sequence failed")

    # 4. _inject_system_as_message (DOPO repair, altrimenti repair scarta il system iniettato)
    try:
        from minimax_body import _inject_system_as_message
        _inject_system_as_message(data)
    except ImportError:
        logger.warning("transition_filters: _inject_system_as_message not available")
    except Exception:
        logger.exception("transition_filters: _inject_system_as_message failed")

    # 5. floor max_tokens >= MINIMAX_MIN_MAX_TOKENS
    if "max_tokens" in data and isinstance(data["max_tokens"], int):
        if data["max_tokens"] < MINIMAX_MIN_MAX_TOKENS:
            data["max_tokens"] = MINIMAX_MIN_MAX_TOKENS

    body = _safe_json_dumps(data)

    # 6. filter_tools_for_backend
    try:
        from tool_isolation import filter_tools_for_backend
        body = filter_tools_for_backend(body, "minimax")
    except ImportError:
        logger.warning("transition_filters: filter_tools_for_backend not available")
    except Exception:
        logger.exception("transition_filters: filter_tools_for_backend failed")

    return body


def _build_glm_body(data: dict) -> bytes:
    """Pipeline for GLM backend (no model/SSE touch)."""
    if "messages" not in data:
        return _safe_json_dumps(data)

    # 1. strip_server_tools_for_minimax (generic server_tool_use blocks)
    try:
        from minimax_body import strip_server_tools_for_minimax
        strip_server_tools_for_minimax(data)
    except ImportError:
        logger.warning("transition_filters: strip_server_tools_for_minimax not available")
    except Exception:
        logger.exception("transition_filters: strip_server_tools_for_minimax failed")

    # 2. system as top-level string (z.ai rejects role=system in messages)
    if "messages" in data and isinstance(data["messages"], list):
        try:
            from minimax_body import _system_to_text

            # Prendi il system esistente (può essere str o lista di blocchi)
            sys_val = data.get("system")
            system_str = ""

            # Se c'è un system field, convertilo in stringa
            if sys_val is not None:
                system_str = _system_to_text(sys_val)

            # Cerca messaggi role=system nei messages (da inject precedente)
            system_messages = [
                m for m in data["messages"]
                if isinstance(m, dict) and m.get("role") == "system"
            ]

            # Se ci sono messaggi system, estrai i contenuti e mergiali
            if system_messages:
                system_contents = [m.get("content", "") for m in system_messages if isinstance(m, dict)]
                additional_system = "\n".join(filter(None, system_contents))

                # Merge: system_str esistente + "\n" + contenuti dei messaggi system rimossi
                if system_str and additional_system:
                    system_str = system_str + "\n" + additional_system
                elif additional_system:
                    system_str = additional_system

            # Rimuovi i messaggi role=system dall'array messages
            data["messages"] = [
                m for m in data["messages"]
                if not (isinstance(m, dict) and m.get("role") == "system")
            ]

            # Se il risultato system_str è vuoto, non impostare data["system"]
            if system_str:
                data["system"] = system_str
        except ImportError:
            logger.warning("transition_filters: _system_to_text not available")
        except Exception:
            logger.exception("transition_filters: system-to-string conversion failed")

        # 3. _strip_images_from_messages
        try:
            from pipeline_common import _strip_images_from_messages
            data["messages"] = _strip_images_from_messages(data["messages"])
        except ImportError:
            logger.warning("transition_filters: _strip_images_from_messages not available")
        except Exception:
            logger.exception("transition_filters: _strip_images_from_messages failed")

        # 4. _repair_message_sequence
        try:
            from router_utils import _repair_message_sequence
            data["messages"] = _repair_message_sequence(data["messages"])
        except ImportError:
            logger.warning("transition_filters: _repair_message_sequence not available")
        except Exception:
            logger.exception("transition_filters: _repair_message_sequence failed")

    body = _safe_json_dumps(data)

    # 5. filter_tools_for_backend
    try:
        from tool_isolation import filter_tools_for_backend
        body = filter_tools_for_backend(body, "glm")
    except ImportError:
        logger.warning("transition_filters: filter_tools_for_backend not available")
    except Exception:
        logger.exception("transition_filters: filter_tools_for_backend failed")

    # 6. clamp_glm_max_tokens (limit z.ai to 32768)
    try:
        from glm_backend import clamp_glm_max_tokens
        body = clamp_glm_max_tokens(body)
    except ImportError:
        logger.warning("transition_filters: clamp_glm_max_tokens not available")
    except Exception:
        logger.exception("transition_filters: clamp_glm_max_tokens failed")

    return body
