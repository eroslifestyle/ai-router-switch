"""Sanitizzazione parametri OpenRouter-specifici prima di LiteLLM."""
import json


def sanitize_for_openrouter(body: bytes) -> bytes:
    """Rimuove parametri non supportati da OpenRouter prima di LiteLLM.

    OpenRouter (tramite LiteLLM) rifiuta parametri Anthropic-specifici come:
    - reasoning_effort
    - Altri parametri che OpenRouter/OpenAI non supportano

    Questa funzione viene chiamata solo quando il target è openrouter/ox-alpha.
    """
    try:
        data = json.loads(body)
        modified = False

        # Rimuovi reasoning_effort se presente
        if "reasoning_effort" in data:
            del data["reasoning_effort"]
            modified = True

        # Rimuovi altri parametri problematici per OpenRouter
        for key in ["beta", "thinking", "redacted_thinking"]:
            if key in data:
                del data[key]
                modified = True

        if modified:
            return json.dumps(data).encode()
        return body
    except (json.JSONDecodeError, TypeError):
        return body


def is_openrouter_target(model: str) -> bool:
    """Verifica se il target è un modello OpenRouter."""
    return model and (
        "ox-alpha" in model.lower() or
        "openrouter" in model.lower() or
        model.startswith("stealth/")
    )
