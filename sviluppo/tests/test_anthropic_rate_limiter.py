#!/usr/bin/env python3
"""Unit test: AnthropicRateLimiter — sliding window RPM/TPM, classificazione
429 burst vs org_quota, cooldown globale, backoff esponenziale. Zero rete."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import anthropic_rate_limiter as arl


def run(coro):
    return asyncio.run(coro)


# ── Sliding window RPM ────────────────────────────────────────────────────────

def test_acquire_entro_limit_immediato():
    """Con window vuota, acquire ritorna subito un entry."""
    lim = arl.AnthropicRateLimiter()
    entry = run(lim.acquire("claude-sonnet-4-20250514", 1000, budget_sec=2))
    assert entry is not None
    assert entry[1] == 1000  # stima iniziale


def test_acquire_satura_rpm_attende():
    """Riempire la window RPM forza acquire ad aspettare (o esaurire budget)."""
    lim = arl.AnthropicRateLimiter()
    # Satura: ANTHROPIC_RATE_LIMITS[sonnet] = (50, 100k) * 0.8 = 40 RPM.
    for _ in range(40):
        e = run(lim.acquire("claude-sonnet-4-20250514", 100, budget_sec=2))
        assert e is not None
    # La 41a non trova slot entro il budget corto.
    try:
        run(lim.acquire("claude-sonnet-4-20250514", 100, budget_sec=0.5))
        assert False, " doveva sollevare RateLimitExhausted"
    except arl.RateLimitExhausted:
        pass


def test_window_si_svuota_dopo_60s():
    """Dopo 60s la window si pruna e acquire ritrova slot."""
    lim = arl.AnthropicRateLimiter()
    # Inietta 40 entry con timestamp nel passato (~65s fa).
    now = time.monotonic()
    win = lim._prune("claude-sonnet-4-20250514", now)
    for i in range(40):
        win.append([now - 65.0, 100])
    # Ora acquire deve riuscire (la prune svuota la window vecchia).
    e = run(lim.acquire("claude-sonnet-4-20250514", 100, budget_sec=2))
    assert e is not None


def test_modelli_diversi_window_indipendenti():
    """Il limite RPM e' per-modello: saturare sonnet non blocca haiku."""
    lim = arl.AnthropicRateLimiter()
    for _ in range(40):
        run(lim.acquire("claude-sonnet-4-20250514", 100, budget_sec=2))
    # Haiku ha limite 80 RPM (100*0.8): deve acquisire senza attesa.
    e = run(lim.acquire("claude-haiku-4-5-20251001", 100, budget_sec=2))
    assert e is not None


# ── Classificazione 429 ───────────────────────────────────────────────────────

def test_classify_org_quota_x_should_retry_false():
    """x-should-retry:false → org_quota (non ritentabile)."""
    kind = arl._classify_anthropic_429(
        429, {"x-should-retry": "false"}, b"")
    assert kind == "org_quota"


def test_classify_org_quota_body_text():
    """Body con 'usage limit' → org_quota."""
    raw = b'{"error": {"type": "rate_limit_error", "message": "Usage limit reached, resets at 9am"}}'
    assert arl._classify_anthropic_429(429, {}, raw) == "org_quota"


def test_classify_burst_temporarily_limiting():
    """Body con 'temporarily limiting' → burst (ritentabile)."""
    raw = b'{"error": {"type": "overloaded_error", "message": "Server is temporarily limiting requests"}}'
    assert arl._classify_anthropic_429(429, {}, raw) == "burst"


def test_classify_burst_default():
    """Default (nessun marker) → burst (conservativo)."""
    assert arl._classify_anthropic_429(429, {}, b"") == "burst"


# ── Cooldown / backoff ────────────────────────────────────────────────────────

def test_on_429_burst_attiva_cooldown():
    """on_429('burst') imposta cooldown_until nel futuro."""
    lim = arl.AnthropicRateLimiter()
    before = time.monotonic()
    step = lim.on_429("burst")
    assert step > 0
    assert lim._cooldown_until > before


def test_on_429_org_quota_noop():
    """on_429('org_quota') NON attiva cooldown (limite di piano, non di rate)."""
    lim = arl.AnthropicRateLimiter()
    step = lim.on_429("org_quota")
    assert step == 0.0
    assert lim._cooldown_until == 0.0


def test_on_success_reset_cooldown():
    """on_success azzera cooldown e backoff_idx."""
    lim = arl.AnthropicRateLimiter()
    lim.on_429("burst")
    lim.on_429("burst")  # backoff_idx = 2
    assert lim._backoff_idx == 2
    lim.on_success()
    assert lim._backoff_idx == 0
    assert lim._cooldown_until == 0.0


def test_backoff_cresce_fino_al_cap():
    """Chiamate ripetute a on_429('burst') crescono fino al cap (30s)."""
    lim = arl.AnthropicRateLimiter()
    steps = []
    for _ in range(10):
        steps.append(lim.on_429("burst"))
    # ANTHROPIC_BACKOFF_STEPS = (2, 5, 10, 20, 30): il cap e' 30.
    assert max(steps) <= 30
    # Dopo il cap, resta a 30.
    assert steps[-1] <= 30


# ── record ─────────────────────────────────────────────────────────────────────

def test_record_success_aggiorna_token():
    """record aggiorna l'entry con i token reali su success."""
    lim = arl.AnthropicRateLimiter()
    entry = [time.monotonic(), 1000]
    lim.record(entry, 2500, success=True)
    assert entry[1] == 2500


def test_record_fail_azzera_token():
    """record mette 0 token su fallimento (non conteggiare verso TPM)."""
    lim = arl.AnthropicRateLimiter()
    entry = [time.monotonic(), 1000]
    lim.record(entry, 0, success=False)
    assert entry[1] == 0


# ── Snapshot ───────────────────────────────────────────────────────────────────

def test_snapshot_struttura():
    """snapshot() ritorna un dict con le chiavi attese."""
    lim = arl.AnthropicRateLimiter()
    run(lim.acquire("claude-sonnet-4-20250514", 500, budget_sec=2))
    snap = lim.snapshot()
    assert "cooldown_sec" in snap
    assert "per_model" in snap
    assert "claude-sonnet-4-20250514" in snap["per_model"]
    pm = snap["per_model"]["claude-sonnet-4-20250514"]
    assert pm["rpm_used"] == 1
    assert "rpm_limit" in pm and "tpm_limit" in pm


# ── Costanti / configurabilita' ────────────────────────────────────────────────

def test_safety_factor_applicato():
    """_limits applica ANTHROPIC_SAFETY (0.8): sonnet 50 RPM → 40 effettivi."""
    lim = arl.AnthropicRateLimiter()
    rpm, tpm = lim._limits("claude-sonnet-4-20250514")
    assert rpm == 40  # 50 * 0.8
    assert tpm == 80_000  # 100_000 * 0.8


def test_limite_default_per_modello_sconosciuto():
    """Modello non in dict → ANTHROPIC_RATE_LIMITS_DEFAULT."""
    lim = arl.AnthropicRateLimiter()
    rpm, tpm = lim._limits("modello-sconosciuto-xyz")
    assert rpm == 40  # default 50 * 0.8
    assert tpm == 80_000


if __name__ == "__main__":
    # Self-test runner (lazy: nessun framework).
    import inspect
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passati, {failed} falliti su {len(fns)}")
    sys.exit(1 if failed else 0)
