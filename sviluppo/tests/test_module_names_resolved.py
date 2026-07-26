#!/usr/bin/env python3
"""Verifica che i nomi usati nei moduli del router siano davvero risolvibili.

Perché esiste: il 2026-07-26 due bug della stessa famiglia sono costati ore.
  1. `pipeline_minimax.py` usava `web.json_response(...)` nei rami d'errore senza
     importare `web`: al primo timeout di MiniMax il *gestore dell'errore*
     sollevava NameError e aiohttp rispondeva un 500 opaco
     ("Server got itself in trouble"), che a valle si manifestava come
     "curl call failed: Extra data: line 1 column 5" — nessun indizio sulla causa.
  2. `ai-router-proxy.py` re-importava dentro `handle()` un nome già importato a
     livello modulo, rendendolo LOCALE all'intera funzione: l'uso precedente
     sollevava UnboundLocalError, inghiottito da un `except`, e il rewrite
     proattivo del contesto non scattava mai.

Entrambi passavano `py_compile` e i test unitari: si vedono solo su un percorso
d'errore raro. Questi controlli sono statici e girano in millisecondi.
"""

import ast
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

# Nomi che devono essere importati se usati come `NOME.attributo`
NOMI_DA_IMPORTARE = ("web",)


def _moduli():
    return sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")


def _nomi_importati(tree: ast.AST) -> set:
    """Nomi introdotti nel modulo da import (inclusi gli alias)."""
    nomi = set()
    for nodo in ast.walk(tree):
        if isinstance(nodo, ast.Import):
            for a in nodo.names:
                nomi.add((a.asname or a.name).split(".")[0])
        elif isinstance(nodo, ast.ImportFrom):
            for a in nodo.names:
                nomi.add(a.asname or a.name)
    return nomi


def test_nomi_usati_sono_importati():
    """`web.qualcosa` deve corrispondere a un import nel modulo."""
    mancanti = []
    for path in _moduli():
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
        importati = _nomi_importati(tree)
        for nodo in ast.walk(tree):
            if (isinstance(nodo, ast.Attribute)
                    and isinstance(nodo.value, ast.Name)
                    and nodo.value.id in NOMI_DA_IMPORTARE
                    and nodo.value.id not in importati):
                mancanti.append(f"{path.name}:{nodo.lineno} usa "
                                f"{nodo.value.id}.{nodo.attr} senza importarlo")
    assert not mancanti, "Nomi usati ma non importati:\n  " + "\n  ".join(mancanti)
    print("  test_nomi_usati_sono_importati: OK")


def test_nessun_import_locale_di_nomi_gia_globali():
    """Un import dentro una funzione rende il nome locale a TUTTA la funzione.

    Se lo stesso nome è già importato a livello modulo, ogni uso che precede
    l'import locale solleva UnboundLocalError a runtime.
    """
    conflitti = []
    for path in _moduli():
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
        globali = set()
        for nodo in tree.body:  # solo livello modulo
            if isinstance(nodo, (ast.Import, ast.ImportFrom)):
                for a in nodo.names:
                    globali.add((a.asname or a.name).split(".")[0])

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for nodo in ast.walk(fn):
                if not isinstance(nodo, (ast.Import, ast.ImportFrom)):
                    continue
                for a in nodo.names:
                    nome = (a.asname or a.name).split(".")[0]
                    if nome in globali:
                        conflitti.append(
                            f"{path.name}:{nodo.lineno} in {fn.name}() re-importa "
                            f"'{nome}', già importato a livello modulo "
                            f"→ diventa locale all'intera funzione")
    assert not conflitti, ("Import locali che ombreggiano import di modulo:\n  "
                           + "\n  ".join(conflitti))
    print("  test_nessun_import_locale_di_nomi_gia_globali: OK")


def test_ramo_errore_minimax_risponde_json():
    """Test che verifica il comportamento del gestore d'errore quando
    pm.forward_minimax solleva un asyncio.TimeoutError. Il gestore
    deve restituire una risposta HTTP 502 con un body JSON contenente
    la chiave 'error'. Inoltre, il test rileva eventuali NameError
    causati da nomi non importati (es. 'web'), che provocherebbero
    una risposta 500 opaca."""
    # Importazioni locali richieste
    import sys
    import asyncio
    import json
    import pathlib

    # Aggiunge SRC al path per poter importare pipeline_minimax
    # (SRC è già definito a livello di modulo come pathlib.Path della cartella src)
    sys.path.insert(0, str(SRC))

    # Importa il modulo sotto test
    import pipeline_minimax as pm

    # Definisce una coroutine fittizia che solleva TimeoutError
    async def mock_forward_minimax(*args, **kwargs):
        raise asyncio.TimeoutError('Timeout on reading data from socket')

    # `pipeline_minimax` fa `from forward_minimax import forward_minimax` DENTRO
    # la funzione: va sostituito il modulo sorgente, non l'attributo di pm.
    import forward_minimax as fm_mod
    original_forward = fm_mod.forward_minimax
    fm_mod.forward_minimax = mock_forward_minimax
    try:
        # Classe che simula una richiesta HTTP in entrata
        class FakeReq:
            path = '/v1/messages'
            headers = {}
            transport = None
            remote = '127.0.0.1'
            method = 'POST'
            query = {}
            path_qs = '/v1/messages'

        # Dati della richiesta originale
        orig = {'model': 'MiniMax-M2.7', 'messages': [{'role': 'user', 'content': 'ciao'}]}
        body = json.dumps(orig).encode()

        # Esegue la funzione di orchestrazione e cattura eventuali NameError
        try:
            resp = asyncio.run(
                pm._pipeline_minimax_orchestrate(FakeReq(), body, None, orig, relay=None)
            )
        except NameError as e:
            # Se si verifica un NameError, significa che il gestore d'errore
            # utilizza un nome non importato (es. 'web'), restituendo una risposta
            # 500 opaca invece di JSON strutturato.
            raise AssertionError(
                f"NameError sollevato durante l'esecuzione: {e}. "
                "Il gestore d'errore usa un nome non importato, "
                "restituendo una risposta 500 opaca invece di JSON strutturato."
            ) from e

        # Verifica che la risposta abbia stato HTTP 502
        assert resp.status == 502, (
            f"Aspettato stato HTTP 502, ottenuto {resp.status}. "
            "Il gestore d'errore dovrebbe restituire 502 Bad Gateway."
        )

        # Verifica che il body sia un JSON con chiave 'error'
        resp_body = json.loads(resp.body)
        assert isinstance(resp_body, dict) and 'error' in resp_body, (
            f"Aspettato un dict JSON con chiave 'error', ottenuto: {resp_body}"
        )
    finally:
        # Ripristina l'implementazione originale di forward_minimax
        fm_mod.forward_minimax = original_forward

    print("  test_ramo_errore_minimax_risponde_json: OK")


def main():
    print("=" * 60)
    test_nomi_usati_sono_importati()
    test_nessun_import_locale_di_nomi_gia_globali()
    test_ramo_errore_minimax_risponde_json()
    print("=" * 60)
    print("TUTTI I TEST PASSATI")


if __name__ == "__main__":
    sys.exit(main())
