"""
GLM/z.ai Backend Module for AI Router Proxy.
Handles tiering, classification, and rate limiting for GLM models.
"""
import asyncio
import json
import os
import time
import random
import subprocess
from pathlib import Path
from collections import deque
from typing import Optional, Dict, Any, List, Tuple

from peak_scheduler import is_peak_hour, peak_tier_cap, cost_multiplier, should_block_glm_model

GLM_UPSTREAM = os.getenv("AIROUTER_GLM_UPSTREAM", "https://api.z.ai/api/anthropic")
GLM_TIER_TURBO = os.getenv("AIROUTER_GLM_TIER_TURBO", "glm-5-turbo")
GLM_TIER_MID = os.getenv("AIROUTER_GLM_TIER_MID", "glm-4.7")
GLM_TIER_TOP = os.getenv("AIROUTER_GLM_TIER_TOP", "glm-5.2")
GLM_TIERS = [GLM_TIER_TURBO, GLM_TIER_MID, GLM_TIER_TOP]
GLM_REASONING = {GLM_TIER_TURBO: "low", GLM_TIER_MID: "medium", GLM_TIER_TOP: "high"}
GLM_CLASSIFIER_MODEL = os.getenv("AIROUTER_GLM_CLASSIFIER", GLM_TIER_TOP)
GLM_RPM_LIMIT = int(os.getenv("AIROUTER_GLM_RPM", "60"))
GLM_TPM_LIMIT = int(os.getenv("AIROUTER_GLM_TPM", "2000000"))
GLM_SAFETY = float(os.getenv("AIROUTER_GLM_SAFETY", "0.8"))
KEY_FILE = Path.home() / ".claude" / "secrets" / "secrets.sh"
GLM_ALERTS_LOG = os.path.expanduser("~/.claude/logs/glm-alerts.log")

_ANTHROPIC_BLOCKED = "__ANTHROPIC__"

_key_cache: Dict[str, Any] = {"key": None, "ts": 0}

GLM_LIMITER = None


def _log_if(log_fn, msg):
    if log_fn:
        try:
            log_fn(msg)
        except Exception:
            pass


async def get_glm_key(log_fn=None) -> str:
    """
    Retrieves GLM API key from environment or secrets file.
    Caches result for 60 seconds.
    """
    now = time.time()
    if _key_cache["key"] and (now - _key_cache["ts"]) < 60:
        return _key_cache["key"]
    key = os.getenv("GLM_API_KEY", "").strip()
    if key:
        _key_cache["key"] = key
        _key_cache["ts"] = now
        return key
    try:
        key = await asyncio.to_thread(
            lambda: subprocess.check_output(
                ["bash", str(KEY_FILE), "get", "glm.api_key"],
                timeout=5,
                text=True
            ).strip()
        )
        _key_cache["key"] = key
        _key_cache["ts"] = now
        return key
    except Exception as e:
        _log_if(log_fn, f"[GLM] Failed to get key: {e}")
        return ""


def build_glm_body(orig_body: bytes, model: str, force_stream: Optional[bool] = None) -> bytes:
    """
    Builds GLM-compatible request body from original Anthropic body.
    Adds reasoning_effort, removes incompatible fields.
    """
    try:
        body = json.loads(orig_body)
    except Exception:
        return orig_body
    body["model"] = model
    if model in GLM_REASONING:
        body["reasoning_effort"] = GLM_REASONING[model]
    if force_stream is not None:
        body["stream"] = force_stream
    for field in ["context_management", "output_config", "mcp_servers"]:
        body.pop(field, None)
    try:
        return json.dumps(body).encode()
    except Exception:
        return orig_body


async def forward_glm(request, body: bytes, session, model: str, log_fn=None) -> Any:
    """
    Forwards request to GLM upstream with proper headers and auth.
    """
    key = await get_glm_key(log_fn)
    if not key:
        raise RuntimeError("[GLM] No API key available")
    path = getattr(request, "path_qs", getattr(request, "path", "/v1/messages"))
    url = GLM_UPSTREAM + path
    headers = {}
    hop_headers = {
        "host", "content-length", "authorization", "x-api-key",
        "connection", "keep-alive", "transfer-encoding", "upgrade"
    }
    for k, v in request.headers.items():
        if k.lower() not in hop_headers and not k.lower().startswith("proxy-"):
            headers[k] = v
    headers["Authorization"] = f"Bearer {key}"
    headers.setdefault("anthropic-version", "2023-06-01")
    new_body = build_glm_body(body, model)
    try:
        entry = await GLM_LIMITER.acquire(len(new_body), 30.0) if GLM_LIMITER else None
        resp = await session.request(
            request.method, url, data=new_body, headers=headers, allow_redirects=False
        )
        if GLM_LIMITER and entry is not None:
            GLM_LIMITER.record(entry, resp.headers.get("x-token-count", "0"), resp.status < 400)
        return resp
    except Exception as e:
        if GLM_LIMITER and entry is not None:
            GLM_LIMITER.record(entry, 0, False)
        _log_if(log_fn, f"[GLM] Forward error: {e}")
        raise


def heuristic_tier(orig_body: bytes) -> str:
    """
    Local zero-token heuristic to estimate request complexity tier.
    Returns GLM tier model name.
    """
    try:
        body = json.loads(orig_body)
    except Exception:
        return GLM_TIER_MID
    score = 0
    if len(orig_body) > 40000:
        score += 1
    messages = body.get("messages", [])
    if len(messages) > 20:
        score += 1
    if body.get("tools"):
        score += 2
    msg_text = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                msg_text = content
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        msg_text = p.get("text", "")
                        break
            break
    for marker in ["```", "tool_result", "tool_use"]:
        if marker in msg_text:
            score += 1
            break
    hard_keywords = ["refactor", "architettura", "architecture", "debug",
                     "exploit", "sicurezza", "security", "ottimizza",
                     "optimize", "algoritmo", "concurrency", "race condition",
                     "redesign", "migration", "refactoring", "vulnerability"]
    easy_keywords = ["formatta", "format", "rinomina", "rename",
                     "traduci", "translate", "spiega brevemente", "lista", "list",
                     "summarize", "riassumi", "capitalize"]
    for kw in hard_keywords:
        if kw.lower() in msg_text.lower():
            score += 2
            break
    for kw in easy_keywords:
        if kw.lower() in msg_text.lower():
            score -= 1
            break
    if body.get("thinking") or body.get("reasoning_effort") in ("high", "max"):
        score += 2
    if score >= 2:
        return GLM_TIER_TOP
    elif score == 1:
        return GLM_TIER_MID
    else:
        return GLM_TIER_TURBO


async def _call_glm_json(
    body_dict: Dict[str, Any],
    session,
    log_fn=None,
    timeout: float = 15.0
) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """
    Helper to call GLM with JSON body and return parsed response.
    """
    key = await get_glm_key(log_fn)
    if not key:
        return None, None
    url = GLM_UPSTREAM + "/v1/messages"
    headers = {
        "Authorization": f"Bearer {key}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    try:
        resp = await asyncio.wait_for(
            session.request("POST", url, json=body_dict, headers=headers),
            timeout=timeout
        )
        status = resp.status
        try:
            data = await resp.json()
        except Exception:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                data = None
        return status, data
    except asyncio.TimeoutError:
        _log_if(log_fn, "[GLM] Classifier timeout")
        return None, None
    except Exception as e:
        _log_if(log_fn, f"[GLM] Classifier error: {e}")
        return None, None


async def classify_tier(orig_body: bytes, request, session, log_fn=None) -> str:
    """
    Classifies request complexity using GLM-5.2 classifier.
    Falls back to heuristic on failure or ambiguity.
    """
    try:
        body = json.loads(orig_body)
    except Exception:
        return heuristic_tier(orig_body)
    messages = body.get("messages", [])
    task_text = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                task_text = content
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        task_text = p.get("text", "")
                        break
            break
    task_text = task_text[:2000]
    system_prompt = (
        "Sei un classificatore di complessita. Rispondi SOLO con una parola: "
        "SEMPLICE, MEDIO, o COMPLESSO. SEMPLICE=formattazione/rinomina/traduzioni banali. "
        "MEDIO=modifiche localizzate, domande tecniche. COMPLESSO=refactor multi-file, "
        "architettura, debug difficile, sicurezza/exploit, task agentici con molti tool."
    )
    classifier_body = {
        "model": GLM_CLASSIFIER_MODEL,
        "max_tokens": 20,
        "stream": False,
        "system": system_prompt,
        "messages": [{"role": "user", "content": task_text}]
    }
    status, data = await _call_glm_json(classifier_body, session, log_fn, timeout=15.0)
    if status is None or data is None:
        result = heuristic_tier(orig_body)
        _log_if(log_fn, f"[GLM] Classifier fallback -> {result}")
        return result
    content = ""
    if data.get("type") == "message" and data.get("content"):
        for block in data["content"]:
            if block.get("type") == "text":
                content = block.get("text", "")
                break
    content_lower = content.lower().strip()
    if "complesso" in content_lower or "complex" in content_lower:
        result = GLM_TIER_TOP
    elif "medio" in content_lower or "medium" in content_lower:
        result = GLM_TIER_MID
    elif "semplice" in content_lower or "simple" in content_lower:
        result = GLM_TIER_TURBO
    else:
        result = heuristic_tier(orig_body)
        _log_if(log_fn, f"[GLM] Classifier ambiguous '{content}' -> heuristic {result}")
        return result
    _log_if(log_fn, f"[GLM] Classifier -> {result}")
    return result


def apply_peak_cap(chosen_model: str, now=None) -> Tuple[str, bool]:
    """
    Applica il cap orario peak (delega a peak_scheduler).
    `now` è un datetime opzionale (NON un float): se None, gli helper scheduler
    usano l'ora corrente nel fuso PEAK_TZ. Ritorna (modello_effettivo, is_capped).
    In peak i modelli 3x vengono degradati o reindirizzati ad Anthropic.
    """
    if not is_peak_hour(now):
        return chosen_model, False
    if chosen_model == _ANTHROPIC_BLOCKED:
        return _ANTHROPIC_BLOCKED, True
    # In peak, solo i modelli 3x (glm-5.2, glm-5-turbo) sono bloccati.
    # Regola (decisione utente 2026-07-10):
    #   TOP (5.2, task complesso) bloccato -> Anthropic esegue (qualità)
    #   TURBO (5-turbo, task semplice) bloccato -> degrada a MID (glm-4.7, non-3x)
    #   MID (4.7) non è 3x -> resta sempre usabile in peak
    if should_block_glm_model(chosen_model):
        if chosen_model == GLM_TIER_TOP:
            return _ANTHROPIC_BLOCKED, True
        # qualsiasi altro 3x (turbo o modelli extra da env) -> cap al tier MID
        return GLM_TIER_MID, True
    return chosen_model, False


class GlmRateLimiter:
    """
    Async sliding window rate limiter for GLM (60s window).
    Tracks RPM and TPM with safety multiplier.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._window = 60.0
        rpm_raw = int(GLM_RPM_LIMIT * GLM_SAFETY)
        tpm_raw = int(GLM_TPM_LIMIT * GLM_SAFETY)
        self._rpm_limit = max(rpm_raw, 1)
        self._tpm_limit = max(tpm_raw, 1000)
        self._requests: deque = deque()
        self._tokens: deque = deque()
        self._backoff: List[float] = [5, 10, 20, 40, 60]
        self._backoff_idx: int = 0
        self._cooldown_until: float = 0.0

    def _clean_window(self, now: float) -> Tuple[int, int]:
        """Returns (req_count, token_count) in current window."""
        cutoff = now - self._window
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < cutoff:
            self._tokens.popleft()
        return len(self._requests), sum(t[1] for t in self._tokens)

    async def acquire(self, est_tokens: int, budget_sec: float) -> Optional[dict]:
        """
        Acquires rate limit slot. Returns entry dict or None if blocked.
        """
        async with self._lock:
            now = time.time()
            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                if wait > budget_sec:
                    return None
                await asyncio.sleep(wait)
                now = time.time()
            req_count, tok_count = self._clean_window(now)
            if req_count >= self._rpm_limit or tok_count + est_tokens > self._tpm_limit:
                wait_time = self._window - (now - self._requests[0]) if self._requests else self._window
                if budget_sec <= 0 or wait_time > budget_sec:
                    return None
                await asyncio.sleep(min(wait_time, budget_sec))
                now = time.time()
                req_count, tok_count = self._clean_window(now)
                if req_count >= self._rpm_limit or tok_count + est_tokens > self._tpm_limit:
                    return None
            entry = {"ts": now, "est_tokens": est_tokens}
            self._requests.append(now)
            self._tokens.append((now, est_tokens))
            return entry

    def record(self, entry: Optional[dict], actual_tokens: Any, success: bool):
        """Records actual token usage after request completes."""
        if entry is None:
            return
        try:
            actual = int(actual_tokens) if actual_tokens else entry.get("est_tokens", 0)
        except (ValueError, TypeError):
            actual = entry.get("est_tokens", 0)
        if not success:
            entry["failed"] = True

    def on_429(self):
        """Handles 429 response with exponential backoff."""
        self._backoff_idx = min(self._backoff_idx + 1, len(self._backoff) - 1)
        self._cooldown_until = time.time() + self._backoff[self._backoff_idx]

    def snapshot(self) -> Dict[str, Any]:
        """Returns current rate limit state."""
        now = time.time()
        req_count, tok_count = self._clean_window(now)
        return {
            "rpm_used": req_count,
            "rpm_limit": self._rpm_limit,
            "tpm_used": tok_count,
            "tpm_limit": self._tpm_limit,
            "backoff_idx": self._backoff_idx,
            "cooldown_until": max(0, self._cooldown_until - now)
        }


def classify_429_glm(raw: bytes) -> str:
    """
    Classifies 429 error type for GLM.
    Returns 'quota_5h' for 5-hour quota errors, else 'rpm'.
    """
    try:
        text = raw[:2000].lower()
    except Exception:
        return "rpm"
    markers = [b"quota", b"5 hour", b"5-hour", b"resets at", b"usage limit"]
    for marker in markers:
        if marker in text:
            return "quota_5h"
    return "rpm"


def glm_alert(msg: str):
    """
    Logs alert to file and sends desktop notification.
    """
    try:
        os.makedirs(os.path.dirname(GLM_ALERTS_LOG), exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(GLM_ALERTS_LOG, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["notify-send", "-u", "normal", "-t", "20000", "GLM Quota", msg[:300]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


def _init_limiter():
    global GLM_LIMITER
    if GLM_LIMITER is None:
        GLM_LIMITER = GlmRateLimiter()

_init_limiter()
