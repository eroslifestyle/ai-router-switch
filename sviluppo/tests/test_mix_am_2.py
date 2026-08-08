"""Regressione per la modalità mix-am-2 (delega aggressiva).

mix-am-2 deve avere routing IDENTICO a mix-am (THINK->Anthropic, ACT->MiniMax)
ed essere raggiungibile come mode canonica e tramite alias mixam2 da entrambi
gli ingressi (!router in-chat e la normalizzazione). La differenza reale sta in
enforce_hierarchy (deny mode-specific), testato a parte con HOME finto.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import role_routing  # noqa: E402
from role_routing import resolve_route, VALID_MODES as RR_MODES  # noqa: E402
from router_constants import VALID_MODES as RC_MODES, PORT_MODE  # noqa: E402
from router_commands import parse_router_command  # noqa: E402

_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-fable-5",
           "claude-haiku-4-5-20251001", "MiniMax-M2.7"]


def test_mix_am_2_in_valid_modes():
    assert "mix-am-2" in RR_MODES
    assert "mix-am-2" in RC_MODES


def test_mix_am_2_ha_porta_dedicata():
    assert "mix-am-2" in PORT_MODE.values()


def test_routing_uguale_a_mix_am_per_ogni_ruolo():
    for m in _MODELS:
        assert resolve_route("mix-am-2", m) == resolve_route("mix-am", m), (
            f"divergenza routing su modello {m}")


def test_think_anthropic_act_minimax():
    assert resolve_route("mix-am-2", "claude-opus-5") == ("anthropic", None)
    assert resolve_route("mix-am-2", "claude-haiku-4-5-20251001") == ("minimax", "MiniMax-M2.7")


def test_alias_mixam2_risolve_canonico():
    assert parse_router_command("!router mixam2") == {"action": "set", "mode": "mix-am-2"}
    assert parse_router_command("!router mix-am-2") == {"action": "set", "mode": "mix-am-2"}
