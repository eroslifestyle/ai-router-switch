import sys
sys.path.insert(0, 'src')
from role_routing import resolve_route, MODE_ALIASES, VALID_MODES
from router_constants import VALID_MODES as VALID_MODES_CONST


def test_mix_ag_2_not_in_valid_modes():
    """mix-ag-2 è assorbita in mix-ag: non deve essere in VALID_MODES."""
    assert "mix-ag-2" not in VALID_MODES, "mix-ag-2 non deve essere in VALID_MODES"
    assert "mix-ag-2" not in VALID_MODES_CONST, "mix-ag-2 non deve essere in VALID_MODES_CONST"


def test_mix_ag_2_in_legacy_map():
    """mix-ag-2 è in MODE_ALIASES e si mapparebbe a mix-ag."""
    assert MODE_ALIASES.get("mix-ag-2") == "mix-ag", "mix-ag-2 deve mappare a mix-ag"


def test_resolve_route_accepts_mix_ag_2():
    """resolve_route accetta mix-ag-2 come alias di mix-ag."""
    provider1, model1 = resolve_route("mix-ag", "claude-haiku")
    provider2, model2 = resolve_route("mix-ag-2", "claude-haiku")
    assert provider1 == provider2, f"Provider deve essere identico: {provider1} vs {provider2}"
    assert model1 == model2, f"Model override deve essere identico: {model1} vs {model2}"


def test_resolve_route_mix_ag_is_glm_act():
    """mix-ag deve risolvere a GLM per l'ACT (haiku = ROLE_ACT)."""
    provider, model_override = resolve_route("mix-ag", "claude-haiku")
    assert provider == "glm", f"Atteso provider='glm' per mix-ag + haiku, ottenuto '{provider}'"


def test_resolve_route_mix_ag_2_is_glm_act():
    """mix-ag-2 (via alias) deve risolvere a GLM per l'ACT."""
    provider, model_override = resolve_route("mix-ag-2", "claude-haiku")
    assert provider == "glm", f"Atteso provider='glm' per mix-ag-2 + haiku, ottenuto '{provider}'"
