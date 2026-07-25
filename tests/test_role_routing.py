"""Tests for role_routing module.

Tests the pure routing function for LLM proxy. Covers:
- model_role classification (THINK, ACT, unknown)
- resolve_route for all 12 combinations (6 modes × 2 roles)
- unknown role routing (should use mode default)
- invalid mode handling
- invariant: non-Anthropic modes never route to Anthropic for any role/input
"""

import sys
from pathlib import Path

# Add src/ to path so we can import role_routing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
import role_routing as rr


class TestModelRole:
    """Test model_role() classification."""

    def test_role_think_opus(self):
        assert rr.model_role("claude-opus-5") == rr.ROLE_THINK

    def test_role_think_sonnet(self):
        assert rr.model_role("claude-sonnet-5") == rr.ROLE_THINK

    def test_role_think_fable(self):
        assert rr.model_role("claude-fable-5") == rr.ROLE_THINK

    def test_role_think_uppercase(self):
        """Uppercase variants should also match."""
        assert rr.model_role("CLAUDE-OPUS-5") == rr.ROLE_THINK
        assert rr.model_role("Claude-Sonnet-5") == rr.ROLE_THINK

    def test_role_act_haiku(self):
        assert rr.model_role("claude-haiku-4-5-20251001") == rr.ROLE_ACT

    def test_role_act_haiku_simple(self):
        assert rr.model_role("claude-haiku-3") == rr.ROLE_ACT

    def test_role_act_haiku_uppercase(self):
        assert rr.model_role("CLAUDE-HAIKU-4-5") == rr.ROLE_ACT

    def test_role_unknown_minimax(self):
        assert rr.model_role("MiniMax-M3") == rr.ROLE_UNKNOWN

    def test_role_unknown_glm(self):
        assert rr.model_role("glm-4.7") == rr.ROLE_UNKNOWN

    def test_role_unknown_empty_string(self):
        assert rr.model_role("") == rr.ROLE_UNKNOWN

    def test_role_unknown_none(self):
        assert rr.model_role(None) == rr.ROLE_UNKNOWN

    def test_role_unknown_arbitrary_string(self):
        assert rr.model_role("some-random-model") == rr.ROLE_UNKNOWN


class TestResolveRouteValidModes:
    """Test resolve_route() for all valid modes and roles."""

    def test_anthropic_think(self):
        provider, model_override = rr.resolve_route("anthropic", "claude-opus-5")
        assert provider == "anthropic"
        assert model_override is None

    def test_anthropic_act(self):
        provider, model_override = rr.resolve_route("anthropic", "claude-haiku-4-5-20251001")
        assert provider == "anthropic"
        assert model_override is None

    def test_minimax_think(self):
        provider, model_override = rr.resolve_route("minimax", "claude-opus-5")
        assert provider == "minimax"
        assert model_override == rr.MINIMAX_THINK

    def test_minimax_act(self):
        provider, model_override = rr.resolve_route("minimax", "claude-haiku-4-5-20251001")
        assert provider == "minimax"
        assert model_override == rr.MINIMAX_ACT

    def test_glm_think(self):
        provider, model_override = rr.resolve_route("glm", "claude-sonnet-5")
        assert provider == "glm"
        assert model_override == rr.GLM_THINK

    def test_glm_act(self):
        provider, model_override = rr.resolve_route("glm", "claude-haiku-3")
        assert provider == "glm"
        assert model_override == rr.GLM_ACT

    def test_mix_am_think(self):
        """mix-am: THINK -> Anthropic."""
        provider, model_override = rr.resolve_route("mix-am", "claude-opus-5")
        assert provider == "anthropic"
        assert model_override is None

    def test_mix_am_act(self):
        """mix-am: ACT -> MiniMax."""
        provider, model_override = rr.resolve_route("mix-am", "claude-haiku-4-5-20251001")
        assert provider == "minimax"
        assert model_override == rr.MINIMAX_ACT

    def test_mix_ag_think(self):
        """mix-ag: THINK -> Anthropic."""
        provider, model_override = rr.resolve_route("mix-ag", "claude-fable-5")
        assert provider == "anthropic"
        assert model_override is None

    def test_mix_ag_act(self):
        """mix-ag: ACT -> GLM."""
        provider, model_override = rr.resolve_route("mix-ag", "claude-haiku-3")
        assert provider == "glm"
        assert model_override == rr.GLM_ACT

    def test_mix_gm_think(self):
        """mix-gm: THINK -> GLM."""
        provider, model_override = rr.resolve_route("mix-gm", "claude-sonnet-5")
        assert provider == "glm"
        assert model_override == rr.GLM_THINK

    def test_mix_gm_act(self):
        """mix-gm: ACT -> MiniMax."""
        provider, model_override = rr.resolve_route("mix-gm", "claude-haiku-4-5-20251001")
        assert provider == "minimax"
        assert model_override == rr.MINIMAX_ACT


class TestResolveRouteUnknownRole:
    """Test resolve_route() with unknown model role (uses mode default)."""

    def test_anthropic_unknown_defaults_to_anthropic(self):
        """anthropic mode + unknown role -> anthropic provider, no override."""
        provider, model_override = rr.resolve_route("anthropic", "MiniMax-M3")
        assert provider == "anthropic"
        assert model_override is None

    def test_minimax_unknown_defaults_to_minimax(self):
        """minimax mode + unknown role -> minimax provider, no override."""
        provider, model_override = rr.resolve_route("minimax", "glm-4.7")
        assert provider == "minimax"
        assert model_override is None

    def test_glm_unknown_defaults_to_glm(self):
        """glm mode + unknown role -> glm provider, no override."""
        provider, model_override = rr.resolve_route("glm", "MiniMax-M3")
        assert provider == "glm"
        assert model_override is None

    def test_mix_am_unknown_defaults_to_minimax(self):
        """mix-am mode + unknown role -> minimax provider (mode default), no override."""
        provider, model_override = rr.resolve_route("mix-am", "MiniMax-M3")
        assert provider == "minimax"
        assert model_override is None

    def test_mix_ag_unknown_defaults_to_glm(self):
        """mix-ag mode + unknown role -> glm provider (mode default), no override."""
        provider, model_override = rr.resolve_route("mix-ag", "MiniMax-M3")
        assert provider == "glm"
        assert model_override is None

    def test_mix_gm_unknown_defaults_to_minimax(self):
        """mix-gm mode + unknown role -> minimax provider (mode default), no override."""
        provider, model_override = rr.resolve_route("mix-gm", "glm-4.7")
        assert provider == "minimax"
        assert model_override is None


class TestResolveRouteInvalidMode:
    """Test resolve_route() with invalid mode."""

    def test_invalid_mode_raises_value_error(self):
        with pytest.raises(ValueError) as exc_info:
            rr.resolve_route("invalid-mode", "claude-opus-5")
        assert "Invalid mode" in str(exc_info.value)
        assert "invalid-mode" in str(exc_info.value)
        assert "Valid modes are" in str(exc_info.value)

    def test_invalid_mode_lists_valid_options(self):
        with pytest.raises(ValueError) as exc_info:
            rr.resolve_route("unknown", "claude-haiku-3")
        error_msg = str(exc_info.value)
        for mode in rr.VALID_MODES:
            assert mode in error_msg


class TestInvariantNonAnthropicProviders:
    """Invariant: minimax, glm, mix-gm never route to Anthropic for any role or input."""

    def test_minimax_never_anthropic_think(self):
        """minimax with THINK should never route to Anthropic."""
        provider, _ = rr.resolve_route("minimax", "claude-opus-5")
        assert provider != "anthropic", "minimax mode should never use Anthropic provider"

    def test_minimax_never_anthropic_act(self):
        """minimax with ACT should never route to Anthropic."""
        provider, _ = rr.resolve_route("minimax", "claude-haiku-3")
        assert provider != "anthropic", "minimax mode should never use Anthropic provider"

    def test_minimax_never_anthropic_unknown(self):
        """minimax with unknown role should never route to Anthropic."""
        provider, _ = rr.resolve_route("minimax", "MiniMax-M3")
        assert provider != "anthropic", "minimax mode should never use Anthropic provider"

    def test_glm_never_anthropic_think(self):
        """glm with THINK should never route to Anthropic."""
        provider, _ = rr.resolve_route("glm", "claude-sonnet-5")
        assert provider != "anthropic", "glm mode should never use Anthropic provider"

    def test_glm_never_anthropic_act(self):
        """glm with ACT should never route to Anthropic."""
        provider, _ = rr.resolve_route("glm", "claude-haiku-3")
        assert provider != "anthropic", "glm mode should never use Anthropic provider"

    def test_glm_never_anthropic_unknown(self):
        """glm with unknown role should never route to Anthropic."""
        provider, _ = rr.resolve_route("glm", "glm-4.7")
        assert provider != "anthropic", "glm mode should never use Anthropic provider"

    def test_mix_gm_never_anthropic_think(self):
        """mix-gm with THINK should never route to Anthropic."""
        provider, _ = rr.resolve_route("mix-gm", "claude-fable-5")
        assert provider != "anthropic", "mix-gm mode should never use Anthropic provider"

    def test_mix_gm_never_anthropic_act(self):
        """mix-gm with ACT should never route to Anthropic."""
        provider, _ = rr.resolve_route("mix-gm", "claude-haiku-4-5-20251001")
        assert provider != "anthropic", "mix-gm mode should never use Anthropic provider"

    def test_mix_gm_never_anthropic_unknown(self):
        """mix-gm with unknown role should never route to Anthropic."""
        provider, _ = rr.resolve_route("mix-gm", "MiniMax-M3")
        assert provider != "anthropic", "mix-gm mode should never use Anthropic provider"


class TestVerifyFollowsThink:
    """Il VERIFY non ha una rotta propria: usa quella del THINK.

    La gerarchia ha tre fasi ma il routing due ruoli, perché il VERIFY lo esegue
    lo stesso modello che ha fatto il THINK. Questi test rendono la scelta
    esplicita e la bloccano: se qualcuno un giorno aggiungesse una rotta VERIFY
    divergente, o cambiasse quella del THINK dimenticando il verify, qui si rompe.
    """

    THINK_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5")

    def test_verify_alias_points_to_think(self):
        assert rr.ROLE_VERIFY == rr.ROLE_THINK

    def test_verify_route_equals_think_route_every_mode(self):
        """Per ogni modalità, verificare costa esattamente come pensare."""
        for mode in rr.VALID_MODES:
            think = rr.ROUTING_TABLE[(mode, rr.ROLE_THINK)]
            verify = rr.ROUTING_TABLE[(mode, rr.ROLE_VERIFY)]
            assert think == verify, f"{mode}: VERIFY {verify} diverge da THINK {think}"

    def test_verify_never_lands_on_the_executor(self):
        """Chi verifica non deve essere chi ha eseguito, nelle modalità miste."""
        for mode in ("mix-am", "mix-ag", "mix-gm"):
            verify_provider, _ = rr.ROUTING_TABLE[(mode, rr.ROLE_VERIFY)]
            act_provider, _ = rr.ROUTING_TABLE[(mode, rr.ROLE_ACT)]
            assert verify_provider != act_provider, (
                f"{mode}: il verify finirebbe sullo stesso provider dell'esecutore "
                f"({verify_provider}) — l'esecutore non può validare se stesso"
            )

    def test_verify_reaches_think_provider_for_real_model_names(self):
        """Una richiesta di verifica porta il nome del modello THINK: deve bastare."""
        for mode in rr.VALID_MODES:
            expected = rr.ROUTING_TABLE[(mode, rr.ROLE_THINK)]
            for model in self.THINK_MODELS:
                assert rr.resolve_route(mode, model) == expected


class TestModeListCoherence:
    """role_routing ridefinisce VALID_MODES invece di importarlo.

    È voluto: importare router_constants creerebbe un ciclo (router_constants
    importa glm_backend, e un ciclo lì spegne silenziosamente GLM_AVAILABLE).
    Il prezzo è una seconda fonte di verità: questi test la tengono allineata,
    così una modalità aggiunta da una parte e non dall'altra fa fallire la
    suite invece di produrre un routing incoerente a runtime.
    """

    def test_same_mode_set_as_router_constants(self):
        import router_constants as rc
        assert set(rr.VALID_MODES) == set(rc.VALID_MODES), (
            f"disallineate: solo in role_routing={set(rr.VALID_MODES) - set(rc.VALID_MODES)}, "
            f"solo in router_constants={set(rc.VALID_MODES) - set(rr.VALID_MODES)}"
        )

    def test_routing_table_covers_every_mode(self):
        """Ogni modalità valida deve avere una rotta per THINK e per ACT."""
        for mode in rr.VALID_MODES:
            for role in (rr.ROLE_THINK, rr.ROLE_ACT):
                assert (mode, role) in rr.ROUTING_TABLE, f"manca la rotta ({mode}, {role})"

    def test_every_mode_has_a_default_provider(self):
        """Il ruolo sconosciuto non deve mai far esplodere resolve_route."""
        for mode in rr.VALID_MODES:
            provider, override = rr.resolve_route(mode, "un-modello-mai-visto")
            assert provider in ("anthropic", "minimax", "glm")
            assert override is None


def run_tests():
    """Run all tests and print results."""
    # Run pytest with minimal output
    exit_code = pytest.main([__file__, "-v", "--tb=short"])
    return exit_code


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
