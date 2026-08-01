import json
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, '.')
from cache_optimizer import count_breakpoints, ensure_tail_cache_breakpoint, describe


def _big(n=30000):
    return 'x' * n


def _body(d):
    return json.dumps(d).encode()


def _payload_coda_scoperta():
    """system con 2 breakpoint, 1 breakpoint su un messaggio VECCHIO, coda libera.

    E' il caso reale che azzerava il caching: bp=s2/m1/t0 con 226k token di coda
    riprocessati a ogni turno finche' il client chiudeva lo stream.
    """
    return {
        'model': 'claude-opus-5',
        'system': [
            {'type': 'text', 'text': _big(), 'cache_control': {'type': 'ephemeral'}},
            {'type': 'text', 'text': _big(), 'cache_control': {'type': 'ephemeral'}},
        ],
        'tools': [{'name': 'Bash', 'description': 'x',
                   'input_schema': {'type': 'object'}}],
        'messages': [
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'vecchio',
                 'cache_control': {'type': 'ephemeral'}}]},
            {'role': 'assistant', 'content': [{'type': 'text', 'text': _big()}]},
            {'role': 'user', 'content': [{'type': 'text', 'text': _big()}]},
        ],
    }


def test_aggiunge_breakpoint_su_coda_scoperta():
    data = _payload_coda_scoperta()
    assert count_breakpoints(data) == 3, f"atteso 3 breakpoint, trovati {count_breakpoints(data)}"
    out = ensure_tail_cache_breakpoint(_body(data))
    got = json.loads(out)
    ultimo = got['messages'][-1]['content'][-1]
    assert 'cache_control' in ultimo, f"coda non coperta: {ultimo}"
    assert count_breakpoints(got) == 4, f"attesi 4 breakpoint, trovati {count_breakpoints(got)}"


def test_non_supera_il_tetto_di_4():
    data = _payload_coda_scoperta()
    data['tools'][0]['cache_control'] = {'type': 'ephemeral'}
    data['messages'][-1]['content'][-1]['cache_control'] = {'type': 'ephemeral'}
    body = _body(data)
    assert count_breakpoints(data) >= 4, "il payload di test deve avere almeno 4 breakpoint"
    assert ensure_tail_cache_breakpoint(body) == body, \
        "con 4 breakpoint il body deve restare identico: oltre il tetto Anthropic risponde 400"


def test_body_piccolo_invariato():
    body = _body({'model': 'x', 'messages': [{'role': 'user', 'content': 'ciao'}]})
    assert ensure_tail_cache_breakpoint(body) == body, \
        "sotto la soglia minima il body deve restare identico"


def test_non_json_invariato():
    assert ensure_tail_cache_breakpoint(b'non-json') == b'non-json'


def test_coda_gia_coperta_invariata():
    data = {'model': 'x', 'messages': [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': _big(), 'cache_control': {'type': 'ephemeral'}}]}]}
    body = _body(data)
    assert ensure_tail_cache_breakpoint(body) == body, \
        "se la coda ha gia' un breakpoint il body non va toccato"


def test_content_stringa_convertito():
    testo = _big()
    body = _body({'model': 'x', 'messages': [{'role': 'user', 'content': testo}]})
    got = json.loads(ensure_tail_cache_breakpoint(body))
    content = got['messages'][-1]['content']
    assert isinstance(content, list), f"content non convertito in lista: {type(content)}"
    blocco = content[-1]
    assert blocco.get('type') == 'text', f"tipo errato: {blocco.get('type')}"
    assert blocco.get('text') == testo, "il testo originale deve essere preservato"
    assert 'cache_control' in blocco, "manca il cache_control sul blocco convertito"


def test_describe_riporta_conteggio_e_copertura():
    d = describe(_body(_payload_coda_scoperta()))
    assert 'tail_covered=False' in d, f"describe inatteso: {d}"
    assert describe(b'non-json') == 'bp=?'
