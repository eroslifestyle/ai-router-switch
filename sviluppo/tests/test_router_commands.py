import sys
import pytest
sys.path.insert(0, 'src')
from router_commands import (
    parse_router_command, _router_reply_text, _synthetic_message,
    _INTERNAL_TO_DISPLAY, _strip_injected_blocks,
)
from router_constants import VALID_MODES


def test_ogni_modalita_valida_e_riconosciuta():
    """
    Verifica che ogni modalita in VALID_MODES sia riconosciuta da !router <modalita>.
    La settima modalita qwen, aggiunta il 2026-08-03, funzionava gia nel parser
    ma mancava dall'elenco nell'help fino al 2026-08-04.
    """
    for modo in VALID_MODES:
        result = parse_router_command(f"!router {modo}")
        assert result is not None, f"Atteso: riconoscimento non-None per '{modo}', Ottenuto: None"
        assert result.get("action") == "set", f"Atteso: action='set' per '{modo}', Ottenuto: {result.get('action')}"
        assert result.get("mode") == modo, f"Atteso: mode='{modo}' per '{modo}', Ottenuto: {result.get('mode')}"


def test_alias_brevi():
    """
    Gli alias brevi mixam, mixgm, mixag vengono risolti nei nomi canonici
    mix-am, mix-gm, mix-ag rispettivamente.
    """
    alias_map = {
        "mixam": "mix-am",
        "mixgm": "mix-gm",
        "mixag": "mix-ag",
    }
    for alias, canonico in alias_map.items():
        result = parse_router_command(f"!router {alias}")
        assert result is not None, f"Atteso: risultato non-None per alias '{alias}', Ottenuto: None"
        assert result.get("action") == "set", f"Atteso: action='set' per alias '{alias}', Ottenuto: {result.get('action')}"
        assert result.get("mode") == canonico, f"Atteso: mode='{canonico}' per alias '{alias}', Ottenuto: {result.get('mode')}"


def test_nomi_storici_normalizzati_al_canonico():
    """
    Fino al 2026-08-04 i nomi storici mixed, glm-minimax e anthropic-glm
    finivano nel ramo help e non cambiavano nulla, mentre ai-mode da terminale
    li accettava gia: era un'incoerenza fra i due punti d'ingresso.
    Ora vengono normalizzati al canonico.
    """
    storici_map = {
        "mixed": "mix-am",
        "glm-minimax": "mix-gm",
        "anthropic-glm": "mix-ag",
    }
    for storico, canonico in storici_map.items():
        result = parse_router_command(f"!router {storico}")
        assert result is not None, f"Atteso: risultato non-None per storico '{storico}', Ottenuto: None"
        assert result.get("action") == "set", f"Atteso: action='set' per storico '{storico}', Ottenuto: {result.get('action')}"
        assert result.get("mode") == canonico, f"Atteso: mode='{canonico}' per storico '{storico}', Ottenuto: {result.get('mode')}"


def test_comandi_status_reset_help():
    """
    I comandi espliciti status, reset e help vengono riconosciuti
    e restituiscono l'action corrispondente.
    """
    for cmd in ("status", "reset", "help"):
        result = parse_router_command(f"!router {cmd}")
        assert result is not None, f"Atteso: risultato non-None per '{cmd}', Ottenuto: None"
        assert result.get("action") == cmd, f"Atteso: action='{cmd}' per '!router {cmd}', Ottenuto: {result.get('action')}"


def test_argomento_sconosciuto_ricade_su_help():
    """
    Argomenti non riconosciuti causano il fallback all'action help.
    """
    for arg in ("inesistente", "pippo"):
        result = parse_router_command(f"!router {arg}")
        assert result is not None, f"Atteso: risultato non-None per '{arg}', Ottenuto: None"
        assert result.get("action") == "help", f"Atteso: action='help' per '{arg}', Ottenuto: {result.get('action')}"


def test_testo_senza_comando_non_e_riconosciuto():
    """
    Stringhe che non contengono il comando esplicito !router restituiscono None.
    """
    assert parse_router_command("") is None, "Atteso: None per stringa vuota, Ottenuto: qualcosa"
    assert parse_router_command(None) is None, "Atteso: None per None, Ottenuto: qualcosa"
    assert parse_router_command("parlami del router") is None, "Atteso: None per 'parlami del router', Ottenuto: qualcosa"
    assert parse_router_command("il router e lento") is None, "Atteso: None per 'il router e lento', Ottenuto: qualcosa"


def test_guardrail_comando_citato_in_un_discorso():
    """
    Il guardrail scarta il comando quando dopo l'argomento resta piu di qualche
    carattere: protegge dal cambio-modalita accidentale se !router compare a
    inizio riga dentro un discorso vero. Nota: questi casi devono avere il
    comando a INIZIO RIGA, altrimenti e la regex a fermarli e il guardrail
    non viene mai raggiunto.
    """
    assert parse_router_command("!router glm e poi spiegami come funziona il proxy") is None, \
        "Atteso: None per !router con coda lunga dopo l'argomento, Ottenuto: qualcosa"
    assert parse_router_command("!router mixed ieri non ha funzionato, come mai?") is None, \
        "Atteso: None per !router con coda lunga dopo l'argomento, Ottenuto: qualcosa"

    result = parse_router_command("!router glm")
    assert result is not None, \
        "Atteso: risultato non-None per !router glm senza coda, Ottenuto: None"
    assert result.get("action") == "set", \
        f"Atteso: action='set', Ottenuto: {result.get('action')}"


def test_blocchi_iniettati_non_bloccano_il_comando():
    """
    Il CLI APPENDE al messaggio dell'utente blocchi che l'utente non ha scritto.
    Fino al fix del 2026-07-26 lo strip toglieva solo i tag e lasciava il
    contenuto: la coda dopo l'argomento risultava lunga centinaia di caratteri,
    il guardrail scartava il comando e '!router glm' partiva verso il provider
    come messaggio normale, causando un errore API in chat.
    """
    # Caso reale: blocco DOPO il comando (come fa il CLI)
    testo_dopo = "!router qwen\n<system-reminder>" + "contenuto lungo iniettato dall'harness " * 5 + "</system-reminder>"
    result_dopo = parse_router_command(testo_dopo)
    assert result_dopo is not None, \
        "Atteso: risultato non-None con blocco iniettato dopo il comando, Ottenuto: None"
    assert result_dopo.get("action") == "set", \
        f"Atteso: action='set', Ottenuto: {result_dopo.get('action')}"
    assert result_dopo.get("mode") == "qwen", \
        f"Atteso: mode='qwen', Ottenuto: {result_dopo.get('mode')}"

    # Variante: blocco PRIMA del comando (deve funzionare ugualmente)
    testo_prima = "<system-reminder>contenuto lungo iniettato dall'harness " * 5 + "</system-reminder>\n!router qwen"
    result_prima = parse_router_command(testo_prima)
    assert result_prima is not None, \
        "Atteso: risultato non-None con blocco iniettato prima del comando, Ottenuto: None"
    assert result_prima.get("action") == "set", \
        f"Atteso: action='set', Ottenuto: {result_prima.get('action')}"
    assert result_prima.get("mode") == "qwen", \
        f"Atteso: mode='qwen', Ottenuto: {result_prima.get('mode')}"
def test_strip_rimuove_tag_e_contenuto():
    """
    _strip_injected_blocks rimuove sia i tag che il contenuto dei blocchi
    iniettati, preservando il resto del testo.
    """
    testo = "<ide_selection>del testo</ide_selection>ciao"
    stripped = _strip_injected_blocks(testo)
    assert "ide_selection" not in stripped, f"Atteso: 'ide_selection' assente, Ottenuto: presente in '{stripped}'"
    assert "del testo" not in stripped, f"Atteso: 'del testo' assente, Ottenuto: presente in '{stripped}'"
    assert "ciao" in stripped, f"Atteso: 'ciao' presente, Ottenuto: assente in '{stripped}'"


def test_ogni_modalita_ha_un_nome_visualizzato():
    """
    Ogni modalita in VALID_MODES deve avere un corrispondente nome
    visualizzato in _INTERNAL_TO_DISPLAY. La modalita qwen mancava
    fino al 2026-08-04; il .get col default mascherava l'assenza.
    """
    for modo in VALID_MODES:
        assert modo in _INTERNAL_TO_DISPLAY, f"Atteso: '{modo}' presente in _INTERNAL_TO_DISPLAY, Ottenuto: assente"


def test_help_elenca_tutte_le_modalita():
    """
    Il testo dell'help deve contenere tutte le modalita e i nomi storici.
    """
    help_text = _router_reply_text({"action": "help"}, "fp-test")
    assert "anthropic" in help_text, f"Atteso: 'anthropic' presente nell'help, Ottenuto: assente"
    assert "minimax" in help_text, f"Atteso: 'minimax' presente nell'help, Ottenuto: assente"
    assert "glm" in help_text, f"Atteso: 'glm' presente nell'help, Ottenuto: assente"
    assert "qwen" in help_text, f"Atteso: 'qwen' presente nell'help, Ottenuto: assente"
    assert "mix" in help_text, f"Atteso: almeno un alias 'mix' presente nell'help, Ottenuto: assente"
    assert "mixed" in help_text, f"Atteso: nome storico 'mixed' presente nell'help, Ottenuto: assente"
    assert "glm-minimax" in help_text, f"Atteso: nome storico 'glm-minimax' presente nell'help, Ottenuto: assente"


def test_messaggio_sintetico_ben_formato():
    """
    _synthetic_message restituisce un dict con la struttura corretta per
    il formato messaggio previsto dal router.
    """
    msg = _synthetic_message("ciao")
    assert isinstance(msg, dict), f"Atteso: dict, Ottenuto: {type(msg)}"
    assert msg.get("role") == "assistant", f"Atteso: role='assistant', Ottenuto: {msg.get('role')}"
    assert msg.get("type") == "message", f"Atteso: type='message', Ottenuto: {msg.get('type')}"
    assert msg.get("stop_reason") == "end_turn", f"Atteso: stop_reason='end_turn', Ottenuto: {msg.get('stop_reason')}"
    content = msg.get("content")
    assert isinstance(content, list), f"Atteso: content list, Ottenuto: {type(content)}"
    assert len(content) == 1, f"Atteso: content con 1 elemento, Ottenuto: {len(content)}"
    assert content[0].get("type") == "text", f"Atteso: content[0].type='text', Ottenuto: {content[0].get('type')}"
    assert content[0].get("text") == "ciao", f"Atteso: content[0].text='ciao', Ottenuto: {content[0].get('text')}"
