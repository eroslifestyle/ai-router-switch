#!/usr/bin/env python3
"""Sonda periodica di disponibilita' su tutte e nove le modalita' (audit A8).

Le modalita' poco usate degradano in silenzio: mix-ag ha ricevuto 15 richieste
in sette giorni e l'80% e' fallito con 429, ma nessuno se ne accorge finche'
non la usa. Questa sonda manda a ciascuna porta fissa una richiesta minima e
registra esito, latenza e forma della risposta.

Ogni richiesta porta l'header 'x-airouter-synthetic: 1', quindi le entry che
produce sono marcate nel sidecar ed escluse dalle statistiche e
dall'apprendimento della policy (audit A7/D5). Senza quella marcatura la sonda
sarebbe l'ennesima fonte di contaminazione delle metriche: e' gia' successo
quattro volte in questo progetto.

INTERRUTTORE: senza AIROUTER_PROBE_ENABLED=1 la sonda non manda nulla. Il
default e' spento di proposito — accenderla consuma quota su sette provider a
pagamento, e quella e' una decisione dell'utente, non un effetto collaterale
dell'installazione di un timer.

Uso:
    python3 sviluppo/tools/probe_modes.py            # rispetta l'interruttore
    python3 sviluppo/tools/probe_modes.py --force    # ignora l'interruttore
    python3 sviluppo/tools/probe_modes.py --modo local --force   # una sola
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import paths  # noqa: E402

# Porta fissa per modalita'. Sono le stesse di router_constants.PORT_MODE piu'
# la dinamica :8787, che qui NON si sonda: seguirebbe la modalita' corrente e
# produrrebbe una riga di significato variabile.
PORTE = {
    "anthropic": 8771,
    "minimax": 8772,
    "mix-am": 8773,
    "mix-al": 8774,
    "glm": 8775,
    "mix-gm": 8776,
    "mix-ag": 8777,
    "qwen": 8778,
    "local": 8779,
}

# Richiesta piu' piccola possibile: il costo di un ciclo completo deve restare
# trascurabile anche a cadenza di mezz'ora.
MAX_TOKENS_SONDA = 8
TIMEOUT_S = 45
MODELLO_SONDA = "claude-haiku-4-5-20251001"

FILE_ESITI = paths.log_file("probe-modes.jsonl")


def _corpo():
    return json.dumps({
        "model": MODELLO_SONDA,
        "max_tokens": MAX_TOKENS_SONDA,
        "messages": [{"role": "user", "content": "ok"}],
    }).encode()


def sonda(modo, porta, timeout=TIMEOUT_S):
    """Interroga una modalita' e ritorna un dizionario di esito.

    Non solleva mai: un provider giu' e' un dato, non un errore del programma.
    """
    esito = {
        "ts": int(time.time()), "modo": modo, "porta": porta,
        "http": 0, "ttfb_ms": None, "total_ms": None,
        "encoding": "", "id_prefisso": "", "modello_dichiarato": "",
        "modello_reale": "", "raggiungibile": False, "errore": "",
    }
    richiesta = urllib.request.Request(
        f"http://127.0.0.1:{porta}/v1/messages",
        data=_corpo(),
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": "probe",
            # La marcatura che tiene questa sonda fuori dalle statistiche.
            "x-airouter-synthetic": "1",
        },
        method="POST",
    )
    inizio = time.monotonic()
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as risposta:
            esito["http"] = risposta.status
            esito["ttfb_ms"] = round((time.monotonic() - inizio) * 1000, 1)
            grezzo = risposta.read()
            esito["total_ms"] = round((time.monotonic() - inizio) * 1000, 1)
            esito["encoding"] = risposta.headers.get("Content-Encoding", "") or "identity"
            esito["modello_reale"] = risposta.headers.get("x-ai-actual-model", "")
            _analizza_corpo(esito, grezzo)
    except urllib.error.HTTPError as e:
        esito["http"] = e.code
        esito["total_ms"] = round((time.monotonic() - inizio) * 1000, 1)
        esito["errore"] = f"HTTPError: {str(e)[:120]}"
    except Exception as e:
        esito["total_ms"] = round((time.monotonic() - inizio) * 1000, 1)
        esito["errore"] = f"{type(e).__name__}: {str(e)[:120]}"
    esito["raggiungibile"] = _e_raggiungibile(esito["http"])
    return esito


# 401 e 403 significano che il router ha risposto E che l'upstream ha valutato
# le credenziali: la catena e' viva. Succede sulla modalita' anthropic, l'unica
# che valida la chiave del CLIENT invece di usare solo quelle del server — la
# sonda non ha un token OAuth valido, e non deve averlo. Trattarli come
# indisponibilita' darebbe un allarme falso ogni mezz'ora.
STATI_RAGGIUNGIBILE = frozenset({200, 401, 403})


def _e_raggiungibile(http):
    return http in STATI_RAGGIUNGIBILE


def _analizza_corpo(esito, grezzo):
    """Estrae forma dell'id e modello dichiarato, se il corpo e' leggibile.

    Il corpo puo' arrivare compresso: il router passa i byte come li riceve
    (auto_decompress=False), e non tutte le modalita' comprimono. Un corpo
    illeggibile non e' un errore, e' un fatto da registrare.
    """
    try:
        if grezzo[:2] == b"\x1f\x8b":
            import gzip
            grezzo = gzip.decompress(grezzo)
        dati = json.loads(grezzo.decode("utf-8"))
    except Exception as e:
        esito["errore"] = esito["errore"] or f"corpo non leggibile: {type(e).__name__}"
        return
    identificativo = str(dati.get("id") or "")
    esito["id_prefisso"] = identificativo.split("_")[0] + "_" if "_" in identificativo else identificativo[:8]
    esito["modello_dichiarato"] = str(dati.get("model") or "")


def esegui(modi=None, forza=False, timeout=TIMEOUT_S, scrivi=True):
    """Sonda le modalita' richieste e scrive gli esiti. Ritorna la lista."""
    if not forza and os.environ.get("AIROUTER_PROBE_ENABLED", "0") != "1":
        print("Sonda disattivata (AIROUTER_PROBE_ENABLED diverso da 1). "
              "Accenderla consuma quota su sette provider a pagamento: "
              "e' una decisione da prendere, non un default.")
        return []
    scelte = modi or list(PORTE)
    esiti = [sonda(m, PORTE[m], timeout) for m in scelte if m in PORTE]
    if scrivi and esiti:
        try:
            FILE_ESITI.parent.mkdir(parents=True, exist_ok=True)
            with FILE_ESITI.open("a", encoding="utf-8") as f:
                for e in esiti:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"esiti non scritti su {FILE_ESITI}: {type(e).__name__}: {e}",
                  file=sys.stderr)
    return esiti


def _stampa(esiti):
    print(f"{'modalita':10s} {'porta':>5s} {'http':>4s} {'ok':>3s} {'ttfb':>8s} {'tot':>8s} "
          f"{'encoding':10s} {'id':10s} {'modello reale':22s} errore")
    for e in esiti:
        print(f"{e['modo']:10s} {e['porta']:5d} {e['http']:4d} "
              f"{('si' if e['raggiungibile'] else 'NO'):>3s} "
              f"{(str(e['ttfb_ms']) if e['ttfb_ms'] is not None else '-'):>8s} "
              f"{(str(e['total_ms']) if e['total_ms'] is not None else '-'):>8s} "
              f"{e['encoding']:10s} {e['id_prefisso']:10s} "
              f"{e['modello_reale'][:22]:22s} {e['errore']}")
    giu = [e["modo"] for e in esiti if not e["raggiungibile"]]
    if giu:
        print(f"\nNON raggiungibili: {', '.join(giu)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true",
                        help="Ignora AIROUTER_PROBE_ENABLED")
    parser.add_argument("--modo", action="append", choices=sorted(PORTE),
                        help="Sonda solo questa modalita' (ripetibile)")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_S)
    parser.add_argument("--no-write", action="store_true",
                        help="Non scrivere il file degli esiti")
    args = parser.parse_args()

    esiti = esegui(modi=args.modo, forza=args.force, timeout=args.timeout,
                   scrivi=not args.no_write)
    if esiti:
        _stampa(esiti)
        print(f"\nEsiti in {FILE_ESITI}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
