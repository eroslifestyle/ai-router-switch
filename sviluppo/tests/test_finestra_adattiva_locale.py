"""La finestra utile in locale deve essere quella vera, e va riempita.

Tre difetti misurati il 2026-08-19, uno per gruppo di test:
1. i modelli locali non erano in MODEL_CONTEXT_MAP e cadevano sul default 200.000,
   mentre code-fast ne regge 65.536: il gate non scattava e Ollama troncava in
   silenzio (saturazione immediata in mode local/mix-al);
2. lo shrink teneva SHRINK_KEEP_TAIL=6 messaggi a prescindere dallo spazio libero,
   consegnando 179 KB dove ne erano ammessi ~390 su code-max.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from context_rewrite import SHRINK_KEEP_TAIL, rewrite_for_context  # noqa: E402
from model_context_map import get_context_limit, get_safe_input_limit  # noqa: E402
from token_counter import estimate_tokens_body  # noqa: E402

MAX_TOKENS = 32_000
DEFAULT_NON_MAPPATO = 200_000


def _corpo_lungo(model: str, coppie: int = 300) -> bytes:
    msgs = []
    for i in range(coppie):
        msgs.append({"role": "user", "content": f"passo {i}: " + "richiesta di lavoro " * 60})
        msgs.append({"role": "assistant", "content": f"esito {i}: " + "analisi e conclusioni " * 60})
    return json.dumps({
        "model": model, "max_tokens": MAX_TOKENS,
        "system": "prompt di sistema " * 500, "messages": msgs,
    }).encode()


# ── 1. le finestre locali sono quelle vere ──────────────────────────────────

def test_i_modelli_locali_non_cadono_piu_sul_default():
    # Valori dal num_ctx dei Modelfile Ollama e dal -c delle unit llama.cpp.
    attesi = {
        "code-max": 131_072, "code-fast": 65_536, "coding-fast": 32_768,
        "fast-max": 32_768, "cyber-max": 32_768, "coding-light": 16_384,
        "coder-abliterated": 131_072, "chat-max": 131_072,
    }
    for model, limite in attesi.items():
        assert get_context_limit(model) == limite, model
        assert get_context_limit(model) != DEFAULT_NON_MAPPATO or limite == DEFAULT_NON_MAPPATO


def test_code_fast_non_promette_piu_di_quanto_regge():
    """Il caso che causava il troncamento silenzioso: ACT di local e mix-al."""
    assert get_context_limit("code-fast") < DEFAULT_NON_MAPPATO
    assert get_safe_input_limit("code-fast", MAX_TOKENS) < get_context_limit("code-fast")


# ── 2. la finestra si riempie invece di sprecarsi ───────────────────────────

def test_la_coda_si_adatta_allo_spazio_disponibile():
    model = "code-max"
    body = _corpo_lungo(model)
    safe = get_safe_input_limit(model, MAX_TOKENS)

    out, riscritto = rewrite_for_context(body, model, "sid:test-adattiva")

    assert riscritto, "un corpo oltre il limite va riscritto"
    tenuti = len(json.loads(out)["messages"])
    assert tenuti > SHRINK_KEEP_TAIL * 5, f"attesi molti piu' di {SHRINK_KEEP_TAIL} messaggi, tenuti {tenuti}"
    assert estimate_tokens_body(out, model) <= safe, "e comunque entro il limite sicuro"


def test_la_finestra_e_sfruttata_quasi_tutta():
    model = "code-max"
    safe = get_safe_input_limit(model, MAX_TOKENS)
    out, _ = rewrite_for_context(_corpo_lungo(model), model, "sid:test-riempimento")
    assert estimate_tokens_body(out, model) >= safe * 0.8, "meno dell'80% e' spreco di finestra"


def test_una_finestra_piu_stretta_tiene_meno_messaggi():
    """Stesso corpo, due modelli: la coda deve scalare con la finestra, non essere fissa."""
    body = _corpo_lungo("code-max")
    grandi = len(json.loads(rewrite_for_context(body, "code-max", "sid:g")[0])["messages"])
    piccoli = len(json.loads(rewrite_for_context(body, "code-fast", "sid:p")[0])["messages"])
    assert grandi > piccoli > SHRINK_KEEP_TAIL, (grandi, piccoli)


def test_un_corpo_gia_dentro_il_limite_non_viene_toccato():
    model = "code-max"
    body = json.dumps({"model": model, "max_tokens": MAX_TOKENS,
                       "messages": [{"role": "user", "content": "ciao"}]}).encode()
    out, riscritto = rewrite_for_context(body, model, "sid:corto")
    assert not riscritto and out == body


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
