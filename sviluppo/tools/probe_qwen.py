#!/usr/bin/env python3
"""Sonda l'account Alibaba Cloud Model Studio per stabilire fatti verificati."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from typing import Optional

MODELLI_CANDIDATI = [
    "qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.7-flash",
    "qwen3.6-plus", "qwen3.6-flash", "qwen3-max", "qwen3-coder-next",
    "qwen3-coder-plus", "qwen3-coder-flash", "qwen3-vl-plus",
    "qwen-plus", "qwen-flash"
]


def maschera_chiave(chiave: str) -> str:
    """Maschera una chiave API mostrando solo i primi 6 caratteri."""
    if not chiave:
        return "<non impostata>"
    if len(chiave) <= 6:
        return chiave[:3] + "..."
    return chiave[:6] + "..."


def leggi_segreto(nome: str) -> Optional[str]:
    """Legge un segreto dallo script secrets.sh."""
    secrets_path = os.path.expanduser("~/.claude/secrets/secrets.sh")
    if not os.path.isfile(secrets_path):
        return None
    try:
        result = subprocess.run(
            ["bash", secrets_path, "get", nome],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def ottieni_credenziali() -> tuple[Optional[str], Optional[str], str]:
    """Ottiene API key, workspace ID e regione."""
    api_key = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or leggi_segreto("qwen.api_key")
    workspace_id = os.environ.get("QWEN_WORKSPACE_ID") or leggi_segreto("qwen.workspace_id")
    region = os.environ.get("QWEN_REGION") or "ap-southeast-1"
    return api_key, workspace_id, region


def costruisci_base_url(workspace_id: Optional[str], region: str) -> str:
    """Costruisce l'URL base Anthropic-compatible."""
    if workspace_id:
        return f"https://{workspace_id}.{region}.maas.aliyuncs.com/apps/anthropic"
    return "https://dashscope-intl.aliyuncs.com/apps/anthropic"


def richiesta_http(url: str, method: str, headers: dict, data: Optional[bytes] = None, timeout: int = 30) -> tuple[int, str]:
    """Effettua una richiesta HTTP e restituisce status code e body."""
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except urllib.error.URLError as e:
        return 0, f"ERRORE RETE: {e.reason}"
    except TimeoutError:
        return 0, "TIMEOUT"


def testa_modello(base_url: str, api_key: str, modello: str, delay: float) -> dict:
    """Testa se un modello risponde all'endpoint /v1/messages."""
    time.sleep(delay)
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    body = json.dumps({
        "model": modello,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "ping"}]
    }).encode("utf-8")

    status, risposta = richiesta_http(url, "POST", headers, body)

    risultato = {"modello_richiesto": modello, "status": status, "risposta_breve": risposta[:200]}

    if status == 200:
        try:
            dati = json.loads(risposta)
            model_id_risposto = dati.get("model", modello)
            risultato["esito"] = "DISPONIBILE"
            risultato["model_id_reale"] = model_id_risposto
        except json.JSONDecodeError:
            risultato["esito"] = "DISPONIBILE (risposta non JSON)"
    elif status in (401, 403):
        risultato["esito"] = "AUTENTICAZIONE"
    elif status == 404:
        if "model" in risposta.lower() or "not found" in risposta.lower():
            risultato["esito"] = "ASSENTE"
        else:
            risultato["esito"] = f"ERRORE {status}"
    elif status == 429:
        risultato["esito"] = "RATE LIMIT"
    else:
        risultato["esito"] = f"ERRORE {status}"

    return risultato


def cerca_context_window(base_url: str, api_key: str, modello: str, delay: float) -> dict:
    """Cerca il context window prima cercando il limite dichiarato a costo zero
    e solo in subordine per accettazione."""
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    # 1 token = 5 caratteri (misurato 2026-08-03: 50.000 caratteri -> 10.010 token).
    CHAR_PER_TOKEN = 5
    # Il gateway accetta corpi fino a ~4,8 MB, rifiuta con HTTP 413 oltre ~6,7 MB.
    # Questo e' un limite ORTOGONALE al context window del modello.
    MAX_BODY_PROBE_BYTES = 4_800_000

    # Sonda preliminare a costo zero: invia un payload oversized che il gateway
    # rifiutera' con HTTP 400 dichiarando il limite effettivo. Il filler "a " vale
    # 1 token ogni 2 caratteri (misurato: 40.000 caratteri hanno prodotto 20.010 token).
    # 1.500.000 token danno un corpo di circa 2,86 MB, sopra ogni limite noto e sotto
    # il tetto byte di 4,8 MB.
    OVERSHOOT_TOKEN = 1_500_000
    PATTERN_RANGE = re.compile(r"should be \[1,\s*(\d+)\]")

    sys.stderr.write(f"  Sonda a costo zero con {OVERSHOOT_TOKEN} token...\n")
    parole_overshoot = "a " * OVERSHOOT_TOKEN
    body_overshoot = json.dumps({
        "model": modello,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": parole_overshoot}]
    }).encode("utf-8")

    status_overshoot, risposta_overshoot = richiesta_http(url, "POST", headers, body_overshoot, timeout=600)
    time.sleep(delay)

    if status_overshoot == 400:
        match = PATTERN_RANGE.search(risposta_overshoot)
        if match:
            limite_dichiarato = int(match.group(1))
            sys.stderr.write(f"  -> Limite dichiarato dal gateway a costo zero: {limite_dichiarato} token\n")
            return {"tipo": "DICHIARATO", "valore": limite_dichiarato, "fonte": "limite dichiarato dal gateway in HTTP 400, misura a costo zero"}
        else:
            sys.stderr.write("  HTTP 400 ma pattern non trovato, proseguo con scala\n")
    elif status_overshoot == 200:
        sys.stderr.write(f"  Attenzione: il modello ha accettato piu' di {OVERSHOOT_TOKEN} token, la sonda non e' conclusiva (costo input applicato)\n")
    else:
        sys.stderr.write(f"  Stato {status_overshoot} - {risposta_overshoot[:200]}\n")

    CANDIDATI = [1_000_000, 524_288, 262_144, 131_072, 65_536, 32_768]
    pattern_token = re.compile(r"(\d{1,3}(?:,\d{3})*|\d+)\s*(?:token|contesto|max)", re.IGNORECASE)

    for candidato in CANDIDATI:
        parole = "word " * candidato
        byte_stimati = candidato * CHAR_PER_TOKEN

        if byte_stimati > MAX_BODY_PROBE_BYTES:
            sys.stderr.write(f"  {candidato} token ({byte_stimati / 1_048_576:.2f} MB): salta - supera il limite byte del gateway\n")
            continue

        body = json.dumps({
            "model": modello,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": parole}]
        }).encode("utf-8")

        status, risposta = richiesta_http(url, "POST", headers, body, timeout=600)
        time.sleep(delay)

        if status == 200:
            dati = json.loads(risposta)
            input_tokens = dati.get("usage", {}).get("input_tokens")
            valore = input_tokens if input_tokens is not None else candidato
            fonte = "usage.input_tokens su richiesta realmente accettata" if input_tokens is not None else "usage non disponibile, uso candidato"
            sys.stderr.write(f"  {candidato} token ({byte_stimati / 1_048_576:.2f} MB): HTTP 200 - input_tokens={valore}\n")
            return {"tipo": "MISURATO", "valore": valore, "candidato": candidato, "fonte": fonte}

        if status == 400:
            sys.stderr.write(f"  {candidato} token ({byte_stimati / 1_048_576:.2f} MB): HTTP 400\n")
            if any(p in risposta.lower() for p in ["too long", "exceed", "maximum", "limit"]):
                match = pattern_token.search(risposta)
                if match:
                    numeri = re.sub(r",", "", match.group(1))
                    try:
                        token_certo = int(numeri)
                        sys.stderr.write(f"    -> limite trovato: {token_certo}\n")
                        return {"tipo": "CERTO", "valore": token_certo, "fonte": "messaggio d'errore upstream"}
                    except ValueError:
                        pass
                continue  # rifiuto per contesto: scendi al candidato successivo
            break
        elif status == 413:
            sys.stderr.write(f"  {candidato} token ({byte_stimati / 1_048_576:.2f} MB): HTTP 413 - salta (limite byte, non contesto)\n")
            continue
        else:
            sys.stderr.write(f"  {candidato} token ({byte_stimati / 1_048_576:.2f} MB): HTTP {status} - interrompi\n")
            break

    max_tentato = CANDIDATI[-1]
    return {"tipo": "INDETERMINATO", "min": 0, "max": max_tentato, "note": "nessun candidato accettato"}


def testa_streaming(base_url: str, api_key: str, modello: str, delay: float) -> dict:
    """Testa se l'endpoint supporta streaming."""
    time.sleep(delay)
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    body = json.dumps({
        "model": modello,
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": "ping"}]
    }).encode("utf-8")

    status, risposta = richiesta_http(url, "POST", headers, body)

    if status == 200:
        righe = risposta.split("\n")
        event_lines = [riga for riga in righe if riga.startswith("event:")]
        risultato = {"status": status, "esito": "OK", "righe_event": len(event_lines), "anteprima": risposta[:300]}
    else:
        risultato = {"status": status, "esito": "KO", "errore": risposta[:200]}

    return risultato


def testa_tools(base_url: str, api_key: str, modello: str, delay: float) -> dict:
    """Testa se l'endpoint supporta tools."""
    time.sleep(delay)
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    body = json.dumps({
        "model": modello,
        "max_tokens": 64,
        "messages": [
            {"role": "user", "content": "Che tempo fa a Roma?"}
        ],
        "tools": [{
            "name": "get_meteo",
            "description": "Meteo",
            "input_schema": {
                "type": "object",
                "properties": {"citta": {"type": "string"}},
                "required": ["citta"]
            }
        }]
    }).encode("utf-8")

    status, risposta = richiesta_http(url, "POST", headers, body)

    if status == 200 and "tool_use" in risposta:
        risultato = {"status": status, "esito": "OK", "note": "trovato blocco tool_use"}
    elif status == 200:
        risultato = {"status": status, "esito": "KO", "note": "nessun tool_use nella risposta"}
    else:
        risultato = {"status": status, "esito": "KO", "errore": risposta[:200]}

    return risultato


def testa_thinking(base_url: str, api_key: str, modello: str, delay: float) -> dict:
    """Testa se l'endpoint supporta thinking."""
    time.sleep(delay)
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    body = json.dumps({
        "model": modello,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "Quanto fa 2+2? Spiega il ragionamento."}],
        "thinking": {"type": "enabled", "budget_tokens": 1024}
    }).encode("utf-8")

    status, risposta = richiesta_http(url, "POST", headers, body)

    if status == 200 and "thinking" in risposta:
        risultato = {"status": status, "esito": "OK", "note": "trovato blocco thinking"}
    elif status == 200:
        risultato = {"status": status, "esito": "KO", "note": "nessun thinking nella risposta"}
    else:
        risultato = {"status": status, "esito": "KO", "errore": risposta[:200]}

    return risultato


def testa_cache_control(base_url: str, api_key: str, modello: str, delay: float) -> dict:
    """Testa se l'endpoint supporta cache_control."""
    time.sleep(delay)
    url = f"{base_url}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    system_block = [{"type": "text", "text": "Questo e' un testo di test ripetuto. " * 100, "cache_control": {"type": "ephemeral"}}]
    body = json.dumps({
        "model": modello,
        "max_tokens": 16,
        "system": system_block,
        "messages": [{"role": "user", "content": "Ciao."}]
    }).encode("utf-8")

    status, risposta = richiesta_http(url, "POST", headers, body)

    if status == 200:
        dati = json.loads(risposta) if risposta.startswith("{") else {}
        usage = dati.get("usage", {})
        if usage.get("cache_creation_input_tokens") or usage.get("cache_read_input_tokens"):
            risultato = {"status": status, "esito": "OK", "cache_creation": usage.get("cache_creation_input_tokens"), "cache_read": usage.get("cache_read_input_tokens")}
        else:
            risultato = {"status": status, "esito": "KO", "note": "nessun cache token in usage"}
    else:
        risultato = {"status": status, "esito": "KO", "errore": risposta[:200]}

    return risultato


def testa_v1_models(base_url: str, api_key: str, delay: float) -> dict:
    """Testa l'endpoint /v1/models."""
    time.sleep(delay)
    url = f"{base_url}/v1/models"
    headers = {"x-api-key": api_key}
    status, risposta = richiesta_http(url, "GET", headers)

    if status == 404:
        risultato = {"status": status, "esito": "OK_ATTESO", "note": "404 atteso, path non supportato"}
    else:
        risultato = {"status": status, "esito": f"STATUS_{status}", "risposta": risposta[:200]}

    return risultato


def testa_dashscope_native(dashscope_host: str, api_key: str, delay: float) -> list:
    """Testa i servizi nativi DashScope."""
    time.sleep(delay)
    results = []

    # I servizi nativi DashScope autenticano con Authorization: Bearer.
    # Con x-api-key rispondono 401 "No API-key provided" (solo l'endpoint
    # Anthropic-compatible accetta x-api-key).
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    url_img = f"{dashscope_host}/api/v1/services/aigc/multimodal-generation/generation"
    body_img = json.dumps({"model": "qwen-image-2.0-pro", "input": {}}).encode("utf-8")
    status, risp = richiesta_http(url_img, "POST", headers, body_img)
    results.append({
        "servizio": "multimodal-generation",
        "url": url_img,
        "status": status,
        "risposta": risp[:200]
    })

    time.sleep(delay)
    url_emb = f"{dashscope_host}/compatible-mode/v1/embeddings"
    body_emb = json.dumps({"model": "text-embedding-v4", "input": "ping"}).encode("utf-8")
    status, risp = richiesta_http(url_emb, "POST", headers, body_emb)
    results.append({
        "servizio": "embeddings",
        "url": url_emb,
        "status": status,
        "risposta": risp[:200]
    })

    return results


def genera_azioni_codice(modelli_disponibili: list, context_map: dict) -> list:
    """Genera la lista delle azioni da compiere sul codice."""
    azioni = []

    azioni.append({
        "file": "src/role_routing.py",
        "note": "QWEN_THINK / QWEN_ACT",
        "dettaglio": "Verificare che i model ID scelti esistano in base ai risultati del TEST 1"
    })

    azioni.append({
        "file": "src/model_context_map.py",
        "note": "context window per modelli qwen",
        "dettaglio": "Aggiornare con i valori misurati nel TEST 2" if context_map else "Nessun context map disponibile (usa --deep)"
    })

    azioni.append({
        "file": "src/qwen_backend.py",
        "note": "QWEN_MODEL_* per servizi nativi",
        "dettaglio": "Aggiornare in base ai risultati del TEST 4"
    })

    return azioni


def formatta_report(risultati: dict, maschera: str) -> str:
    """Formatta il report leggibile."""
    lines = []
    lines.append("=" * 70)
    lines.append("REPORT SONDAGGIO ALIBABA CLOUD MODEL STUDIO")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"API Key: {maschera}")
    lines.append(f"Base URL: {risultati['base_url']}")
    lines.append("")

    lines.append("-" * 70)
    lines.append("TEST 1 - MODELLI DISPONIBILI")
    lines.append("-" * 70)
    for r in risultati["test_modelli"]:
        status_simbolo = {"DISPONIBILE": "✓", "ASSENTE": "✗", "AUTENTICAZIONE": "!", "RATE LIMIT": "@", "ERRORE 429": "@"}.get(r["esito"], "?")
        line = f"  [{status_simbolo}] {r['modello_richiesto']}"
        if r.get("model_id_reale") and r["model_id_reale"] != r["modello_richiesto"]:
            line += f" -> {r['model_id_reale']}"
        line += f" ({r['esito']}, HTTP {r['status']})"
        lines.append(line)
    lines.append("")

    modelli_ok = [r for r in risultati["test_modelli"] if r["esito"] == "DISPONIBILE"]
    if modelli_ok:
        lines.append(f"Modelli DISPONIBILI: {[r['model_id_reale'] or r['modello_richiesto'] for r in modelli_ok]}")
    lines.append("")

    if risultati.get("test_context"):
        lines.append("-" * 70)
        lines.append("TEST 2 - CONTEXT WINDOW")
        lines.append("-" * 70)
        for modello, ctx in risultati["test_context"].items():
            if ctx["tipo"] in ("CERTO", "MISURATO", "DICHIARATO"):
                lines.append(f"  {modello}: {ctx['valore']} token (fonte: {ctx['fonte']})")
            else:
                lines.append(f"  {modello}: fra {ctx['min']} e {ctx['max']} token ({ctx['note']})")
        lines.append("")

    if risultati.get("test_capacita"):
        lines.append("-" * 70)
        lines.append("TEST 3 - CAPACITA' ENDPOINT")
        lines.append("-" * 70)
        modello_testa = modelli_ok[0]["model_id_reale"] or modelli_ok[0]["modello_richiesto"] if modelli_ok else "N/A"
        cap = risultati["test_capacita"]
        lines.append(f"  Modello usato per test: {modello_testa}")
        lines.append(f"  Streaming:     {cap['streaming']['esito']} (HTTP {cap['streaming']['status']})")
        lines.append(f"  Tools:         {cap['tools']['esito']}")
        lines.append(f"  Thinking:      {cap['thinking']['esito']}")
        lines.append(f"  Cache Control: {cap['cache_control']['esito']}")
        lines.append(f"  /v1/models:    {cap['v1_models']['esito']} (HTTP {cap['v1_models']['status']})")
        lines.append("")

    if risultati.get("test_dashscope"):
        lines.append("-" * 70)
        lines.append("TEST 4 - SERVIZI NATIVI DASHSCOPE")
        lines.append("-" * 70)
        for ds in risultati["test_dashscope"]:
            path_ok = "PATH_VALIDO" if ds["status"] == 400 else ("PATH_SBAGLIATO" if ds["status"] == 404 else f"HTTP_{ds['status']}")
            lines.append(f"  {ds['servizio']}: HTTP {ds['status']} ({path_ok})")
            lines.append(f"    URL: {ds['url']}")
            lines.append(f"    Risposta: {ds['risposta'][:150]}...")
        lines.append("")

    lines.append("=" * 70)
    lines.append("AZIONI SUL CODICE")
    lines.append("=" * 70)
    for azione in risultati.get("azioni", []):
        lines.append(f"  File: {azione['file']}")
        lines.append(f"    Cosa: {azione['note']}")
        lines.append(f"    Dettaglio: {azione['dettaglio']}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sonda Alibaba Cloud Model Studio")
    parser.add_argument("--models", help="Modelli da testare (default: lista interna)")
    parser.add_argument("--delay", type=float, default=0.5, help="Ritardo tra richieste (default: 0.5)")
    parser.add_argument("--deep", action="store_true", help="Esegue test context window approfondito")
    parser.add_argument("--json", action="store_true", help="Output solo JSON")
    args = parser.parse_args()

    api_key, workspace_id, region = ottieni_credenziali()
    if not api_key:
        sys.stderr.write("ERRORE: Chiave API non trovata. Imposta QWEN_API_KEY, DASHSCOPE_API_KEY o esegui secrets.sh.\n")
        sys.exit(2)

    base_url = costruisci_base_url(workspace_id, region)
    # Host DEDICATO del workspace: il CSV delle credenziali emesso dalla console
    # riporta dashScope = https://{ws}.{region}.maas.aliyuncs.com/api/v1. Usare
    # il generico dashscope-intl punta al tenant sbagliato e risponde 401.
    dashscope_host = os.environ.get("QWEN_DASHSCOPE_HOST") or (
        f"https://{workspace_id}.{region}.maas.aliyuncs.com" if workspace_id
        else "https://dashscope-intl.aliyuncs.com")

    modelli = args.models.split(",") if args.models else MODELLI_CANDIDATI

    risultati = {
        "base_url": base_url,
        "workspace_id": workspace_id,
        "region": region,
        "api_key_masked": maschera_chiave(api_key),
        "test_modelli": [],
        "test_context": {},
        "test_capacita": None,
        "test_dashscope": [],
        "azioni": []
    }

    print("Sondaggio in corso...", file=sys.stderr)

    for modello in modelli:
        r = testa_modello(base_url, api_key, modello, args.delay)
        risultati["test_modelli"].append(r)
        print(f"  {modello}: {r['esito']}", file=sys.stderr)

    modelli_disponibili = [r for r in risultati["test_modelli"] if r["esito"] == "DISPONIBILE"]

    if args.deep and modelli_disponibili:
        print("\nTest context window in corso...", file=sys.stderr)
        for r in modelli_disponibili:
            mod = r["model_id_reale"] or r["modello_richiesto"]
            print(f"  {mod}...", file=sys.stderr, end=" ", flush=True)
            ctx = cerca_context_window(base_url, api_key, mod, args.delay)
            risultati["test_context"][mod] = ctx
            print("fatto", file=sys.stderr)

    if modelli_disponibili:
        modello_testa = modelli_disponibili[0]["model_id_reale"] or modelli_disponibili[0]["modello_richiesto"]
        print("\nTest capacita' endpoint...", file=sys.stderr)

        risultati["test_capacita"] = {
            "streaming": testa_streaming(base_url, api_key, modello_testa, args.delay),
            "tools": testa_tools(base_url, api_key, modello_testa, args.delay),
            "thinking": testa_thinking(base_url, api_key, modello_testa, args.delay),
            "cache_control": testa_cache_control(base_url, api_key, modello_testa, args.delay),
            "v1_models": testa_v1_models(base_url, api_key, args.delay)
        }

    print("\nTest servizi nativi DashScope...", file=sys.stderr)
    risultati["test_dashscope"] = testa_dashscope_native(dashscope_host, api_key, args.delay)

    risultati["azioni"] = genera_azioni_codice(modelli_disponibili, risultati["test_context"])

    if args.json:
        print(json.dumps(risultati, indent=2, ensure_ascii=False))
    else:
        print(formatta_report(risultati, maschera_chiave(api_key)))


if __name__ == "__main__":
    main()
