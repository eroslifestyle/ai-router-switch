"""Rotte delle modalità mixaq (Anthropic THINK + Qwen ACT) e mixql (Qwen THINK + local ACT).

Aggiunte il 2026-10-06 (endpoint Qwen token-plan). Il ruolo è dedotto dal nome del
modello richiesto: un modello THINK (claude-opus/sonnet) attiva ROLE_THINK, Haiku
attiva ROLE_ACT. resolve_route mappa il ruolo sul provider della modalità.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import role_routing as rr  # noqa: E402
import router_constants as rc  # noqa: E402

THINK_MODEL = "claude-opus-5"
ACT_MODEL = "claude-haiku-4-5-20251001"


def test_valid_modes_includono_mixaq_mixql():
    assert "mixaq" in rr.VALID_MODES
    assert "mixql" in rr.VALID_MODES
    assert "mixaq" in rc.VALID_MODES
    assert "mixql" in rc.VALID_MODES


def test_valid_modes_coerenti_tra_moduli():
    # role_routing e router_constants NON devono divergere.
    assert set(rr.VALID_MODES) == set(rc.VALID_MODES)


def test_mixaq_think_resta_anthropic_passthrough():
    # Anthropic THINK = pass-through (model None, scelto manualmente via /model).
    assert rr.resolve_route("mixaq", THINK_MODEL) == ("anthropic", None)


def test_mixaq_act_va_su_qwen_flash():
    # ACT su Qwen: default della cascade policy §2.1 (deepseek-v4.1-flash),
    # NON qwen3.8-flash (vietato §2.4 — timeout). Ladder agent-side.
    assert rr.MIXAQ_ACT == "deepseek-v4.1-flash"
    assert rr.resolve_route("mixaq", ACT_MODEL) == ("qwen", "deepseek-v4.1-flash")


def test_mixql_think_va_su_qwen_max():
    assert rr.resolve_route("mixql", THINK_MODEL) == ("qwen", "qwen3.8-max")


def test_mixql_act_va_sul_modello_locale():
    assert rr.resolve_route("mixql", ACT_MODEL) == ("local", "coder-next-ablit")


def test_qwen_act_non_e_piu_coder_plus():
    # qwen3-coder-plus NON è servito dal token-plan (probe 2026-10-06).
    # qwen pura segue la stessa cascade policy §2.1 di mixaq (utente 2026-10-09).
    assert rr.QWEN_ACT == "deepseek-v4.1-flash"
    assert rr.resolve_route("qwen", ACT_MODEL) == ("qwen", "deepseek-v4.1-flash")


def test_porte_sandbox_mixaq_mixql():
    assert rc.PORT_MODE.get(8789) == "mixaq"
    assert rc.PORT_MODE.get(8791) == "mixql"


def test_default_provider_nuove_modalita():
    assert rr._MODE_DEFAULT_PROVIDER["mixaq"] == "qwen"
    assert rr._MODE_DEFAULT_PROVIDER["mixql"] == "local"
