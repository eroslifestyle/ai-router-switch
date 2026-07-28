"""Context window per ogni modello supportato."""

MODEL_CONTEXT_MAP = {
    # Anthropic (SPEC: opus-4-8, sonnet-4-6/4-7/4-8, haiku-4-5)
    "opus": 1_000_000,          "claude-opus-4-5": 200_000,
    "claude-opus-4-8": 1_000_000,
    "sonnet": 200_000,
    # VERIFICATO 2026-07-28 dalla Models API (/v1/models, max_input_tokens):
    # l'id di catalogo claude-sonnet-4-5-20250929 riporta 1.000.000, non 200.000.
    # Il vecchio valore veniva dalla sezione SPEC non verificata e sottostimava
    # la finestra di 5 volte. Rilevato da sviluppo/tools/check_model_context.py.
    "claude-sonnet-4-5": 1_000_000,
    "sonnet-4-6": 1_000_000,  "claude-sonnet-4-6": 1_000_000,
    "sonnet-4-7": 1_000_000,  "claude-sonnet-4-7": 1_000_000,
    "sonnet-4-8": 1_000_000,  "claude-sonnet-4-8": 1_000_000,
    "haiku": 200_000,          "claude-haiku-4-5": 200_000,
    # Anthropic Gen 5 + legacy 4.6/4.7 — valori dalla DOC UFFICIALE, verificati
    # 2026-07-27 su platform.claude.com/docs/en/about-claude/models/overview
    # ("Latest models comparison") e .../build-with-claude/context-windows.
    # Per ogni modello con finestra da 1M, 1M e' il DEFAULT: nessun beta header.
    "claude-opus-5": 1_000_000,      # doc ufficiale; coerente col traffico reale (449.783 token con status 200)
    "claude-sonnet-5": 1_000_000,    # doc ufficiale
    "claude-fable-5": 1_000_000,     # doc ufficiale (tokenizer di Opus 4.7: ~30% token in piu' a parita' di testo)
    "claude-mythos-5": 1_000_000,    # doc ufficiale (stesse specifiche di Fable 5)
    "claude-mythos-preview": 1_000_000,   # doc ufficiale
    "claude-haiku-4-5-20251001": 200_000,  # doc ufficiale; confermato dall'errore upstream 400 "prompt is too long: 208904 tokens > 200000 maximum"
    # Legacy che MANCAVANO: senza queste voci cadevano sul default 200k pur
    # avendo 1M, e il gate le avrebbe riscritte a 160k senza motivo.
    "claude-opus-4-7": 1_000_000,    # doc ufficiale (tabella Legacy models)
    "claude-opus-4-6": 1_000_000,    # doc ufficiale (tabella Legacy models)
    "claude-opus-4-1": 200_000,      # doc ufficiale; deprecato, ritiro 2026-08-05
    # MiniMax — doc ufficiale platform.minimax.io/docs/guides/text-generation
    # (verificata 2026-07-27). ATTENZIONE: M3 ha 1M, non 200k: il valore
    # precedente lo sottostimava di 5 volte e il gate lo comprimeva a 160k,
    # buttando via contesto utile in modalita' minimax e mix-*m.
    "MiniMax-M3": 1_000_000,   # doc ufficiale: 1.000.000 (max output 512k)
    "MiniMax-M2.7": 204_800,   # doc ufficiale: 204.800
    "MiniMax-M2.5": 204_800,   # doc ufficiale: 204.800
    "MiniMax-M2": 204_800,     # doc ufficiale: 204.800
    "MiniMax-M3.5": 204_800,   # NON documentato (modello non ancora pubblico): valore prudente
    "MiniMax-Haiku": 204_800,  # NON documentato: valore prudente
    # GLM — doc ufficiale docs.z.ai/guides/llm/<modello> (verificata 2026-07-27)
    "glm-5.2": 1_000_000,     # doc ufficiale: 1M di default, nessuna variante [1m] separata (max output 128k)
    "glm-5-turbo": 200_000,   # doc ufficiale: 200K (max output 128k)
    "glm-4.7": 200_000,       # doc ufficiale: 200K — prima era 128_000, sottostima del 36%
    "glm-4.6v": 131_000,      # NON verificato sulla doc: valore storico
    "glm-4v": 131_000,        # NON verificato sulla doc: valore storico
    "glm-4": 128_000,         # modello vecchio, valore storico
    "glm-5V-Turbo": 200_000,  # allineato a glm-5-turbo
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

MIN_OUTPUT_HEADROOM = 8_192  # spazio minimo garantito all'output


def get_safe_input_limit(model: str, max_output: int | None = None) -> int:
    """Limite sicuro per l'input: context window meno lo spazio per l'output.

    Se `max_output` e' noto (il campo `max_tokens` della richiesta) si riserva
    esattamente quello, con un minimo di MIN_OUTPUT_HEADROOM. Altrimenti si
    ricade sul vecchio BUFFER_PERCENT fisso.

    Perche' la percentuale fissa non va bene (misurato 2026-07-28 sulle doc
    ufficiali): il 20%% e' scollegato dall'output reale del modello, e sbaglia
    in entrambe le direzioni.
      - glm-4.7 e glm-5-turbo: 200k di contesto, 128k di output massimo.
        Il buffer da 40k ne copre meno di un terzo: input + output potevano
        sforare la finestra.
      - claude-haiku-4-5: 200k / 64k output → buffer 40k, mancano 24k.
      - opus-5 / sonnet-5 / fable-5: 1M / 128k output → il buffer da 200k ne
        spreca 72k di contesto utile a ogni richiesta.
    Riservare il `max_tokens` effettivo risolve entrambi i lati: chi genera
    poco tiene piu' contesto, chi genera molto e' davvero protetto.

    Effetto collaterale voluto: la soglia di riscrittura si sposta piu' in
    alto (per una richiesta tipica con max_tokens=32k su un modello da 1M si
    passa dall'80%% al 96,8%%), e cosi' gli alert a 80%%/88%% tornano ad
    arrivare PRIMA della compressione, come il loro commento dichiara.
    """
    ctx = get_context_limit(model)
    if max_output is None:
        buf = int(ctx * BUFFER_PERCENT / 100)
    else:
        try:
            buf = max(int(max_output), MIN_OUTPUT_HEADROOM)
        except (TypeError, ValueError):
            buf = int(ctx * BUFFER_PERCENT / 100)
    return max(1, ctx - buf)

# Dimensione riassunto per modello
SUMMARY_BUDGET_MAP = {
    "opus": 15_000, "sonnet": 10_000, "haiku": 8_000,
    "MiniMax-M3": 10_000, "MiniMax-M2.7": 10_000,
    "glm-5-turbo": 15_000, "glm-4": 8_000,
}
def get_summary_budget(model: str) -> int:
    return SUMMARY_BUDGET_MAP.get(model.lower(), 10_000)
