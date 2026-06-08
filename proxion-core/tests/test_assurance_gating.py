"""Tests: governance/assurance subsystem is opt-in by default (R35).

Locks in the design that a default gateway does no governance work unless
the operator explicitly enables it. Guards against regressions that would
make assurance/integrity checks run (and potentially fail startup) for
ordinary users.
"""
from __future__ import annotations
import os
import pytest
from unittest.mock import patch


def test_continuous_assurance_disabled_by_default():
    """is_continuous_assurance_enabled() is False unless explicitly enabled."""
    from proxion_messenger_core.continuous_assurance import is_continuous_assurance_enabled
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PROXION_ENABLE_CONTINUOUS_ASSURANCE", None)
        assert is_continuous_assurance_enabled() is False


def test_continuous_assurance_opt_in():
    """Setting the flag enables the assurance loop."""
    from proxion_messenger_core.continuous_assurance import is_continuous_assurance_enabled
    with patch.dict(os.environ, {"PROXION_ENABLE_CONTINUOUS_ASSURANCE": "1"}):
        assert is_continuous_assurance_enabled() is True


def test_runtime_integrity_startup_noop_by_default():
    """check_runtime_integrity_startup is a no-op unless PROXION_REQUIRE_RUNTIME_INTEGRITY=1."""
    from proxion_messenger_core.supply_chain import check_runtime_integrity_startup
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PROXION_REQUIRE_RUNTIME_INTEGRITY", None)
        # Must return cleanly without raising and without doing verification work.
        with patch("proxion_messenger_core.supply_chain.verify_runtime_integrity") as mock_vri:
            check_runtime_integrity_startup(store=None)
            mock_vri.assert_not_called()


def test_provenance_guard_noop_by_default():
    """enforce_provenance_guard is a no-op unless PROXION_REQUIRE_BUILD_PROVENANCE=1."""
    from proxion_messenger_core.provenance_verify import enforce_provenance_guard
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PROXION_REQUIRE_BUILD_PROVENANCE", None)
        enforce_provenance_guard()  # must not raise
