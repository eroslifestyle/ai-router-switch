"""pipeline_common.py — Helper condivisi per retry verso Anthropic.

Fornisce logica certificata di retry su errori transienti (429/5xx), parsing di
retry-after (intero, float o data RFC 7231), e backoff esponenziale con equal jitter.
Usato da ai-router-proxy.py e pipeline Anthropic delle modalità mix per garantire
che i 429 transienti non rimbalzino al client in loop infinito.
"""

import asyncio
import os
import random


# ── Retry certificato SDK Anthropic (condiviso da tutte le leg Anthropic) ────────
# Riuso della logica del fix anthropic puro `e6fb4fb` (era duplicata in
# ai-router-proxy.py) così che le leg Anthropic delle modalità MIX (mix-am THINK,
# mix-ag THINK/VERIFY, e i rescue Haiku) usino lo STESSO backoff certificato invece
# di rimbalzare il 429 al client (che fa girare le chat all'infinito).
# Certificato da platform.claude.com/docs/en/api/errors + /rate-limits:
#   - gli SDK ufficiali ritentano i transienti (429/5xx) con exponential backoff,
#     due volte di default, onorando retry-after quando presente;
#   - retry-after = secondi d'attesa (le retry anticipate falliscono);
#   - x-should-retry:false ⇒ NON ritentare (definitivo).
# Questo modulo vive senza import di pipeline (solo stdlib) → nessun ciclo.
ANTHROPIC_MAX_RETRIES = int(os.environ.get("AIROUTER_ANTHROPIC_MAX_RETRIES", "2"))
ANTHROPIC_RETRY_BASE_SEC = float(os.environ.get("AIROUTER_ANTHROPIC_RETRY_BASE_SEC", "0.5"))
ANTHROPIC_RETRY_MAX_SLEEP_SEC = float(os.environ.get("AIROUTER_ANTHROPIC_RETRY_MAX_SLEEP_SEC", "60"))
ANTHROPIC_RETRIABLE_STATUSES = frozenset({429, 500, 502, 503, 504, 529})


def parse_retry_after(value: str):
    """retry-after può essere int, float, o data HTTP (RFC 7231). Ritorna secondi o None."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt is not None:
            import datetime as _dt
            now = _dt.datetime.now(_dt.timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return max(0.0, (dt - now).total_seconds())
    except Exception:
        pass
    return None


def backoff_sleep_sec(attempt: int, retry_after):
    """Delay del tentativo `attempt` (0-based). Onora retry-after se presente,
    altrimenti exponential backoff con equal jitter. Cap a MAX_SLEEP."""
    if retry_after is not None:
        return min(retry_after, ANTHROPIC_RETRY_MAX_SLEEP_SEC)
    exp = ANTHROPIC_RETRY_BASE_SEC * (2 ** attempt)
    exp = min(exp, ANTHROPIC_RETRY_MAX_SLEEP_SEC)
    return exp / 2 + random.uniform(0, exp / 2)


async def anthropic_call_with_retry(forward_fn, request, body, session,
                                    log_fn=None, tag: str = "anthropic"):
    """Chiama forward_fn(request, body, session) con retry certificato SDK sui
    429/5xx transienti. Ritorna (up, exhausted): `up` è la ClientResponse finale
    (aperta, da leggere/relayare); `exhausted` True se il transiente è persistito
    dopo tutti i retry. Il chiamante decide cosa fare del 429 persistente (relay
    onesto o fallback) — NON si rimbalza in loop verso il client.
    Solleva su eccezione di rete solo dopo l'ultimo tentativo."""
    last_up = None
    last_exc = None
    for attempt in range(ANTHROPIC_MAX_RETRIES + 1):
        try:
            up = await forward_fn(request, body, session)
        except Exception as e:
            last_exc = e
            if attempt >= ANTHROPIC_MAX_RETRIES:
                raise
            delay = backoff_sleep_sec(attempt, None)
            if log_fn:
                log_fn(f"{tag} EXC: retry {attempt+1}/{ANTHROPIC_MAX_RETRIES} "
                       f"sleep={delay:.2f}s ({e})")
            await asyncio.sleep(delay)
            continue
        if up.status not in ANTHROPIC_RETRIABLE_STATUSES:
            return up, False
        if str(up.headers.get("x-should-retry", "")).lower() == "false":
            return up, True
        if attempt >= ANTHROPIC_MAX_RETRIES:
            last_up = up
            break
        retry_after = parse_retry_after(up.headers.get("retry-after", ""))
        delay = backoff_sleep_sec(attempt, retry_after)
        if log_fn:
            log_fn(f"{tag} {up.status}: retry {attempt+1}/{ANTHROPIC_MAX_RETRIES} "
                   f"retry-after={retry_after} sleep={delay:.2f}s")
        try:
            up.release()
        except Exception:
            pass
        await asyncio.sleep(delay)
    if last_up is not None:
        return last_up, True
    raise last_exc if last_exc else RuntimeError(f"{tag}: nessun tentativo eseguito")
