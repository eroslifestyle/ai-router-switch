#!/usr/bin/env python3
"""Test per resolve_effective_route: funzione unica usata sia dal gate
sia dal dispatch in ai-router-proxy.py.

Verifica che provider/modello siano IDENTICI sia per il gate sia per il
dispatch (ovviamente la stessa funzione, ma verifica i casi concreti).
"""
import json
import sys
sys.path.insert(0, "src")

import peak_scheduler as ps
import importlib


def run_with_hour(hour, func, *args, modulo=None, giorno=0, **kwargs):
    """Esegue func con orario simulato (stesso pattern di test_peak_scheduler)."""
    target = modulo if modulo is not None else ps
    original = target.datetime
    class FakeDatetime:
        _hour = hour
        _giorno = giorno
        @classmethod
        def now(cls, tz=None):
            class MockTime:
                def __init__(self, h, g):
                    self.hour = h
                    self._g = g
                def weekday(self):
                    return self._g
            return MockTime(cls._hour, cls._giorno)
    target.datetime = FakeDatetime
    try:
        return func(*args, **kwargs)
    finally:
        target.datetime = original


def make_body(model="claude-opus-4-8", with_image=False, max_tokens=4096) -> bytes:
    content = [{"type": "text", "text": "hello"}]
    if with_image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}})
    return json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens
    }).encode()


def test_anthropic_pure_no_change():
    """anthropic: risolve a se stesso."""
    from role_routing import resolve_effective_route
    body = make_body(model="claude-haiku-4-5")
    prov, model = resolve_effective_route("anthropic", "claude-haiku-4-5", body)
    assert prov == "anthropic", f"Atteso anthropic, ottenuto {prov}"
    assert model is None, f"Atteso None, ottenuto {model}"


def _large_body(size_kb: int) -> bytes:
    """Body con testo grande per simulare token > act_limit."""
    large_text = "x" * (size_kb * 1024)
    return json.dumps({
        "model": "MiniMax-M2.7",
        "messages": [{"role": "user", "content": large_text}],
        "max_tokens": 4096
    }).encode()


def test_minimax_oversize_reroutes_to_m3():
    """minimax puro oversize -> reroute a MiniMax-M3."""
    from role_routing import resolve_effective_route
    # 900k byte ~= 225k token > M2.7 safe limit (~163k)
    body = _large_body(900)
    prov, model = resolve_effective_route("minimax", "MiniMax-M2.7", body)
    assert prov == "minimax", f"Atteso minimax, ottenuto {prov}"
    assert model == "MiniMax-M3", f"Atteso MiniMax-M3, ottenuto {model}"


def test_minimax_small_body_no_reroute():
    """minimax puro sotto soglia -> no reroute (model coincide con ACT -> None)."""
    from role_routing import resolve_effective_route
    body = make_body(model="MiniMax-M2.7")
    prov, model = resolve_effective_route("minimax", "MiniMax-M2.7", body)
    assert prov == "minimax"
    # ACT = MiniMax-M2.7, quindi override = None (nessuna riscrittura necessaria)
    assert model is None, f"Atteso None (model coincide con ACT), ottenuto {model}"


def test_glm_with_image_routes_to_vision():
    """GLM con immagini -> glm-4.6V."""
    from role_routing import resolve_effective_route
    body = make_body(model="glm-5.3", with_image=True)
    prov, model = resolve_effective_route("glm", "glm-5.3", body)
    assert prov == "glm"
    assert model == "glm-4.6V", f"Atteso glm-4.6V, ottenuto {model}"


def test_glm_without_image_no_vision():
    """GLM senza immagini -> non cambia."""
    from role_routing import resolve_effective_route
    body = make_body(model="glm-4.7", with_image=False)
    prov, model = resolve_effective_route("glm", "glm-4.7", body)
    assert prov == "glm"
    assert model == "glm-4.7"


def test_glm_in_peak_hours_capped():
    """GLM glm-5.3 in fascia peak -> glm-4.7."""
    from role_routing import resolve_effective_route
    ps_vivo = importlib.import_module("peak_scheduler")
    body = make_body(model="glm-5.3")
    prov, model = run_with_hour(15, resolve_effective_route, "glm", "glm-5.3", body, modulo=ps_vivo)
    assert prov == "glm"
    assert model == "glm-4.7", f"Atteso glm-4.7 (peak cap), ottenuto {model}"


def test_glm_out_of_peak_hours_not_capped():
    """GLM glm-5.3 fuori fascia peak -> glm-5.3."""
    from role_routing import resolve_effective_route
    ps_vivo = importlib.import_module("peak_scheduler")
    body = make_body(model="glm-5.3")
    prov, model = run_with_hour(9, resolve_effective_route, "glm", "glm-5.3", body, modulo=ps_vivo)
    assert prov == "glm"
    assert model == "glm-5.3", f"Atteso glm-5.3 (no peak), ottenuto {model}"


def test_glm_image_plus_peak_routes_to_vision():
    """GLM con immagini in fascia peak -> glm-4.6V (vision survives peak cap)."""
    from role_routing import resolve_effective_route
    ps_vivo = importlib.import_module("peak_scheduler")
    body = make_body(model="glm-5.3", with_image=True)
    prov, model = run_with_hour(15, resolve_effective_route, "glm", "glm-5.3", body, modulo=ps_vivo)
    assert prov == "glm"
    assert model == "glm-4.6V", f"Atteso glm-4.6V (vision survives peak cap), ottenuto {model}"


def test_mix_gm_oversize_reroutes():
    """mix-gm oversize -> reroute a glm-5.3."""
    """mix-gm oversize -> reroute a glm-5.3.

    Pinnata fuori fascia peak (ora 9, stesso pattern dei test gemelli sopra):
    senza run_with_hour il test chiama datetime.now() reale e fallisce ogni
    pomeriggio feriale (peak-cap lun-ven 14-18 Asia/Shanghai declassa a
    glm-4.7 correttamente) — non e' un bug di resolve_effective_route, e'
    l'oversize-reroute che va isolato dal peak-cap, non confuso con esso.
    """
    from role_routing import resolve_effective_route
    ps_vivo = importlib.import_module("peak_scheduler")
    body = _large_body(900)
    prov, model = run_with_hour(9, resolve_effective_route, "mix-gm", "MiniMax-M2.7", body, modulo=ps_vivo)
    assert prov == "glm", f"Atteso glm, ottenuto {prov}"
    assert model == "glm-5.3", f"Atteso glm-5.3, ottenuto {model}"


def test_mix_am_no_reroute():
    """mix-am: THINK=None -> no reroute."""
    from role_routing import resolve_effective_route
    body = _large_body(900)
    prov, model = resolve_effective_route("mix-am", "claude-opus-4-8", body)
    assert prov == "anthropic"
    assert model is None


def test_ultra_no_reroute():
    """ultra: esclusa esplicitamente."""
    from role_routing import resolve_effective_route
    body = _large_body(900)
    prov, model = resolve_effective_route("ultra", "glm-4.7", body)
    assert prov == "glm"
    assert model == "glm-4.7"


def test_gate_and_dispatch_return_same_value():
    """Il valore di ritorno e' lo stesso (stessa funzione) per gate e dispatch."""
    from role_routing import resolve_effective_route
    # Body grande ma JSON valido
    body = _large_body(100)
    body_with_max = json.dumps({"model": "MiniMax-M2.7", "messages": [{"role": "user", "content": "x" * 100000}], "max_tokens": 4096}).encode()
    gate_prov, gate_model = resolve_effective_route("minimax", "MiniMax-M2.7", body_with_max, max_output=4096)
    dispatch_prov, dispatch_model = resolve_effective_route("minimax", "MiniMax-M2.7", body_with_max, max_output=4096)
    assert gate_prov == dispatch_prov, f"provider diverge: {gate_prov} != {dispatch_prov}"
    assert gate_model == dispatch_model, f"model diverge: {gate_model} != {dispatch_model}"


if __name__ == "__main__":
    tests = [
        test_anthropic_pure_no_change,
        test_minimax_oversize_reroutes_to_m3,
        test_minimax_small_body_no_reroute,
        test_glm_with_image_routes_to_vision,
        test_glm_without_image_no_vision,
        test_glm_in_peak_hours_capped,
        test_glm_out_of_peak_hours_not_capped,
        test_glm_image_plus_peak_routes_to_vision,
        test_mix_gm_oversize_reroutes,
        test_mix_am_no_reroute,
        test_ultra_no_reroute,
        test_gate_and_dispatch_return_same_value,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {t.__name__}: {e}")
            failed += 1

    print(f"\nRisultato: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
