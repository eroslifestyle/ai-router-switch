"""Context window per ogni modello supportato."""

MODEL_CONTEXT_MAP = {
    # Anthropic (SPEC: opus-4-8, sonnet-4-6/4-7/4-8, haiku-4-5)
    "opus": 1_000_000,          "claude-opus-4-5": 200_000,
    "claude-opus-4-8": 1_000_000,
    "sonnet": 200_000,         "claude-sonnet-4-5": 200_000,
    "sonnet-4-6": 1_000_000,  "claude-sonnet-4-6": 1_000_000,
    "sonnet-4-7": 1_000_000,  "claude-sonnet-4-7": 1_000_000,
    "sonnet-4-8": 1_000_000,  "claude-sonnet-4-8": 1_000_000,
    "haiku": 200_000,          "claude-haiku-4-5": 200_000,
    # MiniMax (SPEC: M2.5)
    "MiniMax-M2": 200_000,    "MiniMax-M2.5": 200_000,
    "MiniMax-M2.7": 200_000,  "MiniMax-M3": 200_000,
    "MiniMax-M3.5": 200_000,  "MiniMax-Haiku": 200_000,
    # GLM (SPEC: glm-4.6V, glm-5V-Turbo, glm-5.2)
    "glm-4.6v": 131_000,      "glm-4.7": 128_000,
    "glm-4v": 131_000,       "glm-4": 128_000,
    "glm-5-turbo": 200_000,
    "glm-5.2": 1_000_000,
    "glm-5V-Turbo": 200_000,
}

BUFFER_PERCENT = 20  # 20% libero per output

# Config env vars
import os
for model in list(MODEL_CONTEXT_MAP.keys()):
    env_key = f"AIROUTER_CONTEXT_{model.upper().replace('-', '_').replace('.', '_')}"
    val = os.getenv(env_key)
    if val:
        MODEL_CONTEXT_MAP[model] = int(val)

def get_context_limit(model: str) -> int:
    """Restituisce il context window per un modello. Default 200K."""
    return MODEL_CONTEXT_MAP.get(model.lower(), 200_000)

def get_safe_input_limit(model: str) -> int:
    """Restituisce il limite sicuro per input: context - buffer%."""
    ctx = get_context_limit(model)
    buf = int(ctx * BUFFER_PERCENT / 100)
    return ctx - buf

# Dimensione riassunto per modello
SUMMARY_BUDGET_MAP = {
    "opus": 15_000, "sonnet": 10_000, "haiku": 8_000,
    "MiniMax-M3": 10_000, "MiniMax-M2.7": 10_000,
    "glm-5-turbo": 15_000, "glm-4": 8_000,
}
def get_summary_budget(model: str) -> int:
    return SUMMARY_BUDGET_MAP.get(model.lower(), 10_000)
