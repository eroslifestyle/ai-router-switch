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


def run_tests():
    """Run all tests and print results."""
    # Run pytest with minimal output
    exit_code = pytest.main([__file__, "-v", "--tb=short"])
    return exit_code


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
