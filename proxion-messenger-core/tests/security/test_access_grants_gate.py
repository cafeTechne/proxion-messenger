"""Tests for access grants feature gate."""
import os
import pytest
from unittest.mock import patch


class TestAccessGrantsDisabledByDefault:
    def test_access_grants_disabled_without_env(self):
        """access_grants_enabled() returns False when env var is absent."""
        from proxion_messenger_core.solid_migration import access_grants_enabled
        env = os.environ.copy()
        env.pop("PROXION_ENABLE_ACCESS_GRANTS", None)
        with patch.dict(os.environ, env, clear=True):
            assert not access_grants_enabled()

    def test_access_grants_disabled_when_0(self):
        from proxion_messenger_core.solid_migration import access_grants_enabled
        with patch.dict(os.environ, {"PROXION_ENABLE_ACCESS_GRANTS": "0"}):
            assert not access_grants_enabled()

    def test_access_grants_env_default_is_0_in_env_example(self):
        """The .env.example must declare PROXION_ENABLE_ACCESS_GRANTS=0."""
        from pathlib import Path
        env_example = Path(__file__).parents[3] / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "PROXION_ENABLE_ACCESS_GRANTS=0" in content, (
            ".env.example must set PROXION_ENABLE_ACCESS_GRANTS=0 (disabled by default)"
        )


class TestAccessGrantsRequiresFeatureFlag:
    def test_access_grants_enabled_when_flag_is_1(self):
        from proxion_messenger_core.solid_migration import access_grants_enabled
        with patch.dict(os.environ, {"PROXION_ENABLE_ACCESS_GRANTS": "1"}):
            assert access_grants_enabled()

    def test_access_grants_matrix_doc_has_not_in_scope_section(self):
        """The migration matrix must have a 'Not in Scope' section for access grants."""
        from pathlib import Path
        matrix = Path(__file__).parents[3] / "docs" / "solid_sdk_migration_matrix.md"
        content = matrix.read_text(encoding="utf-8")
        assert "Not in Scope" in content
        assert "access grant" in content.lower()


class TestAccessGrantsErrorsNormalized:
    def test_solid_not_supported_code_is_defined(self):
        from proxion_messenger_core.solid_migration import SOLID_NOT_SUPPORTED
        assert SOLID_NOT_SUPPORTED == "SOLID_NOT_SUPPORTED"

    def test_error_record_uses_normalised_code(self):
        """Errors can be recorded with normalised codes."""
        from proxion_messenger_core.solid_migration import migration_store, SOLID_NOT_SUPPORTED
        before = migration_store._counts.get(SOLID_NOT_SUPPORTED, {}).get("legacy", 0)
        migration_store.record(SOLID_NOT_SUPPORTED, "legacy")
        after = migration_store._counts[SOLID_NOT_SUPPORTED]["legacy"]
        assert after == before + 1
