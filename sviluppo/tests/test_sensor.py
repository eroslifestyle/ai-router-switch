import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from self_healing.sensor import classify_outcome, classify_task

def test_outcome_empty():
    assert classify_outcome(200, "end_turn", 0, 0, 0) == "empty"

def test_outcome_ok():
    assert classify_outcome(200, "end_turn", 2, 0, 100) == "ok"

def test_outcome_error_status():
    assert classify_outcome(502, "", 0, 0, 0) == "error"

def test_outcome_error_stop():
    assert classify_outcome(200, "error", 1, 0, 10) == "error"

def test_outcome_tool_only():
    assert classify_outcome(200, "end_turn", 0, 1, 50) == "tool_only"

def test_outcome_truncated():
    assert classify_outcome(200, "max_tokens", 2, 0, 8000) == "truncated"

def test_outcome_max_tokens_empty():
    assert classify_outcome(200, "max_tokens", 0, 0, 8000) == "empty"

def test_outcome_none_safe():
    assert classify_outcome(None, None, None, None, None) == "empty"

def test_task_coding():
    assert classify_task({"messages":[{"role":"user","content":"def foo(): fix the bug and implement refactor"}], "tools":[]}) == "coding"

def test_task_coding_no_tools():
    assert classify_task({"messages":[{"role":"user","content":"def foo class bar import baz refactor implement fix bug"}]}) == "coding"

def test_task_vision():
    assert classify_task({"messages":[{"role":"user","content":[{"type":"image","source":{}}]}]}) == "vision"

def test_task_chat():
    assert classify_task({"messages":[{"role":"user","content":"ciao"}]}) == "chat"

def test_task_bytes_body():
    assert classify_task(b'{"messages":[{"role":"user","content":"hello"}]}') == "chat"
def test_task_reasoning_large():
    large_content = "a " * 5000
    assert classify_task({"messages":[{"role":"user","content": large_content}]}) == "reasoning"

def test_outcome_thinking_only_stream_interrotto():
    # thinking_blocks>=1, niente text ne tool, stop_reason VUOTO: lo stream non ha
    # raggiunto il message_delta finale, quindi la misura e incompleta -> unknown.
    # Caso reale: entry glm-5.2 task_class vision con output_tokens 87770, che prima
    # del 2026-08-04 risultava "empty" e penalizzava il modello (empty e in FAIL_OUTCOMES).
    result = classify_outcome(200, "", 0, 0, 87770, False, 1)
    assert result == "unknown", f"atteso='unknown', ottenuto={result}"

def test_outcome_thinking_only_stream_concluso():
    # stesso quadro ma stop_reason valorizzato: il modello ha davvero concluso
    # producendo solo thinking -> fallimento reale, resta empty.
    result = classify_outcome(200, "end_turn", 0, 0, 500, False, 1)
    assert result == "empty", f"atteso='empty', ottenuto={result}"

def test_outcome_thinking_non_altera_i_casi_con_testo():
    # con almeno un blocco text la presenza di thinking non cambia nulla
    result1 = classify_outcome(200, "end_turn", 2, 0, 100, False, 3)
    assert result1 == "ok", f"atteso='ok', ottenuto={result1}"
    result2 = classify_outcome(200, "max_tokens", 2, 0, 8000, False, 1)
    assert result2 == "truncated", f"atteso='truncated', ottenuto={result2}"

def test_outcome_retrocompatibile_senza_thinking():
    # le chiamate posizionali a cinque argomenti, che esistevano prima del parametro
    # thinking_blocks, devono dare esattamente lo stesso risultato di prima
    result1 = classify_outcome(200, "end_turn", 0, 0, 0)
    assert result1 == "empty", f"atteso='empty', ottenuto={result1}"
    result2 = classify_outcome(200, "end_turn", 0, 1, 50)
    assert result2 == "tool_only", f"atteso='tool_only', ottenuto={result2}"
    result3 = classify_outcome(200, "max_tokens", 0, 0, 8000)
    assert result3 == "empty", f"atteso='empty', ottenuto={result3}"


def test_measure_partial_con_stop_reason_non_e_unknown():
    """Misura incompleta ma stream concluso: classifica per stop_reason.

    Il relay accumula solo i primi e gli ultimi 16KB: i content_block_start di
    una risposta lunga cadono nel buco e risultano zero blocchi. Se pero' il
    message_delta finale e' arrivato (stop_reason valorizzato), la generazione
    NON e' stata interrotta. Misurato il 2026-08-16: 575 delle 1.089 entry
    'unknown' erano cosi', una con 3.682 token di output prodotti."""
    assert classify_outcome(200, "tool_use", 0, 0, 3682, True, 1) == "tool_only"
    assert classify_outcome(200, "end_turn", 0, 0, 900, True, 1) == "ok"
    assert classify_outcome(200, "max_tokens", 0, 0, 4096, True, 0) == "truncated"
    # senza stop_reason resta una misura davvero incompleta
    assert classify_outcome(200, "", 0, 0, 120, True, 1) == "unknown"
