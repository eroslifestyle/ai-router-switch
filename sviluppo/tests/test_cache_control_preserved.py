import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from context_rewrite import rewrite_for_context


def _body_grande(system_value, n_messaggi=200, chars=10000):
    """
    Costruisce un body JSON grande abbastanza da superare il safe limit.
    system_value: None, str, o list di blocchi
    n_messaggi: numero di messaggi alternati user/assistant
    chars: lunghezza in caratteri di ogni contenuto
    """
    body = {
        "model": "claude-haiku-4-5-20251001",
        "messages": []
    }
    
    if system_value is not None:
        body["system"] = system_value
    
    contenuto = "x" * chars
    for i in range(n_messaggi):
        if i % 2 == 0:
            body["messages"].append({"role": "user", "content": contenuto})
        else:
            body["messages"].append({"role": "assistant", "content": contenuto})
    
    return json.dumps(body).encode()


def test_system_lista_preserva_cache_control():
    """System come lista con cache_control: preserva la struttura dopo rewrite."""
    system_originale = [
        {"text": "Istruzioni base", "role": "system"},
        {"text": "Contesto cache", "cache_control": {"type": "ephemeral"}}
    ]
    
    body = _body_grande(system_originale)
    risultato, was_rewritten = rewrite_for_context(body, "claude-haiku-4-5-20251001", __file__)
    
    assert was_rewritten is True, "Doveva essere riscritto"
    
    parsed = json.loads(risultato)
    assert isinstance(parsed["system"], list), "Il system deve rimanere una lista"
    
    # Cerca un blocco con cache_control e verifica che il testo sia identico
    trovato = False
    for blocco in parsed["system"]:
        if "cache_control" in blocco:
            assert blocco["text"] == "Contesto cache", f"Testo non preservato: {blocco['text']}"
            assert "cache_control" in blocco and blocco["cache_control"]["type"] == "ephemeral"
            trovato = True
    
    assert trovato, "Nessun blocco con cache_control trovato nel system riscritto"


def test_system_lista_blocchi_originali_invariati():
    """I blocchi originali devono apparire identici e in ordine all'inizio della lista."""
    blocco1 = {"text": "Primo blocco", "role": "system"}
    blocco2 = {"text": "Secondo blocco con cache", "cache_control": {"type": "ephemeral"}}
    system_originale = [blocco1, blocco2]
    
    body = _body_grande(system_originale)
    risultato, was_rewritten = rewrite_for_context(body, "claude-haiku-4-5-20251001", __file__)
    
    assert was_rewritten is True, "Doveva essere riscritto"
    
    parsed = json.loads(risultato)
    system_riscritto = parsed["system"]
    
    # Verifica che i primi due blocchi siano identici e nell'ordine originale
    assert len(system_riscritto) >= 2, "Devono esserci almeno i due blocchi originali"
    assert system_riscritto[0] == blocco1, "Primo blocco non invariato"
    assert system_riscritto[1] == blocco2, "Secondo blocco non invariato"
    
    # Se c'è un blocco di summary, deve essere in coda
    if len(system_riscritto) > 2:
        ultimo = system_riscritto[-1]
        assert "summary" in str(ultimo).lower() or "cache_control" not in ultimo, \
            "Blocco summary non in coda"


def test_system_stringa_comportamento_invariato():
    """System come stringa: deve rimanere stringa e iniziare col testo originale."""
    system_originale = "Tu sei un assistente gentile e preciso."
    
    body = _body_grande(system_originale)
    risultato, was_rewritten = rewrite_for_context(body, "claude-haiku-4-5-20251001", __file__)
    
    assert was_rewritten is True, "Doveva essere riscritto"
    
    parsed = json.loads(risultato)
    assert isinstance(parsed["system"], str), "Il system deve rimanere una stringa"
    assert parsed["system"].startswith(system_originale), \
        f"System non inizia con testo originale: {parsed['system'][:50]}"


def test_system_assente_non_crasha():
    """Body senza system sopra il limite: nessuna eccezione, JSON valido."""
    body = _body_grande(None)
    risultato, was_rewritten = rewrite_for_context(body, "claude-haiku-4-5-20251001", __file__)
    
    # Non deve crashare. Nota: se il body viene riscritto, il summary finisce in
    # "system" come stringa — comportamento PREESISTENTE al fix cache-aware, non
    # una regressione: nel codice originale `system_content = summary` quando il
    # system era assente. Qui si verifica solo che l'output sia JSON valido e che
    # il system, se presente, sia una stringa (mai una lista inventata dal nulla).
    parsed = json.loads(risultato)
    assert "messages" in parsed
    if "system" in parsed:
        assert isinstance(parsed["system"], str), "Senza system originale, il summary va come stringa"


def test_nessun_rewrite_sotto_il_limite():
    """Body piccolo: nessun rewrite, system byte-identico."""
    system_originale = "Istruzioni semplici."
    
    body = {
        "model": "claude-haiku-4-5-20251001",
        "system": system_originale,
        "messages": [
            {"role": "user", "content": "Ciao"},
            {"role": "assistant", "content": "Ciao!"}
        ]
    }
    body_bytes = json.dumps(body).encode()
    
    risultato, was_rewritten = rewrite_for_context(body_bytes, "claude-haiku-4-5-20251001", __file__)
    
    assert was_rewritten is False, "Non doveva essere riscritto"
    assert risultato == body_bytes, "Il body deve essere byte-identico"
