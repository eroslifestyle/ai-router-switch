"""La finestra utile in locale deve essere quella vera, e va riempita.

Tre difetti misurati il 2026-08-19, uno per gruppo di test:
1. i modelli locali non erano in MODEL_CONTEXT_MAP e cadevano sul default 200.000,
   mentre coding-fast ne regge 32.768: il gate non scattava e Ollama troncava in
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


def _corpo_lungo(model: str, coppie: int = 0, max_tokens: int = MAX_TOKENS) -> bytes:
    """Corpo che supera di sicuro la finestra di `model`.

    Il numero di coppie si ricava dal limite del modello invece di essere fisso:
    con un valore scritto a mano il test invecchia in silenzio alla prima finestra
    piu' larga (successo il 2026-08-19, quando code-max e' passato a 262.144 e le
    300 coppie di allora ci stavano comode dentro).
    """
    if not coppie:
        # ~170 token a coppia; il doppio del limite garantisce che lo shrink scatti.
        coppie = max(300, (get_context_limit(model) * 2) // 170)
    msgs = []
    for i in range(coppie):
        msgs.append({"role": "user", "content": f"passo {i}: " + "richiesta di lavoro " * 60})
        msgs.append({"role": "assistant", "content": f"esito {i}: " + "analisi e conclusioni " * 60})
    return json.dumps({
        "model": model, "max_tokens": max_tokens,
        "system": "prompt di sistema " * 500, "messages": msgs,
    }).encode()


# ── 1. le finestre locali sono quelle vere ──────────────────────────────────

def test_i_modelli_locali_non_cadono_piu_sul_default():
    # Valori dal num_ctx dei Modelfile Ollama e dal -c delle unit llama.cpp.
    attesi = {
        "code-max": 262_144, "coding-fast": 32_768,
        "fast-max": 32_768, "cyber-max": 32_768, "coding-light": 16_384,
        "coder-abliterated": 131_072, "chat-max": 131_072,
    }
    for model, limite in attesi.items():
        assert get_context_limit(model) == limite, model
        assert get_context_limit(model) != DEFAULT_NON_MAPPATO or limite == DEFAULT_NON_MAPPATO


def test_un_modello_stretto_non_promette_piu_di_quanto_regge():
    """Il caso che causava il troncamento silenzioso su ogni modello Ollama."""
    assert get_context_limit("coding-fast") < DEFAULT_NON_MAPPATO
    assert get_safe_input_limit("coding-fast", MAX_TOKENS) < get_context_limit("coding-fast")


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
    """Stesso corpo, due modelli: la coda deve scalare con la finestra, non essere fissa.

    max_tokens basso di proposito: con 32.000 riservati all'output, su una finestra
    da 32.768 non resterebbe spazio per l'input e il confronto non direbbe nulla.
    """
    # Corpo dimensionato sul modello STRETTO: tarato sul largo diventerebbe cosi'
    # grande che al piccolo non resterebbe spazio nemmeno per la coda minima, e il
    # confronto misurerebbe il caso degenere invece della finestra scorrevole.
    body = _corpo_lungo("coding-fast", max_tokens=4_000)

    grandi = len(json.loads(rewrite_for_context(body, "code-max", "sid:g")[0])["messages"])
    piccoli = len(json.loads(rewrite_for_context(body, "coding-fast", "sid:p")[0])["messages"])
    assert grandi > piccoli > SHRINK_KEEP_TAIL, (grandi, piccoli)


def test_un_corpo_gia_dentro_il_limite_non_viene_toccato():
    model = "code-max"
    body = json.dumps({"model": model, "max_tokens": MAX_TOKENS,
                       "messages": [{"role": "user", "content": "ciao"}]}).encode()
    out, riscritto = rewrite_for_context(body, model, "sid:corto")
    assert not riscritto and out == body




# ── 3. sticky drop_count: il punto di taglio resta stabile tra turni ─────────

def test_drop_count_stabile_tra_turni():
    """Il drop_count non cambia se il body con l'aggiunta di pochi messaggi
    rientra ancora nel budget: il prefisso msgs[drop_count:] resta identico."""
    model = "MiniMax-M2.7"
    # Pulisci la cache sticky per isolation
    from context_rewrite import _STICKY_DROP_COUNT
    _STICKY_DROP_COUNT.clear()

    base_body = _corpo_lungo(model, coppie=100, max_tokens=MAX_TOKENS)
    fp = "sid:test-sticky-drop"

    out1, _ = rewrite_for_context(base_body, model, fp)
    msgs1 = json.loads(out1)["messages"]
    keep1 = len(msgs1)

    # Aggiungi 2 messaggi piccoli in coda
    dati2 = json.loads(base_body)
    dati2["messages"].append({"role": "user", "content": "un messaggio"})
    dati2["messages"].append({"role": "assistant", "content": "ok"})
    extended_body = json.dumps(dati2).encode()

    out2, _ = rewrite_for_context(extended_body, model, fp)
    msgs2 = json.loads(out2)["messages"]
    keep2 = len(msgs2)

    # drop_count implicito deve essere lo stesso: len(originali) - keep
    n_base = len(json.loads(base_body)["messages"])
    drop1 = n_base - keep1
    drop2 = (n_base + 2) - keep2
    assert drop1 == drop2, f"drop_count cambiato: {drop1} -> {drop2}"

    # I messaggi comuni devono essere identici (prefisso stabile)
    assert msgs1 == msgs2[:keep1], "il prefisso dei messaggi non e' stabile"

if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
