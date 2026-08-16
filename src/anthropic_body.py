"""Modulo per la sanificazione dei body delle richieste API inviate ad Anthropic.

Anthropic rifiuta con HTTP 400 i body che contengono blocchi *server_tool_use* il cui campo ``id``
non rispetta il pattern ``'^srvtoolu_[a-zA-Z0-9_]+$'``. Questo si verifica in modalità *mix-am*,
dove l'esecutore MiniMax genera id propri che vengono inseriti nella history rimandata ad Anthropic.
Messaggio di errore osservato: ``messages.1.content.0.server_tool_use.id: String should match pattern '^srvtoolu_[a-zA-Z0-9_]+$'``.

La funzione ``sanitize_server_tool_ids`` corregge in modo deterministico gli id non conformi
e aggiorna tutti i riferimenti ``tool_use_id`` nei blocchi risultato, in modo che il body
possa essere ritrasmesso senza errori 400.
"""

import hashlib
import json
import re

SRVTOOLU_ID_PATTERN = re.compile(r'^srvtoolu_[a-zA-Z0-9_]+$')

def sanitize_server_tool_ids(body: bytes) -> tuple[bytes, int]:
    """
    Sanifica i body che potrebbero contenere id ``server_tool_use`` non conformi.

    :param body: corpo della richiesta in bytes (JSON)
    :return: tuple (body_sanificato, numero_id_corretti). Se nessuna correzione
             è necessaria viene restituito il body originale e 0.
    """
    # Fast path: se non c'è traccia di 'server_tool_use' non serve parsare il JSON
    if b'server_tool_use' not in body:
        return body, 0

    try:
        data = json.loads(body)
    except Exception:
        # JSON non valido o altri errori di parsing: non possiamo sanificare
        return body, 0

    # Mappa per tenere traccia delle sostituzioni vecchio_id -> nuovo_id
    id_mapping: dict[str, str] = {}
    # Contatore delle correzioni effettuate (numero di id server_tool_use sostituiti)
    corrected_count = 0

    try:
        messages = data.get('messages', [])
        for message in messages:
            content = message.get('content')
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue

                # --- blocco server_tool_use ---
                if block.get('type') == 'server_tool_use':
                    old_id = block.get('id')
                    if isinstance(old_id, str) and not SRVTOOLU_ID_PATTERN.match(old_id):
                        # Generazione deterministica del nuovo id
                        alphanum_underscore = re.sub(r'[^a-zA-Z0-9_]', '', old_id)
                        if alphanum_underscore:
                            new_id = 'srvtoolu_' + alphanum_underscore
                        else:
                            # nessun carattere valido -> si usa un hash MD5 troncato a 16 esadecimali
                            new_id = 'srvtoolu_' + hashlib.md5(old_id.encode()).hexdigest()[:16]

                        id_mapping[old_id] = new_id
                        block['id'] = new_id
                        corrected_count += 1
                    # else: già conforme, non si tocca

        # SECONDA PASSATA sui riferimenti: non può stare nel ciclo sopra, perché
        # un blocco risultato che PRECEDE il suo server_tool_use troverebbe la
        # mappa ancora vuota e resterebbe orfano (→ altro 400 di Anthropic per
        # accoppiamento rotto). Si aggiorna qualunque blocco con 'tool_use_id',
        # non solo i tipi noti: un tipo nuovo lato Anthropic romperebbe in silenzio.
        if id_mapping:
            for message in messages:
                content = message.get('content')
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    ref = block.get('tool_use_id')
                    if isinstance(ref, str) and ref in id_mapping:
                        block['tool_use_id'] = id_mapping[ref]
    except Exception:
        # Fail‑safe: qualsiasi errore inatteso non deve propagarsi
        return body, 0

    # Se non è stato corretto nulla, restituisco il body originale senza riserializzare
    if corrected_count == 0:
        return body, 0

    # Serializzazione del body sanificato
    sanitized_body = json.dumps(data, ensure_ascii=False).encode('utf-8')
    return sanitized_body, corrected_count


_FIRMA_ESTRANEA_MAX_LEN = 64
_HEX = set("0123456789abcdefABCDEF")


def _firma_e_estranea(signature) -> bool:
    """True se la ``signature`` di un thinking block NON puo' venire da Anthropic.

    Misurato il 2026-08-16 con sonde dirette sulle porte di modalita':
    GLM (8775) firma con 24 caratteri esadecimali, MiniMax (8781, ACT di mix-am-2)
    con 64. Anthropic firma con un blob crittografico in base64, quindi molto piu'
    lungo e con caratteri fuori dall'alfabeto esadecimale (maiuscole miste, ``+``,
    ``/``, ``=``). Il criterio e' deliberatamente conservativo — solo firme corte E
    interamente esadecimali — perche' sbagliare in difetto lascia le cose come sono
    oggi, mentre sbagliare in eccesso toglierebbe un thinking block valido.
    """
    if signature is None or signature == "":
        return True
    s = str(signature)
    if len(s) > _FIRMA_ESTRANEA_MAX_LEN:
        return False
    return all(c in _HEX for c in s)


def declassa_thinking_estranei(body: bytes) -> tuple[bytes, int]:
    """Converte in blocchi ``text`` i thinking block con firma non-Anthropic.

    Anthropic rifiuta con 400 ``messages.N.content.M: Invalid `signature` in
    `thinking` block`` la cronologia che contiene blocchi thinking prodotti da un
    altro provider. Succede per costruzione nelle modalita' miste: l'ACT (MiniMax
    o GLM) emette thinking nel 98-99% delle risposte, il client li conserva nella
    conversazione, e al turno THINK successivo tornano ad Anthropic che li rifiuta.
    Misurato sui 7 giorni al 2026-08-16: 1.001 turni utente falliti, 553 in
    mix-am-2, 442 in mix-am, 6 in anthropic (cronologie contaminate da sessioni
    miste precedenti), sempre a ``messages.1`` o ``messages.3`` — cioe' il turno
    eseguito dall'ACT.

    Si converte invece di eliminare per due motivi: il ragionamento resta a
    disposizione del modello (nessuna perdita di contesto) e un messaggio il cui
    unico blocco fosse il thinking non resta con ``content`` vuoto, che sarebbe un
    altro 400. La trasformazione e' deterministica, quindi due turni consecutivi
    danno lo stesso prefisso e il prompt caching non ne risente.

    Gemella in uscita di ``strip_thinking_blocks``: quella toglie i thinking di
    Anthropic prima di mandarli altrove, questa neutralizza quelli altrui prima di
    riportarli ad Anthropic. Stesso schema gia' usato da
    ``sanitize_server_tool_ids`` per gli id ``server_tool_use``.
    """
    if b'"thinking"' not in body:
        return body, 0
    try:
        data = json.loads(body)
    except Exception:
        return body, 0

    convertiti = 0
    try:
        for msg in data.get("messages", []):
            if not isinstance(msg, dict):
                continue
            cl = msg.get("content")
            if not isinstance(cl, list):
                continue
            for i, blk in enumerate(cl):
                if not isinstance(blk, dict) or blk.get("type") != "thinking":
                    continue
                if not _firma_e_estranea(blk.get("signature")):
                    continue
                testo = blk.get("thinking")
                cl[i] = {"type": "text",
                         "text": testo if isinstance(testo, str) and testo else "[ragionamento]"}
                convertiti += 1
    except Exception:
        return body, 0

    if convertiti == 0:
        return body, 0
    return json.dumps(data, ensure_ascii=False).encode("utf-8"), convertiti


def strip_thinking_blocks(body: bytes) -> bytes:
    """Rimuove i thinking content blocks dai messaggi prima di forwardare a
    upstream non-Anthropic (MiniMax/GLM/Qwen/local).

    I thinking blocks Anthropic contengono una ``signature`` crittografica che
    un provider non-Anthropic non puo' validare. Se la history con questi blocchi
    torna ad Anthropic in un turno THINK successivo, Anthropic risponde 400
    ``messages.N.content.M: Invalid signature in thinking block``.

    Fast path: se il body non contiene ``"thinking"`` il JSON non viene parsato.
    """
    if b'"thinking"' not in body:
        return body
    try:
        data = json.loads(body)
    except Exception:
        return body

    removed = 0
    for msg in data.get("messages", []):
        cl = msg.get("content")
        if not isinstance(cl, list):
            continue
        filtered = [b for b in cl
                    if not (isinstance(b, dict) and b.get("type") == "thinking")]
        removed += len(cl) - len(filtered)
        if len(filtered) != len(cl):
            msg["content"] = filtered

    if removed == 0:
        return body
    return json.dumps(data, ensure_ascii=False).encode("utf-8")
