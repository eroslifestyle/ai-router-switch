"""Validazione SEMANTICA della richiesta, in sola osservazione (audit A2).

Il router valida la sintassi — JSON malformato, body non oggetto, campi
obbligatori assenti — e lo fa bene: 400 in circa 146 ms senza contattare
l'upstream. Non valida invece il SIGNIFICATO dei valori. L'audit del
2026-08-08 ha misurato:

    model = "modello-inesistente-xyz"  -> 200 in 5,77 s, 519 token fatturati
    model = "../../etc/passwd"         -> 200 in 8,52 s
    max_tokens = -5                    -> 200 in 5,30 s
    role = "hacker"                    -> 200 in 9,88 s

Un typo nel nome del modello non produce un errore ma una generazione
completa a pagamento. Il path traversal NON e' sfruttabile — il valore finisce
nel body JSON verso l'upstream, non in un percorso — ma dimostra che sul campo
`model` non c'e' nessuna allowlist.

## Perche' questo modulo NON rifiuta nulla

Il rifiuto e' deliberatamente assente. Un'allowlist troppo stretta romperebbe
modelli legittimi non previsti — i nomi cambiano spesso e da nove provider
diversi — e il danno sarebbe silenzioso e immediato. La modalita' `audit`
serve a raccogliere, sul traffico vero, le richieste che verrebbero rifiutate:
se dopo un periodo di osservazione l'elenco contiene solo errori reali, il
rifiuto si potra' attivare con cognizione di causa. Fino ad allora il
comportamento osservabile dal client e' identico a prima.

Modalita' via AIROUTER_VALIDATE_SEMANTIC:
    "audit" (default) -> valuta e ritorna i rilievi, il chiamante li logga
    "off"             -> non valuta nulla
Il valore "enforce" NON e' implementato: aggiungerlo e' una decisione da
prendere guardando i dati raccolti, non un interruttore da lasciare a portata.
"""
import os
import re

MODALITA_VALIDAZIONE = os.environ.get("AIROUTER_VALIDATE_SEMANTIC", "audit").strip().lower()

# Ruoli ammessi dall'API Anthropic per i messaggi. "system" non c'e': il system
# prompt e' un campo top-level, e un messaggio con role=system viene scartato a
# valle da _repair_message_sequence.
RUOLI_AMMESSI = frozenset({"user", "assistant"})

# Pattern dei nomi di modello noti, per provider. Volutamente larghi: servono a
# riconoscere una famiglia, non a fissare un elenco di versioni che invecchia.
PATTERN_MODELLI = (
    re.compile(r"^claude-[a-z0-9.\-]+$", re.I),        # Anthropic
    re.compile(r"^minimax-[a-z0-9.\-]+$", re.I),       # MiniMax
    re.compile(r"^glm-[a-z0-9.\-]+$", re.I),           # GLM / z.ai
    re.compile(r"^qwen[a-z0-9.\-]*$", re.I),           # Qwen / Alibaba
    re.compile(r"^(code|fast)-max$", re.I),            # locale via LiteLLM
    re.compile(r"^local:[a-z0-9.\-]+$", re.I),         # locale, forma esplicita
)

# Oltre questa lunghezza il valore non e' un nome di modello ma un payload.
MAX_LUNGHEZZA_MODELLO = 128


def _modello_riconosciuto(nome):
    return any(p.match(nome) for p in PATTERN_MODELLI)


def valuta(body_json, modalita=None):
    """Ritorna la lista dei rilievi semantici. Non solleva, non rifiuta.

    Ogni rilievo e' un dict {campo, valore, motivo}. Lista vuota = nessun
    rilievo, oppure validazione disattivata.
    """
    modalita = MODALITA_VALIDAZIONE if modalita is None else modalita.strip().lower()
    if modalita == "off":
        return []
    if not isinstance(body_json, dict):
        return []

    rilievi = []

    modello = body_json.get("model")
    if modello is None:
        pass  # assenza gia' coperta dalla validazione sintattica
    elif not isinstance(modello, str) or not modello.strip():
        rilievi.append({"campo": "model", "valore": repr(modello)[:80],
                        "motivo": "non e' una stringa non vuota"})
    elif len(modello) > MAX_LUNGHEZZA_MODELLO:
        rilievi.append({"campo": "model", "valore": modello[:40] + "...",
                        "motivo": f"lungo {len(modello)} caratteri, oltre il limite di {MAX_LUNGHEZZA_MODELLO}"})
    elif not _modello_riconosciuto(modello.strip()):
        rilievi.append({"campo": "model", "valore": modello[:80],
                        "motivo": "non corrisponde ad alcun pattern noto per provider"})

    if "max_tokens" in body_json:
        mt = body_json["max_tokens"]
        # I booleani sono int in Python: True passerebbe per 1 senza il controllo.
        if isinstance(mt, bool) or not isinstance(mt, int):
            rilievi.append({"campo": "max_tokens", "valore": repr(mt)[:40],
                            "motivo": "non e' un intero"})
        elif mt < 1:
            rilievi.append({"campo": "max_tokens", "valore": str(mt),
                            "motivo": "deve essere almeno 1"})

    messaggi = body_json.get("messages")
    if isinstance(messaggi, list):
        for indice, messaggio in enumerate(messaggi):
            if not isinstance(messaggio, dict):
                rilievi.append({"campo": f"messages[{indice}]",
                                "valore": repr(messaggio)[:40],
                                "motivo": "non e' un oggetto"})
                continue
            ruolo = messaggio.get("role")
            if ruolo not in RUOLI_AMMESSI:
                rilievi.append({"campo": f"messages[{indice}].role",
                                "valore": repr(ruolo)[:40],
                                "motivo": f"non e' fra {sorted(RUOLI_AMMESSI)}"})

    return rilievi


def descrivi(rilievi):
    """Riga sintetica per il log, vuota se non c'e' nulla da dire."""
    if not rilievi:
        return ""
    parti = [f"{r['campo']}={r['valore']} ({r['motivo']})" for r in rilievi]
    return "; ".join(parti)
