"""
Controllo di coerenza tra la mappa statica dei context e il catalogo Models API.

Questo script esiste per intercettare la classe di bug "modello Anthropic nuovo
non mappato", che cade sul default 200000 e viene compresso a 160000 dal gate.

PERCHE' LA MODELS API NON E' USATA A RUNTIME:
Misurato il 2026-07-28 su 112.244 richieste Anthropic reali, mappa e API
concordano al 99,97%. Introdurre rete e cache nell'hot path del proxy per
correggere lo 0,01% non e' giustificato. Inoltre i modelli fuori catalogo
perderebbero copertura: la mappa statica include modelli non ancora
disponibili nel nostro account. Quindi: fonte runtime = mappa, questo script
= solo controllo periodico offline.
"""

import sys
import json
import argparse
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Costanti di configurazione
API_URL = "https://api.anthropic.com/v1/models?limit=100"
HTTP_TIMEOUT_SEC = 20
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
MAX_CONCORDANZE_MOSTRATE = 10

# Codici di uscita: distinti per non confondere fail di rete con drift reale
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_UNAVAILABLE = 2

# Modelli che legittimamente non compaiono nel catalogo del nostro account.
# Sono nomi di chiave della mappa, non ID di catalogo.
NOT_IN_CATALOG = frozenset({
    "opus",
    "sonnet",
    "haiku",
    "claude-sonnet-4-7",
    "claude-sonnet-4-8",
    "sonnet-4-7",
    "sonnet-4-8",
    "claude-mythos-5",
    "claude-mythos-preview",
})

# Prefissi delle famiglie Anthropic usati anche senza "claude-" davanti.
ALIAS_PREFIXES = ("opus", "sonnet", "haiku", "fable", "mythos")

# Header fissi per la chiamata API
HEADER_BETA = "oauth-2025-04-20"
HEADER_VERSION = "2023-06-01"


def _read_oauth_token() -> str:
    """
    Legge il token OAuth dalle credentials locali.

    NON importa router_auth per evitare di trascinare l'intera catena del proxy
    (router_constants, dipendenze, ecc.) dentro uno script che deve funzionare
    offline e indipendentemente.
    """
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            credentials = json.load(f)
        return credentials.get("claudeAiOauth", {}).get("accessToken", "")
    except FileNotFoundError as e:
        print(f"ERRORE: credentials non trovate - {e}", file=sys.stderr)
        return ""
    except json.JSONDecodeError as e:
        print(f"ERRORE: credentials non valide (JSON malformato) - {e}", file=sys.stderr)
        return ""


def fetch_catalog(token: str) -> dict[str, int]:
    """
    Scarica l'intero catalogo dei modelli dall'API Anthropic con paginazione.

    Returns:
        Dizionario {id_model: max_input_tokens} per i modelli che hanno
        il campo max_input_tokens valorizzato.

    Raises:
        URLError: errore di rete (host irraggiungibile, timeout, ecc.)
        HTTPError: risposta HTTP con codice >= 400
    """
    catalog: dict[str, int] = {}
    after_id: str | None = None

    while True:
        url = API_URL
        if after_id:
            url = f"{API_URL}&after_id={after_id}"

        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": HEADER_BETA,
                "anthropic-version": HEADER_VERSION,
            },
        )

        with urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
            data = json.load(response)

        # Estrae i max_input_tokens disponibili
        for item in data.get("data", []):
            max_tokens = item.get("max_input_tokens")
            if max_tokens is not None:
                catalog[item["id"]] = max_tokens

        # Gestisce la paginazione: continua finche' ci sono altre pagine
        if data.get("has_more", False):
            after_id = data.get("last_id")
            if not after_id:
                break
        else:
            break

    return catalog


def is_anthropic_key(name: str) -> bool:
    """
    Determina se una chiave della mappa appartiene al dominio Anthropic.

    Filtra via le chiavi MiniMax e GLM che sono gestite manualmente dal progetto
    e non devono essere confrontate con il catalogo Anthropic.
    """
    name_lower = name.lower()
    if "claude" in name_lower:
        return True
    # Alias senza il prefisso "claude-": la mappa contiene sia "sonnet-4-6" sia
    # "claude-sonnet-4-6". Cercare solo "claude" lasciava fuori dal controllo
    # tre chiavi Anthropic a tutti gli effetti (sonnet-4-6/4-7/4-8).
    return name_lower.startswith(ALIAS_PREFIXES)


def find_map_key(model_id: str, static_map: dict[str, int]) -> str | None:
    """
    Cerca la chiave nella mappa statica che corrisponde a un ID del catalogo.

    Replica la logica di get_context_limit per la direzione catalogo -> mappa:
    - match esatto case-insensitive sulla chiave;
    - altrimenti la chiave PIU' LUNGA tale che model_id.lower() inizi con
      quella chiave in lowercase.

    Args:
        model_id: ID del modello dal catalogo (es. "claude-opus-4-5-20251101")
        static_map: mappa statica MODEL_CONTEXT_MAP

    Returns:
        La chiave trovata, oppure None se nessun match.
        Esempio: "claude-opus-4-5-20251101" -> "claude-opus-4-5"
    """
    model_lower = model_id.lower()

    # Tentativo 1: match esatto case-insensitive
    for key in static_map:
        if key.lower() == model_lower:
            return key

    # Tentativo 2: chiave piu' lunga tale che model_id inizi con la chiave
    best_key: str | None = None
    best_length = 0
    for key in static_map:
        key_lower = key.lower()
        if model_lower.startswith(key_lower):
            if len(key_lower) > best_length:
                best_length = len(key_lower)
                best_key = key

    return best_key


def find_catalog_entry(map_key: str, catalog: dict[str, int]) -> tuple[str, int] | None:
    """
    Cerca l'ID nel catalogo che corrisponde a una chiave della mappa.

    Direzione mappa -> catalogo:
    - match esatto case-insensitive sull'ID;
    - altrimenti l'ID PIU' CORTO del catalogo che INIZI con map_key in lowercase.

    Args:
        map_key: chiave della mappa statica (es. "claude-sonnet-4-5")
        catalog: catalogo dei modelli

    Returns:
        Tupla (id_trovato, max_tokens) oppure None se nessun match.
        Esempio: "claude-sonnet-4-5" -> ("claude-sonnet-4-5-20250929", 200000)
    """
    map_key_lower = map_key.lower()

    # Tentativo 1: match esatto case-insensitive
    for catalog_id, max_tokens in catalog.items():
        if catalog_id.lower() == map_key_lower:
            return catalog_id, max_tokens

    # Tentativo 2: ID piu' corto che inizia con map_key
    best_id: str | None = None
    best_length = float('inf')
    best_tokens: int = 0
    for catalog_id, max_tokens in catalog.items():
        catalog_lower = catalog_id.lower()
        if catalog_lower.startswith(map_key_lower):
            if len(catalog_id) < best_length:
                best_length = len(catalog_id)
                best_id = catalog_id
                best_tokens = max_tokens

    if best_id is not None:
        return best_id, best_tokens

    # Alias senza prefisso: la mappa scrive "sonnet-4-6", il catalogo
    # "claude-sonnet-4-6". Si ritenta una volta sola col prefisso aggiunto.
    # Sicuro perche' gli alias generici ("sonnet", "opus", "haiku") sono gia'
    # stati intercettati da NOT_IN_CATALOG prima di arrivare qui.
    if not map_key_lower.startswith("claude-"):
        return find_catalog_entry(f"claude-{map_key}", catalog)
    return None


def compare(static_map: dict[str, int], catalog: dict[str, int]) -> tuple[list[str], list[str], list[str]]:
    """
    Confronta la mappa statica con il catalogo e identifica le discrepanze.

    Returns:
        Tupla (errori, avvisi, concordanze) dove ogni elemento e' una lista
        di stringhe gia' formattate per la stampa.
    """
    errors: list[str] = []
    warnings: list[str] = []
    matches: list[str] = []

    # Raccolta delle chiavi Anthropic della mappa per il controllo
    anthropic_map_keys = [k for k in static_map if is_anthropic_key(k)]

    # CONTROLLO 1: modelli nel catalogo assenti dalla mappa
    # Questo e' il bug che lo script esiste per catturare
    for catalog_id in catalog:
        map_key = find_map_key(catalog_id, static_map)
        if map_key is None:
            errors.append(
                f"ERRORE: modello '{catalog_id}' presente nel catalogo "
                f"({catalog[catalog_id]:,} token) ma assente dalla mappa statica"
            )

    # CONTROLLO 2 e 3: chiavi Anthropic della mappa
    for key in anthropic_map_keys:
        expected_value = static_map[key]

        # NOT_IN_CATALOG va controllato PRIMA della ricerca: un alias generico
        # come "sonnet" verrebbe altrimenti agganciato per prefisso al primo
        # "claude-sonnet-*" del catalogo, producendo una divergenza inventata.
        if key in NOT_IN_CATALOG:
            warnings.append(
                f"AVVISO: chiave '{key}' fuori catalogo (atteso: modello "
                f"non disponibile nel nostro account)"
            )
            continue

        entry = find_catalog_entry(key, catalog)

        if entry is None:
            # Non dichiarata fuori catalogo e comunque non trovata: o il modello
            # e' stato ritirato, o la chiave e' scritta male. Va guardata a mano.
            warnings.append(
                f"AVVISO: chiave '{key}' ({expected_value:,} token) "
                f"non trovata nel catalogo - DA VERIFICARE"
            )
        else:
            catalog_id, catalog_value = entry
            if catalog_value != expected_value:
                # Valore divergente
                if expected_value > 0:
                    diff_percent = abs(catalog_value - expected_value) / expected_value * 100
                    diff_str = f" (diff {diff_percent:.1f}%)"
                else:
                    diff_str = ""
                errors.append(
                    f"ERRORE: valore divergente per '{key}': "
                    f"mappa={expected_value:,}, catalogo={catalog_value:,}{diff_str} "
                    f"[confronto con id catalogo '{catalog_id}']"
                )
            else:
                matches.append(f"OK: '{key}' - {expected_value:,} token")

    return errors, warnings, matches


def main() -> int:
    """
    Orchestratore: legge credentials, fetch del catalogo, confronto, report.

    Returns:
        Codice di uscita: EXIT_OK / EXIT_DRIFT / EXIT_UNAVAILABLE
    """
    parser = argparse.ArgumentParser(description="Controlla coerenza mappa context vs catalogo API")
    parser.add_argument("--quiet", action="store_true", help="Stampa solo errori e riepilogo")
    args = parser.parse_args()

    # Importazione della mappa statica (aggiunge src/ al path)
    script_dir = Path(__file__).resolve().parents[2]
    src_path = script_dir / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    try:
        from model_context_map import MODEL_CONTEXT_MAP
    except ImportError as e:
        print(f"ERRORE: impossibile importare MODEL_CONTEXT_MAP - {e}", file=sys.stderr)
        return EXIT_UNAVAILABLE

    # Lettura del token OAuth
    token = _read_oauth_token()
    if not token:
        print(
            "ERRORE: token OAuth non disponibile in ~/.claude/.credentials.json",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE

    # Fetch del catalogo (potrebbe fallire per rete/HTTP)
    try:
        catalog = fetch_catalog(token)
    except HTTPError as e:
        print(f"ERRORE HTTP {e.code} contattando l'API Anthropic: {e.reason}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except URLError as e:
        print(f"ERRORE di rete: impossibile contattare api.anthropic.com - {e.reason}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except Exception as e:
        print(f"ERRORE imprevisto durante il fetch: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_UNAVAILABLE

    # Esecuzione del confronto
    errors, warnings, matches = compare(MODEL_CONTEXT_MAP, catalog)

    # Stampa del report
    if not args.quiet:
        print("\n" + "=" * 60)
        print("CONTROLLO COERENZA: mappa statica vs catalogo Models API")
        print("=" * 60)

    if errors:
        print("\nERRORI:")
        for e in errors:
            print(f"  {e}")

    if warnings and not args.quiet:
        print("\nAVVISI:")
        for w in warnings:
            print(f"  {w}")

    if matches and not args.quiet:
        print("\nCONCORDANZE:")
        for m in matches[:MAX_CONCORDANZE_MOSTRATE]:
            print(f"  {m}")
        if len(matches) > MAX_CONCORDANZE_MOSTRATE:
            print(f"  ... e altri {len(matches) - MAX_CONCORDANZE_MOSTRATE} modelli")

    # Riepilogo finale
    print("\n" + "-" * 60)
    print(f"Riepilogo: {len(errors)} errori, {len(warnings)} avvisi, {len(matches)} concordanze")

    if errors:
        return EXIT_DRIFT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
