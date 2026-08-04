"""Self-healing sensor module for AI router."""

import json
from typing import Any, Dict, List, Union

# Constants
CODING_SIGNAL_THRESHOLD = 3
LARGE_BODY_BYTES = 20000
LARGE_TEXT_CHARS = 8000
CODING_KEYWORDS = ["def ", "function ", "class ", "import ", "fix ", "bug", "implement", "refactor", "```"]


def classify_outcome(
    status: int = 200,
    stop_reason: str = "",
    text_blocks: int = 0,
    tool_use_blocks: int = 0,
    output_tokens: int = 0,
    measure_partial: bool = False,
    thinking_blocks: int = 0,
) -> str:
    """
    Classify the actual outcome of a response.

    Returns: str in {ok, empty, truncated, tool_only, error, unknown}
    'unknown' means incomplete measurement, not an empty response.
    When thinking_blocks>=1 with no text or tool blocks and stop_reason
    empty, the stream never reached the final message_delta so the
    measurement is incomplete - this is 'unknown', not 'empty'.
    """
    # Handle None values by treating them as defaults
    if status is None:
        status = 200
    if stop_reason is None:
        stop_reason = ""
    if text_blocks is None:
        text_blocks = 0
    if tool_use_blocks is None:
        tool_use_blocks = 0
    if output_tokens is None:
        output_tokens = 0
    if measure_partial is None:
        measure_partial = False
    if thinking_blocks is None:
        thinking_blocks = 0

    # Logic order - return at first match
    if status >= 400 or stop_reason == "error":
        return "error"
    if measure_partial and text_blocks == 0 and tool_use_blocks == 0:
        return "unknown"
    # The relay accumulates only the first and last 16KB of the response.
    # A "text" content_block_start that falls in the middle 16KB gap
    # is never counted, while "thinking" blocks (at the start) are.
    # Result: text_blocks=0 with thinking_blocks>=1.
    # "empty" is in FAIL_OUTCOMES, so false "empty" degrades the model.
    if text_blocks == 0 and tool_use_blocks == 0 and thinking_blocks >= 1:
        # stop_reason empty = stream never reached final message_delta,
        # measurement incomplete -> unknown (not empty).
        if not stop_reason:
            return "unknown"
        # stop_reason set = model concluded producing only thinking,
        # no text or tools -> real failure.
        return "empty"
    if text_blocks == 0 and tool_use_blocks == 0:
        return "empty"
    if text_blocks == 0 and tool_use_blocks > 0:
        return "tool_only"
    if stop_reason == "max_tokens":
        # text_blocks==0 e' gia' stato catturato da empty/tool_only sopra;
        # qui arriviamo con text_blocks>=1: output prodotto ma troncato.
        return "truncated"
    return "ok"


def classify_task(body: Union[Dict[str, Any], bytes, str]) -> str:
    """
    Classify the type of task from the request body (Anthropic Messages API).

    Returns: str in {coding, reasoning, chat, vision, tool_heavy}
    """
    # Normalize body to dict
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            body = {}
    elif not isinstance(body, dict):
        body = {}

    messages = body.get("messages", []) or []
    has_tools = bool(body.get("tools"))
    has_images = False
    full_text_parts: List[str] = []

    # Process messages to extract text and detect images
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if content is None:
            continue

        # Handle string content
        if isinstance(content, str):
            full_text_parts.append(content.lower())
        # Handle list of content blocks
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                # Check for images
                if block_type == "image":
                    has_images = True
                elif block_type == "image_url":
                    has_images = True
                # Extract text from text blocks
                elif block_type == "text":
                    text = block.get("text") or ""
                    if text:
                        full_text_parts.append(text.lower())

    full_text = "".join(full_text_parts)

    # Count coding signals
    coding_signals = 0
    for keyword in CODING_KEYWORDS:
        if keyword in full_text:
            coding_signals += 1

    # Calculate body size as proxy for complexity
    body_size = len(json.dumps(body))

    # Logic order - return at first match
    if has_images:
        return "vision"
    if has_tools and coding_signals >= 2:
        return "coding"
    if has_tools:
        return "tool_heavy"
    if coding_signals >= CODING_SIGNAL_THRESHOLD:
        return "coding"
    if body_size > LARGE_BODY_BYTES or len(full_text) > LARGE_TEXT_CHARS:
        return "reasoning"
    return "chat"


if __name__ == "__main__":
    # Self-test assertions
    assert classify_outcome(200, "end_turn", 0, 0, 0) == "empty"
    assert classify_outcome(200, "end_turn", 2, 0, 100) == "ok"
    assert classify_outcome(200, "max_tokens", 0, 0, 8000) == "empty"
    assert classify_outcome(200, "max_tokens", 0, 1, 100) == "tool_only"
    assert classify_outcome(200, "max_tokens", 2, 0, 8000) == "truncated"
    assert classify_outcome(500, "", 0, 0, 0) == "error"
    assert classify_task({"messages":[{"role":"user","content":"fix the bug in def foo()"}], "tools":[{}]}) == "coding"
    assert classify_task({"messages":[{"role":"user","content":[{"type":"image","source":{}}]}]}) == "vision"
    assert classify_task({"messages":[{"role":"user","content":"ciao"}]}) == "chat"
    print("OK")
