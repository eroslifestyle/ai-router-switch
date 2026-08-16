"""
Modulo per la correlazione delle richieste HTTP in ai-router-switch.

Problema: senza un identificatore univoco per ogni richiesta, e' impossibile
collegare le righe di ai-router.log, le entry di router-usage.jsonl, le entry
del BUG-CATALOG e la risposta al client. Per debuggare un errore bisognava
incrociare i timestamp a mano, operazione tediosa e soggetta a errori.

Soluzione: questo modulo fornisce un request_id breve che viene propagato
tramite contextvars.ContextVar. La scelta di ContextVar (anziche' variabili
globali) garantisce l'isolamento tra task asyncio concorrenti, evitando che
una richiesta erediti l'identificatore di un'altra.
"""

from contextvars import ContextVar
from uuid import uuid4

# Variabile di contesto per l'isolamento tra task asyncio.
# Default: stringa vuota (nessun request_id impostato).
_request_id: ContextVar[str] = ContextVar('_request_id', default='')

# Header HTTP con cui il request_id viene comunicato al client.
HEADER_NAME = 'x-airouter-request-id'


def new_request_id() -> str:
    """Genera un nuovo request_id: 12 caratteri esadecimali da uuid4."""
    return uuid4().hex[:12]


def set_request_id(rid: str) -> None:
    """Imposta il request_id nel contesto asyncio corrente."""
    _request_id.set(rid)


def get_request_id() -> str:
    """Ritorna il request_id corrente, o stringa vuota se non impostato."""
    try:
        return _request_id.get()
    except LookupError:
        return ''
