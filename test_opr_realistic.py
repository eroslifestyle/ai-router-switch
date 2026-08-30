#!/usr/bin/env python3
"""Test carico realistico sulla modalità opr (porta 8789) - versione ridotta per evitare rate limit."""
import asyncio
import json
import base64
from aiohttp import ClientSession, ClientTimeout
from datetime import datetime

OPR_PORT = 8789
OPR_URL = f"http://127.0.0.1:{OPR_PORT}/v1/messages"

# System prompt con cache_control
SYSTEM_PROMPT = [
    {
        "type": "text",
        "text": """Sei un assistente AI con accesso a strumenti avanzati. Puoi leggere file, eseguire comandi, navigare sul web e gestire code complesse.

ISTRUZIONI OPERATIVE:
1. LEGGI SEMPRE il file prima di modificarlo - usa Read con path assoluto
2. ESEGUI comandi solo dopo aver verificato la sicurezza - usa Bash
3. Non indovinare: verifica ogni path, comando, nome funzione
4. Citazione-fonte: ogni affermazione fattuale deve citare la fonte
5. Evidence-gate: mai dire "fatto" senza output letterale di prova
""" * 3  # ~1200 caratteri
    },
    {
        "type": "text",
        "text": "Questo blocco è cached per ottimizzare richieste ripetute.",
        "cache_control": {"type": "ephemeral"}
    }
]

# Tools array ridotto
TOOLS = [
    {
        "name": "read_file",
        "description": "Read an entire file from disk",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "bash_execute",
        "description": "Execute a bash command",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 120000}
            },
            "required": ["command"]
        }
    },
    {
        "name": "edit_file",
        "description": "Edit a file with exact string replacement",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"}
            },
            "required": ["file_path", "old_string", "new_string"]
        }
    }
]

# History con thinking block
MESSAGES = [
    {
        "role": "user",
        "content": "Leggi il file /tmp/test.txt e dimmi cosa contiene"
    },
    {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": "Devo leggere il file usando Read tool con path assoluto.",
                "signature": "sig_123"
            },
            {
                "type": "tool_use",
                "id": "toolu_001",
                "name": "read_file",
                "input": {"file_path": "/tmp/test.txt"}
            }
        ]
    },
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_001",
                "content": "contenuto del file di test"
            }
        ]
    },
    {
        "role": "assistant",
        "content": "Il file contiene: 'contenuto del file di test'"
    },
    {
        "role": "user",
        "content": "Ora esegui 'echo test > /tmp/output.txt'"
    }
]


async def test_opr_with_params():
    """Test con parametri che potrebbero causare errori."""
    print(f"\n=== TEST WITH PARAMS {datetime.now()} ===")

    payload = {
        "model": "ox-alpha",
        "max_tokens": 512,
        "system": SYSTEM_PROMPT,
        "messages": MESSAGES,
        "tools": TOOLS
    }

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    try:
        timeout = ClientTimeout(total=60)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(OPR_URL, json=payload, headers=headers) as resp:
                print(f"Status: {resp.status}")
                print(f"Headers: x-ai-verified={resp.headers.get('x-ai-verified')}, x-ai-actual-model={resp.headers.get('x-ai-actual-model')}")

                body = await resp.text()
                print(f"Body length: {len(body)}")

                if resp.status == 400:
                    print(f"400 BAD REQUEST - Body:\n{body[:1000]}")
                    # Controlla se è l'errore reasoning_effort
                    if "reasoning_effort" in body or "UnsupportedParamsError" in body:
                        print("⚠️  ERRORE: reasoning_effort o parametri non supportati presenti")
                        return False
                elif resp.status == 429:
                    print("⚠️  RATE LIMIT - OpenRouter momentaneamente saturato (non errore formato)")
                    return "rate_limited"
                elif resp.status == 200:
                    try:
                        result = json.loads(body)
                        if "error" in result:
                            print(f"ERROR: {result['error']}")
                            return False
                        print("✓ SUCCESS - Request processed")
                        return True
                    except json.JSONDecodeError:
                        print(f"INVALID JSON - Body preview:\n{body[:300]}")
                        return False
                else:
                    print(f"UNEXPECTED STATUS {resp.status}: {body[:500]}")
                    return False

    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
        return False


async def main():
    """Esegue il test."""
    print("=" * 60)
    print("TEST OPR PARAMS SANITIZATION")
    print("=" * 60)

    result = await test_opr_with_params()

    print("\n" + "=" * 60)
    if result == "rate_limited":
        print("RESULT: RATE LIMITED (OpenRouter temporarily unavailable)")
        print("The fix cannot be tested due to upstream rate limit.")
        print("Check later when ox-alpha is available.")
    elif result:
        print("RESULT: PASS - Request accepted and processed")
    else:
        print("RESULT: FAIL - Request rejected with error")
    print("=" * 60)

    return result in (True, "rate_limited")


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
