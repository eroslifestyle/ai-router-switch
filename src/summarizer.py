"""LLM summarization per messaggi troppo vecchi."""

import json, os, time, aiohttp, logging
from typing import Optional
from .model_context_map import get_summary_budget

log = logging.getLogger(__name__)

SUMMARY_DIR = "/tmp/ai-router-summary"
SUMMARY_TTL_SEC = 86400  # 24 ore

def _ensure_summary_dir():
    try:
        os.makedirs(SUMMARY_DIR, exist_ok=True)
    except Exception:
        pass

def get_summary_fp(fp: str) -> str:
    return f"{SUMMARY_DIR}/{fp}.json"

def load_cached_summary(fp: str) -> Optional[dict]:
    """Carica riassunto cachato se non scaduto."""
    _ensure_summary_dir()
    path = get_summary_fp(fp)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) > SUMMARY_TTL_SEC:
            os.remove(path)
            return None
        return data
    except Exception:
        return None

async def summarize_old_messages(
    messages: list,
    model: str,
    fp: str,
    upstream_url: str,
    api_key: str
) -> Optional[list]:
    """
    Il modello stesso che ha fallito riassume i messaggi vecchi.
    Ritorna nuova lista di messaggi con riassunto al posto dei vecchi.
    """
    _ensure_summary_dir()

    # Check cache
    cached = load_cached_summary(fp)
    if cached:
        return cached.get("summary_messages")

    budget = get_summary_budget(model)

    # Prepara i messaggi da riassumere (tutto tranne gli ultimi 6)
    if len(messages) <= 6:
        return messages

    to_summarize = messages[:-6]
    recent = messages[-6:]

    # Costruisci prompt per il modello
    summarize_prompt = (
        "Summarize the following conversation concisely, preserving ALL important facts, "
        f"decisions, code snippets, errors, and context. Max {budget} tokens output.\n\n"
        + "\n".join(f"[{m.get('role','?')}]: {str(m.get('content',''))[:500]}" for m in to_summarize)
    )

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                upstream_url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": budget,
                    "messages": [{"role": "user", "content": summarize_prompt}]
                },
                timeout=60
            ) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
                summary_text = result.get("content", [{}])[0].get("text", "")

                # Costruisci nuovo messages array
                summary_msg = {
                    "role": "system",
                    "content": f"[Context summary of {len(to_summarize)} earlier messages]:\n{summary_text[:budget * 4]}"
                }

                summary_messages = [summary_msg] + recent

                # Cache
                with open(get_summary_fp(fp), 'w') as f:
                    json.dump({
                        "ts": time.time(),
                        "summary_messages": summary_messages,
                        "original_count": len(messages),
                        "budget": budget
                    }, f)

                log.info(f"[summarizer] summarized {len(to_summarize)} msgs → {budget} tokens, model={model} fp={fp}")
                return summary_messages

    except Exception as e:
        log.warning(f"[summarizer] failed: {e}")
        return None
