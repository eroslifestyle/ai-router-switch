"""Test di guardia per il rilevamento unificato del contesto esaurito.

Il rilevamento del contesto esaurito nel corpo di un errore 400 esisteva in quattro
copie divergenti; la copia dentro forward_anthropic non conteneva il marker "too long",
quindi non riconosceva l'errore più frequente di Anthropic, che nei log di produzione
si presenta come "prompt is too long: 232125 tokens > 200000 maximum" (dieci occorrenze
su dodici), e il retry che rimuove le immagini e ritenta non scattava mai.
Ora la lista è unica in router_constants: questi test sono la guardia che impedisce
di tornare a divergere.
"""

import sys
sys.path.insert(0, "src")
from router_constants import CONTEXT_EXCEED_MARKERS, is_context_exceeded_body

ERRORI_REALI = (
    b'{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long: 232125 tokens > 200000 maximum"}}',
    b'{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long: 208904 tokens > 200000 maximum"}}',
    b'{"error":{"message":"context window exceeds limit (2013)"}}',
)


def test_riconosce_gli_errori_reali_dei_log():
    """Sono i corpi osservati davvero in produzione; il primo e la forma piu' frequente,
    quella che la copia dentro forward_anthropic non riconosceva."""
    for errore in ERRORI_REALI:
        risultato = is_context_exceeded_body(errore)
        assert risultato is True, f"Atteso: True, Ottenuto: {risultato} per errore {errore}"


def test_non_riconosce_errori_estranei():
    """Il rilevamento non deve scattare su errori di altra natura, altrimenti il retry
    senza immagini partirebbe a sproposito."""
    errori_estranei = (
        b'{"error":{"message":"invalid x-api-key"}}',
        b'{"error":{"message":"rate limit exceeded, please retry"}}',
        b'{"error":{"message":"overloaded"}}',
        b'',
        b'{}',
    )
    for errore in errori_estranei:
        risultato = is_context_exceeded_body(errore)
        assert risultato is False, f"Atteso: False, Ottenuto: {risultato} per errore {errore}"


def test_marker_essenziali_presenti():
    """'too long' in particolare e' il marker la cui assenza causava il difetto;
    se qualcuno lo rimuove, questo test deve diventare rosso."""
    marker_essenziali = (
        b"too long",
        b"context window",
        b"context_exceeded",
        b"maximum context",
        b"context_length",
        b"2013",
    )
    for marker in marker_essenziali:
        assert marker in CONTEXT_EXCEED_MARKERS, f"Atteso: {marker} presente in CONTEXT_EXCEED_MARKERS, Ottenuto: assente"


def test_tutte_le_copie_riconoscono_lo_stesso_insieme():
    """Le quattro implementazioni storiche devono ora rispondere allo stesso modo;
    se qualcuno reintroduce una lista locale, questo test se ne accorge."""
    import providers.base as base
    from context_manager import ContextManager

    cm = ContextManager()
    for errore in ERRORI_REALI:
        risultato_router = is_context_exceeded_body(errore)
        risultato_base = base._is_context_exceed_400(errore)[0]
        risultato_cm = cm._is_context_error(errore)

        assert risultato_router == risultato_base, \
            f"Atteso: router={risultato_router} == base={risultato_base}, Ottenuto: divergenza per {errore}"
        assert risultato_router == risultato_cm, \
            f"Atteso: router={risultato_router} == context_manager={risultato_cm}, Ottenuto: divergenza per {errore}"


def test_estrattore_testo_fonte_unica():
    """
    Verifica che _text_from_message abbia una sola fonte in providers.base.

    La versione arretrata leggeva solo il primo blocco e restituiva stringa vuota
    quando la risposta cominciava con un blocco thinking, causa nota delle risposte vuote.
    Ora esiste una sola definizione e il test sorveglia che resti una sola.
    """
    import providers.base as base
    import pipeline_anthropic
    import forward_anthropic

    risposta = {
        'content': [
            {'type': 'thinking', 'thinking': {'thinking': 'ragiono'}},
            {'type': 'text', 'text': 'risposta'}
        ]
    }

    risultato = base._text_from_message(risposta)
    atteso = 'ragionorisposta'
    assert risultato == atteso, f"Estrazione testo fallita: atteso {atteso!r}, ottenuto {risultato!r}"

    assert pipeline_anthropic._text_from_message is base._text_from_message, (
        "pipeline_anthropic._text_from_message deve essere re-export di base._text_from_message, "
        "non una copia separata"
    )

    assert not hasattr(forward_anthropic, '_text_from_message'), (
        "forward_anthropic non deve avere piu' _text_from_message: "
        "la copia morta e' stata rimossa il 2026-08-07 e non va reintrodotta"
    )
