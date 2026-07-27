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
    # Anthropic Gen 5
    "claude-opus-5": 1_000_000,      # VERIFICATO 2026-07-27 sul traffico reale, richiesta da 449783 token con status 200
    "claude-sonnet-5": 1_000_000,    # NON verificato, valore che preserva il comportamento precedente del gate (usava opus-4-8 a 1M per tutto il provider anthropic). Da confermare
    "claude-fable-5": 1_000_000,     # NON verificato, come sopra
    "claude-haiku-4-5-20251001": 200_000,  # VERIFICATO dall'errore upstream 400 "prompt is too long: 208904 tokens > 200000 maximum"
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
    """Restituisce il context window per un modello. Default 200K.

    Lookup case-insensitive: le chiavi della mappa contengono maiuscole
    ("MiniMax-M2.7", "glm-5V-Turbo") mentre il modello richiesto veniva
    normalizzato a lowercase — quelle voci non erano MAI trovate e cadevano
    sul default 200K. Finora innocuo (i valori coincidevano col default), ma
    sarebbe diventato un bug silenzioso al primo modello con context diverso.

    Match per prefisso: se nessun match esatto, cerca la chiave piu lunga
    nella mappa tale che il modello (normalizzato lowercase) inizi con quella
    chiave. Questo gestisce suffissi di data (es. claude-haiku-4-5-20251001)
    e varianti con suffissi numerici (es. claude-sonnet-5[1m]). Viene scelta
    la chiave piu lunga per massimizzare la precisione del match.

    AVVISO: un modello Anthropic nuovo e non mappato cade su 200000 e verra
    riscritto a 160000 dal gate di contesto. Quando entra in uso un nuovo
    modello, aggiungere qui la voce con il context corretto.
    """
    key = model.lower()
    if key in MODEL_CONTEXT_MAP:
        return MODEL_CONTEXT_MAP[key]
    for name, limit in MODEL_CONTEXT_MAP.items():
        if name.lower() == key:
            return limit
    # Match per prefisso: scegli la chiave piu lunga che e' prefisso di key
    best_match = None
    best_len = 0
    for name in MODEL_CONTEXT_MAP.keys():
        name_lower = name.lower()
        if key.startswith(name_lower) and len(name_lower) > best_len:
            best_match = MODEL_CONTEXT_MAP[name]
            best_len = len(name_lower)
    if best_match is not None:
        return best_match
    return 200_000

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
