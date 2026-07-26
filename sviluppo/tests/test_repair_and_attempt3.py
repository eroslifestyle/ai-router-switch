import sys
import json
import os

# Inserisce il percorso della directory src per consentire gli import relativi
sys.path.insert(0, "src")

from pipeline_anthropic import _repair_message_sequence
from context_rewrite import rewrite_for_context
from token_counter import estimate_tokens_body
from model_context_map import get_safe_input_limit

# ---------------------------------------------------------------------------
# Gruppo A – Test per _repair_message_sequence
# ---------------------------------------------------------------------------

def test_repair_message_sequence_basic():
    """
    Verifica che una sequenza di messaggi corrotta (contiene un blocco
    tool_result in un messaggio user) venga riparata correttamente.
    Il primo messaggio deve diventare un normale user con contenuto 'hello'.
    """
    broken = [
        {"role": "user", "content": [{"type": "tool_result", "id": "t1", "content": "res1"}]},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "think"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "foo"}]},
    ]
    fixed = _repair_message_sequence(broken)
    # Verifica che il primo messaggio sia stato riparato
    assert fixed[0]["role"] == "user" and fixed[0]["content"] == "hello", \
        "Il primo messaggio non è stato riparato correttamente"
    # Restano 2 messaggi: il tool_result orfano in testa e il tool_use orfano
    # in coda vengono rimossi, sopravvivono "hello" (user) e "think" (assistant).
    assert len(fixed) == 2, f"Numero di messaggi inatteso: {len(fixed)}"
    print("  test_repair_message_sequence_basic: OK")

def test_repair_message_sequence_empty():
    """
    Verifica che una lista vuota non generi eccezioni e restituisca una lista vuota.
    """
    result = _repair_message_sequence([])
    assert isinstance(result, list) and len(result) == 0, \
        "La lista vuota non è stata restituita correttamente"
    print("  test_repair_message_sequence_empty: OK")

def test_repair_message_sequence_valid_unchanged():
    """
    Verifica che una sequenza già valida non venga alterata.
    """
    # NB: nell'Anthropic Messages API il system e' un campo top-level del body,
    # NON un messaggio: qui la lista contiene solo user/assistant.
    valid = [
        {"role": "user", "content": "Ciao"},
        {"role": "assistant", "content": "Ciao! Come posso aiutarti?"},
    ]
    fixed = _repair_message_sequence(valid)
    # Il numero di messaggi non deve cambiare
    assert len(fixed) == len(valid), "La sequenza valida è stata alterata"
    # I contenuti devono rimanere identici
    assert fixed[0] == valid[0] and fixed[1] == valid[1], \
        "I contenuti dei messaggi sono cambiati"

    # Un eventuale role=system nella lista viene scartato (non e' un messaggio)
    con_system = [{"role": "system", "content": "Sei un assistente."}] + valid
    assert len(_repair_message_sequence(con_system)) == len(valid), \
        "Il messaggio con role=system non e' stato scartato"
    print("  test_repair_message_sequence_valid_unchanged: OK")

# ---------------------------------------------------------------------------
# Gruppo B – Test per ATTEMPT 3 (troncamento hard) di context_rewrite
# ---------------------------------------------------------------------------

def _make_body(model: str, messages: list) -> bytes:
    """
    Helper per creare un body JSON in bytes secondo il formato
    Anthropic Messages API.
    """
    body_dict = {"model": model, "messages": messages}
    return json.dumps(body_dict, ensure_ascii=False).encode("utf-8")

def test_attempt3_messaggio_singolo_gigante():
    """
    Verifica che un singolo messaggio utente più grande del limite di contesto
    venga troncato e che il body risultante rispetti i vincoli di token e lunghezza.
    """
    model = "MiniMax-M2.7"
    # Crea un messaggio user con 1.200.000 caratteri 'x'
    huge_text = "x" * 1_200_000
    original_body = _make_body(model, [{"role": "user", "content": huge_text}])

    rewritten_body, changed = rewrite_for_context(original_body, model, "test_fp")

    # Il troncamento deve essere applicato
    assert changed is True, "Il troncamento non è stato applicato (changed == False)"

    # Verifica che il numero di token stimato non superi il limite
    new_tokens = estimate_tokens_body(rewritten_body)
    limit = get_safe_input_limit(model)
    assert new_tokens <= limit, \
        f"Token stimati {new_tokens} superano il limite {limit}"

    # La dimensione in byte del body troncato deve essere inferiore
    assert len(rewritten_body) < len(original_body), \
        "La dimensione del body non è stata ridotta"

    print("  test_attempt3_messaggio_singolo_gigante: OK")

def test_attempt3_preserva_ultimo_messaggio():
    """
    Verifica che, in presenza di un primo messaggio enorme e di un secondo
    messaggio piccolo riconoscibile, il troncamento preservi l'ultimo messaggio
    (la richiesta corrente) e non lo elimini.
    """
    model = "MiniMax-M2.7"
    # Primo messaggio enorme (1.500.000 caratteri)
    huge_text = "a" * 1_500_000
    # Ultimo messaggio con marker riconoscibile
    marker = "DOMANDA_CORRENTE_"
    # Costruisce una stringa di ~3000 caratteri basata sul marker
    repeated = (marker * (3000 // len(marker) + 1))[:3000]

    original_body = _make_body(model, [
        {"role": "user", "content": huge_text},
        {"role": "user", "content": repeated}
    ])

    rewritten_body, changed = rewrite_for_context(original_body, model, "test_fp")

    # Il troncamento deve essere applicato
    assert changed is True, "Il troncamento non è stato applicato"

    # Il marker deve essere ancora presente nel body risultante
    rewritten_str = rewritten_body.decode("utf-8", errors="replace")
    assert marker in rewritten_str, \
        "Il marker 'DOMANDA_CORRENTE_' non è stato preservato nel body risultante"

    print("  test_attempt3_preserva_ultimo_messaggio: OK")

def test_attempt3_non_tocca_tool_use():
    """
    Verifica che, quando il troncamento coinvolge un messaggio assistant
    contenente un blocco tool_use, id e name del blocco restino invariati.
    I blocchi tool_use non devono essere alterati per non compromettere
    l'accoppiamento con i relativi tool_result.
    """
    model = "MiniMax-M2.7"
    # Primo messaggio user enorme
    huge_text = "b" * 1_200_000
    # Ultimo messaggio assistant con un blocco tool_use
    tool_use_block = {
        "type": "tool_use",
        "id": "t9",
        "name": "Bash",
        "input": {"command": "ls"}
    }

    original_body = _make_body(model, [
        {"role": "user", "content": huge_text},
        {"role": "assistant", "content": [tool_use_block]}
    ])

    rewritten_body, changed = rewrite_for_context(original_body, model, "test_fp")

    # Il troncamento deve essere applicato
    assert changed is True, "Il troncamento non è stato applicato"

    # Analizza il body risultante per cercare eventuali blocchi tool_use
    rewritten_dict = json.loads(rewritten_body.decode("utf-8"))
    for msg in rewritten_dict.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    # id e name devono rimanere uguali
                    assert block.get("id") == "t9", \
                        f"ID del tool_use modificato: {block.get('id')}"
                    assert block.get("name") == "Bash", \
                        f"Name del tool_use modificato: {block.get('name')}"

    print("  test_attempt3_non_tocca_tool_use: OK")

def test_marcatore_troncamento():
    """
    Verifica che, quando il troncamento hard viene eseguito,
    il body contenga il marcatore testuale '[troncato per limiti di contesto]'.
    """
    model = "MiniMax-M2.7"
    # Body sicuramente sopra il limite
    huge_text = "c" * 2_000_000
    original_body = _make_body(model, [{"role": "user", "content": huge_text}])

    rewritten_body, changed = rewrite_for_context(original_body, model, "test_fp")

    # Deve essere stato applicato un troncamento
    assert changed is True, "Il troncamento non è stato applicato"

    rewritten_str = rewritten_body.decode("utf-8", errors="replace")
    marker = "[troncato per limiti di contesto]"
    assert marker in rewritten_str, \
        f"Marcatore di troncamento '{marker}' non trovato nel body risultante"

    print("  test_marcatore_troncamento: OK")

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    # Gruppo A
    print("Gruppo A – _repair_message_sequence")
    test_repair_message_sequence_basic()
    test_repair_message_sequence_empty()
    test_repair_message_sequence_valid_unchanged()

    # Gruppo B
    print("Gruppo B – ATTEMPT 3 (troncamento hard)")
    test_attempt3_messaggio_singolo_gigante()
    test_attempt3_preserva_ultimo_messaggio()
    test_attempt3_non_tocca_tool_use()
    test_marcatore_troncamento()

    print("=" * 60)
    print("TUTTI I TEST PASSATI")

if __name__ == "__main__":
    main()
