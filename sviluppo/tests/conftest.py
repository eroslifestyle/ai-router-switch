"""Fixture condivise per i test end-to-end che pilotano il proxy in-process.

Perché esiste: `test_gate_e2e.py` e `test_mixgm_stream_ttfb.py` nascono come
script standalone. Ciascuno definisce una propria classe `Harness` e dichiara i
test come `async def test_x(h)`, contando su un runner in fondo al file che
costruiva l'harness a mano. pytest li raccoglieva per il solo prefisso `test_`,
non sapeva da dove far venire `h`, e li archiviava come `fixture 'h' not found`:
quattro test mai eseguiti, mentre la suite continuava a dichiarare «106 passed».

Perché il purge di `sys.modules`: ogni harness redirige gli upstream su un fake
server con porta effimera, esportando le variabili d'ambiente PRIMA di importare
il proxy. Ma `router_constants` risolve quegli indirizzi una sola volta, al
momento dell'import, e poi resta in cache. Nella stessa sessione pytest il
secondo file ereditava così la porta del fake server del primo — ormai chiuso —
e riceveva 502. Scaricare i moduli di `src/` prima di ogni avvio li obbliga a
rileggere l'ambiente corrente.
"""

import os
import pathlib
import shutil
import sys
import tempfile

import pytest
import pytest_asyncio

# Il conftest vive in sviluppo/tests/, quindi parents[2] è la root del repo.
SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


# ── Isolamento del BUG-CATALOG ───────────────────────────────────────────────
# `debug_catalog` ancora il percorso alla root del repo e lo risolve UNA volta,
# all'import: senza override ogni test che importa il modulo scrive nel catalogo
# di PRODUZIONE. Misurato il 2026-08-06: 7 eventi aggiunti a ogni esecuzione
# della suite e, soprattutto, l'`example_snippet` delle entry reali sovrascritto
# da esempi sintetici — `tool_isolation_strip` era passato da "kept=72/73" di
# produzione a "kept=0/1" di test. Il campo esempio è ciò che rende il catalogo
# diagnostico: la contaminazione non era cosmetica, cancellava l'informazione.
#
# Perché al TOP-LEVEL e non in una fixture: `test_tool_isolation_cache.py` e
# `test_qwen_tool_trim.py` importano i moduli di `src/` a livello di modulo,
# quindi durante la COLLECTION, che precede l'esecuzione di qualunque fixture —
# anche di una autouse con scope=session. Una fixture arrivava troppo tardi e
# due contatori continuavano a muoversi. La conftest viene invece importata
# prima dei moduli di test, quindi qui la variabile è già pronta.
_CATALOGO_TMP = tempfile.mkdtemp(prefix="bug-catalog-test-")
os.environ["AIROUTER_CATALOG_PATH"] = str(
    pathlib.Path(_CATALOGO_TMP) / "BUG-CATALOG.jsonl"
)


def pytest_sessionfinish(session, exitstatus):
    """Rimuove la tmpdir del catalogo isolato a fine sessione."""
    shutil.rmtree(_CATALOGO_TMP, ignore_errors=True)


def _purge_src_modules():
    """Scarica i moduli importati da `src/`, così l'import successivo rilegge l'ambiente."""
    da_rimuovere = []
    for nome, modulo in list(sys.modules.items()):
        percorso = getattr(modulo, "__file__", None)
        if not percorso:
            continue
        try:
            if str(pathlib.Path(percorso).resolve()).startswith(str(SRC)):
                da_rimuovere.append(nome)
        except (TypeError, ValueError, OSError):
            continue  # moduli con __file__ anomalo: non sono nostri, si ignorano
    for nome in da_rimuovere:
        sys.modules.pop(nome, None)


@pytest_asyncio.fixture
async def h(request):
    """Costruisce e avvia l'`Harness` definito dal modulo di test corrente.

    La classe si prende dal modulo invece di importarla: i due file hanno
    harness diversi (upstream JSON contro upstream SSE lento) e una fixture sola
    li serve entrambi senza accoppiarli.
    """
    harness_cls = getattr(request.module, "Harness", None)
    if harness_cls is None:
        pytest.skip("Il modulo non definisce una classe Harness")

    _purge_src_modules()
    istanza = harness_cls()
    await istanza.start()
    try:
        yield istanza
    finally:
        # stop() deve girare anche se il test fallisce: altrimenti il fake
        # server resta in ascolto e inquina i test successivi.
        await istanza.stop()
        _purge_src_modules()
