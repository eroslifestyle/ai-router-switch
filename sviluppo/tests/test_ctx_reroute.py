#!/usr/bin/env python3
"""Test per ctx-reroute: reindirizzamento automatico su THINK quando il body
supera la finestra dell'ACT nelle modalita' miste.
"""

import sys
sys.path.insert(0, "src")

from role_routing import reroute_if_oversized


def test_mix_gm_oversize_reroutes():
    r = reroute_if_oversized('mix-gm', 'minimax', 'MiniMax-M2.7', 900_000)
    assert r == ('glm', 'glm-5.3'), f"Atteso ('glm', 'glm-5.3'), ottenuto {r}"


def test_mix_gm_2_oversize_reroutes():
    r = reroute_if_oversized('mix-gm-2', 'minimax', 'MiniMax-M2.7', 900_000)
    assert r == ('glm', 'glm-5.3'), f"Atteso ('glm', 'glm-5.3'), ottenuto {r}"


def test_mix_gm_small_body_no_reroute():
    r = reroute_if_oversized('mix-gm', 'minimax', 'MiniMax-M2.7', 100_000)
    assert r is None, f"Atteso None, ottenuto {r}"


def test_mix_am_2_oversize_no_reroute():
    r = reroute_if_oversized('mix-am-2', 'minimax', 'MiniMax-M2.7', 900_000)
    assert r is None, f"Atteso None, ottenuto {r}"


def test_mix_ag_2_oversize_no_reroute():
    r = reroute_if_oversized('mix-ag-2', 'glm', 'glm-4.7', 900_000)
    assert r is None, f"Atteso None, ottenuto {r}"


def test_pure_modes_no_reroute():
    for mode in ['anthropic', 'minimax', 'glm', 'qwen', 'local', 'mix-al', 'gpt']:
        r = reroute_if_oversized(mode, 'minimax', 'MiniMax-M2.7', 900_000)
        assert r is None, f"{mode}: atteso None, ottenuto {r}"


def test_ultra_no_reroute():
    r = reroute_if_oversized('ultra', 'glm', 'glm-4.7', 900_000)
    assert r is None, f"Atteso None, ottenuto {r}"


def test_mix_am_no_reroute():
    r = reroute_if_oversized('mix-am', 'minimax', 'MiniMax-M2.7', 900_000)
    assert r is None, f"Atteso None, ottenuto {r}"


def test_mix_ag_no_reroute():
    r = reroute_if_oversized('mix-ag', 'glm', 'glm-4.7', 900_000)
    assert r is None, f"Atteso None, ottenuto {r}"


if __name__ == "__main__":
    tests = [
        test_mix_gm_oversize_reroutes,
        test_mix_gm_2_oversize_reroutes,
        test_mix_gm_small_body_no_reroute,
        test_mix_am_2_oversize_no_reroute,
        test_mix_ag_2_oversize_no_reroute,
        test_pure_modes_no_reroute,
        test_ultra_no_reroute,
        test_mix_am_no_reroute,
        test_mix_ag_no_reroute,
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
