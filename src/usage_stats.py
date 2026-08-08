"""Statistiche di latenza dal sidecar router-usage.jsonl (audit D6).

Le rotte /debug/ esistevano gia' ma non c'era modo di rispondere alla domanda
piu' semplice: «questa richiesta quanto e' durata?». Da quando il sidecar
registra ttfb_ms e total_ms (audit A9) i dati ci sono, e qui si aggregano per
modalita'.

Due scelte deliberate:

- Si legge solo la CODA del file. Il sidecar in produzione supera i 45 MB e
  l'endpoint /debug/stats deve restare istantaneo: leggerlo tutto a ogni
  richiesta HTTP sarebbe un modo per trasformare uno strumento diagnostico in
  un problema di prestazioni.
- Le entry marcate `synthetic` vengono escluse (audit A7/D5). Sonde e test sono
  il 6,3% delle entry storiche e falsavano ogni analisi per modalita': senza
  filtro la modalita' minimax risultava al 95,3% di errori mentre le richieste
  reali erano 9, con zero errori.
"""
import json
import time

# Coda del file da leggere. 8 MB coprono varie migliaia di entry: abbastanza
# per percentili stabili, poco abbastanza da restare sotto il decimo di secondo.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
# Finestra temporale di default: un giorno. Oltre, i percentili raccontano una
# configurazione che nel frattempo e' cambiata.
DEFAULT_WINDOW_S = 86400
PERCENTILI = (50, 90, 99)


def _percentile(valori_ordinati, percentuale):
    """Percentile col metodo del rango piu' vicino, senza dipendenze esterne.

    numpy non e' fra le dipendenze del router e statistics.quantiles ha una
    semantica di interpolazione diversa: per una lettura diagnostica il rango
    piu' vicino e' piu' facile da spiegare e da verificare a mano.
    """
    if not valori_ordinati:
        return None
    if len(valori_ordinati) == 1:
        return round(float(valori_ordinati[0]), 1)
    indice = int(round((percentuale / 100.0) * (len(valori_ordinati) - 1)))
    indice = max(0, min(indice, len(valori_ordinati) - 1))
    return round(float(valori_ordinati[indice]), 1)


def _leggi_coda(percorso, max_bytes):
    """Ritorna le righe complete presenti negli ultimi max_bytes del file."""
    with open(percorso, "rb") as f:
        f.seek(0, 2)
        dimensione = f.tell()
        inizio = max(0, dimensione - max_bytes)
        f.seek(inizio)
        dati = f.read()
    testo = dati.decode("utf-8", errors="replace")
    righe = testo.split("\n")
    if inizio > 0 and righe:
        # La prima riga e' quasi certamente tagliata a meta': si scarta.
        righe = righe[1:]
    return righe


def latenza_per_modalita(percorso, finestra_s=DEFAULT_WINDOW_S,
                         max_bytes=DEFAULT_MAX_BYTES, adesso=None,
                         includi_sintetiche=False):
    """Percentili di ttfb_ms e total_ms per modalita'.

    Ritorna un dict {modalita: {n, ttfb_ms: {p50,p90,p99}, total_ms: {...}}}.
    Le modalita' senza alcuna entry con latenza non compaiono: sono quelle il
    cui traffico e' tutto anteriore all'introduzione dei campi.
    """
    adesso = time.time() if adesso is None else adesso
    taglio = adesso - finestra_s if finestra_s else 0
    per_modalita = {}
    scartate_sintetiche = 0

    try:
        righe = _leggi_coda(percorso, max_bytes)
    except (FileNotFoundError, OSError):
        return {"modalita": {}, "finestra_s": finestra_s, "campioni": 0,
                "scartate_sintetiche": 0}

    for riga in righe:
        riga = riga.strip()
        if not riga:
            continue
        try:
            entry = json.loads(riga)
        except (ValueError, TypeError):
            continue
        if (entry.get("ts") or 0) < taglio:
            continue
        if entry.get("synthetic") and not includi_sintetiche:
            scartate_sintetiche += 1
            continue
        modalita = entry.get("mode") or "?"
        accumulatore = per_modalita.setdefault(modalita, {"ttfb": [], "total": []})
        for chiave, campo in (("ttfb", "ttfb_ms"), ("total", "total_ms")):
            valore = entry.get(campo)
            if isinstance(valore, (int, float)):
                accumulatore[chiave].append(float(valore))

    risultato = {}
    campioni = 0
    for modalita, acc in per_modalita.items():
        totali = sorted(acc["total"])
        ttfb = sorted(acc["ttfb"])
        if not totali and not ttfb:
            continue  # traffico solo pre-A9: nessuna latenza registrata
        campioni += len(totali)
        risultato[modalita] = {
            "n": len(totali) or len(ttfb),
            "ttfb_ms": {f"p{p}": _percentile(ttfb, p) for p in PERCENTILI},
            "total_ms": {f"p{p}": _percentile(totali, p) for p in PERCENTILI},
        }

    return {
        "modalita": dict(sorted(risultato.items())),
        "finestra_s": finestra_s,
        "campioni": campioni,
        "scartate_sintetiche": scartate_sintetiche,
    }
