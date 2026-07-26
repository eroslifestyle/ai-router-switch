import sys, json; sys.path.insert(0, 'src'); from anthropic_body import sanitize_server_tool_ids, SRVTOOLU_ID_PATTERN

# Test 1: id non conforme viene corretto e riferimento associato aggiornato
def test_id_non_conforme_viene_corretto():
    # Corpo con server_tool_use avente id non valido e web_search_tool_result
    # che lo referenzia nel messaggio successivo.
    body = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "server_tool_use", "id": "call_abc-123", "name": "some_tool"}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "web_search_tool_result", "tool_use_id": "call_abc-123", "raw_content": "some result"}
                ]
            }
        ]
    }
    body_bytes = json.dumps(body).encode()
    new_body_bytes, n = sanitize_server_tool_ids(body_bytes)
    # Devono essere stati corretti esattamente 1 id
    assert n == 1, f"Atteso n=1, ottenuto {n}"
    new_body_json = json.loads(new_body_bytes)
    # Il nuovo id deve rispettare il pattern
    sid = new_body_json["messages"][0]["content"][0]["id"]
    assert SRVTOOLU_ID_PATTERN.fullmatch(sid), f"Id {sid} non rispetta il pattern"
    # Il tool_use_id del risultato deve essere stato aggiornato allo stesso nuovo id
    tool_id = new_body_json["messages"][1]["content"][0]["tool_use_id"]
    assert tool_id == sid, f"tool_use_id {tool_id} diverso dal server_tool_use id {sid}"

# Test 2: riferimento precede l'uso (richiede seconda passata sui riferimenti)
def test_riferimento_precedente_allo_uso():
    # Corpo dove il web_search_tool_result appare prima del server_tool_use.
    # La funzione deve comunque accoppiare correttamente id e tool_use_id.
    body = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "web_search_tool_result", "tool_use_id": "call_abc-123", "raw_content": "some result"}
                ]
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "server_tool_use", "id": "call_abc-123", "name": "some_tool"}
                ]
            }
        ]
    }
    body_bytes = json.dumps(body).encode()
    new_body_bytes, n = sanitize_server_tool_ids(body_bytes)
    assert n == 1, f"Atteso n=1, ottenuto {n}"
    new_body_json = json.loads(new_body_bytes)
    sid = new_body_json["messages"][1]["content"][0]["id"]
    assert SRVTOOLU_ID_PATTERN.fullmatch(sid), f"Id {sid} non rispetta il pattern"
    tool_id = new_body_json["messages"][0]["content"][0]["tool_use_id"]
    assert tool_id == sid, f"tool_use_id {tool_id} != server_tool_use id {sid}"

# Test 3: id già conforme non viene toccato
def test_id_gia_conforme_non_toccato():
    # Corpo con server_tool_use id già nel formato corretto.
    body = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "server_tool_use", "id": "srvtoolu_01ABCdef", "name": "some_tool"}
                ]
            }
        ]
    }
    body_bytes = json.dumps(body).encode()
    new_body_bytes, n = sanitize_server_tool_ids(body_bytes)
    assert n == 0, f"Atteso n=0, ottenuto {n}"
    # Il body restituito deve essere identico all'originale
    assert new_body_bytes == body_bytes, "Il body non deve essere modificato"

# Test 4: percorso veloce senza server_tool_use
def test_fast_path_senza_server_tool_use():
    # Corpo senza alcun blocco server_tool_use.
    body = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Ciao"}
        ]
    }
    body_bytes = json.dumps(body).encode()
    new_body_bytes, n = sanitize_server_tool_ids(body_bytes)
    assert n == 0, f"Atteso n=0, ottenuto {n}"
    assert new_body_bytes == body_bytes, "Il body non deve essere modificato"

# Test 5: determinismo dell'output
def test_deterministico():
    # Due chiamate con lo stesso body devono produrre lo stesso output byte per byte.
    body = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "server_tool_use", "id": "call_abc-123", "name": "some_tool"}
                ]
            }
        ]
    }
    body_bytes = json.dumps(body).encode()
    new_body_bytes1, n1 = sanitize_server_tool_ids(body_bytes)
    new_body_bytes2, n2 = sanitize_server_tool_ids(body_bytes)
    assert new_body_bytes1 == new_body_bytes2, "L'output deve essere deterministicamente identico"
    assert n1 == n2, "Il conteggio delle correzioni deve coincidere"

# Test 6: body non JSON non solleva eccezioni
def test_body_non_json_non_solleva():
    # Body non valido JSON che contiene comunque la stringa "server_tool_use".
    body_bytes = b'{non-json "server_tool_use": "call_abc-123"}'
    try:
        new_body_bytes, n = sanitize_server_tool_ids(body_bytes)
    except Exception as e:
        raise AssertionError(f"La funzione non deve sollevare eccezioni, invece ha generato: {e}")
    assert n == 0, f"Atteso n=0, ottenuto {n}"
    assert new_body_bytes == body_bytes, "Il body non deve essere modificato"

def main():
    tests = [
        test_id_non_conforme_viene_corretto,
        test_riferimento_precedente_allo_uso,
        test_id_gia_conforme_non_toccato,
        test_fast_path_senza_server_tool_use,
        test_deterministico,
        test_body_non_json_non_solleva,
    ]
    print('='*60)
    for t in tests:
        t()
        print(f'  {t.__name__}: OK')
    print('='*60)
    print('TUTTI I TEST PASSATI')

if __name__ == '__main__':
    main()
