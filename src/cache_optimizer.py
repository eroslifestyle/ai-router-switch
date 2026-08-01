"""Ottimizzazione del prompt caching verso Anthropic.

Anthropic ammette al massimo MAX_CACHE_BREAKPOINTS blocchi `cache_control`. Quando il
client ne usa meno e lascia la coda della conversazione scoperta, ogni turno riprocessa
l'intero contesto: il costo cresce, lo stream rallenta e il client lo dichiara stallato.
Qui si aggiunge un solo breakpoint sull'ultimo blocco utile, senza mai superare il tetto.
"""

import json

MAX_CACHE_BREAKPOINTS = 4
MIN_INPUT_CHARS_FOR_BREAKPOINT = 20000


def count_breakpoints(data: dict) -> int:
    """Conta i blocchi ``cache_control`` presenti nel payload.

    Include:
    - blocchi ``cache_control`` nel campo ``system`` se è una lista;
    - ``cache_control`` a livello di messaggio e nei suoi blocchi di contenuto;
    - ``cache_control`` negli strumenti (``tools``) se è una lista.

    Parametri
    ----------
    data : dict
        Payload JSON già decodificato.

    Restituisce
    -------
    int
        Numero totale di occorrenze di ``cache_control``.
    """
    if not isinstance(data, dict):
        return 0

    total = 0

    # System (potrebbe essere una lista di blocchi)
    system = data.get('system')
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and 'cache_control' in block:
                total += 1

    # Messages
    messages = data.get('messages')
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if 'cache_control' in msg:
                total += 1
            content = msg.get('content')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and 'cache_control' in block:
                        total += 1

    # Tools
    tools = data.get('tools')
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and 'cache_control' in tool:
                total += 1

    return total


def _last_content_block(messages: list):
    """Individua l'ultimo blocco di contenuto dell'ultimo messaggio.

    Parametri
    ----------
    messages : list
        Lista di messaggi (già deserializzata).

    Restituisce
    -------
    tuple
        ``(msg, block)`` dove ``msg`` è il dizionario del messaggio e ``block``
        è l'ultimo elemento di contenuto che sia un dict, oppure ``(None, None)``
        se non applicabile. Se il contenuto è una stringa restituisce
        ``(msg, None)`` per indicare che il chiamante dovrà convertirlo.
    """
    if not isinstance(messages, list):
        return None, None

    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        content = msg.get('content')
        if content is None:
            continue
        if isinstance(content, str):
            return msg, None
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict):
                    return msg, block
            return None, None
    return None, None


def ensure_tail_cache_breakpoint(body: bytes) -> bytes:
    """Aggiunge un breakpoint ``cache_control`` all'ultimo blocco utile,
    se non è già presente e se il numero totale di breakpoint resta
    sotto ``MAX_CACHE_BREAKPOINTS``.

    La funzione è sicura: qualsiasi errore restituisce il ``body`` originale
    senza modifiche.

    Parametri
    ----------
    body : bytes
        Payload JSON in forma di byte.

    Restituisce
    -------
    bytes
        Payload JSON modificato (o originale in caso di errore).
    """
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            return body

        if len(body) < MIN_INPUT_CHARS_FOR_BREAKPOINT:
            return body

        if count_breakpoints(data) >= MAX_CACHE_BREAKPOINTS:
            return body

        msg, block = _last_content_block(data.get('messages'))
        if msg is None:
            return body

        if block is not None:
            if not isinstance(block, dict) or 'cache_control' in block:
                return body
            block['cache_control'] = {'type': 'ephemeral'}
        else:
            original_content = msg.get('content')
            if not isinstance(original_content, str):
                return body
            msg['content'] = [
                {
                    'type': 'text',
                    'text': original_content,
                    'cache_control': {'type': 'ephemeral'}
                }
            ]

        return json.dumps(data, ensure_ascii=False).encode('utf-8')
    except Exception:
        return body


def describe(body: bytes) -> str:
    """Restituisce una stringa diagnostica compatta che riporta
    il numero di breakpoint presenti, il tetto massimo e se l'ultimo
    blocco di contenuto è già coperto da un ``cache_control``.

    In caso di errore di parsing restituisce ``"bp=?"``.

    Parametri
    ----------
    body : bytes
        Payload JSON in forma di byte.

    Restituisce
    -------
    str
        Rappresentazione diagnostica.
    """
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            return "bp=?"

        count = count_breakpoints(data)

        msg, block = _last_content_block(data.get('messages'))
        tail_covered = isinstance(block, dict) and 'cache_control' in block

        return f"bp={count}/{MAX_CACHE_BREAKPOINTS} tail_covered={tail_covered}"
    except Exception:
        return "bp=?"
