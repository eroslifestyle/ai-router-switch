#!/usr/bin/env python3
"""Riproduce il 400 `Tool reference ... not found in available tools` e ne prova la chiusura.

Tre invii. Il **controllo negativo** referenzia un tool inesistente e non
brandizzato, che nessun filtro tocca: l'API deve rispondere 400, ed e' la prova
che l'errore e' davvero l'orfanita' del riferimento. Gli altri due mandano il
riferimento brandizzato, prima attraverso il router (che ora lo ripulisce) e poi
gia' filtrato in locale: entrambi 200. Col codice pre-`c3d16a8` il primo dei due
dava 400 — riprodurlo oggi richiederebbe una versione vecchia in esecuzione.

    python sviluppo/audit/2026-09-14-tool-reference-400/riproduci.py [porta]

Porta di default 8771 (modalita' anthropic). Con 8787 si prova il router live.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from tool_isolation import filter_tools_for_backend  # noqa: E402

PORTA_DEFAULT = 8771
TIMEOUT_SEC = 90
TOOL_ESTRANEO = "mcp__zai__web_search_prime"
TOOL_ORFANO = "tool_che_non_esiste"  # non brandizzato: nessun filtro lo rimuove

TOOLS = [
    {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
    {"name": TOOL_ESTRANEO, "description": "Ricerca web zai", "defer_loading": True,
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}},
    {"name": "leggi_file", "description": "Legge un file", "defer_loading": True,
     "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}}},
]


def costruisci_body(nome_tool: str = TOOL_ESTRANEO) -> dict:
    """History con un tool_reference annidato, nella forma vera dell'API."""
    ricerca = [
        {"type": "server_tool_use", "id": "srvtoolu_01WpJYjFZWfHXARL4ndTGT8b",
         "name": "tool_search_tool_regex", "input": {"pattern": "search|file"}},
        {"type": "tool_search_tool_result", "tool_use_id": "srvtoolu_01WpJYjFZWfHXARL4ndTGT8b",
         "content": {"type": "tool_search_tool_search_result", "tool_references": [
             {"type": "tool_reference", "tool_name": nome_tool},
             {"type": "tool_reference", "tool_name": "leggi_file"},
         ]}},
    ]
    return {"model": "claude-haiku-4-5-20251001", "max_tokens": 32, "tools": TOOLS,
            "messages": [{"role": "user", "content": "cerca i tool"},
                         {"role": "assistant", "content": ricerca},
                         {"role": "user", "content": "di' solo OK"}]}


def invia(body: dict, porta: int, etichetta: str) -> None:
    richiesta = urllib.request.Request(
        f"http://127.0.0.1:{porta}/v1/messages", data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "anthropic-beta": "advanced-tool-use-2025-11-20"})
    try:
        print(f"{etichetta}: HTTP {urllib.request.urlopen(richiesta, timeout=TIMEOUT_SEC).status}")
    except urllib.error.HTTPError as errore:
        print(f"{etichetta}: HTTP {errore.code} :: {errore.read().decode()[:170]}")


def main() -> None:
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else PORTA_DEFAULT
    invia(costruisci_body(TOOL_ORFANO), porta,
          "controllo negativo, riferimento orfano non brandizzato (400 atteso)")
    body = costruisci_body()
    invia(body, porta, "riferimento brandizzato via router (200: il router lo ripulisce)")
    filtrato = json.loads(filter_tools_for_backend(json.dumps(body).encode(), "anthropic"))
    riferimenti = filtrato["messages"][1]["content"][1]["content"]["tool_references"]
    print("   tools rimasti:", [t.get("name") for t in filtrato.get("tools", [])])
    print("   tool_references rimasti:", [r["tool_name"] for r in riferimenti])
    assert all(r["tool_name"] != TOOL_ESTRANEO for r in riferimenti), "riferimento estraneo sopravvissuto"
    invia(filtrato, porta, "stesso body gia' filtrato in locale (200 atteso)")


if __name__ == "__main__":
    main()
