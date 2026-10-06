#!/usr/bin/env python3
"""MCP server qwen-native — capacità Qwen sul token-plan.

Scope token-plan (2026-10-06, verificato dal vivo):
- OCR / vision  -> deepseek-v4-flash via /apps/anthropic/v1/messages (Anthropic-format)
- web search    -> enable_search su /compatible-mode/v1/chat/completions
- web scrape     -> riuso di `websearch-local` locale (NON reimplementato)

I servizi multimediali nativi DashScope (image/video/tts/asr/embed/rerank) vivono sull'host
WORKSPACE aliyuncs e richiedono QWEN_WORKSPACE_ID, non configurato: restano STUB che
ritornano un errore chiaro invece di fallire in modo opaco.

Chiave: env QWEN_API_KEY, altrimenti `secret get qwen.QWEN_API_KEY` (vault TPM).
Endpoint: env QWEN_API_BASE (default token-plan). Il root compatible-mode è derivato.
"""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastmcp import FastMCP

DEFAULT_BASE = "https://token-plan.maas.qwencloudapi.com/apps/anthropic"
VISION_MODEL = os.environ.get("QWEN_VISION_MODEL", "deepseek-v4-flash")
SEARCH_MODEL = os.environ.get("QWEN_SEARCH_MODEL", "qwen3.8-flash")
TIMEOUT_SEC = float(os.environ.get("QWEN_MCP_TIMEOUT_SEC", "90"))
WORKSPACE_STUB = (
    "servizio non disponibile: la chiave token-plan è 401 su dashscope-intl (nativi Qwen "
    "richiedono una DASHSCOPE_API_KEY separata) e il backend locale corrispondente non è "
    "attivo. Fornisci una chiave Alibaba Model Studio, oppure avvia il backend locale."
)
# Immagini: backend LOCALE sd-cli via lo script imgforge della skill hulk-image
# (stable-diffusion.cpp, Vulkan, offline). Preset verificato: sdxl-turbo.
IMGFORGE = os.environ.get(
    "IMGFORGE_BIN", str(Path.home() / ".claude/skills/hulk-image/scripts/imgforge.sh")
)
IMAGE_PRESET = os.environ.get("QWEN_IMAGE_PRESET", "sdxl-turbo")
IMAGE_TIMEOUT_SEC = float(os.environ.get("QWEN_IMAGE_TIMEOUT_SEC", "600"))

mcp = FastMCP("qwen-native")


def _api_key() -> str:
    key = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if key:
        return key
    try:
        out = subprocess.run(
            ["secret", "get", "qwen.QWEN_API_KEY"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise RuntimeError("chiave Qwen assente: imposta QWEN_API_KEY o `secret set qwen.QWEN_API_KEY`")


def _anthropic_base() -> str:
    return os.environ.get("QWEN_API_BASE", DEFAULT_BASE).rstrip("/")


def _compatible_base() -> str:
    """Root OpenAI-compatible: stesso host del base Anthropic, path /compatible-mode/v1."""
    parts = urlsplit(_anthropic_base())
    return f"{parts.scheme}://{parts.netloc}/compatible-mode/v1"


def _image_block(image: str) -> dict:
    """Costruisce un content-block image da path locale, URL o base64 grezzo."""
    if image.startswith(("http://", "https://")):
        data = httpx.get(image, timeout=TIMEOUT_SEC).content
        media = "image/png" if image.lower().endswith(".png") else "image/jpeg"
        b64 = base64.b64encode(data).decode()
    elif Path(image).is_file():
        raw = Path(image).read_bytes()
        media = "image/png" if image.lower().endswith(".png") else "image/jpeg"
        b64 = base64.b64encode(raw).decode()
    else:
        b64, media = image, "image/png"  # già base64
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}


def _vision_call(image: str, prompt: str) -> str:
    body = {
        "model": VISION_MODEL,
        "max_tokens": 2048,
        # I modelli token-plan hanno thinking nativo che altrimenti consuma TUTTO il
        # budget (stop=max_tokens, zero testo). "disabled" funziona su questo endpoint.
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": [_image_block(image), {"type": "text", "text": prompt}]}],
    }
    headers = {"content-type": "application/json", "x-api-key": _api_key(), "anthropic-version": "2023-06-01"}
    r = httpx.post(f"{_anthropic_base()}/v1/messages", json=body, headers=headers, timeout=TIMEOUT_SEC)
    r.raise_for_status()
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


@mcp.tool
def qwen_ocr(image: str) -> str:
    """Estrae il testo da un'immagine (path locale, URL o base64). Usa la VLM Qwen token-plan.

    Nota: per OCR a costo-token-zero preferisci gli strumenti locali (tomd --ocr / vision_local);
    questo tool è l'alternativa cloud.
    """
    return _vision_call(image, "Estrai TUTTO il testo presente nell'immagine, verbatim, senza commenti.")


@mcp.tool
def qwen_vision(image: str, question: str) -> str:
    """Analizza/descrive un'immagine rispondendo a una domanda (VQA). image = path/URL/base64."""
    return _vision_call(image, question)


def _web_search(query: str) -> str:
    body = {
        "model": SEARCH_MODEL,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": query}],
        "enable_search": True,
    }
    headers = {"content-type": "application/json", "Authorization": f"Bearer {_api_key()}"}
    r = httpx.post(f"{_compatible_base()}/chat/completions", json=body, headers=headers, timeout=TIMEOUT_SEC)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


@mcp.tool
def qwen_web_search(query: str) -> str:
    """Ricerca web grounded via Qwen (enable_search): risposta con citazioni, non HTML grezzo."""
    return _web_search(query)


@mcp.tool
def web_scrape(target: str) -> str:
    """Scraping/ricerca web REALE riusando lo stack locale `websearch-local` (NON reimplementato).

    target = query o URL. Per aree protette/JS pesante usa la skill web-protected-harvest / playwright.
    """
    exe = os.environ.get("WEBSEARCH_LOCAL_BIN", "websearch-local")
    try:
        out = subprocess.run([exe, target], capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except FileNotFoundError:
        return f"`{exe}` non trovato nel PATH: installa websearch-local o imposta WEBSEARCH_LOCAL_BIN."
    except subprocess.TimeoutExpired:
        return "websearch-local: timeout."
    return out.stdout.strip() or (out.stderr.strip() or "nessun risultato")


def _image_generate(prompt: str, output_path: str = "", preset: str = "") -> str:
    """Genera un'immagine in LOCALE (sd-cli/imgforge). Ritorna il path del PNG prodotto."""
    if not Path(IMGFORGE).is_file():
        return f"imgforge non trovato: {IMGFORGE} (skill hulk-image assente?)"
    out = output_path or str(Path("/tmp/qwen-native") / f"img_{abs(hash(prompt)) % 10**8}.png")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            [IMGFORGE, preset or IMAGE_PRESET, prompt, out],
            capture_output=True, text=True, timeout=IMAGE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return f"timeout generazione immagine ({IMAGE_TIMEOUT_SEC}s)"
    if r.returncode != 0 or not Path(out).is_file():
        return f"errore sd-cli (rc={r.returncode}): {(r.stderr or r.stdout)[-300:]}"
    return out


@mcp.tool
def qwen_image(prompt: str, output_path: str = "", preset: str = "") -> str:
    """Genera un'immagine da testo in LOCALE (stable-diffusion.cpp, offline, zero cloud).

    preset: sdxl-turbo (default, veloce) | sdxl-lightning | flux2-klein | qwen-image (buono per testo).
    Ritorna il path del PNG. NON usa Qwen cloud (chiave token-plan non abilita i nativi DashScope).
    """
    return _image_generate(prompt, output_path, preset)


def _native_stub():
    def _tool() -> str:
        return WORKSPACE_STUB
    return _tool


# Nativi ancora bloccati: backend locale assente/giù (chatterbox TTS, whisper ASR, embed)
# oppure serve DASHSCOPE_API_KEY (video Qwen, rerank). Stub con messaggio accurato.
for _n in ("qwen_video", "qwen_tts", "qwen_asr", "qwen_embed", "qwen_rerank"):
    mcp.tool(name=_n, description=f"[STUB] {WORKSPACE_STUB}")(_native_stub())
# qwen_music: fun-music-v1 dà 404 sull'account anche col workspace -> stub permanente.
mcp.tool(name="qwen_music", description="[STUB] fun-music-v1 non esiste sull'account (404).")(_native_stub())


if __name__ == "__main__":
    mcp.run()
