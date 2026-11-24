import sys
import json

# Inserisce il percorso della directory src per consentire gli import relativi
sys.path.insert(0, "src")

from pipeline_anthropic import _repair_message_sequence
from router_utils import MESSAGGIO_CRONOLOGIA_COMPRESSA, repair_tail
from context_rewrite import rewrite_for_context, _fit_keep, _tail
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


def test_repair_coda_lunga_e_lineare():
    """
    Coda di 2.000 coppie assistant(tool_use)/user(tool_result) che parte da un
    tool_result orfano: la riparazione deve restare lineare (sotto 0.5 s).
    """
    import time
    msgs = [{"role": "user", "content": "testo iniziale"}]
    for i in range(2000):
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "foo"}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]})
    start = time.perf_counter()
    fixed = _repair_message_sequence(msgs[2:])
    elapsed = time.perf_counter() - start
    assert isinstance(fixed, list), "Il risultato non e' una lista"
    assert elapsed < 0.5, f"Riparazione troppo lenta: {elapsed:.3f} s"
    print("  test_repair_coda_lunga_e_lineare: OK")


def test_repair_orfano_in_testa_scarta_la_coppia_dopo():
    """
    Il tool_result di 'b' e' orfano perche' il suo assistant(tool_use 'b') e'
    caduto in testa: il blocco text 'ciao' deve sopravvivere, seguito da 'ok'.
    """
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "res"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "b", "name": "foo"}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "res"},
            {"type": "text", "text": "ciao"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]
    fixed = _repair_message_sequence(messages)
    assert fixed[0]["role"] == "user", f"Primo ruolo inatteso: {fixed[0]['role']}"
    assert fixed[0]["content"] == [{"type": "text", "text": "ciao"}], \
        f"Contenuto inatteso: {fixed[0]['content']}"
    assert fixed[1]["role"] == "assistant" and fixed[1]["content"] == [
        {"type": "text", "text": "ok"}], f"Secondo messaggio inatteso: {fixed[1]}"
    print("  test_repair_orfano_in_testa_scarta_la_coppia_dopo: OK")


# ---------------------------------------------------------------------------
# Gruppo C – Test per repair_tail (coda tagliata a metà)
# ---------------------------------------------------------------------------

def _coda_subagent(n_terne: int = 30) -> list:
    """Chat a forma di subagent: un solo utente di testo, poi terne system/tool_use/tool_result."""
    msgs = [{"role": "user", "content": "incarico iniziale"}]
    for i in range(n_terne):
        msgs.append({"role": "system", "content": f"sistema {i}"})
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"u{i}", "name": "bash", "input": {}}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"u{i}",
             "content": f"risultato-{i}"}]})
    return msgs


def test_repair_tail_coda_subagent_mantiene_ultime_terne():
    """
    Coda di 9 messaggi (3 terne) da chat subagent: repair_tail deve partire dal
    sintetico, mantenere gli ultimi tool_result e non lasciare tool_result orfani.
    """
    fixed = repair_tail(_coda_subagent()[-9:])
    assert fixed[0]["role"] == "user", f"Primo ruolo inatteso: {fixed[0]['role']}"
    assert fixed[0]["content"] == MESSAGGIO_CRONOLOGIA_COMPRESSA, "Manca il sintetico"
    dump = json.dumps(fixed, ensure_ascii=False)
    assert "risultato-29" in dump, "Ultimo tool_result perso"
    assert "risultato-27" in dump, "Terna completa precedente persa"
    assert "risultato-26" not in dump, "Entra una terna fuori dalla coda"
    tool_use_visti = set()
    for msg in fixed:
        for blocco in msg["content"] if isinstance(msg["content"], list) else []:
            if blocco.get("type") == "tool_use":
                tool_use_visti.add(blocco["id"])
            if blocco.get("type") == "tool_result":
                assert blocco["tool_use_id"] in tool_use_visti, \
                    f"tool_result orfano: {blocco['tool_use_id']}"
    print("  test_repair_tail_coda_subagent_mantiene_ultime_terne: OK")


def test_repair_tail_scarta_user_solo_tool_result_orfano():
    """
    Coda che inizia con uno user di soli tool_result orfani: quel blocco sparisce,
    il resto resta e il primo messaggio e' il sintetico.
    """
    coda = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "orf", "content": "orfano"}]},
        {"role": "system", "content": "sistema"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "risultato-1"}]},
    ]
    fixed = repair_tail(coda)
    assert fixed[0]["content"] == MESSAGGIO_CRONOLOGIA_COMPRESSA, "Manca il sintetico"
    assert "orfano" not in json.dumps(fixed, ensure_ascii=False), "Il tool_result orfano e' rimasto"
    assert fixed[1] == coda[2], f"Coppia tool_use non tenuta: {fixed[1]}"
    assert fixed[2] == coda[3], f"tool_result di t1 non tenuto: {fixed[2]}"
    assert len(fixed) == 3, f"Messaggi inattesi: {len(fixed)}"
    print("  test_repair_tail_scarta_user_solo_tool_result_orfano: OK")


def test_repair_tail_chat_normale_identica_a_repair():
    """
    Chat normale: la coda comincia con assistant di solo testo e poi user di testo.
    Nessun ancoraggio a tool_use: risultato IDENTICO a _repair_message_sequence.
    """
    coda = [
        {"role": "assistant", "content": [{"type": "text", "text": "testo"}]},
        {"role": "user", "content": "domanda"},
        {"role": "assistant", "content": [{"type": "text", "text": "risposta"}]},
    ]
    assert repair_tail(coda) == _repair_message_sequence(coda), "Diverso da _repair_message_sequence"
    assert MESSAGGIO_CRONOLOGIA_COMPRESSA not in json.dumps(repair_tail(coda), ensure_ascii=False)
    print("  test_repair_tail_chat_normale_identica_a_repair: OK")


def test_repair_tail_ancora_tool_use_con_user_testo_in_coda():
    """
    Coda [tool_use t1, tool_result t1, user di testo]: sintetico in testa e il
    tool_result di t1 deve essere presente.
    """
    coda = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "risultato-1"}]},
        {"role": "user", "content": "testo"},
    ]
    fixed = repair_tail(coda)
    assert fixed[0]["content"] == MESSAGGIO_CRONOLOGIA_COMPRESSA, "Manca il sintetico"
    assert "risultato-1" in json.dumps(fixed, ensure_ascii=False), "tool_result di t1 perso"
    print("  test_repair_tail_ancora_tool_use_con_user_testo_in_coda: OK")


# ---------------------------------------------------------------------------
# Gruppo D – Test per _fit_keep (ricerca sotto il primo scalino della griglia)
# ---------------------------------------------------------------------------

def test_fit_keep_ricerca_sotto_primo_scalino():
    """
    Chat normale lunga (2000 messaggi da ~1000 caratteri): con FIT_GRID_STEPS di
    default il primo scalino e' 31 e non entra. La ricerca binaria sotto lo scalino
    deve trovare un keep tra 1 e 31 che entra nel limite, non ricadere a 1.
    """
    modello = "claude-haiku-4-5"
    msgs = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"{i:05d} " + "x" * 1000} for i in range(2000)]

    def _candidato(coda: list) -> bytes:
        return json.dumps({"messages": coda}).encode()

    tokens_10 = estimate_tokens_body(_candidato(_tail(msgs, 10)), modello)
    tokens_31 = estimate_tokens_body(_candidato(_tail(msgs, 31)), modello)
    assert tokens_10 < tokens_31, "Le misure devono crescere con la coda"
    limite = (tokens_10 + tokens_31) // 2
    keep = _fit_keep(msgs, modello, limite, _candidato)
    assert 1 < keep < 31, f"keep inatteso: {keep}"
    assert estimate_tokens_body(_candidato(_tail(msgs, keep)), modello) <= limite
    print(f"  test_fit_keep_ricerca_sotto_primo_scalino: OK (keep={keep})")


def test_repair_tail_coda_con_user_testo_identica_a_repair():
    """
    Coda che inizia gia' con uno user di testo: nessun sintetico aggiunto,
    risultato identico a _repair_message_sequence.
    """
    coda = [
        {"role": "user", "content": "inizio"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "risultato-1"}]},
    ]
    assert repair_tail(coda) == _repair_message_sequence(coda), "Diverso da _repair_message_sequence"
    assert MESSAGGIO_CRONOLOGIA_COMPRESSA not in json.dumps(repair_tail(coda), ensure_ascii=False)
    print("  test_repair_tail_coda_con_user_testo_identica_a_repair: OK")


def test_repair_tail_lista_vuota():
    """Lista vuota → lista vuota."""
    assert repair_tail([]) == []
    print("  test_repair_tail_lista_vuota: OK")


def test_repair_message_sequence_coda_subagent_resta_uno():
    """
    Documenta che il comportamento di _repair_message_sequence e' INVARIATO:
    sulla stessa coda da subagent restituisce solo il messaggio sintetico.
    """
    fixed = _repair_message_sequence(_coda_subagent()[-9:])
    assert len(fixed) == 1, f"Attesi 1 messaggio, ottenuti {len(fixed)}"
    assert fixed[0]["content"] == MESSAGGIO_CRONOLOGIA_COMPRESSA
    print("  test_repair_message_sequence_coda_subagent_resta_uno: OK")
